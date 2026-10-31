from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path

import pandas as pd

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import run_memory  # noqa: E402


class ReleasePreviousRunTest(unittest.TestCase):
    def setUp(self) -> None:
        self.clear_state()
        self.addCleanup(self.clear_state)

    @staticmethod
    def clear_state() -> None:
        run_memory._previous_runs.clear()
        run_memory._still_running.clear()

    def test_next_run_clears_tables_of_the_previous_run_only(self) -> None:
        first = {"data": pd.DataFrame({"x": [1]}), "series": pd.Series([1]), "label": "keep"}
        run_memory.release_previous_run("session", first)
        self.assertIsInstance(first["data"], pd.DataFrame)

        second = {"data": pd.DataFrame({"x": [2]})}
        run_memory.release_previous_run("session", second)
        self.assertIsNone(first["data"])
        self.assertIsNone(first["series"])
        self.assertEqual(first["label"], "keep")
        self.assertIsInstance(second["data"], pd.DataFrame)

    def test_other_sessions_keep_their_tables(self) -> None:
        other = {"data": pd.DataFrame({"x": [1]})}
        run_memory.release_previous_run("other", other)
        run_memory.release_previous_run("session", {"data": pd.DataFrame({"x": [2]})})
        run_memory.release_previous_run("session", {"data": pd.DataFrame({"x": [3]})})
        self.assertIsInstance(other["data"], pd.DataFrame)

    def test_abandoned_sessions_are_evicted_and_cleared(self) -> None:
        abandoned = {"data": pd.DataFrame({"x": [1]})}
        run_memory.release_previous_run("abandoned", abandoned)
        for index in range(run_memory.MAX_TRACKED_SESSIONS):
            run_memory.release_previous_run(f"session-{index}", {})
        self.assertIsNone(abandoned["data"])

    def test_a_run_still_executing_keeps_its_tables_until_it_ends(self) -> None:
        running = {"data": pd.DataFrame({"x": [1]})}
        registered = threading.Event()
        finish = threading.Event()

        def old_run() -> None:
            run_memory.release_previous_run("session", running)
            registered.set()
            finish.wait(10)

        thread = threading.Thread(target=old_run)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(finish.set)
        registered.wait(10)

        run_memory.release_previous_run("session", {"data": pd.DataFrame({"x": [2]})})
        self.assertIsInstance(running["data"], pd.DataFrame)

        finish.set()
        thread.join(10)
        run_memory.release_previous_run("session", {"data": pd.DataFrame({"x": [3]})})
        self.assertIsNone(running["data"])


if __name__ == "__main__":
    unittest.main()
