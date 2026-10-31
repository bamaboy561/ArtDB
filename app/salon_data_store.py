from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path
import pickle
import re
from typing import Any, Iterable
import uuid

import pandas as pd
import pyarrow as pa

from db import database_enabled, ensure_database_ready, get_db_connection, isoformat_seconds
from sales_analytics import PreparedSalesData, load_input_file, prepare_sales_data


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("APP_DATA_DIR", str(BASE_DIR.parent / "data"))).resolve()
UPLOADS_DIR = Path(os.getenv("APP_UPLOADS_DIR", str(DATA_DIR / "uploads"))).resolve()
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
# Bump this when load_input_file changes the shape of its parsed output.
PARSED_CACHE_VERSION = 1
# Prepared archive files are cached on disk; the key includes the source of the
# preparation code, so any change there invalidates the cache automatically.
_PREPARED_CACHE_CODE_HASH = hashlib.sha1(
    b"".join(
        (BASE_DIR / name).read_bytes() for name in ("sales_analytics.py", "product_identity.py", "pandas_config.py")
    )
).hexdigest()[:12]


FILE_REPORT_COLUMNS = [
    "upload_id",
    "salon",
    "report_date",
    "source_filename",
    "uploaded_at",
    "raw_rows",
    "loaded_rows",
    "excluded_rows",
    "error_rows",
    "overlap_rows",
    "reasons",
    "problem",
]


@dataclass
class ArchiveLoadResult:
    data: pd.DataFrame
    manifest: pd.DataFrame
    warnings: list[str]
    file_report: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=FILE_REPORT_COLUMNS))
    exclusions: pd.DataFrame = field(default_factory=pd.DataFrame)
    overlaps: pd.DataFrame = field(default_factory=pd.DataFrame)


# Columns that SKU aliases and category rules may rewrite after enrichment.
PRE_ALIAS_TRACKED_COLUMNS = ("product_key", "product", "item_code", "category", "brand", "supplier")


@dataclass
class EnrichedArchive:
    """Lives in the process-wide cache, so it is defined outside the Streamlit script:
    an instance of a script-defined class would keep that run's whole namespace alive."""

    result: ArchiveLoadResult
    data_key: str
    pre_alias_rows: pd.DataFrame

    def pre_alias_frame(self) -> pd.DataFrame:
        """Archive rows as they were before SKU aliases were applied (for the alias editor)."""
        frame = self.result.data.copy(deep=False)
        if not self.pre_alias_rows.empty:
            frame.loc[self.pre_alias_rows.index, list(self.pre_alias_rows.columns)] = self.pre_alias_rows
        return frame


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


def _parsed_cache_path(stored_path: Path) -> Path:
    return stored_path.with_name(
        f"{stored_path.name}.parsed-v{PARSED_CACHE_VERSION}.parquet"
    )


def _remove_upload_artifacts(stored_path: Path) -> int:
    removed = 0
    candidates = [
        stored_path,
        *stored_path.parent.glob(f"{stored_path.name}.parsed-v*.parquet"),
        *stored_path.parent.glob(f"{stored_path.name}.prepared-*.pkl"),
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            candidate.unlink()
            removed += 1
    return removed


def _temporary_path(cache_path: Path) -> Path:
    """Several script runs can build one cache at once, so each writes a file of its own."""
    return cache_path.with_name(f"{cache_path.name}.{uuid.uuid4().hex}.tmp")


def _discard_temporary(temporary_path: Path) -> None:
    # A cache that failed to save only costs a rebuild; this must not fail the upload.
    try:
        temporary_path.unlink(missing_ok=True)
    except OSError:
        pass


def _write_parsed_cache(stored_path: Path, frame: pd.DataFrame) -> bool:
    cache_path = _parsed_cache_path(stored_path)
    temporary_path = _temporary_path(cache_path)
    try:
        frame.to_parquet(temporary_path, index=False)
        temporary_path.replace(cache_path)
        for old_cache_path in stored_path.parent.glob(
            f"{stored_path.name}.parsed-v*.parquet"
        ):
            if old_cache_path != cache_path:
                old_cache_path.unlink(missing_ok=True)
        return True
    except Exception:
        _discard_temporary(temporary_path)
        return False


def _read_parsed_cache(stored_path: Path) -> pd.DataFrame | None:
    cache_path = _parsed_cache_path(stored_path)
    if not cache_path.exists():
        return None
    try:
        return pd.read_parquet(cache_path)
    except Exception:
        cache_path.unlink(missing_ok=True)
        return None


def _prepared_cache_path(stored_path: Path, mapping_json: str, supplier_rules: object) -> Path:
    key = hashlib.sha1(
        f"{_PREPARED_CACHE_CODE_HASH}|{pd.__version__}|{mapping_json}|{supplier_rules!r}".encode("utf-8")
    ).hexdigest()[:16]
    return stored_path.with_name(f"{stored_path.name}.prepared-{key}.pkl")


def _read_prepared_cache(cache_path: Path) -> PreparedSalesData | None:
    if not cache_path.exists():
        return None
    try:
        with cache_path.open("rb") as handle:
            prepared = pickle.load(handle)
        return prepared if isinstance(prepared, PreparedSalesData) else None
    except Exception:
        cache_path.unlink(missing_ok=True)
        return None


def _write_prepared_cache(cache_path: Path, prepared: PreparedSalesData) -> bool:
    temporary_path = _temporary_path(cache_path)
    try:
        with temporary_path.open("wb") as handle:
            pickle.dump(prepared, handle, protocol=pickle.HIGHEST_PROTOCOL)
        temporary_path.replace(cache_path)
        return True
    except Exception:
        _discard_temporary(temporary_path)
        return False


def _remove_stale_prepared_caches(directory: Path, current_names: set[str]) -> None:
    """Drop prepared caches left over from older rules or code (one directory scan)."""
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return
    for entry in entries:
        if ".prepared-" in entry.name and entry.name.endswith(".pkl") and entry.name not in current_names:
            Path(entry.path).unlink(missing_ok=True)


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
            deleted_files += _remove_upload_artifacts(path)
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


def _resolve_archive_date_overlaps(data: pd.DataFrame) -> tuple[pd.DataFrame, str | None, pd.DataFrame]:
    helper_columns = [
        "_archive_upload_id",
        "_archive_uploaded_at",
        "_archive_report_date",
    ]
    required_columns = {"date", "salon", *helper_columns}
    if data.empty or not required_columns.issubset(data.columns):
        return data.drop(columns=helper_columns, errors="ignore"), None, pd.DataFrame()

    working = data.copy()
    working["_archive_sales_date"] = pd.to_datetime(
        working["date"], errors="coerce"
    ).dt.normalize()
    working["_archive_uploaded_sort"] = pd.to_datetime(
        working["_archive_uploaded_at"], errors="coerce", utc=True
    )
    working["_archive_report_sort"] = pd.to_datetime(
        working["_archive_report_date"], errors="coerce"
    )

    valid_mask = working["_archive_sales_date"].notna()
    valid = working.loc[valid_mask].copy()
    invalid = working.loc[~valid_mask].copy()
    if valid.empty:
        return working.drop(
            columns=[*helper_columns, "_archive_sales_date", "_archive_uploaded_sort", "_archive_report_sort"],
            errors="ignore",
        ), None, pd.DataFrame()

    coverage_columns = [
        "salon",
        "_archive_sales_date",
        "_archive_upload_id",
        "_archive_uploaded_sort",
        "_archive_report_sort",
    ]
    coverage = valid[coverage_columns].drop_duplicates()
    overlap_counts = coverage.groupby(
        ["salon", "_archive_sales_date"], dropna=False
    ).size()
    overlapping_days = overlap_counts[overlap_counts > 1]
    if overlapping_days.empty:
        return working.drop(
            columns=[*helper_columns, "_archive_sales_date", "_archive_uploaded_sort", "_archive_report_sort"],
            errors="ignore",
        ), None, pd.DataFrame()

    winners = (
        coverage.sort_values(
            [
                "salon",
                "_archive_sales_date",
                "_archive_uploaded_sort",
                "_archive_report_sort",
                "_archive_upload_id",
            ],
            kind="stable",
            na_position="first",
        )
        .drop_duplicates(["salon", "_archive_sales_date"], keep="last")
        [["salon", "_archive_sales_date", "_archive_upload_id"]]
    )
    resolved = valid.merge(
        winners,
        on=["salon", "_archive_sales_date", "_archive_upload_id"],
        how="inner",
        validate="many_to_one",
    )
    superseded = valid[["salon", "_archive_sales_date", "_archive_upload_id"]].merge(
        winners.rename(columns={"_archive_upload_id": "_winner_upload_id"}),
        on=["salon", "_archive_sales_date"],
        how="inner",
    )
    superseded = superseded[superseded["_archive_upload_id"] != superseded["_winner_upload_id"]]
    overlap_details = (
        superseded.groupby(
            ["salon", "_archive_sales_date", "_archive_upload_id", "_winner_upload_id"],
            as_index=False,
        )
        .size()
        .rename(
            columns={
                "_archive_sales_date": "sales_date",
                "_archive_upload_id": "upload_id",
                "_winner_upload_id": "winner_upload_id",
                "size": "rows",
            }
        )
    )
    if not invalid.empty:
        resolved = pd.concat([resolved, invalid], ignore_index=True, sort=False)

    dropped_rows = len(working) - len(resolved)
    affected_salons = overlapping_days.index.get_level_values("salon").nunique()
    clean = resolved.drop(
        columns=[*helper_columns, "_archive_sales_date", "_archive_uploaded_sort", "_archive_report_sort"],
        errors="ignore",
    ).reset_index(drop=True)
    warning = (
        "Обнаружены пересекающиеся выгрузки: "
        f"{len(overlapping_days)} дат в {affected_salons} салонах. "
        "Для каждой даты использована самая свежая выгрузка; "
        f"из повторного подсчёта исключено строк: {dropped_rows}."
    )
    return clean, warning, overlap_details


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
    parsed_data: pd.DataFrame | None = None,
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
            _remove_upload_artifacts(Path(path_text))
        manifest = manifest.loc[~duplicate_mask].copy()

    stored_path.write_bytes(file_bytes)
    cache_written = bool(
        parsed_data is not None
        and not parsed_data.empty
        and _write_parsed_cache(stored_path, parsed_data)
    )

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
        "cache_written": cache_written,
    }


_ARCHIVE_SOURCE_COLUMNS = (
    "salon",
    "report_date",
    "source_filename",
    "_archive_upload_id",
    "_archive_uploaded_at",
    "_archive_report_date",
)


def _concat_archive_frames(frames: list[pd.DataFrame], sources: list[dict[str, Any]]) -> pd.DataFrame:
    """Concatenate prepared files and tag every row with its source upload.

    The tags are added once on the combined frame: adding them per file costs
    more than preparing the data itself on archives with thousands of uploads.
    """
    columns: dict[str, None] = {}
    for frame in frames:
        columns.update(dict.fromkeys(frame.columns))
        columns.update(dict.fromkeys(_ARCHIVE_SOURCE_COLUMNS))
    lengths = [len(frame) for frame in frames]
    data = pd.concat(frames, ignore_index=True)
    frames.clear()
    data = data.drop(columns=[column for column in _ARCHIVE_SOURCE_COLUMNS if column in data.columns])

    def repeated(field: str) -> list[str]:
        return [str(source[field]) for source in sources]

    report_dates = repeated("report_date")
    data["salon"] = pd.Series(repeated("salon")).repeat(lengths).to_numpy()
    data["report_date"] = pd.to_datetime(pd.Series(report_dates), errors="coerce").repeat(lengths).to_numpy()
    data["source_filename"] = pd.Series(repeated("source_filename")).repeat(lengths).to_numpy()
    data["_archive_upload_id"] = pd.Series(repeated("upload_id")).repeat(lengths).to_numpy()
    data["_archive_uploaded_at"] = pd.Series(repeated("uploaded_at")).repeat(lengths).to_numpy()
    data["_archive_report_date"] = pd.Series(report_dates).repeat(lengths).to_numpy()
    return _compact_string_columns(data[list(columns)])


def _compact_string_columns(data: pd.DataFrame) -> pd.DataFrame:
    """Merge the per-file Arrow chunks of string columns into one continuous block.

    Thousands of small chunks waste memory and make every later filter visit each chunk.
    """
    compact = {}
    for column in data.columns:
        values = data[column].array
        chunked = getattr(values, "_pa_array", None)
        if chunked is None or getattr(chunked, "num_chunks", 1) <= 1:
            continue
        try:
            compact[column] = pd.Series(type(values)(pa.chunked_array([chunked.combine_chunks()])), index=data.index)
        except (pa.ArrowInvalid, TypeError):
            continue  # Column too large for one block: keep its chunks.
    if compact:
        data = data.copy(deep=False)
        for column, values in compact.items():
            data[column] = values
    pa.default_memory_pool().release_unused()
    return data


def load_archive_data(
    *,
    salons: list[str] | None = None,
    supplier_rules: Iterable[dict[str, object] | tuple[object, object]] | None = None,
) -> ArchiveLoadResult:
    manifest = load_manifest()
    warnings: list[str] = []

    if manifest.empty:
        return ArchiveLoadResult(data=pd.DataFrame(), manifest=manifest, warnings=warnings)

    if salons:
        manifest = manifest[manifest["salon"].astype(str).isin(salons)].copy()

    frames: list[pd.DataFrame] = []
    file_rows: list[dict[str, Any]] = []
    exclusion_frames: list[pd.DataFrame] = []
    frame_sources: list[dict[str, Any]] = []
    prepared_cache_names: dict[Path, set[str]] = {}
    refreshed_cache_dirs: set[Path] = set()

    def describe_file(row: dict[str, Any], **values: Any) -> dict[str, Any]:
        return {
            "upload_id": str(row["upload_id"]),
            "salon": str(row["salon"]),
            "report_date": str(row["report_date"]),
            "source_filename": str(row["source_filename"]),
            "uploaded_at": str(row["uploaded_at"]),
            "raw_rows": 0,
            "loaded_rows": 0,
            "excluded_rows": 0,
            "error_rows": 0,
            "overlap_rows": 0,
            "reasons": "",
            "problem": "",
            **values,
        }

    for row in manifest.to_dict(orient="records"):
        stored_path = Path(str(row["stored_path"]))
        cache_path = _parsed_cache_path(stored_path)
        prepared_cache_path = _prepared_cache_path(stored_path, str(row["mapping_json"]), supplier_rules)
        prepared_cache_names.setdefault(prepared_cache_path.parent, set()).add(prepared_cache_path.name)
        if not stored_path.exists() and not cache_path.exists() and not prepared_cache_path.exists():
            warnings.append(f"Не найден архивный файл: {stored_path}")
            file_rows.append(describe_file(row, problem="Файл не найден в архиве"))
            continue

        try:
            prepared = _read_prepared_cache(prepared_cache_path)
            if prepared is None:
                raw_data = _read_parsed_cache(stored_path)
                if raw_data is None:
                    raw_data = load_input_file(
                        stored_path.read_bytes(),
                        str(row["source_filename"]),
                        csv_separator=str(row["csv_separator"] or ";"),
                        csv_encoding=str(row["csv_encoding"] or "utf-8"),
                        sheet_name=_parse_sheet_name(row["sheet_name"]),
                    )
                    _write_parsed_cache(stored_path, raw_data)
                mapping = json.loads(str(row["mapping_json"]))
                prepared = prepare_sales_data(raw_data, mapping, supplier_rules=supplier_rules)
                if _write_prepared_cache(prepared_cache_path, prepared):
                    refreshed_cache_dirs.add(prepared_cache_path.parent)
            frame = prepared.data
            frames.append(frame)
            frame_sources.append(row)

            stats = prepared.stats or {}
            reason_counts = stats.get("reason_counts") or {}
            file_rows.append(
                describe_file(
                    row,
                    raw_rows=int(stats.get("raw_rows", len(frame))),
                    loaded_rows=int(stats.get("loaded_rows", len(frame))),
                    excluded_rows=int(stats.get("excluded_rows", 0)),
                    error_rows=int(stats.get("error_rows", 0)),
                    reasons="; ".join(f"{label}: {count}" for label, count in reason_counts.items()),
                )
            )
            if not prepared.exclusions.empty:
                file_exclusions = prepared.exclusions.copy()
                file_exclusions.insert(0, "source_filename", str(row["source_filename"]))
                file_exclusions.insert(0, "report_date", str(row["report_date"]))
                file_exclusions.insert(0, "salon", str(row["salon"]))
                exclusion_frames.append(file_exclusions)

            for warning in prepared.warnings:
                warnings.append(f"{row['salon']} / {row['report_date']}: {warning}")
        except Exception as error:
            warnings.append(f"{row['salon']} / {row['report_date']}: не удалось обработать файл ({error})")
            file_rows.append(describe_file(row, problem=f"Не удалось обработать файл: {error}"))

    for directory in refreshed_cache_dirs:
        _remove_stale_prepared_caches(directory, prepared_cache_names.get(directory, set()))

    file_report = pd.DataFrame(file_rows, columns=FILE_REPORT_COLUMNS)
    exclusions = pd.concat(exclusion_frames, ignore_index=True) if exclusion_frames else pd.DataFrame()

    if not frames:
        return ArchiveLoadResult(
            data=pd.DataFrame(),
            manifest=manifest,
            warnings=warnings,
            file_report=file_report,
            exclusions=exclusions,
        )

    data = _concat_archive_frames(frames, frame_sources)
    data, overlap_warning, overlap_details = _resolve_archive_date_overlaps(data)
    overlaps = pd.DataFrame()
    if overlap_warning:
        warnings.append(overlap_warning)
    if not overlap_details.empty:
        file_lookup = file_report.set_index("upload_id")
        overlaps = overlap_details.assign(
            report_date=overlap_details["upload_id"].map(file_lookup["report_date"]),
            source_filename=overlap_details["upload_id"].map(file_lookup["source_filename"]),
            winner_report_date=overlap_details["winner_upload_id"].map(file_lookup["report_date"]),
            winner_filename=overlap_details["winner_upload_id"].map(file_lookup["source_filename"]),
        )[
            [
                "salon",
                "sales_date",
                "report_date",
                "source_filename",
                "rows",
                "winner_report_date",
                "winner_filename",
            ]
        ].sort_values(["salon", "sales_date"]).reset_index(drop=True)
        overlap_totals = overlap_details.groupby("upload_id")["rows"].sum()
        file_report["overlap_rows"] = (
            file_report["upload_id"].map(overlap_totals).fillna(0).astype(int)
        )
    return ArchiveLoadResult(
        data=data,
        manifest=manifest,
        warnings=warnings,
        file_report=file_report,
        exclusions=exclusions,
        overlaps=overlaps,
    )
