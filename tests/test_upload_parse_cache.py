from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from salon_data_store import (
    _parsed_cache_path,
    _read_parsed_cache,
    _remove_upload_artifacts,
    _write_parsed_cache,
)


class UploadParseCacheTests(unittest.TestCase):
    def test_parsed_frame_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_path = Path(temporary_directory) / "sales.xls"
            source_path.write_bytes(b"source")
            frame = pd.DataFrame(
                {
                    "Дата": pd.to_datetime(["2026-08-01"]),
                    "Номенклатура": ["Тестовый товар"],
                    "Всего": [100.0],
                }
            )

            self.assertTrue(_write_parsed_cache(source_path, frame))
            cached = _read_parsed_cache(source_path)

            self.assertIsNotNone(cached)
            pd.testing.assert_frame_equal(cached, frame)

    def test_removing_upload_removes_all_cache_versions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_path = Path(temporary_directory) / "sales.xls"
            source_path.write_bytes(b"source")
            current_cache = _parsed_cache_path(source_path)
            current_cache.write_bytes(b"cache")
            old_cache = source_path.with_name(f"{source_path.name}.parsed-v0.parquet")
            old_cache.write_bytes(b"old-cache")

            removed = _remove_upload_artifacts(source_path)

            self.assertEqual(removed, 3)
            self.assertFalse(source_path.exists())
            self.assertFalse(current_cache.exists())
            self.assertFalse(old_cache.exists())


if __name__ == "__main__":
    unittest.main()
