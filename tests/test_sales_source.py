from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import sales_source  # noqa: E402
from salon_data_store import ArchiveLoadResult  # noqa: E402


def references(rules: tuple[tuple[str, str], ...] = ()) -> sales_source.SalesReferences:
    empty = pd.DataFrame()
    return sales_source.SalesReferences(
        procurement_items=pd.DataFrame({"product": ["Петля"], "supplier": ["Blum"], "brand": ["Blum"]}),
        supplier_rule_items=rules,
        supplier_product_assignments=empty,
        sku_attribute_overrides=empty,
        sku_aliases=empty,
    )


class SharedSalesArchiveTest(unittest.TestCase):
    """The old site, the new site, the bot and the daily report read one archive."""

    def setUp(self) -> None:
        sales_source.forget_sales_archive()
        self.addCleanup(sales_source.forget_sales_archive)
        self.manifest = pd.DataFrame({"upload_id": ["a", "b"], "salon": ["Центр", "Восток"]})
        self.salons = ["Центр", "Восток"]
        self.builds: list[list[str] | None] = []

        def build(*, salons, supplier_rules):
            self.builds.append(salons)
            time.sleep(0.05)
            return ArchiveLoadResult(
                data=pd.DataFrame(
                    {
                        "salon": ["Центр", "Центр", "Восток"],
                        "product": ["Петля", "Ручка", "Петля"],
                        "revenue": [10.0, 20.0, 30.0],
                    }
                ),
                manifest=self.manifest,
                warnings=["Центр / 2026-11-01: предупреждение", "Восток / 2026-11-01: другое"],
                file_report=pd.DataFrame({"salon": ["Центр", "Восток"], "upload_id": ["a", "b"]}),
            )

        for item in (
            patch.object(sales_source, "load_manifest", lambda: self.manifest),
            patch.object(sales_source, "load_salons", lambda: list(self.salons)),
            patch.object(sales_source, "load_archive_data", build),
        ):
            item.start()
            self.addCleanup(item.stop)

    def test_every_caller_shares_one_build(self) -> None:
        site = sales_source.load_sales_archive(["Центр", "Восток"], references())
        bot = sales_source.load_sales_archive(None, references())
        manager = sales_source.load_sales_archive(["Центр"], references())
        self.assertEqual(len(self.builds), 1)
        self.assertEqual(self.builds[0], ["Центр", "Восток"])
        self.assertEqual(site.result.data["revenue"].sum(), 60.0)
        self.assertEqual(bot.result.data["revenue"].sum(), 60.0)
        self.assertEqual(manager.result.data["revenue"].sum(), 30.0)

    def test_suppliers_come_from_the_catalog(self) -> None:
        data = sales_source.load_sales_archive(None, references()).result.data
        self.assertEqual(data.set_index("product")["supplier"].to_dict(), {"Петля": "Blum", "Ручка": "Не назначен"})

    def test_a_salon_sees_only_its_own_files_and_warnings(self) -> None:
        view = sales_source.load_sales_archive(["Восток"], references()).result
        self.assertEqual(view.data["salon"].unique().tolist(), ["Восток"])
        self.assertEqual(view.manifest["salon"].tolist(), ["Восток"])
        self.assertEqual(view.file_report["salon"].tolist(), ["Восток"])
        self.assertEqual(view.warnings, ["Восток / 2026-11-01: другое"])

    def test_each_salon_choice_has_its_own_data_key(self) -> None:
        keys = {
            sales_source.load_sales_archive(choice, references()).data_key
            for choice in (["Центр"], ["Восток"], ["Центр", "Восток"])
        }
        self.assertEqual(len(keys), 3)

    def test_a_new_upload_or_new_rules_build_it_again(self) -> None:
        sales_source.load_sales_archive(None, references())
        self.manifest = pd.DataFrame({"upload_id": ["a", "b", "c"], "salon": ["Центр", "Восток", "Центр"]})
        sales_source.load_sales_archive(None, references())
        sales_source.load_sales_archive(None, references((("Hettich", "hettich"),)))
        self.assertEqual(len(self.builds), 3)

    def test_a_caller_changing_its_tables_leaves_the_others_alone(self) -> None:
        first = sales_source.load_sales_archive(None, references()).result
        first.data["revenue"] = 0.0
        first.data.loc[first.data.index[0], "product"] = "Другое"
        first.warnings.append("ещё одно")
        second = sales_source.load_sales_archive(None, references()).result
        self.assertEqual(second.data["revenue"].sum(), 60.0)
        self.assertEqual(second.data["product"].iloc[0], "Петля")
        self.assertEqual(len(second.warnings), 2)

    def test_requests_at_once_wait_for_one_build(self) -> None:
        threads = [
            threading.Thread(target=sales_source.load_sales_archive, args=(None, references()))
            for _ in range(5)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(self.builds), 1)

    def test_an_archive_nobody_asked_for_is_dropped(self) -> None:
        sales_source.load_sales_archive(None, references())
        with patch.object(sales_source, "IDLE_SECONDS", 0):
            time.sleep(0.01)
            sales_source.load_sales_archive(None, references())
        self.assertEqual(len(self.builds), 2)


if __name__ == "__main__":
    unittest.main()
