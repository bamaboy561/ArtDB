"""Sales as every part of the server sees them: one archive, enriched once, shared.

The old site, the new site, the Telegram bot and the daily report all read sales
through load_sales_archive. The archive of every salon is built and enriched once
(suppliers, brands, SKU attributes, aliases, categories) and each caller gets a cheap
view of its salons. Building it again only when uploads or settings change, and never
two builds at once, is what keeps the server inside 512 MB.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import threading
import time
from typing import Sequence
import uuid

import pandas_config  # noqa: F401
import pandas as pd

from procurement_store import load_procurement_items
from product_identity import apply_product_category_rules, map_unique, normalize_product_match_key
from sales_analytics import apply_brand_supplier, fill_supplier_from_text
from salon_data_store import (
    PRE_ALIAS_TRACKED_COLUMNS,
    ArchiveLoadResult,
    EnrichedArchive,
    load_archive_data,
    load_manifest,
    load_salons,
)
from sku_catalog_store import (
    apply_sku_aliases,
    apply_sku_attribute_overrides,
    load_sku_aliases,
    load_sku_attribute_overrides,
)
from supplier_rules_store import load_supplier_keyword_rules, load_supplier_product_assignments


def changed_tracked_rows(before: pd.DataFrame, after: pd.DataFrame) -> pd.DataFrame:
    """Original values of the rows that aliases or category rules rewrote."""
    columns = [column for column in PRE_ALIAS_TRACKED_COLUMNS if column in before.columns and column in after.columns]
    if not columns or before.empty:
        return pd.DataFrame()
    changed = pd.Series(False, index=before.index)
    for column in columns:
        old_values = before[column].astype(object)
        new_values = after[column].astype(object)
        changed |= ~(old_values.eq(new_values) | (old_values.isna() & new_values.isna()))
    return before.loc[changed, columns].copy()


def supplier_rule_items_from_frame(frame: pd.DataFrame) -> tuple[tuple[str, str], ...]:
    if frame.empty or not {"supplier", "keyword"}.issubset(frame.columns):
        return ()

    if "is_active" in frame.columns:
        active_frame = frame[frame["is_active"].fillna(True).astype(bool)].copy()
    else:
        active_frame = frame.copy()

    items = []
    seen_keywords: set[str] = set()
    for row in active_frame.to_dict(orient="records"):
        supplier = str(row.get("supplier", "")).strip()
        keyword = str(row.get("keyword", "")).strip()
        keyword_key = keyword.casefold()
        if supplier and keyword and keyword_key not in seen_keywords:
            seen_keywords.add(keyword_key)
            items.append((supplier, keyword))
    return tuple(items)


def enrich_sales_with_supplier(
    data: pd.DataFrame,
    procurement_items: pd.DataFrame,
    supplier_rule_items: tuple[tuple[str, str], ...] = (),
    supplier_product_assignments: pd.DataFrame | None = None,
    *,
    copy_data: bool = True,
) -> pd.DataFrame:
    if data.empty:
        return data

    enriched = data.copy() if copy_data else data
    if "supplier" not in enriched.columns:
        enriched["supplier"] = ""

    supplier_text = enriched["supplier"].fillna("").astype(str).str.strip()
    missing_supplier = supplier_text.eq("") | supplier_text.str.casefold().eq("не назначен")
    enriched["supplier"] = supplier_text

    if not procurement_items.empty and {"product", "supplier"}.issubset(procurement_items.columns):
        supplier_lookup_source = procurement_items.copy()
        supplier_lookup_source["product_lookup_key"] = supplier_lookup_source["product"].map(
            normalize_product_match_key
        )
        supplier_lookup_source["supplier"] = supplier_lookup_source["supplier"].fillna("").astype(str).str.strip()
        supplier_lookup_source = supplier_lookup_source[
            supplier_lookup_source["product_lookup_key"].ne("") & supplier_lookup_source["supplier"].ne("")
        ]

        if not supplier_lookup_source.empty:
            supplier_lookup = (
                supplier_lookup_source.drop_duplicates("product_lookup_key", keep="last")
                .set_index("product_lookup_key")["supplier"]
                .to_dict()
            )
            product_lookup = map_unique(enriched["product"], normalize_product_match_key)
            mapped_supplier = product_lookup.map(supplier_lookup)

            if "product_key" in enriched.columns:
                product_key_lookup = map_unique(enriched["product_key"], normalize_product_match_key)
                mapped_supplier = mapped_supplier.fillna(product_key_lookup.map(supplier_lookup))

            fill_mask = missing_supplier & mapped_supplier.fillna("").astype(str).str.strip().ne("")
            enriched.loc[fill_mask, "supplier"] = mapped_supplier.loc[fill_mask].astype(str).str.strip()

    fill_supplier_from_text(enriched, supplier_rule_items)

    if (
        supplier_product_assignments is not None
        and not supplier_product_assignments.empty
        and {"product_key", "supplier"}.issubset(supplier_product_assignments.columns)
    ):
        assignment_source = supplier_product_assignments.copy()
        assignment_source["assignment_key"] = (
            assignment_source["product_key"].fillna("").astype(str).str.strip().str.casefold()
        )
        assignment_source["supplier"] = assignment_source["supplier"].fillna("").astype(str).str.strip()
        assignment_source = assignment_source[
            assignment_source["assignment_key"].ne("") & assignment_source["supplier"].ne("")
        ]
        if not assignment_source.empty:
            assignment_lookup = (
                assignment_source.drop_duplicates("assignment_key", keep="last")
                .set_index("assignment_key")["supplier"]
                .to_dict()
            )
            if "product_key" in enriched.columns:
                product_key_lookup = enriched["product_key"].fillna("").astype(str).str.strip().str.casefold()
            else:
                product_key_lookup = enriched["product"].fillna("").astype(str).str.strip().str.casefold()
            assigned_supplier = product_key_lookup.map(assignment_lookup)

            product_lookup = enriched["product"].fillna("").astype(str).str.strip().str.casefold()
            assigned_supplier = assigned_supplier.fillna(product_lookup.map(assignment_lookup))
            assigned_mask = assigned_supplier.fillna("").astype(str).str.strip().ne("")
            if assigned_mask.any():
                enriched.loc[assigned_mask, "supplier"] = assigned_supplier.loc[assigned_mask].astype(str).str.strip()

    enriched["supplier"] = enriched["supplier"].fillna("").astype(str).str.strip().replace("", "Не назначен")
    return enriched


def enrich_sales_with_brand(
    data: pd.DataFrame,
    procurement_items: pd.DataFrame,
    *,
    copy_data: bool = True,
) -> pd.DataFrame:
    if data.empty:
        return data

    enriched = data.copy() if copy_data else data
    enriched["brand"] = (
        enriched.get("brand", pd.Series("", index=enriched.index))
        .fillna("")
        .astype(str)
        .str.strip()
    )
    if procurement_items.empty or not {"product", "brand"}.issubset(procurement_items.columns):
        enriched["brand"] = enriched["brand"].replace("", "Не назначен")
        return enriched

    brand_lookup_source = procurement_items[["product", "brand"]].copy()
    brand_lookup_source["product_lookup_key"] = brand_lookup_source["product"].map(
        normalize_product_match_key
    )
    brand_lookup_source["brand"] = (
        brand_lookup_source["brand"].fillna("").astype(str).str.strip()
    )
    brand_lookup_source = brand_lookup_source[
        brand_lookup_source["product_lookup_key"].ne("")
        & brand_lookup_source["brand"].ne("")
    ]
    if not brand_lookup_source.empty:
        brand_lookup = (
            brand_lookup_source.drop_duplicates("product_lookup_key", keep="last")
            .set_index("product_lookup_key")["brand"]
            .to_dict()
        )
        product_lookup = map_unique(enriched["product"], normalize_product_match_key)
        mapped_brand = product_lookup.map(brand_lookup)
        if "product_key" in enriched.columns:
            product_key_lookup = map_unique(enriched["product_key"], normalize_product_match_key)
            mapped_brand = mapped_brand.fillna(product_key_lookup.map(brand_lookup))

        missing_brand = enriched["brand"].eq("") | enriched["brand"].str.casefold().eq("не назначен")
        fill_mask = missing_brand & mapped_brand.fillna("").astype(str).str.strip().ne("")
        enriched.loc[fill_mask, "brand"] = mapped_brand.loc[fill_mask].astype(str).str.strip()

    enriched["brand"] = enriched["brand"].fillna("").astype(str).str.strip().replace("", "Не назначен")
    return enriched


def dataframe_cache_key(frame: pd.DataFrame) -> str:
    if frame.empty:
        return f"empty:{','.join(map(str, frame.columns))}"

    try:
        row_hashes = pd.util.hash_pandas_object(frame, index=True, categorize=True).values
        digest = hashlib.blake2b(row_hashes.tobytes(), digest_size=16).hexdigest()
    except (TypeError, ValueError):
        digest_source = (
            len(frame),
            tuple(map(str, frame.columns)),
            tuple(map(str, frame.dtypes)),
            tuple(map(str, frame.index[[0, -1]])),
        )
        digest = hashlib.blake2b(repr(digest_source).encode("utf-8"), digest_size=16).hexdigest()
    return f"{len(frame)}:{len(frame.columns)}:{digest}"


@dataclass(frozen=True)
class SalesReferences:
    """What enrichment reads besides the uploads; the old site passes the copies it already has."""

    procurement_items: pd.DataFrame
    supplier_rule_items: tuple[tuple[str, str], ...]
    supplier_product_assignments: pd.DataFrame
    sku_attribute_overrides: pd.DataFrame
    sku_aliases: pd.DataFrame

    def key(self) -> str:
        frames = (
            self.procurement_items,
            self.supplier_product_assignments,
            self.sku_attribute_overrides,
            self.sku_aliases,
        )
        return ":".join([*(dataframe_cache_key(frame) for frame in frames), repr(self.supplier_rule_items)])


def load_references() -> SalesReferences:
    aliases = load_sku_aliases()
    rules = load_supplier_keyword_rules(include_inactive=True)
    if not rules.empty and "is_active" in rules.columns:
        rules = rules[rules["is_active"].fillna(True).astype(bool)]
    return SalesReferences(
        procurement_items=apply_sku_aliases(load_procurement_items(), aliases, aggregate_inventory=True),
        supplier_rule_items=supplier_rule_items_from_frame(rules),
        supplier_product_assignments=load_supplier_product_assignments(),
        sku_attribute_overrides=load_sku_attribute_overrides(),
        sku_aliases=aliases,
    )


def enrich_archive(result: ArchiveLoadResult, references: SalesReferences) -> EnrichedArchive:
    """Suppliers, brands, SKU attributes, aliases and categories, as the site shows them."""
    pre_alias_rows = pd.DataFrame()
    if not result.data.empty:
        data = enrich_sales_with_supplier(
            result.data,
            references.procurement_items,
            supplier_rule_items=references.supplier_rule_items,
            supplier_product_assignments=references.supplier_product_assignments,
            copy_data=False,
        )
        data = enrich_sales_with_brand(data, references.procurement_items, copy_data=False)
        data = apply_sku_attribute_overrides(data, references.sku_attribute_overrides, copy_data=False)
        data = apply_brand_supplier(data, references.supplier_product_assignments, copy_data=False)
        tracked = [column for column in PRE_ALIAS_TRACKED_COLUMNS if column in data.columns]
        before_aliases = data[tracked].copy()
        data = apply_sku_aliases(data, references.sku_aliases, copy_data=False)
        data = apply_product_category_rules(data, copy_data=False)
        pre_alias_rows = changed_tracked_rows(before_aliases, data)
        result.data = data
    return EnrichedArchive(result=result, data_key=uuid.uuid4().hex, pre_alias_rows=pre_alias_rows)


# The archive is dropped after an hour nobody asked for it, so a quiet server gives the memory back.
IDLE_SECONDS = 3600
_lock = threading.Lock()
_archive: EnrichedArchive | None = None
_archive_key: tuple[object, ...] | None = None
_last_used = 0.0


def forget_sales_archive() -> None:
    global _archive, _archive_key
    with _lock:
        _archive = None
        _archive_key = None


def _salon_view(archive: EnrichedArchive, salons: Sequence[str]) -> EnrichedArchive:
    """The chosen salons only. Every salon is handed out as shallow copies, which cost nothing."""
    result = archive.result
    chosen = {str(salon) for salon in salons}
    data = result.data
    every_salon = data.empty or "salon" not in data.columns or bool(data["salon"].isin(chosen).all())
    if not every_salon:
        data = data[data["salon"].isin(chosen)]

    def only_chosen(frame: pd.DataFrame) -> pd.DataFrame:
        if every_salon or frame.empty or "salon" not in frame.columns:
            return frame.copy(deep=False)
        return frame[frame["salon"].astype(str).isin(chosen)].reset_index(drop=True)

    warnings = [
        warning
        for warning in result.warnings
        if every_salon or " / " not in warning or warning.split(" / ", 1)[0] in chosen
    ]
    pre_alias_rows = archive.pre_alias_rows
    if not every_salon and not pre_alias_rows.empty:
        pre_alias_rows = pre_alias_rows[pre_alias_rows.index.isin(data.index)]
    view_key = hashlib.blake2b(repr(sorted(chosen)).encode("utf-8"), digest_size=8).hexdigest()
    return EnrichedArchive(
        result=ArchiveLoadResult(
            data=data.copy(deep=False),
            manifest=only_chosen(result.manifest),
            warnings=warnings,
            file_report=only_chosen(result.file_report),
            exclusions=only_chosen(result.exclusions),
            overlaps=only_chosen(result.overlaps),
        ),
        data_key=f"{archive.data_key}:{view_key}",
        pre_alias_rows=pre_alias_rows.copy(deep=False),
    )


def load_sales_archive(
    salons: Sequence[str] | None = None,
    references: SalesReferences | None = None,
) -> EnrichedArchive:
    """Enriched sales of the chosen salons; of every registered salon when none are chosen."""
    global _archive, _archive_key, _last_used
    references = references or load_references()
    registered = [str(salon) for salon in load_salons()]
    chosen = [str(salon) for salon in salons] if salons else registered
    manifest = load_manifest()
    key = (
        tuple(sorted(registered)),
        references.key(),
        tuple(manifest.astype(str).itertuples(index=False, name=None)),
    )
    # Callers wait for a build in progress instead of starting a second one.
    with _lock:
        now = time.monotonic()
        if _archive is None or _archive_key != key or now - _last_used > IDLE_SECONDS:
            # Free the old archive before building the next one.
            _archive = None
            _archive_key = None
            result = load_archive_data(salons=registered or None, supplier_rules=references.supplier_rule_items)
            _archive = enrich_archive(result, references)
            _archive_key = key
        _last_used = now
        archive = _archive
    return _salon_view(archive, chosen)
