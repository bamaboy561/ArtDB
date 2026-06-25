from __future__ import annotations

from dataclasses import dataclass
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
        "available stock",
        "stock",
        "stock on hand",
        "on hand",
        "qty on hand",
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

    text_fields = ("supplier", "notes")
    numeric_fields = ("stock_on_hand", "stock_in_transit", "min_order_qty", "order_multiple")
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
                record[field] = float(values.sum()) if field in {"stock_on_hand", "stock_in_transit"} else float(values.max())
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
