from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import pandas as pd

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import salon_data_store  # noqa: E402
from product_identity import map_unique, normalize_product_match_key  # noqa: E402
from sales_analytics import infer_supplier_from_text, infer_supplier_series  # noqa: E402

MAPPING = {"date": "Дата", "product": "Товар", "quantity": "Количество", "revenue": "Выручка"}


class CacheWriteTest(unittest.TestCase):
    def test_writers_at_the_same_time_leave_a_readable_cache(self) -> None:
        prepared = salon_data_store.PreparedSalesData(
            data=pd.DataFrame({"product": ["Плита"] * 2000}),
            warnings=[],
        )
        with tempfile.TemporaryDirectory() as folder:
            cache_path = Path(folder) / "report.xlsx.prepared-key.pkl"
            threads = [
                threading.Thread(target=salon_data_store._write_prepared_cache, args=(cache_path, prepared))
                for _ in range(8)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(len(salon_data_store._read_prepared_cache(cache_path).data), 2000)
            self.assertEqual([path.name for path in Path(folder).iterdir()], [cache_path.name])

    def test_failed_write_does_not_raise(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            cache_path = Path(folder) / "report.pkl"
            with mock.patch("salon_data_store.pickle.dump", side_effect=OSError("disk full")):
                self.assertFalse(salon_data_store._write_prepared_cache(cache_path, object()))
            with (
                mock.patch("salon_data_store.pickle.dump", side_effect=OSError("disk full")),
                mock.patch.object(Path, "unlink", side_effect=PermissionError("in use")),
            ):
                self.assertFalse(salon_data_store._write_prepared_cache(cache_path, object()))


class SupplierInferenceTest(unittest.TestCase):
    def test_series_matches_per_value_inference(self) -> None:
        values = pd.Series(
            ["Плита Zzqx 16мм", "плита zzqx", None, "Кромка ABS", "Плита Zzqx 16мм", "", float("nan")],
            index=[10, 11, 12, 13, 14, 15, 16],
        )
        for rules in (None, (("Поставщик Z", "zzqx"),), [{"supplier": "Поставщик Z", "keyword": "zzqx"}]):
            with self.subTest(rules=rules):
                expected = values.map(lambda value: infer_supplier_from_text(value, rules))
                result = infer_supplier_series(values, rules)
                self.assertEqual(result.index.tolist(), values.index.tolist())
                self.assertEqual(result.tolist(), expected.tolist())
        self.assertEqual(infer_supplier_series(values, (("Поставщик Z", "zzqx"),)).loc[10], "Поставщик Z")

    def test_map_unique_keeps_index_and_missing_values(self) -> None:
        values = pd.Series(["Плита 16 x 2800", None, "плита 16х2800", "Плита 16 x 2800"], index=[3, 1, 2, 0])
        result = map_unique(values, normalize_product_match_key)
        self.assertEqual(result.index.tolist(), [3, 1, 2, 0])
        self.assertEqual(result.tolist(), [normalize_product_match_key(value) for value in values])


class PreparedArchiveCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        for name, value in {
            "DATA_DIR": root,
            "UPLOADS_DIR": root / "uploads",
            "MANIFEST_PATH": root / "upload_manifest.csv",
            "SALONS_PATH": root / "salons.json",
        }.items():
            patcher = mock.patch.object(salon_data_store, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name in ("database_enabled",):
            patcher = mock.patch.object(salon_data_store, name, return_value=False)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.uploads_dir = root / "uploads"

        for day, products in ((1, ["Плита Zzqx", "Кромка"]), (2, ["Плита Zzqx", "Петля"]), (3, ["Профиль"])):
            frame = pd.DataFrame(
                {
                    "Дата": [f"0{day}.04.2026"] * len(products),
                    "Товар": products,
                    "Количество": [1] * len(products),
                    "Выручка": [100.0 * day] * len(products),
                }
            )
            salon_data_store.register_upload(
                file_bytes=b"x",
                filename=f"report_{day}.xlsx",
                salon="Тест",
                report_date=date(2026, 4, day),
                mapping=MAPPING,
                parsed_data=frame,
            )

    def cache_files(self) -> list[Path]:
        return sorted(self.uploads_dir.rglob("*.prepared-*.pkl"))

    def test_second_load_reads_prepared_cache_and_matches(self) -> None:
        first = salon_data_store.load_archive_data(salons=["Тест"])
        self.assertEqual(len(self.cache_files()), 3)
        with mock.patch.object(salon_data_store, "prepare_sales_data", side_effect=AssertionError("re-prepared")):
            second = salon_data_store.load_archive_data(salons=["Тест"])
        pd.testing.assert_frame_equal(first.data, second.data)
        pd.testing.assert_frame_equal(first.file_report, second.file_report)
        self.assertEqual(first.data["source_filename"].tolist(), ["report_1.xlsx"] * 2 + ["report_2.xlsx"] * 2 + ["report_3.xlsx"])

    def test_changed_supplier_rules_rebuild_cache_and_drop_stale_files(self) -> None:
        salon_data_store.load_archive_data(salons=["Тест"])
        stale = set(self.cache_files())
        rules = (("Поставщик Z", "zzqx"),)
        result = salon_data_store.load_archive_data(salons=["Тест"], supplier_rules=rules)
        suppliers = result.data.set_index("product")["supplier"]
        self.assertEqual(set(suppliers.loc["Плита Zzqx"]), {"Поставщик Z"})
        fresh = set(self.cache_files())
        self.assertEqual(len(fresh), 3)
        self.assertFalse(stale & fresh)

    def test_replacing_an_upload_removes_its_prepared_cache(self) -> None:
        salon_data_store.load_archive_data(salons=["Тест"])
        salon_data_store.register_upload(
            file_bytes=b"y",
            filename="report_3_fixed.xlsx",
            salon="Тест",
            report_date=date(2026, 4, 3),
            mapping=MAPPING,
            parsed_data=pd.DataFrame({"Дата": ["03.04.2026"], "Товар": ["Профиль"], "Количество": [2], "Выручка": [600.0]}),
        )
        self.assertEqual(len(self.cache_files()), 2)
        result = salon_data_store.load_archive_data(salons=["Тест"])
        self.assertIn("report_3_fixed.xlsx", result.data["source_filename"].tolist())
        self.assertNotIn("report_3.xlsx", result.data["source_filename"].tolist())


if __name__ == "__main__":
    unittest.main()
