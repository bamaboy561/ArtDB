"""The new site at /new, served by the same server process as the Streamlit site.

Render gives the service one port and one disk: the uploaded reports, the stock and
the orders live there and in its database. So the new site does not run apart; its
FastAPI app is called from Streamlit's own web server (Tornado) for every address
under /new, and both sites read the same data. Anything that goes wrong here leaves
the Streamlit site as it was: the new site is then simply not there.
"""
from __future__ import annotations

import asyncio
import traceback
from urllib.parse import unquote
from typing import Any, Callable

PREFIX = "/new"
ROUTE = r"/new(?:/.*)?"

AsgiApp = Callable[..., Any]
_attached = False


def _handler_class():
    import tornado.web

    class AsgiHandler(tornado.web.RequestHandler):
        """Passes one HTTP request to an ASGI app and writes back its answer."""

        SUPPORTED_METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")

        def initialize(self, asgi_app: AsgiApp) -> None:
            self.asgi_app = asgi_app

        def check_xsrf_cookie(self) -> None:
            # The new site has its own session cookie (SameSite=Lax); Streamlit's form token is not used here.
            return None

        async def _serve(self, *_: object) -> None:
            request = self.request
            if request.path == PREFIX:
                self.redirect(f"{PREFIX}/" + (f"?{request.query}" if request.query else ""))
                return
            forwarded = request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip()
            host, _, port = request.host.partition(":")
            scheme = forwarded or request.protocol
            scope = {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1",
                "method": request.method,
                "scheme": scheme,
                "path": unquote(request.path),
                "raw_path": request.path.encode("latin-1"),
                "root_path": PREFIX,
                "query_string": request.query.encode("latin-1"),
                "headers": [(name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in request.headers.get_all()],
                "client": (request.remote_ip or "", 0),
                "server": (host, int(port) if port.isdigit() else (443 if scheme == "https" else 80)),
            }
            body_sent = False

            async def receive() -> dict:
                nonlocal body_sent
                if not body_sent:
                    body_sent = True
                    return {"type": "http.request", "body": request.body or b"", "more_body": False}
                # Nothing more comes; the app stops waiting once its answer is written.
                await asyncio.Event().wait()
                return {"type": "http.disconnect"}

            async def send(message: dict) -> None:
                if message["type"] == "http.response.start":
                    self.clear()
                    self.set_status(message["status"])
                    for raw_name, raw_value in message.get("headers", []):
                        name, value = raw_name.decode("latin-1"), raw_value.decode("latin-1")
                        if name.casefold() in {"content-type", "content-length"}:
                            self.set_header(name, value)
                        else:
                            self.add_header(name, value)
                elif message["type"] == "http.response.body":
                    chunk = message.get("body", b"")
                    if chunk:
                        self.write(chunk)
                    if message.get("more_body"):
                        await self.flush()

            await self.asgi_app(scope, receive, send)
            if not self._finished:
                self.finish()

        get = head = post = put = patch = delete = options = _serve

    return AsgiHandler


def attach(tornado_app: Any, asgi_app: AsgiApp) -> None:
    """Put the new site's route first, ahead of Streamlit's catch-all pages."""
    global _attached
    rules = tornado_app.wildcard_router.rules
    tornado_app.wildcard_router.add_rules([(ROUTE, _handler_class(), {"asgi_app": asgi_app})])
    rules.insert(0, rules.pop())
    _attached = True


def attach_running() -> bool:
    """For a server started without run_server.py: add /new to the web app already running.

    app.py calls this on every run; after the first success it costs nothing.
    """
    global _attached
    if _attached:
        return True
    _attached = True  # One attempt per process, whatever happens.
    try:
        import gc

        import tornado.web

        from web_api.main import app as new_site

        running = [item for item in gc.get_objects() if isinstance(item, tornado.web.Application)]
        for tornado_app in running:
            attach(tornado_app, new_site)
        return bool(running)
    except Exception:
        print("new site not attached:\n" + traceback.format_exc(), flush=True)
        return False


def install() -> bool:
    """Serve the new site under /new once Streamlit builds its web app. False when it cannot."""
    try:
        from streamlit.web.server import server as streamlit_server

        from web_api.main import app as new_site
    except Exception:  # A missing package must not stop the old site.
        print("new site not started:\n" + traceback.format_exc(), flush=True)
        return False

    original = streamlit_server.Server._create_app

    def create_app(self):  # type: ignore[no-untyped-def]
        tornado_app = original(self)
        try:
            attach(tornado_app, new_site)
        except Exception:
            print("new site not attached:\n" + traceback.format_exc(), flush=True)
        return tornado_app

    streamlit_server.Server._create_app = create_app
    return True
