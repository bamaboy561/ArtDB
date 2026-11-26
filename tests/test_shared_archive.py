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

import salon_data_store  # noqa: E402
from salon_data_store import ArchiveLoadResult  # noqa: E402


class SharedArchiveTest(unittest.TestCase):
    """The site, the bot and the daily report share one archive instead of building their own."""

    def setUp(self) -> None:
        salon_data_store.forget_shared_archive()
        self.addCleanup(salon_data_store.forget_shared_archive)
        self.manifest = pd.DataFrame({"upload_id": ["a"], "salon": ["Салон 1"], "mapping_json": ["{}"]})
        self.builds = 0

        def build(manifest, *, salons, supplier_rules):
            self.builds += 1
            time.sleep(0.05)
            return ArchiveLoadResult(
                data=pd.DataFrame({"salon": ["Салон 1"] * 3, "revenue": [1.0, 2.0, 3.0]}),
                manifest=manifest,
                warnings=["предупреждение"],
            )

        for item in (
            patch.object(salon_data_store, "load_manifest", lambda: self.manifest),
            patch.object(salon_data_store, "_build_archive_data", build),
        ):
            item.start()
            self.addCleanup(item.stop)

    def test_the_archive_is_built_once(self) -> None:
        first = salon_data_store.load_archive_data(salons=None)
        second = salon_data_store.load_archive_data(salons=None)
        self.assertEqual(self.builds, 1)
        self.assertEqual(second.data["revenue"].sum(), 6.0)
        self.assertIsNot(first.data, second.data)

    def test_a_new_upload_builds_it_again(self) -> None:
        salon_data_store.load_archive_data()
        self.manifest = pd.concat(
            [self.manifest, pd.DataFrame({"upload_id": ["b"], "salon": ["Салон 1"], "mapping_json": ["{}"]})]
        )
        salon_data_store.load_archive_data()
        self.assertEqual(self.builds, 2)

    def test_other_salons_or_rules_build_it_again(self) -> None:
        salon_data_store.load_archive_data(salons=["Салон 1"])
        salon_data_store.load_archive_data(salons=["Салон 1"], supplier_rules=(("Hettich", "hettich"),))
        salon_data_store.load_archive_data(salons=["Салон 1"], supplier_rules=(("Hettich", "hettich"),))
        self.assertEqual(self.builds, 2)

    def test_a_caller_changing_its_tables_leaves_the_others_alone(self) -> None:
        first = salon_data_store.load_archive_data()
        first.data["revenue"] = 0.0
        first.data.loc[0, "salon"] = "Другой"
        first.data["extra"] = 1
        first.warnings.append("ещё одно")
        second = salon_data_store.load_archive_data()
        self.assertEqual(second.data["revenue"].sum(), 6.0)
        self.assertEqual(second.data.loc[0, "salon"], "Салон 1")
        self.assertNotIn("extra", second.data.columns)
        self.assertEqual(second.warnings, ["предупреждение"])

    def test_requests_at_once_wait_for_one_build(self) -> None:
        threads = [threading.Thread(target=salon_data_store.load_archive_data) for _ in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(self.builds, 1)

    def test_an_old_archive_is_built_again(self) -> None:
        salon_data_store.load_archive_data()
        with patch.object(salon_data_store, "ARCHIVE_SHARE_SECONDS", 0):
            salon_data_store.load_archive_data()
        self.assertEqual(self.builds, 2)


if __name__ == "__main__":
    unittest.main()
