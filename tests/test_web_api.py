from __future__ import annotations

from io import BytesIO
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from openpyxl import load_workbook

API_DIR = Path(__file__).resolve().parents[1] / "web" / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

try:
    from fastapi.testclient import TestClient

    import main
except ImportError:  # The new site's packages are not installed: its tests are skipped.
    main = None

ADMIN = {"username": "admin", "display_name": "Админ", "role": "admin", "salon": ""}
SALON = {"username": "salon", "display_name": "Салон", "role": "salon", "salon": "Artisan"}
MUFFIN = "US77301/PGR4201 Маффин 18*1220*2800мм"
MUFFIN_EDGE = "PGR4201 Маффин 1*22мм"


def forecast() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "product": [MUFFIN, MUFFIN_EDGE, "Шуруп 4*16", "Петля"],
            "recommended_order_qty": [12, 300, 50, 0],
            "supplier": ["WQ", "WQ", "Samet", "Не назначен"],
            "priority": "Высокий",
            "abc_class": "A",
            "xyz_class": "X",
            "stock_status": "",
            "available_stock_qty": 1.0,
            "forecast_qty": 10.0,
            "lead_time_days": 14,
        }
    )


@unittest.skipIf(main is None, "fastapi is not installed")
class WebApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(main.app)
        patches = [
            patch.object(main.data, "procurement_forecast", lambda salons, settings: forecast()),
            patch.object(main, "authenticate_session", lambda token: {"admin-token": ADMIN, "salon-token": SALON}.get(token)),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def sign_in(self, token: str) -> None:
        self.client.cookies.set(main.SESSION_COOKIE, token)

    def test_without_a_session_nothing_is_shown(self) -> None:
        self.assertEqual(self.client.get("/api/orders/forms").status_code, 401)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)

    def test_a_wrong_password_is_refused(self) -> None:
        with patch.object(main, "authenticate_user", lambda login, password: None):
            response = self.client.post("/api/auth/login", json={"login": "admin", "password": "x"})
        self.assertEqual(response.status_code, 401)

    def test_a_salon_does_not_see_procurement(self) -> None:
        self.sign_in("salon-token")
        self.assertEqual(self.client.get("/api/orders/forms").status_code, 403)
        self.assertEqual(self.client.get("/api/salons").json(), ["Artisan"])

    def test_every_supplier_gets_an_order(self) -> None:
        self.sign_in("admin-token")
        forms = {form["title"]: form for form in self.client.get("/api/orders/forms").json()}
        self.assertEqual(forms["WQ"]["to_order"], 1)
        # A supplier without a form of its own gets the common one; unassigned goods get none.
        self.assertEqual(forms["Samet"]["to_order"], 1)
        self.assertNotIn("Не назначен", forms)

    def test_the_file_holds_the_edited_quantities(self) -> None:
        self.sign_in("admin-token")
        detail = self.client.get("/api/orders/forms/wq").json()
        row = detail["rows"][0]
        self.assertEqual((row["quantity"], row["edge_or_sheets"]), (12, 300))
        response = self.client.post(
            "/api/orders/forms/wq/file",
            json={"lines": [{"row_id": row["row_id"], "quantity": 20, "edge_or_sheets": 150}]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("wq_zakaz_", response.headers["content-disposition"])
        sheet = load_workbook(BytesIO(response.content)).active
        lines = [values for values in sheet.iter_rows(values_only=True) if values[2] == "Маффин"]
        self.assertEqual((lines[0][4], lines[0][5]), (20, 150))

    def test_an_unknown_supplier_is_not_found(self) -> None:
        self.sign_in("admin-token")
        self.assertEqual(self.client.get("/api/orders/forms/nobody").status_code, 404)


if __name__ == "__main__":
    unittest.main()
