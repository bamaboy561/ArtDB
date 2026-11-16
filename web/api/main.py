"""The ArtDB API: the old site's logic behind plain HTTP, for the new interface and anything else.

Run locally from the repository root:
    .venv/Scripts/python.exe -m uvicorn main:app --app-dir web/api --port 8000
"""
from __future__ import annotations

from datetime import date
import os
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import data
import orders
from auth_store import authenticate_session, authenticate_user, create_auth_session, revoke_auth_session

SESSION_COOKIE = "artdb_session"
# Behind HTTPS in production the cookie must be secure; locally the site runs on plain http.
SECURE_COOKIE = os.getenv("ARTDB_SECURE_COOKIE", "false").strip().casefold() in {"1", "true", "yes"}
FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
EXCEL_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

app = FastAPI(title="ArtDB API")


class LoginRequest(BaseModel):
    login: str
    password: str


class OrderLine(BaseModel):
    row_id: str
    quantity: int = 0
    edge_or_sheets: int = 0


class OrderFileRequest(BaseModel):
    salons: list[str] = []
    lines: list[OrderLine] = []


def current_user(request: Request) -> dict:
    user = authenticate_session(request.cookies.get(SESSION_COOKIE, ""))
    if user is None:
        raise HTTPException(status_code=401, detail="Войдите в систему.")
    return user


def network_user(user: dict = Depends(current_user)) -> dict:
    """Procurement covers the whole network, as on the old site: admins and managers."""
    if user["role"] not in {"admin", "manager"}:
        raise HTTPException(status_code=403, detail="Закупки доступны администратору и руководителю.")
    return user


def _salons(raw: str | list[str] | None) -> tuple[str, ...]:
    names = raw.split(",") if isinstance(raw, str) else (raw or [])
    return tuple(sorted({name.strip() for name in names if name and name.strip()}))


@app.post("/api/auth/login")
def login(body: LoginRequest, response: Response) -> dict:
    user = authenticate_user(body.login, body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Неверный логин или пароль.")
    token = create_auth_session(user["username"])
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", secure=SECURE_COOKIE, max_age=30 * 86400)
    return user


@app.post("/api/auth/logout")
def logout(request: Request, response: Response) -> dict:
    revoke_auth_session(request.cookies.get(SESSION_COOKIE, ""))
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@app.get("/api/auth/me")
def me(user: dict = Depends(current_user)) -> dict:
    return user


@app.get("/api/salons")
def salons(user: dict = Depends(current_user)) -> list[str]:
    return [user["salon"]] if user["role"] == "salon" else data.registered_salons()


@app.get("/api/orders/forms")
def order_forms(salons: str = "", user: dict = Depends(network_user)) -> list[dict]:
    settings = data.ForecastSettings()
    forecast = data.procurement_forecast(_salons(salons), settings)
    return orders.form_summaries(forecast, settings.lead_time_days)


@app.get("/api/orders/forms/{key}")
def order_form(key: str, salons: str = "", user: dict = Depends(network_user)) -> dict:
    settings = data.ForecastSettings()
    forecast = data.procurement_forecast(_salons(salons), settings)
    form = orders.find_form(forecast, key)
    if form is None:
        raise HTTPException(status_code=404, detail="Нет такого поставщика.")
    return orders.form_detail(forecast, form, settings.lead_time_days)


@app.post("/api/orders/forms/{key}/file")
def order_file(key: str, body: OrderFileRequest, user: dict = Depends(network_user)) -> Response:
    forecast = data.procurement_forecast(_salons(body.salons), data.ForecastSettings())
    form = orders.find_form(forecast, key)
    if form is None:
        raise HTTPException(status_code=404, detail="Нет такого поставщика.")
    quantities = {line.row_id: (max(line.quantity, 0), max(line.edge_or_sheets, 0)) for line in body.lines}
    content = orders.order_workbook(forecast, form, quantities)
    filename = f"{form.key}_zakaz_{date.today():%Y%m%d}.xlsx"
    return Response(
        content,
        media_type=EXCEL_MIME,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


# The built interface, when there is one; in development Vite serves it and forwards /api here.
if FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def interface(path: str) -> FileResponse:
        return FileResponse(FRONTEND_DIST / "index.html")
