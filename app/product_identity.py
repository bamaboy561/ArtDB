from __future__ import annotations

from functools import lru_cache
import re
import unicodedata
from typing import Any, Callable

import numpy as np
import pandas as pd


_PRODUCT_TOKEN_PATTERN = re.compile(r"[0-9a-zа-яё]+", re.IGNORECASE)
_DIMENSION_SEPARATOR_PATTERN = re.compile(r"(?<=\d)\s*[xх×*]\s*(?=\d)")
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


def map_unique(series: pd.Series, func: Callable[[Any], Any]) -> pd.Series:
    """Apply ``func`` once per distinct value: sales frames repeat the same SKU thousands of times."""
    if series.empty:
        return pd.Series([], index=series.index, dtype=object)
    codes, uniques = pd.factorize(series, use_na_sentinel=False)
    mapped = np.empty(len(uniques), dtype=object)
    mapped[:] = [func(value) for value in uniques]
    return pd.Series(mapped[codes], index=series.index)


@lru_cache(maxsize=200_000)
def _product_match_key_from_str(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).strip().casefold().replace("ё", "е")
    if not text:
        return ""

    text = _DIMENSION_SEPARATOR_PATTERN.sub(" x ", text)
    return " ".join(_PRODUCT_TOKEN_PATTERN.findall(text))


def normalize_product_match_key(value: Any) -> str:
    """Build a conservative SKU key that ignores cosmetic separators and spacing."""
    if isinstance(value, str):
        return _product_match_key_from_str(value)
    text = _text(value)
    return _product_match_key_from_str(text) if text else ""


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

    category_text = map_unique(refined["category"], _text).replace("", "Без категории")
    category_keys = map_unique(category_text, normalize_product_match_key)
    context = map_unique(refined["product"], _text)
    for column in context_columns:
        context = context.str.cat(map_unique(refined[column], _text), sep=" ")

    mdf_mask = map_unique(context, lambda value: bool(_MDF_TOKEN_PATTERN.search(value))).astype(bool)
    reclassify_mask = category_keys.isin(_DEFAULT_CATEGORY_LABELS) & mdf_mask
    refined["category"] = category_text
    refined.loc[reclassify_mask, "category"] = "МДФ панели"
    return refined
