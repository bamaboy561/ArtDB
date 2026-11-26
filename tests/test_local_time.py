from __future__ import annotations

from datetime import date, datetime, timezone
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import local_time  # noqa: E402


class LocalTodayTest(unittest.TestCase):
    def test_after_midnight_local_time_it_is_already_the_next_day(self) -> None:
        utc_evening = datetime(2026, 11, 25, 20, 0, tzinfo=timezone.utc)  # 02:00 on the 26th at UTC+6

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return utc_evening.astimezone(tz) if tz else utc_evening.replace(tzinfo=None)

        with patch.dict("os.environ", {"APP_TIMEZONE": "Asia/Omsk"}), patch.object(local_time, "datetime", Clock):
            self.assertEqual(local_time.local_today(), date(2026, 11, 26))


if __name__ == "__main__":
    unittest.main()
