from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import json
import os
from pathlib import Path
import re
from typing import Any
import uuid

import pandas as pd

from db import database_enabled, ensure_database_ready, get_db_connection, isoformat_seconds
from sales_analytics import load_input_file, prepare_sales_data


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("APP_DATA_DIR", str(BASE_DIR / "data"))).resolve()
UPLOADS_DIR = DATA_DIR / "uploads"
SALONS_PATH = DATA_DIR / "salons.json"
MANIFEST_PATH = DATA_DIR / "upload_manifest.csv"

MANIFEST_COLUMNS = [
    "upload_id",
    "salon",
    "report_date",
    "source_filename",
    "stored_path",
    "uploaded_at",
    "csv_separator",
    "csv_encoding",
    "sheet_name",
    "mapping_json",
]


@dataclass
class ArchiveLoadResult:
    data: pd.DataFrame
    manifest: pd.DataFrame
    warnings: list[str]


def ensure_store() -> None:
    if database_enabled():
        ensure_database_ready()
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        return
    DATA_DIR.mkdir(exist_ok=True)
    UPLOADS_DIR.mkdir(exist_ok=True)

    if not SALONS_PATH.exists():
        SALONS_PATH.write_text("[]", encoding="utf-8")

    if not MANIFEST_PATH.exists():
        pd.DataFrame(columns=MANIFEST_COLUMNS).to_csv(MANIFEST_PATH, index=False, encoding="utf-8-sig")


def _slugify(value: str) -> str:
    cleaned = re.sub(r"\s+", "-", value.strip().casefold())
    cleaned = re.sub(r"[^\w\-]+", "", cleaned, flags=re.UNICODE)
    return cleaned or "salon"


def load_salons() -> list[str]:
    ensure_store()
    if database_enabled():
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT name FROM salons ORDER BY LOWER(name)")
                return [str(row.get("name", "")).strip() for row in cursor.fetchall() if str(row.get("name", "")).strip()]
    try:
        salons = json.loads(SALONS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        salons = []

    if not isinstance(salons, list):
        salons = []

    manifest = load_manifest()
    combined = sorted({str(item).strip() for item in salons if str(item).strip()} | set(manifest["salon"].dropna().astype(str)))
    return combined


def save_salon(salon_name: str) -> None:
    ensure_store()
    salon_name = salon_name.strip()
    if not salon_name:
        return

    if database_enabled():
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO salons (name, created_at)
                    VALUES (%s, %s)
                    ON CONFLICT (name) DO NOTHING
                    """,
                    (salon_name, datetime.now().isoformat(timespec="seconds")),
                )
        return

    salons = load_salons()
    if salon_name not in salons:
        salons.append(salon_name)
        SALONS_PATH.write_text(json.dumps(sorted(salons), ensure_ascii=False, indent=2), encoding="utf-8")


def count_uploads_for_salon(salon_name: str) -> int:
    manifest = load_manifest()
    if manifest.empty:
        return 0
    return int((manifest["salon"].astype(str).str.casefold() == salon_name.strip().casefold()).sum())


def delete_salon(salon_name: str, *, remove_uploads: bool = False) -> dict[str, int]:
    ensure_store()
    normalized_name = salon_name.strip()
    if not normalized_name:
        raise ValueError("Укажите салон для удаления.")

    salons = load_salons()
    if normalized_name not in salons:
        raise ValueError("Салон не найден.")

    manifest = load_manifest()
    if manifest.empty:
        matching_manifest = manifest.copy()
    else:
        matching_manifest = manifest[manifest["salon"].astype(str).str.casefold() == normalized_name.casefold()].copy()

    upload_count = len(matching_manifest)
    deleted_files = 0

    if upload_count and not remove_uploads:
        raise ValueError("У салона есть сохраненные выгрузки. Включите удаление архива, чтобы удалить салон полностью.")

    if upload_count:
        for path_text in matching_manifest["stored_path"].tolist():
            path = Path(str(path_text))
            if path.exists() and path.is_file():
                path.unlink()
                deleted_files += 1
        manifest = manifest.loc[manifest.index.difference(matching_manifest.index)].copy()
        save_manifest(manifest)

    if database_enabled():
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM salons WHERE LOWER(name) = LOWER(%s)", (normalized_name,))
    else:
        salons = [item for item in salons if item.casefold() != normalized_name.casefold()]
        SALONS_PATH.write_text(json.dumps(sorted(salons), ensure_ascii=False, indent=2), encoding="utf-8")

    salon_dir = UPLOADS_DIR / _slugify(normalized_name)
    if salon_dir.exists():
        for child in salon_dir.iterdir():
            if child.is_file():
                child.unlink()
        if not any(salon_dir.iterdir()):
            salon_dir.rmdir()

    return {
        "deleted_uploads": upload_count,
        "deleted_files": deleted_files,
    }


def load_manifest() -> pd.DataFrame:
    ensure_store()
    if database_enabled():
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        upload_id,
                        salon,
                        report_date,
                        source_filename,
                        stored_path,
                        uploaded_at,
                        csv_separator,
                        csv_encoding,
                        COALESCE(sheet_name, '') AS sheet_name,
                        mapping_json
                    FROM uploads
                    ORDER BY salon, report_date, uploaded_at
                    """
                )
                rows = cursor.fetchall()
        if not rows:
            return pd.DataFrame(columns=MANIFEST_COLUMNS)
        manifest_rows = []
        for row in rows:
            manifest_rows.append(
                {
                    "upload_id": str(row.get("upload_id", "")).strip(),
                    "salon": str(row.get("salon", "")).strip(),
                    "report_date": str(row.get("report_date", "")),
                    "source_filename": str(row.get("source_filename", "")).strip(),
                    "stored_path": str(row.get("stored_path", "")).strip(),
                    "uploaded_at": isoformat_seconds(row.get("uploaded_at")),
                    "csv_separator": str(row.get("csv_separator", "") or ";"),
                    "csv_encoding": str(row.get("csv_encoding", "") or "utf-8"),
                    "sheet_name": str(row.get("sheet_name", "") or ""),
                    "mapping_json": json.dumps(row.get("mapping_json") or {}, ensure_ascii=False),
                }
            )
        return pd.DataFrame(manifest_rows, columns=MANIFEST_COLUMNS)
    manifest = pd.read_csv(MANIFEST_PATH, encoding="utf-8-sig")
    for column in MANIFEST_COLUMNS:
        if column not in manifest.columns:
            manifest[column] = ""
    return manifest[MANIFEST_COLUMNS]


def save_manifest(manifest: pd.DataFrame) -> None:
    ensure_store()
    if database_enabled():
        normalized = manifest.copy()
        for column in MANIFEST_COLUMNS:
            if column not in normalized.columns:
                normalized[column] = ""
        normalized = normalized[MANIFEST_COLUMNS]
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM uploads")
                for row in normalized.to_dict(orient="records"):
                    mapping_value = row.get("mapping_json", "{}")
                    try:
                        mapping_json = json.loads(mapping_value) if mapping_value else {}
                    except (TypeError, json.JSONDecodeError):
                        mapping_json = {}
                    cursor.execute(
                        """
                        INSERT INTO uploads (
                            upload_id,
                            salon,
                            report_date,
                            source_filename,
                            stored_path,
                            uploaded_at,
                            csv_separator,
                            csv_encoding,
                            sheet_name,
                            mapping_json
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                        """,
                        (
                            str(row.get("upload_id", "")).strip(),
                            str(row.get("salon", "")).strip(),
                            str(row.get("report_date", "")).strip(),
                            str(row.get("source_filename", "")).strip(),
                            str(row.get("stored_path", "")).strip(),
                            str(row.get("uploaded_at", "")).strip() or datetime.now().isoformat(timespec="seconds"),
                            str(row.get("csv_separator", "") or ";"),
                            str(row.get("csv_encoding", "") or "utf-8"),
                            str(row.get("sheet_name", "") or ""),
                            json.dumps(mapping_json, ensure_ascii=False),
                        ),
                    )
        return
    manifest[MANIFEST_COLUMNS].to_csv(MANIFEST_PATH, index=False, encoding="utf-8-sig")


def _normalize_sheet_name(sheet_name: str | int | None) -> str:
    if sheet_name is None:
        return ""
    return str(sheet_name)


def _parse_sheet_name(sheet_name: Any) -> str | int | None:
    if sheet_name is None or (isinstance(sheet_name, float) and pd.isna(sheet_name)):
        return 0
    text = str(sheet_name).strip()
    if not text:
        return 0
    return int(text) if text.isdigit() else text


def register_upload(
    *,
    file_bytes: bytes,
    filename: str,
    salon: str,
    report_date: date,
    mapping: dict[str, str | None],
    csv_separator: str = ";",
    csv_encoding: str = "utf-8",
    sheet_name: str | int | None = 0,
    replace_existing: bool = True,
) -> dict[str, Any]:
    ensure_store()
    save_salon(salon)

    manifest = load_manifest()
    report_date_text = report_date.isoformat()
    upload_id = uuid.uuid4().hex[:12]
    uploaded_at = datetime.now().isoformat(timespec="seconds")
    extension = Path(filename).suffix or ".bin"

    salon_dir = UPLOADS_DIR / _slugify(salon)
    salon_dir.mkdir(parents=True, exist_ok=True)
    stored_path = salon_dir / f"{report_date_text}__{upload_id}{extension}"

    replaced = 0
    if replace_existing and not manifest.empty:
        duplicate_mask = (manifest["salon"].astype(str) == salon) & (manifest["report_date"].astype(str) == report_date_text)
        duplicates = manifest[duplicate_mask]
        replaced = len(duplicates)
        for path_text in duplicates["stored_path"].tolist():
            path = Path(path_text)
            if path.exists():
                path.unlink()
        manifest = manifest.loc[~duplicate_mask].copy()

    stored_path.write_bytes(file_bytes)

    record = {
        "upload_id": upload_id,
        "salon": salon,
        "report_date": report_date_text,
        "source_filename": filename,
        "stored_path": str(stored_path),
        "uploaded_at": uploaded_at,
        "csv_separator": csv_separator,
        "csv_encoding": csv_encoding,
        "sheet_name": _normalize_sheet_name(sheet_name),
        "mapping_json": json.dumps(mapping, ensure_ascii=False),
    }

    manifest = pd.concat([manifest, pd.DataFrame([record])], ignore_index=True)
    manifest = manifest.sort_values(["salon", "report_date", "uploaded_at"]).reset_index(drop=True)
    save_manifest(manifest)

    return {
        "record": record,
        "replaced": replaced,
    }


def load_archive_data(
    *,
    salons: list[str] | None = None,
) -> ArchiveLoadResult:
    manifest = load_manifest()
    warnings: list[str] = []

    if manifest.empty:
        return ArchiveLoadResult(data=pd.DataFrame(), manifest=manifest, warnings=warnings)

    if salons:
        manifest = manifest[manifest["salon"].astype(str).isin(salons)].copy()

    frames: list[pd.DataFrame] = []

    for row in manifest.to_dict(orient="records"):
        stored_path = Path(str(row["stored_path"]))
        if not stored_path.exists():
            warnings.append(f"Не найден архивный файл: {stored_path}")
            continue

        try:
            raw_data = load_input_file(
                stored_path.read_bytes(),
                str(row["source_filename"]),
                csv_separator=str(row["csv_separator"] or ";"),
                csv_encoding=str(row["csv_encoding"] or "utf-8"),
                sheet_name=_parse_sheet_name(row["sheet_name"]),
            )
            mapping = json.loads(str(row["mapping_json"]))
            prepared = prepare_sales_data(raw_data, mapping)
            frame = prepared.data.copy()
            frame["salon"] = str(row["salon"])
            frame["report_date"] = pd.to_datetime(str(row["report_date"]), errors="coerce")
            frame["source_filename"] = str(row["source_filename"])
            frames.append(frame)

            for warning in prepared.warnings:
                warnings.append(f"{row['salon']} / {row['report_date']}: {warning}")
        except Exception as error:
            warnings.append(f"{row['salon']} / {row['report_date']}: не удалось обработать файл ({error})")

    if not frames:
        return ArchiveLoadResult(data=pd.DataFrame(), manifest=manifest, warnings=warnings)

    data = pd.concat(frames, ignore_index=True)
    return ArchiveLoadResult(data=data, manifest=manifest, warnings=warnings)
