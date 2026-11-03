from __future__ import annotations

import http.client
import json
from pathlib import Path
import queue
import sys
import tempfile
import time
import unittest
from unittest import mock

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from deskhelm_client.texts import ACTIVITY_TEXT, FAILURE_TEXT, OUTCOME_TEXT, RESULT_TEXT, TOOL_TEXT  # noqa: E402
from deskhelm_core.server import BridgeServer  # noqa: E402
from deskhelm_host import BridgeLink  # noqa: E402
from deskhelm_protocol import EffectKind, EffectOutcome, FactKind, SessionKey  # noqa: E402
from deskhelm_web import WebSurface  # noqa: E402
from deskhelm_web.http import load_web_token  # noqa: E402
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

    def _events_later(self, web: WebSurface, count: int):
        """Open the event stream now, take its snapshot, and return a reader for ``count`` events."""
        connection = http.client.HTTPConnection("127.0.0.1", web.port, timeout=3)
        self.addCleanup(connection.close)
        connection.request("GET", "/api/events", headers=AUTH)
        response = connection.getresponse()
        state: dict = {"buffer": b"", "events": []}

        def pump(until: int) -> list[tuple[str, dict]]:
            deadline = time.monotonic() + 5
            while len(state["events"]) < until and time.monotonic() < deadline:
                # read1 returns what arrived; read(n) would wait for n bytes.
                state["buffer"] += response.read1(65536)
                while b"\n\n" in state["buffer"]:
                    block, state["buffer"] = state["buffer"].split(b"\n\n", 1)
                    fields = dict(line.split(": ", 1) for line in block.decode().splitlines() if ": " in line)
                    if "event" in fields:
                        state["events"].append((fields["event"], json.loads(fields["data"])))
            return state["events"]

        pump(1)
        return lambda: pump(count)

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

    def test_the_page_sees_each_session_in_protocol_v2_terms(self) -> None:
        web = self._web()
        session = self._session(web)
        _, snapshot = self._get(web, "/api/snapshot")

        self.assertTrue(snapshot["connected"])
        self.assertEqual((session["lifecycle"], session["activity"], session["label"]), ("active", "idle", "fake"))
        self.assertEqual(session["runtime"], {"name": "fake", "version": "1"})
        self.assertEqual((session["capabilities"]["approvals"], session["capabilities"]["interrupt"]), ("answerable", True))
        self.assertEqual((session["approvals"], session["outcome"], session["preview"]), ([], None, ""))
        self.assertEqual(snapshot["transcripts"], {})
        self.assertIsNone(snapshot["focus"])

    def test_the_page_modules_are_served_and_nothing_else(self) -> None:
        web = self._web()
        module, headers, script = self._raw(web, "GET", "/assets/js/views/transcript.js", AUTH)
        missing = self._raw(web, "GET", "/assets/js/missing.js", AUTH)[0]
        outside = self._raw(web, "GET", "/assets/../http.py", AUTH)[0]

        self.assertEqual((module, headers["Content-Type"]), (200, "text/javascript; charset=utf-8"))
        self.assertIn(b"export function mountTranscript", script)
        self.assertEqual((missing, outside), (404, 404))

    def test_the_snapshot_carries_the_shared_words(self) -> None:
        web = self._web()
        words = self._get(web, "/api/snapshot")[1]["words"]

        self.assertEqual(words, {"result": RESULT_TEXT, "activity": ACTIVITY_TEXT, "outcome": OUTCOME_TEXT,
                                 "failure": FAILURE_TEXT, "tool": TOOL_TEXT})

    def test_a_pending_approval_is_listed_and_decided_by_its_approval_id(self) -> None:
        self._approval()
        web = self._web()
        _wait(lambda: bool(self._session(web)["approvals"]))
        ticket = self._session(web)["approvals"][0]
        waiting = self._session(web)["activity"]
        item = self._get(web, "/api/snapshot")[1]["transcripts"][PAGE_KEY][0]

        status, result = self._post(web, {"kind": "approve", **KEY.to_dict(), "approval_id": ticket["approval_id"]})
        kind, payload = self.driver.performed.get(timeout=2)

        self.assertEqual(waiting, "waiting_approval")
        self.assertEqual((ticket["kind"], ticket["summary"], ticket["answerable"]), ("approval", "请求使用工具 Bash", True))
        self.assertEqual((item["type"], item["approval_id"]), ("approval", ticket["approval_id"]))
        self.assertEqual((status, result["code"], result["message"]), (200, "dispatched", "已交给 Agent"))
        self.assertEqual((kind, payload["provider_ref"], payload["decision"]), (EffectKind.RESPOND, "rpc-1", "approve"))

    def test_recent_activity_reaches_a_page_opened_later(self) -> None:
        self.driver.emit(FactKind.MESSAGE, {"message_ref": "m1", "role": "assistant", "phase": "complete"}, {"text": "hello"})
        web = self._web()
        _wait(lambda: bool(self._get(web, "/api/snapshot")[1]["transcripts"].get(PAGE_KEY)))
        item = self._get(web, "/api/snapshot")[1]["transcripts"][PAGE_KEY][0]

        self.assertEqual((item["type"], item["role"], item["text"], item["streaming"]), ("message", "assistant", "hello", False))
        self.assertEqual(self._session(web)["preview"], "hello")

    def test_a_session_shows_when_it_last_did_something_not_when_the_page_connected(self) -> None:
        before = int(time.time() * 1000)
        self.driver.emit(FactKind.MESSAGE, {"message_ref": "m1", "role": "assistant", "phase": "complete"}, {"text": "hi"})
        time.sleep(0.3)
        web = self._web()
        _wait(lambda: self._session(web)["preview"] == "hi")
        connected = int(time.time() * 1000)

        self.assertGreaterEqual(self._session(web)["updated_at"], before)
        self.assertLess(self._session(web)["updated_at"], connected - 200)

    def test_a_host_that_leaves_stops_its_running_tool(self) -> None:
        web = self._web()
        self.driver.emit(FactKind.TOOL, {"tool_ref": "t1", "label": "command", "phase": "start"}, {"text": "sleep 60"})
        _wait(lambda: bool(self._get(web, "/api/snapshot")[1]["transcripts"].get(PAGE_KEY)))
        # Drop the connection the way a crashed host does, without session_close.
        self.link._stop.set()
        self.link._drop()
        _wait(lambda: self._get(web, "/api/snapshot")[1]["transcripts"][PAGE_KEY][0]["status"] == "stopped")

        self.assertEqual(self._session(web)["lifecycle"], "disconnected")

    def test_a_browser_that_falls_behind_is_cut_off_cleanly(self) -> None:
        web = self._web()
        slow: queue.Queue = queue.Queue(maxsize=2)
        with web._clients_lock:
            web._clients.append(slow)
        for rev in range(3):
            web.broadcast("patch", {"rev": rev})

        self.assertNotIn(slow, web._clients)
        self.assertEqual([slow.get_nowait() for _ in range(slow.qsize())], [None])

    def test_a_session_past_the_transcript_bound_is_named_in_a_notice(self) -> None:
        other = FakeDriver()
        link = BridgeLink(self.path, SessionKey("fake", "session-2", "demo"), other, client_id="host-other",
                          reconnect_initial=0.05, reconnect_max=0.1)
        link.start()
        self.addCleanup(link.close)
        self.assertTrue(link.opened.wait(3))
        other.start(link.emit)
        with mock.patch("deskhelm_web.server.MAX_TRANSCRIPTS", 1):
            web = self._web()
            self.driver.emit(FactKind.MESSAGE, {"message_ref": "m1", "role": "assistant", "phase": "complete"}, {"text": "one"})
            _wait(lambda: PAGE_KEY in self._get(web, "/api/snapshot")[1]["transcripts"])
            other.emit(FactKind.MESSAGE, {"message_ref": "m2", "role": "assistant", "phase": "complete"}, {"text": "two"})
            _wait(lambda: self._get(web, "/api/snapshot")[1]["notice"] == "too_many_sessions")
            snapshot = self._get(web, "/api/snapshot")[1]

        self.assertEqual(list(snapshot["transcripts"]), [PAGE_KEY])

    def test_live_changes_arrive_as_patches_newer_than_the_snapshot(self) -> None:
        web = self._web()
        reader = self._events_later(web, 4)
        for phase, text in (("delta", "Hel"), ("delta", "lo"), ("complete", "Hello.")):
            self.driver.emit(FactKind.MESSAGE, {"message_ref": "m1", "role": "assistant", "phase": phase}, {"text": text})
        events = reader()

        (name, snapshot), *patches = events
        self.assertEqual(name, "snapshot")
        self.assertEqual([name for name, _ in patches], ["patch", "patch", "patch"])
        self.assertEqual([patch["op"] for _, patch in patches], ["upsert", "append", "upsert"])
        self.assertEqual(patches[1][1]["text"], "lo")
        self.assertEqual(patches[2][1]["item"]["text"], "Hello.")
        self.assertEqual(patches[2][1]["preview"], "Hello.")
        revisions = [snapshot["rev"], *(patch["rev"] for _, patch in patches)]
        self.assertEqual(revisions, sorted(set(revisions)))

    def test_controls_carry_the_shared_wording(self) -> None:
        web = self._web()
        prompt = self._post(web, {"kind": "submit_prompt", **KEY.to_dict(), "text": "hello", "command_id": "page-0123456789"})
        _, performed = self.driver.performed.get(timeout=2)
        self.driver.outcome = EffectOutcome.UNDELIVERED
        interrupt = self._post(web, {"kind": "interrupt", **KEY.to_dict()})
        invalid = self._post(web, {"kind": "approve", **KEY.to_dict(), "approval_id": " "})
        bad_id = self._post(web, {"kind": "interrupt", **KEY.to_dict(), "command_id": "x"})
        configure = self._post(web, {"kind": "configure_session", **KEY.to_dict(), "model": "example-model", "effort": "high"})

        self.assertEqual((prompt[0], prompt[1]["code"], prompt[1]["command_id"]), (200, "dispatched", "page-0123456789"))
        self.assertEqual(performed["command_id"], "page-0123456789")
        self.assertEqual(bad_id, (400, {"error": "invalid_control_request"}))
        self.assertEqual((interrupt[1]["code"], interrupt[1]["message"]), ("undelivered", "Agent 当前无法接收这个操作"))
        self.assertEqual(invalid, (400, {"error": "invalid_control_request"}))
        self.assertEqual(configure[1]["code"], "unsupported")

    def test_the_catalog_comes_from_the_bridge_cache(self) -> None:
        self.driver.emit(FactKind.CATALOG, content={"catalog": {"model": "example-model", "models": [], "skills": [],
                                                                 "agent_capabilities": ["fs.read"]}})
        web = self._web()
        found = self._get(web, "/api/catalog?agent_id=fake&session_id=session-1&project_id=demo")
        unknown = self._get(web, "/api/catalog?agent_id=fake&session_id=other&project_id=demo")
        malformed = self._get(web, "/api/catalog?agent_id=fake")

        self.assertEqual((found[1]["available"], found[1]["model"]), (True, "example-model"))
        self.assertEqual(found[1]["agent_capabilities"], ["fs.read"])
        self.assertEqual(self._session(web)["catalog_rev"], 1)
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
        self.assertEqual(after["notice"], "bridge_restarted")

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
