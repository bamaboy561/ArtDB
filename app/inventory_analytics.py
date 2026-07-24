from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Iterable

import pandas as pd

from sales_analytics import _normalize_number_string, coerce_numeric, normalize_column_name


INVENTORY_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "product": (
        "номенклатура",
        "номенклатура товара",
        "товар",
        "наименование",
        "наименование товара",
        "sku",
        "product",
        "item",
    ),
    "stock_on_hand": (
        "остаток",
        "остаток на складе",
        "остаток конечный",
        "остаток текущий",
        "количество остаток",
        "количество",
        "кол во",
        "кол-во",
        "qty",
        "quantity",
        "available stock",
        "stock",
        "stock on hand",
        "on hand",
        "qty on hand",
    ),
    "stock_value": (
        "сумма",
        "сумма остатка",
        "сумма склада",
        "стоимость",
        "стоимость остатка",
        "стоимость запасов",
        "остаток сумма",
        "stock value",
        "inventory value",
        "warehouse value",
    ),
    "stock_in_transit": (
        "в пути",
        "товар в пути",
        "ожидается",
        "поставка в пути",
        "stock in transit",
        "in transit",
    ),
    "supplier": (
        "поставщик",
        "контрагент",
        "supplier",
        "vendor",
    ),
    "brand": (
        "бренд",
        "торговая марка",
        "марка",
        "производитель",
        "brand",
        "manufacturer",
    ),
    "min_order_qty": (
        "moq",
        "минимальный заказ",
        "минимальная партия",
        "мин партия",
        "min order qty",
        "minimum order",
    ),
    "order_multiple": (
        "кратность",
        "кратно",
        "шаг заказа",
        "order multiple",
        "multiple",
        "pack size",
    ),
    "lead_time_days": (
        "срок поставки",
        "дней поставки",
        "срок доставки",
        "lead time",
        "lead time days",
    ),
    "notes": (
        "примечание",
        "комментарий",
        "заметка",
        "notes",
        "comment",
    ),
}


@dataclass
class PreparedInventoryData:
    data: pd.DataFrame
    warnings: list[str]


def _normalize_header_row(row: list[object]) -> list[str]:
    headers: list[str] = []
    used_headers: dict[str, int] = {}
    for index, value in enumerate(row):
        base = _header_cell_text(value)
        if not base:
            base = f"column_{index + 1}"
        counter = used_headers.get(base, 0)
        used_headers[base] = counter + 1
        headers.append(base if counter == 0 else f"{base}_{counter + 1}")
    return headers


def _header_cell_text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _looks_like_inventory_header(row: list[object]) -> bool:
    normalized_values = [normalize_column_name(value) if value is not None else "" for value in row]
    has_product = any(value in {"номенклатура", "товар", "наименование"} for value in normalized_values)
    has_quantity = any(
        "колич" in value
        or value
        in {
            "остаток",
            "остаток на складе",
            "остаток конечный",
            "остаток текущий",
            "stock",
            "stock on hand",
            "quantity",
            "qty",
        }
        for value in normalized_values
        if value
    )
    return has_product and has_quantity


def _parse_1c_inventory_report(file_bytes: bytes, sheet_name: str | int | None = 0) -> pd.DataFrame | None:
    try:
        preview = pd.read_excel(BytesIO(file_bytes), sheet_name=sheet_name, header=None)
    except Exception:
        return None

    if preview.empty:
        return None

    headers: list[str] | None = None
    data_start_index: int | None = None
    for row_index, row in preview.head(40).iterrows():
        if _looks_like_inventory_header(row.tolist()):
            headers = _normalize_header_row(row.tolist())
            data_start_index = int(row_index) + 1
            break

    if headers is None:
        preview_rows = min(len(preview), 40)
        for row_index in range(max(0, preview_rows - 1)):
            upper_row = preview.iloc[row_index].tolist()
            lower_row = preview.iloc[row_index + 1].tolist()
            combined_row = [
                _header_cell_text(lower_row[column_index]) or _header_cell_text(upper_row[column_index])
                for column_index in range(len(preview.columns))
            ]
            if _looks_like_inventory_header(combined_row):
                headers = _normalize_header_row(combined_row)
                data_start_index = row_index + 2
                break

    if headers is None or data_start_index is None:
        return None

    data = preview.iloc[data_start_index:].copy()
    data.columns = headers
    data = data.dropna(how="all").reset_index(drop=True)
    if data.empty:
        return None

    unit_column = next(
        (
            column
            for column in data.columns
            if normalize_column_name(column) in {"ед изм", "единица измерения", "ед изм_2"}
            or "ед" in normalize_column_name(column) and "изм" in normalize_column_name(column)
        ),
        None,
    )
    if unit_column is not None:
        unit_series = data[unit_column].fillna("").astype(str).str.strip()
        filtered = data[unit_series != ""].copy()
        if not filtered.empty:
            data = filtered.reset_index(drop=True)

    return data


def load_inventory_input_file(
    file_bytes: bytes,
    filename: str,
    *,
    csv_separator: str = ";",
    csv_encoding: str = "utf-8",
    sheet_name: str | int | None = 0,
) -> pd.DataFrame:
    if filename.lower().endswith(".csv"):
        return pd.read_csv(BytesIO(file_bytes), sep=csv_separator, encoding=csv_encoding)

    parsed_1c_report = _parse_1c_inventory_report(file_bytes, sheet_name=sheet_name)
    if parsed_1c_report is not None:
        return parsed_1c_report

    return pd.read_excel(BytesIO(file_bytes), sheet_name=sheet_name)


def guess_inventory_column_mapping(columns: Iterable[str]) -> dict[str, str | None]:
    normalized_columns = {column: normalize_column_name(column) for column in columns}
    guesses: dict[str, str | None] = {}

    for field, aliases in INVENTORY_COLUMN_ALIASES.items():
        best_column: str | None = None
        best_score = -1
        normalized_aliases = [normalize_column_name(alias) for alias in aliases]

        for column, normalized_column in normalized_columns.items():
            score = 0
            for alias in normalized_aliases:
                alias_tokens = alias.split()
                if normalized_column == alias:
                    score = max(score, 100)
                elif alias in normalized_column:
                    score = max(score, 80)
                elif alias_tokens and all(token in normalized_column for token in alias_tokens):
                    score = max(score, 60)

            if score > best_score:
                best_score = score
                best_column = column

        guesses[field] = best_column if best_score >= 60 else None

    return guesses


def _series_from_mapping(frame: pd.DataFrame, mapping: dict[str, str | None], key: str) -> pd.Series | None:
    column_name = mapping.get(key)
    if not column_name:
        return None
    return frame[column_name]


def _text_present(series: pd.Series | None, index: pd.Index) -> pd.Series:
    if series is None:
        return pd.Series(False, index=index, dtype="bool")
    return series.fillna("").astype(str).str.strip().ne("")


def _numeric_present(series: pd.Series | None, index: pd.Index) -> pd.Series:
    if series is None:
        return pd.Series(False, index=index, dtype="bool")
    return series.map(_normalize_number_string).notna()


def _first_non_empty(series: pd.Series) -> str:
    for value in series:
        text = str(value).strip()
        if text:
            return text
    return ""


def _prepare_numeric_series(series: pd.Series | None, index: pd.Index) -> pd.Series:
    numeric = coerce_numeric(series)
    if numeric is None:
        return pd.Series(pd.NA, index=index, dtype="Float64")
    return numeric.astype("Float64")


def _prepare_int_series(series: pd.Series | None, index: pd.Index) -> pd.Series:
    numeric = coerce_numeric(series)
    if numeric is None:
        return pd.Series(pd.NA, index=index, dtype="Float64")
    return numeric.round().astype("Float64")


def prepare_inventory_data(frame: pd.DataFrame, mapping: dict[str, str | None]) -> PreparedInventoryData:
    resolved_mapping = dict(mapping)
    guessed_mapping = guess_inventory_column_mapping(frame.columns.astype(str).tolist())

    for field, guessed_column in guessed_mapping.items():
        if not resolved_mapping.get(field) and guessed_column in frame.columns:
            resolved_mapping[field] = guessed_column

    if not resolved_mapping.get("product") or not resolved_mapping.get("stock_on_hand"):
        raise ValueError("Для загрузки остатков укажите хотя бы колонки с товаром и остатком.")

    prepared = pd.DataFrame(index=frame.index.copy())
    warnings: list[str] = []

    raw_product = _series_from_mapping(frame, resolved_mapping, "product")
    prepared["product"] = raw_product.fillna("").astype(str).str.strip() if raw_product is not None else ""

    text_fields = ("supplier", "brand", "notes")
    numeric_fields = ("stock_on_hand", "stock_value", "stock_in_transit", "min_order_qty", "order_multiple")
    integer_fields = ("lead_time_days",)

    for field in text_fields:
        raw_series = _series_from_mapping(frame, resolved_mapping, field)
        prepared[field] = raw_series.fillna("").astype(str).str.strip() if raw_series is not None else ""
        prepared[f"__has_{field}"] = _text_present(raw_series, prepared.index)

    for field in numeric_fields:
        raw_series = _series_from_mapping(frame, resolved_mapping, field)
        prepared[field] = _prepare_numeric_series(raw_series, prepared.index)
        prepared[f"__has_{field}"] = _numeric_present(raw_series, prepared.index)

    for field in integer_fields:
        raw_series = _series_from_mapping(frame, resolved_mapping, field)
        prepared[field] = _prepare_int_series(raw_series, prepared.index)
        prepared[f"__has_{field}"] = _numeric_present(raw_series, prepared.index)

    initial_rows = len(prepared)
    prepared = prepared[prepared["product"] != ""].copy()
    dropped_rows = initial_rows - len(prepared)
    if dropped_rows:
        warnings.append(f"Из загрузки исключено строк без названия товара: {dropped_rows}.")

    if prepared.empty:
        raise ValueError("После очистки не осталось строк с товарами. Проверьте файл остатков.")

    prepared["_product_key"] = prepared["product"].str.casefold()

    aggregated_rows: list[dict[str, object]] = []
    duplicate_rows = len(prepared) - int(prepared["_product_key"].nunique())

    for _, group in prepared.groupby("_product_key", sort=False):
        record: dict[str, object] = {
            "product": _first_non_empty(group["product"]),
        }

        for field in text_fields:
            has_field = bool(group[f"__has_{field}"].fillna(False).any())
            record[f"__has_{field}"] = has_field
            record[field] = _first_non_empty(group.loc[group[f"__has_{field}"].fillna(False), field]) if has_field else ""

        for field in numeric_fields:
            has_field = bool(group[f"__has_{field}"].fillna(False).any())
            record[f"__has_{field}"] = has_field
            if has_field:
                values = pd.to_numeric(group.loc[group[f"__has_{field}"].fillna(False), field], errors="coerce").fillna(0.0)
                record[field] = (
                    float(values.sum())
                    if field in {"stock_on_hand", "stock_value", "stock_in_transit"}
                    else float(values.max())
                )
            else:
                record[field] = pd.NA

        for field in integer_fields:
            has_field = bool(group[f"__has_{field}"].fillna(False).any())
            record[f"__has_{field}"] = has_field
            if has_field:
                values = pd.to_numeric(group.loc[group[f"__has_{field}"].fillna(False), field], errors="coerce").dropna()
                record[field] = int(values.max()) if not values.empty else pd.NA
            else:
                record[field] = pd.NA

        aggregated_rows.append(record)

    if duplicate_rows > 0:
        warnings.append(
            f"Найдены повторяющиеся SKU: {duplicate_rows}. Они были объединены по товару, а остатки суммированы."
        )

    prepared_result = pd.DataFrame.from_records(aggregated_rows)
    if prepared_result.empty:
        raise ValueError("Не удалось подготовить остатки к загрузке.")

    return PreparedInventoryData(data=prepared_result, warnings=warnings)
