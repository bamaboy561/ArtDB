from __future__ import annotations

import re
import unicodedata
from typing import Any

import pandas as pd


_PRODUCT_TOKEN_PATTERN = re.compile(r"[0-9a-zа-яё]+", re.IGNORECASE)
_MDF_TOKEN_PATTERN = re.compile(
    r"(?<![0-9a-zа-яё])(?:mdf|мдф)(?![0-9a-zа-яё])",
    re.IGNORECASE,
)
_DEFAULT_CATEGORY_LABELS = {"", "без категории", "не указана", "лдсп"}


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return unicodedata.normalize("NFKC", str(value)).strip()


def normalize_product_match_key(value: Any) -> str:
    """Build a conservative SKU key that ignores cosmetic separators and spacing."""
    text = _text(value).casefold().replace("ё", "е")
    if not text:
        return ""

    text = re.sub(r"(?<=\d)\s*[xх×*]\s*(?=\d)", " x ", text)
    return " ".join(_PRODUCT_TOKEN_PATTERN.findall(text))


def refine_product_category(
    product: Any,
    category: Any,
    *context_values: Any,
) -> str:
    category_text = _text(category) or "Без категории"
    category_key = normalize_product_match_key(category_text)
    if category_key not in _DEFAULT_CATEGORY_LABELS:
        return category_text

    context = " ".join(
        value
        for value in (_text(product), *(_text(item) for item in context_values))
        if value
    )
    if _MDF_TOKEN_PATTERN.search(context):
        return "МДФ панели"
    return category_text


def apply_product_category_rules(
    data: pd.DataFrame,
    *,
    copy_data: bool = True,
) -> pd.DataFrame:
    if data.empty or "product" not in data.columns:
        return data

    refined = data.copy() if copy_data else data
    if "category" not in refined.columns:
        refined["category"] = "Без категории"

    context_columns = [
        column
        for column in refined.columns
        if str(column).strip().casefold()
        in {
            "group",
            "category path",
            "группа",
            "путь категории",
        }
    ]

    category_text = refined["category"].map(_text).replace("", "Без категории")
    category_keys = category_text.map(normalize_product_match_key)
    context = refined["product"].map(_text)
    for column in context_columns:
        context = context.str.cat(refined[column].map(_text), sep=" ")

    mdf_mask = context.str.contains(_MDF_TOKEN_PATTERN, na=False)
    reclassify_mask = category_keys.isin(_DEFAULT_CATEGORY_LABELS) & mdf_mask
    refined["category"] = category_text
    refined.loc[reclassify_mask, "category"] = "МДФ панели"
    return refined
