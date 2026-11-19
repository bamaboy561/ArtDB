# ArtDB, new site

The Streamlit site in `app/` moves here screen by screen. The logic stays in
`app/` (forecast, supplier forms, stores); `app/web_api` serves it over HTTP and
`web/frontend` is the interface in React, built into `app/web_api/dist`.

On the server the new site is at `/new` of the same address: `app/new_site_bridge.py`
passes every request under `/new` from Streamlit's web server to the FastAPI app,
so both sites share one process, one disk and one database (Render deploys only `app/`).

Ready: sign-in with the old site's logins, Закупки → Заказы (every supplier's
order, quantities editable, the supplier's file to download).

## Run locally

From the repository root, with the project's virtual environment:

```bash
.venv/Scripts/python.exe -m pip install -r web/requirements.txt
cd web/frontend && npm install && npm run build && cd ../..
.venv/Scripts/python.exe -m uvicorn web_api.main:app --app-dir app --port 8000
```

Then open http://127.0.0.1:8000. After changing the interface, build it again and
commit `app/web_api/dist`: the server has no Node and serves the built files. The data folder is the old site's
(`APP_DATA_DIR`, or `DATABASE_URL` for PostgreSQL), so both sites see the same
users, orders and uploads.

While changing the interface, `npm run dev` in `web/frontend` serves it on
http://127.0.0.1:5173 with instant reload and forwards `/api` to port 8000.
