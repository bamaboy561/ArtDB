"""The ArtDB API: the old site's logic behind plain HTTP, for the new interface and anything else.

On the server it is served at /new by the Streamlit process (see new_site_bridge.py).
Run it alone locally from the repository root:
    .venv/Scripts/python.exe -m uvicorn web_api.main:app --app-dir app --port 8000
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import os
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from web_api import data
from web_api import inbound
from web_api import orders
from auth_store import authenticate_session, authenticate_user, create_auth_session, revoke_auth_session
from procurement_analytics import supplier_lead_time

SESSION_COOKIE = "artdb_session"
# Behind HTTPS in production the cookie must be secure; locally the site runs on plain http.
SECURE_COOKIE = os.getenv("ARTDB_SECURE_COOKIE", "false").strip().casefold() in {"1", "true", "yes"}
FRONTEND_DIST = Path(__file__).resolve().parent / "dist"
EXCEL_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

app = FastAPI(title="ArtDB API")
# Loaded as the server starts, so this is when the server (both sites) last started.
STARTED_AT = datetime.now(timezone.utc)


def memory_mb() -> int | None:
    """The memory the whole server process holds now (Linux); None where it cannot be read."""
    try:
        with open("/proc/self/status", encoding="ascii") as status:
            for line in status:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024)
    except OSError:
        return None
    return None


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


class OrderChange(BaseModel):
    action: str  # receive, reschedule or cancel
    expected_date: date | None = None


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


def admin_user(user: dict = Depends(network_user)) -> dict:
    """Orders change only by an administrator, as on the old site."""
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Оформлять и менять заказы может администратор.")
    return user


def _supplier_form(key: str, salons: tuple[str, ...]) -> tuple[object, object, int]:
    settings = data.ForecastSettings()
    forecast = data.procurement_forecast(salons, settings)
    form = orders.find_form(forecast, key)
    if form is None:
        raise HTTPException(status_code=404, detail="Нет такого поставщика.")
    return forecast, form, supplier_lead_time(forecast, form.matches, settings.lead_time_days)


def _salons(raw: str | list[str] | None) -> tuple[str, ...]:
    names = raw.split(",") if isinstance(raw, str) else (raw or [])
    return tuple(sorted({name.strip() for name in names if name and name.strip()}))


@app.post("/api/auth/login")
def login(body: LoginRequest, request: Request, response: Response) -> dict:
    user = authenticate_user(body.login, body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Неверный логин или пароль.")
    token = create_auth_session(user["username"])
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", secure=SECURE_COOKIE or request.url.scheme == "https", max_age=30 * 86400)
    return user


@app.post("/api/auth/logout")
def logout(request: Request, response: Response) -> dict:
    revoke_auth_session(request.cookies.get(SESSION_COOKIE, ""))
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@app.get("/api/health")
def health() -> dict:
    """When the server last started and how much memory it holds: restarts and load show here."""
    return {"ok": True, "started_at": STARTED_AT.isoformat(timespec="seconds"), "memory_mb": memory_mb()}


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


@app.post("/api/orders/forms/{key}/in-transit")
def order_in_transit(key: str, body: OrderFileRequest, user: dict = Depends(admin_user)) -> dict:
    """The supplier confirmed the order unchanged: it is on the way as it stands on the screen."""
    forecast, form, lead_time = _supplier_form(key, _salons(body.salons))
    quantities = {line.row_id: (max(line.quantity, 0), max(line.edge_or_sheets, 0)) for line in body.lines}
    try:
        order_id = orders.order_from_screen(forecast, form, quantities, lead_time=lead_time, username=user["username"])
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"order_id": order_id}


@app.post("/api/orders/forms/{key}/supplier-file")
async def supplier_file(
    key: str,
    file: UploadFile = File(...),
    salons: str = Form(""),
    confirm: bool = Form(False),
    user: dict = Depends(admin_user),
) -> dict:
    """The supplier's corrected file: shown first, an order on the way once confirmed."""
    forecast, form, lead_time = _supplier_form(key, _salons(salons))
    content = await file.read()
    if not confirm:
        preview, _, _ = orders.file_preview(forecast, form, content)
        return preview
    try:
        order_id = orders.order_from_file(
            forecast, form, content, file.filename or "файл.xlsx", lead_time=lead_time, username=user["username"]
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"order_id": order_id}


@app.get("/api/inbound")
def inbound_orders(kind: str = "active", user: dict = Depends(network_user)) -> list[dict]:
    if kind not in inbound.FILTERS:
        raise HTTPException(status_code=400, detail="Неизвестный отбор.")
    return inbound.order_list(kind)


@app.get("/api/inbound/arrivals")
def arrivals(user: dict = Depends(network_user)) -> list[dict]:
    return inbound.arrival_history()


@app.get("/api/inbound/{order_id}")
def inbound_order(order_id: str, user: dict = Depends(network_user)) -> dict:
    detail = inbound.order_detail(order_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Нет такого заказа.")
    return detail


@app.post("/api/inbound/{order_id}")
def change_inbound_order(order_id: str, body: OrderChange, user: dict = Depends(admin_user)) -> dict:
    if inbound.order_detail(order_id) is None:
        raise HTTPException(status_code=404, detail="Нет такого заказа.")
    try:
        inbound.change_order(order_id, action=body.action, expected_date=body.expected_date, username=user["username"])
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return inbound.order_detail(order_id) or {}


# The built interface, when there is one; in development Vite serves it and forwards /api here.
if FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def interface(path: str) -> FileResponse:
        # The page names this build's files: a stale copy after a deploy would ask for files that are gone.
        return FileResponse(FRONTEND_DIST / "index.html", headers={"Cache-Control": "no-cache"})
