from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from web_api import data  # noqa: E402


class ForecastCacheTest(unittest.TestCase):
    """The new site builds a forecast once and builds it again when sales, stock or orders change."""

    def setUp(self) -> None:
        data.forget_forecasts()
        self.addCleanup(data.forget_forecasts)
        self.builds = 0
        self.data_key = "archive-1"
        self.orders = pd.DataFrame({"order_id": ["a"], "status": ["ordered"]})

        def build(sales, **settings):
            self.builds += 1
            time.sleep(0.05)
            return pd.DataFrame({"product": ["Петля"], "recommended_order_qty": [5.0]})

        for item in (
            patch.object(data, "load_sales_archive", lambda salons: SimpleNamespace(data_key=self.data_key, result=SimpleNamespace(data=pd.DataFrame()))),
            patch.object(data, "load_procurement_items", lambda: pd.DataFrame({"product": ["Петля"], "stock_on_hand": [1.0]})),
            patch.object(data, "load_sku_aliases", pd.DataFrame),
            patch.object(data, "apply_sku_aliases", lambda items, aliases, aggregate_inventory: items),
            patch.object(data, "load_procurement_orders", lambda: self.orders),
            patch.object(data, "load_procurement_order_items", pd.DataFrame),
            patch.object(data, "build_open_order_summary", lambda orders, items: orders),
            patch.object(data, "build_procurement_forecast", build),
        ):
            item.start()
            self.addCleanup(item.stop)

    def forecast(self) -> pd.DataFrame:
        return data.procurement_forecast((), data.ForecastSettings())

    def test_requests_at_once_build_one_forecast(self) -> None:
        threads = [threading.Thread(target=self.forecast) for _ in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(self.builds, 1)

    def test_an_order_placed_on_the_old_site_is_seen_at_once(self) -> None:
        self.forecast()
        self.orders = pd.DataFrame({"order_id": ["a", "b"], "status": ["ordered", "ordered"]})
        self.forecast()
        self.assertEqual(self.builds, 2)

    def test_new_sales_build_it_again(self) -> None:
        self.forecast()
        self.forecast()
        self.data_key = "archive-2"
        self.forecast()
        self.assertEqual(self.builds, 2)


if __name__ == "__main__":
    unittest.main()
