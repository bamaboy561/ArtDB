from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import auth_store  # noqa: E402


class SessionStoreTest(unittest.TestCase):
    """The file store: a check only reads, and requests at once never sign the user out."""

    def setUp(self) -> None:
        folder = Path(tempfile.mkdtemp())
        patches = [
            patch.object(auth_store, "database_enabled", lambda: False),
            patch.object(auth_store, "DATA_DIR", folder),
            patch.object(auth_store, "USERS_PATH", folder / "users.json"),
            patch.object(auth_store, "SESSIONS_PATH", folder / "auth_sessions.json"),
            patch.object(auth_store, "find_user", lambda name: {"username": name, "role": "admin", "is_active": True}),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def test_checking_a_session_does_not_rewrite_the_store(self) -> None:
        token = auth_store.create_auth_session("anna")
        with patch.object(auth_store, "_save_raw_sessions") as save:
            self.assertIsNotNone(auth_store.authenticate_session(token))
        save.assert_not_called()

    def test_requests_at_once_keep_the_user_signed_in(self) -> None:
        token = auth_store.create_auth_session("anna")
        failures: list[str] = []

        def check() -> None:
            for _ in range(40):
                if auth_store.authenticate_session(token) is None:
                    failures.append("signed out")

        def others_sign_in() -> None:
            for number in range(40):
                auth_store.create_auth_session(f"user{number}")

        threads = [threading.Thread(target=check) for _ in range(4)] + [threading.Thread(target=others_sign_in)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(failures, [])
        self.assertIsNotNone(auth_store.authenticate_session(token))


if __name__ == "__main__":
    unittest.main()
