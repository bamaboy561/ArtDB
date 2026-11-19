from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import tornado.testing
import tornado.web

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import new_site_bridge  # noqa: E402


async def echo_app(scope, receive, send):
    """A tiny ASGI app that answers with what it was asked."""
    message = await receive()
    body = json.dumps(
        {
            "method": scope["method"],
            "path": scope["path"],
            "root_path": scope["root_path"],
            "query": scope["query_string"].decode(),
            "scheme": scope["scheme"],
            "body": message["body"].decode(),
        }
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 201,
            "headers": [(b"content-type", b"application/json"), (b"set-cookie", b"a=1"), (b"set-cookie", b"b=2")],
        }
    )
    await send({"type": "http.response.body", "body": body})


class OldSitePage(tornado.web.RequestHandler):
    def get(self, path: str) -> None:
        self.write(f"streamlit:{path}")


class BridgeTest(tornado.testing.AsyncHTTPTestCase):
    def get_app(self) -> tornado.web.Application:
        # Like Streamlit's app: a catch-all page handler that would answer every address.
        app = tornado.web.Application([(r"/(.*)", OldSitePage)], xsrf_cookies=True)
        new_site_bridge.attach(app, echo_app)
        return app

    def test_addresses_under_new_reach_the_new_site(self) -> None:
        response = self.fetch("/new/api/orders/forms?salons=Artisan", headers={"X-Forwarded-Proto": "https"})
        self.assertEqual(response.code, 201)
        answer = json.loads(response.body)
        self.assertEqual(
            (answer["path"], answer["root_path"], answer["query"], answer["scheme"]),
            ("/new/api/orders/forms", "/new", "salons=Artisan", "https"),
        )
        self.assertEqual(response.headers.get_list("Set-Cookie"), ["a=1", "b=2"])

    def test_a_post_keeps_its_body_without_streamlits_form_token(self) -> None:
        response = self.fetch("/new/api/auth/login", method="POST", body='{"login":"x"}')
        self.assertEqual(response.code, 201)
        self.assertEqual(json.loads(response.body)["body"], '{"login":"x"}')

    def test_new_without_slash_redirects(self) -> None:
        response = self.fetch("/new", follow_redirects=False)
        self.assertEqual((response.code, response.headers["Location"]), (302, "/new/"))

    def test_the_old_site_keeps_every_other_address(self) -> None:
        for path in ("/", "/news", "/newsletter/x", "/_stcore/health"):
            with self.subTest(path=path):
                self.assertEqual(self.fetch(path).body.decode(), f"streamlit:{path[1:]}")


class RealNewSiteTest(tornado.testing.AsyncHTTPTestCase):
    """The FastAPI app itself behind the bridge: its pages and its API answer under /new."""

    def get_app(self) -> tornado.web.Application:
        try:
            from web_api.main import app as new_site
        except ImportError:  # The new site's packages are not installed.
            raise unittest.SkipTest("fastapi is not installed")
        app = tornado.web.Application([(r"/(.*)", OldSitePage)])
        new_site_bridge.attach(app, new_site)
        return app

    def test_the_api_answers_and_asks_to_sign_in(self) -> None:
        response = self.fetch("/new/api/auth/me")
        self.assertEqual(response.code, 401)
        self.assertIn("Войдите", json.loads(response.body)["detail"])

    def test_the_interface_and_its_files_are_served(self) -> None:
        dist = APP_DIR / "web_api" / "dist"
        if not dist.is_dir():
            self.skipTest("the interface is not built")
        page = self.fetch("/new/")
        self.assertEqual(page.code, 200)
        self.assertIn(b'src="./assets/', page.body)
        script = next((dist / "assets").glob("*.js")).name
        asset = self.fetch(f"/new/assets/{script}")
        self.assertEqual(asset.code, 200)
        self.assertIn("javascript", asset.headers["Content-Type"])


if __name__ == "__main__":
    unittest.main()
