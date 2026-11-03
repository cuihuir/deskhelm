from __future__ import annotations

import http.client
import json
from pathlib import Path
import re
import sys
import tempfile
import time
import unittest

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from deskhelm_client.texts import RESULT_TEXT  # noqa: E402
from deskhelm_core.server import BridgeServer  # noqa: E402
from deskhelm_host import BridgeLink  # noqa: E402
from deskhelm_protocol import EffectKind, EffectOutcome, FactKind, SessionKey  # noqa: E402
from deskhelm_web import WebSurface  # noqa: E402
from deskhelm_web.http import ASSET_DIRECTORY, load_web_token  # noqa: E402
from helpers.v2 import FakeDriver  # noqa: E402


TOKEN = "web-surface-test-token-0123456789"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
KEY = SessionKey("fake", "session-1", "demo")
PAGE_KEY = "fake\u001fsession-1\u001fdemo"


def _wait(predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("condition was not met")
        time.sleep(0.02)


class WebSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "bridge.sock"
        self.server = self._server()
        self.driver = FakeDriver()
        self.link = BridgeLink(self.path, KEY, self.driver, client_id="host-test", reconnect_initial=0.05, reconnect_max=0.1)
        self.link.start()
        self.addCleanup(self.link.close)
        self.assertTrue(self.link.opened.wait(3))
        self.driver.start(self.link.emit)

    def _server(self) -> BridgeServer:
        server = BridgeServer(self.path, tick_seconds=0.05)
        server.start()
        self.addCleanup(server.close)
        return server

    def _web(self) -> WebSurface:
        web = WebSurface(self.path, host="127.0.0.1", port=0, access_token=TOKEN)
        web.client.reconnect_initial, web.client.reconnect_max = 0.05, 0.1
        web.start()
        self.addCleanup(web.close)
        self.assertTrue(web.client.wait_ready(3))
        return web

    def _raw(self, web: WebSurface, method: str, path: str, headers=None, body: bytes | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", web.port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def _get(self, web: WebSurface, path: str) -> tuple[int, dict]:
        status, _, body = self._raw(web, "GET", path, AUTH)
        return status, json.loads(body)

    def _post(self, web: WebSurface, payload: dict) -> tuple[int, dict]:
        headers = {**AUTH, "Content-Type": "application/json", "Origin": f"http://127.0.0.1:{web.port}"}
        status, _, body = self._raw(web, "POST", "/api/control", headers, json.dumps(payload).encode())
        return status, json.loads(body)

    def _session(self, web: WebSurface) -> dict:
        _, snapshot = self._get(web, "/api/snapshot")
        return next(item for item in snapshot["sessions"] if item["key"] == PAGE_KEY)

    def _approval(self) -> None:
        self.driver.emit(
            FactKind.REQUEST_OPENED,
            {"provider_ref": "rpc-1", "kind": "approval", "summary": "请求使用工具 Bash",
             "expires_at": int(time.time() * 1000) + 60_000, "answerable": True},
        )

    def test_requests_need_the_token_and_a_loopback_host(self) -> None:
        web = self._web()
        missing, headers, _ = self._raw(web, "GET", "/api/snapshot")
        redirect, cookie_headers, _ = self._raw(web, "GET", f"/?token={TOKEN}")
        cookie = cookie_headers.get("Set-Cookie", "")
        with_cookie, _, _ = self._raw(web, "GET", "/api/snapshot", {"Cookie": cookie.split(";", 1)[0]})
        foreign, _, _ = self._raw(web, "GET", "/api/snapshot", {**AUTH, "Host": "evil.example"})
        text, _, _ = self._raw(web, "POST", "/api/control", {**AUTH, "Content-Type": "text/plain"}, b"{}")
        cross, _, _ = self._raw(
            web, "POST", "/api/control", {**AUTH, "Content-Type": "application/json", "Origin": "http://evil.example"}, b"{}"
        )

        self.assertEqual((missing, redirect, with_cookie, foreign, text, cross), (401, 303, 200, 403, 415, 403))
        self.assertIn("Bearer", headers.get("WWW-Authenticate", ""))
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)

    def test_the_page_sees_the_console_in_its_v1_shape(self) -> None:
        web = self._web()
        session = self._session(web)
        _, snapshot = self._get(web, "/api/snapshot")
        page, _, script = self._raw(web, "GET", "/assets/app.js", AUTH)

        self.assertTrue(snapshot["connected"])
        self.assertEqual((session["lifecycle"], session["agent_state"], session["label"]), ("active", "idle", "fake"))
        self.assertEqual(session["capabilities"], ["interaction_events", "submit_prompt", "interrupt",
                                                   "approval_requests", "approval_decisions"])
        self.assertIsNone(session["approval"])
        self.assertEqual(page, 200)
        self.assertIn(b"payload.message", script)

    def test_the_page_falls_back_to_the_shared_wording(self) -> None:
        script = (ASSET_DIRECTORY / "app.js").read_text(encoding="utf-8")
        block = re.search(r"var CODE_TEXT = \{(.*?)\n  \};", script, re.S)
        assert block is not None
        fallback = dict(re.findall(r'^ +([a-z_]+): "([^"]*)",?$', block.group(1), re.M))

        self.assertEqual({code: fallback.get(code) for code in RESULT_TEXT}, RESULT_TEXT)

    def test_a_pending_approval_is_listed_and_decided_by_its_approval_id(self) -> None:
        self._approval()
        web = self._web()
        _wait(lambda: self._session(web)["approval"] is not None)
        approval = self._session(web)["approval"]

        waiting = self._session(web)["agent_state"]

        status, result = self._post(web, {"kind": "approve", **KEY.to_dict(), "request_id": approval["request_id"]})
        kind, payload = self.driver.performed.get(timeout=2)

        self.assertEqual(waiting, "waiting_approval")
        self.assertEqual((status, result["code"], result["message"]), (200, "dispatched", "已交给 Agent"))
        self.assertEqual((kind, payload["provider_ref"], payload["decision"]), (EffectKind.RESPOND, "rpc-1", "approve"))

    def test_recent_activity_reaches_a_page_opened_later(self) -> None:
        self.driver.emit(FactKind.MESSAGE, {"message_ref": "m1", "role": "assistant", "phase": "complete"}, {"text": "hello"})
        web = self._web()
        _wait(lambda: bool(self._get(web, "/api/snapshot")[1]["events"]))
        event = self._get(web, "/api/snapshot")[1]["events"][0]

        self.assertEqual((event["key"], event["kind"], event["payload"]["text"]), (PAGE_KEY, "message", "hello"))

    def test_controls_carry_the_shared_wording(self) -> None:
        web = self._web()
        prompt = self._post(web, {"kind": "submit_prompt", **KEY.to_dict(), "text": "hello"})
        self.driver.outcome = EffectOutcome.UNDELIVERED
        interrupt = self._post(web, {"kind": "interrupt", **KEY.to_dict()})
        invalid = self._post(web, {"kind": "approve", **KEY.to_dict(), "request_id": " "})
        configure = self._post(web, {"kind": "configure_session", **KEY.to_dict(), "model": "example-model"})

        self.assertEqual((prompt[0], prompt[1]["code"]), (200, "dispatched"))
        self.assertEqual((interrupt[1]["code"], interrupt[1]["message"]), ("undelivered", "Agent 当前无法接收这个操作"))
        self.assertEqual(invalid, (400, {"error": "invalid_control_request"}))
        self.assertEqual(configure[1]["code"], "unsupported")

    def test_the_catalog_comes_from_the_bridge_cache(self) -> None:
        self.driver.emit(FactKind.CATALOG, content={"catalog": {"model": "example-model", "models": [], "skills": []}})
        web = self._web()
        found = self._get(web, "/api/catalog?agent_id=fake&session_id=session-1&project_id=demo")
        unknown = self._get(web, "/api/catalog?agent_id=fake&session_id=other&project_id=demo")
        malformed = self._get(web, "/api/catalog?agent_id=fake")

        self.assertEqual((found[1]["available"], found[1]["model"]), (True, "example-model"))
        self.assertEqual((unknown[1]["available"], unknown[1]["notice"]), (False, "catalog_unavailable"))
        self.assertEqual(malformed, (400, {"error": "invalid_catalog_request"}))

    def test_a_bridge_restart_starts_a_new_page_epoch(self) -> None:
        web = self._web()
        first = self._get(web, "/api/snapshot")[1]["epoch"]
        self.server.close()
        _wait(lambda: not web.client.connected)
        missing = self._post(web, {"kind": "interrupt", **KEY.to_dict()})
        self.server = self._server()
        _wait(lambda: web.client.connected and self._get(web, "/api/snapshot")[1]["sessions"])
        after = self._get(web, "/api/snapshot")[1]

        self.assertEqual(missing[0], 503)
        self.assertNotEqual(after["epoch"], first)
        self.assertEqual(after["notice"], "interaction stream restarted")

    def test_the_event_stream_starts_with_a_snapshot(self) -> None:
        web = self._web()
        connection = http.client.HTTPConnection("127.0.0.1", web.port, timeout=3)
        connection.request("GET", "/api/events", headers=AUTH)
        response = connection.getresponse()
        data = b""
        while b"\n\n" not in data and len(data) < 100_000:
            # read1 returns what arrived; read(n) would wait for n bytes.
            chunk = response.read1(4096)
            if not chunk:
                break
            data += chunk
        connection.close()

        self.assertEqual(response.status, 200)
        self.assertTrue(data.startswith(b"event: snapshot\n"))

    def test_the_token_file_is_private_and_reused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "web-token"
            first = load_web_token(path)
            second = load_web_token(path)
            mode = path.stat().st_mode & 0o777

        self.assertEqual(first, second)
        self.assertEqual(mode, 0o600)


if __name__ == "__main__":
    unittest.main()
