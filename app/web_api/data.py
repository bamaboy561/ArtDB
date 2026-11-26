"""Sales and the procurement forecast for the API, built by the site's own modules.

The modules in app/ hold all the logic; this layer only picks the salons and the
settings and keeps the result for a few minutes, since one forecast takes seconds.
"""
from __future__ import annotations

from dataclasses import dataclass
import sys
import threading
from pathlib import Path

import pandas as pd

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from procurement_analytics import build_procurement_forecast  # noqa: E402
from procurement_order_store import (  # noqa: E402
    build_open_order_summary,
    load_procurement_order_items,
    load_procurement_orders,
)
from procurement_store import load_procurement_items  # noqa: E402
from salon_data_store import load_salons  # noqa: E402
from sku_catalog_store import apply_sku_aliases, load_sku_aliases  # noqa: E402
from sales_source import dataframe_cache_key, load_sales_archive  # noqa: E402

# A few salon choices at a time: the server has little memory.
CACHE_ENTRIES = 6


@dataclass(frozen=True)
class ForecastSettings:
    """The sidebar's "Параметры закупки" of the old site, with its defaults."""

    history_months: int = 6
    coverage_days: int = 30
    lead_time_days: int = 14
    safety_days: int = 7
    min_active_months: int = 2


# One build at a time: the page asks for several things at once, and each used to
# build its own forecast in parallel before the first one was cached.
_lock = threading.Lock()
_cache: dict[tuple, pd.DataFrame] = {}


def registered_salons() -> list[str]:
    return list(load_salons())


def procurement_forecast(salons: tuple[str, ...], settings: ForecastSettings) -> pd.DataFrame:
    """The forecast of the chosen salons; all salons when none are chosen.

    Kept until the sales, the stock or the orders change, whichever site changed them.
    """
    sales = load_sales_archive(list(salons) or None)
    items = apply_sku_aliases(load_procurement_items(), load_sku_aliases(), aggregate_inventory=True)
    inbound = build_open_order_summary(load_procurement_orders(), load_procurement_order_items())
    key = (sales.data_key, settings, dataframe_cache_key(items), dataframe_cache_key(inbound))
    with _lock:
        cached = _cache.get(key)
        if cached is not None:
            return cached
        forecast = build_procurement_forecast(
            sales.result.data,
            history_months=settings.history_months,
            coverage_days=settings.coverage_days,
            lead_time_days=settings.lead_time_days,
            safety_days=settings.safety_days,
            min_active_months=settings.min_active_months,
            procurement_items=items,
            inbound_orders=inbound,
        )
        _cache[key] = forecast
        while len(_cache) > CACHE_ENTRIES:
            _cache.pop(next(iter(_cache)))
        return forecast


def forget_forecasts() -> None:
    """After an order or a stock change the goods in transit differ, so every forecast is rebuilt."""
    with _lock:
        _cache.clear()
