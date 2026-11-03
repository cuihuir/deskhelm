from __future__ import annotations

from pathlib import Path
import http.client
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from deskhelm_bridge.adapter import AdapterSessionEvent
from deskhelm_bridge.event import AgentEvent, AgentState
from deskhelm_bridge.client import AdapterSessionPublisher
from deskhelm_bridge.control_result import (
    ControlResult,
    ControlResultCode,
    ControlResultStatus,
)
from deskhelm_bridge.interaction import (
    ApprovalRequestPayload,
    InteractionEvent,
    InteractionKind,
    MessagePayload,
    MessagePhase,
    MessageRole,
)
from deskhelm_bridge.transport import (
    INTERACTION_EVENT_V1_CAPABILITY,
    encode_frame,
)
from deskhelm_bridge.web_client import (
    MAX_EVENTS_PER_SESSION,
    WebClient,
    WebControlError,
    _FrameBuffer,
    load_web_token,
    normalize_web_host,
    normalize_web_port,
)


ROOT = Path(__file__).resolve().parents[1]
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
TEST_TOKEN = "test-web-token-0123456789"
AUTH_HEADERS = {"Authorization": f"Bearer {TEST_TOKEN}"}
JSON_HEADERS = {**AUTH_HEADERS, "Content-Type": "application/json"}


def _identity(session_id: str = "session-web") -> dict[str, str]:
    return {
        "agent_id": "codex",
        "session_id": session_id,
        "project_id": "deskhelm",
    }


def _message(event_id: str, text: str = "ok") -> dict[str, object]:
    return {
        "event_id": event_id,
        "kind": "message",
        "payload": {"role": "assistant", "phase": "complete", "text": text},
        **_identity(),
    }


class WebClientBoundsTests(unittest.TestCase):
    def test_one_recv_keeps_every_ndjson_frame(self) -> None:
        pending = _FrameBuffer()
        pending.push(b'{"n":1}\n{"n":2}\n')
        self.assertEqual(json.loads(pending.pop() or b""), {"n": 1})
        self.assertEqual(json.loads(pending.pop() or b""), {"n": 2})
        self.assertIsNone(pending.pop())
        pending.push(b'{"n":3}\n{"n":')
        self.assertEqual(json.loads(pending.pop() or b""), {"n": 3})
        self.assertIsNone(pending.pop())
        pending.push(b'4}\n')
        self.assertEqual(json.loads(pending.pop() or b""), {"n": 4})

    def test_loopback_host_is_required_without_allow_remote(self) -> None:
        self.assertEqual(normalize_web_host("127.0.0.1", allow_remote=False), "127.0.0.1")
        self.assertEqual(normalize_web_host("localhost", allow_remote=False), "127.0.0.1")
        self.assertEqual(normalize_web_host("0.0.0.0", allow_remote=True), "0.0.0.0")
        with self.assertRaises(ValueError):
            normalize_web_host("0.0.0.0", allow_remote=False)
        with self.assertRaises(ValueError):
            normalize_web_host("10.0.0.8", allow_remote=False)
        with self.assertRaises(ValueError):
            normalize_web_port(True)  # type: ignore[arg-type]

    def test_slot_placeholder_does_not_mark_an_active_session_offline(self) -> None:
        client = WebClient(
            Path("/tmp/deskhelm-web-missing.sock"),
            host="127.0.0.1",
            port=9,
            client_id="deskhelm-web",
        )
        client._sessions = [
            {
                "agent_id": "codex",
                "session_id": "local-alpha",
                "project_id": "luna_niao",
                "slot": 0,
                "state": "active",
                "updated_at": 10,
                "registered_at": 10,
            }
        ]
        client._state_events[0] = AgentEvent(
            agent_id="slot-1",
            slot=0,
            state=AgentState.OFFLINE,
        )
        session = client.session_snapshot()["sessions"][0]
        self.assertEqual(session["lifecycle"], "active")
        self.assertEqual(session["agent_state"], "")
        self.assertEqual(session["label"], "")

        client._state_events[0] = AgentEvent(
            agent_id="codex",
            slot=0,
            state=AgentState.THINKING,
            label="luna_niao",
        )
        session = client.session_snapshot()["sessions"][0]
        self.assertEqual(session["agent_state"], "thinking")
        self.assertEqual(session["label"], "luna_niao")
        self.assertEqual(session["runtime_name"], "")
        self.assertEqual(session["capabilities"], [])
        client._sessions[0]["runtime_name"] = "claude-code"
        client._sessions[0]["runtime_version"] = "2"
        client._sessions[0]["capabilities"] = [
            "submit_prompt",
            "not_a_capability",
            "submit_prompt",
        ]
        session = client.session_snapshot()["sessions"][0]
        self.assertEqual(session["runtime_name"], "claude-code")
        self.assertEqual(session["runtime_version"], "2")
        self.assertEqual(session["capabilities"], ["submit_prompt"])

    def test_attention_does_not_become_an_approval(self) -> None:
        client = WebClient(Path("/tmp/deskhelm-web-missing.sock"), host="127.0.0.1", port=9, client_id="deskhelm-web")
        client.remember_interaction({
            "kind": "attention",
            "payload": {"reason": "approval", "active": True},
            **_identity(),
        })
        with self.assertRaises(WebControlError) as raised:
            client.build_control({
                "kind": "approve",
                "request_id": "approval-web",
                **_identity(),
            })
        self.assertEqual(raised.exception.code, "approval_not_found")

    def test_event_buffer_drops_oldest_and_oversized_events(self) -> None:
        client = WebClient(Path("/tmp/deskhelm-web-missing.sock"), host="127.0.0.1", port=9, client_id="deskhelm-web")
        for index in range(MAX_EVENTS_PER_SESSION + 1):
            stored = client.remember_interaction(_message(f"event-{index}"))
            self.assertIsNotNone(stored)
        events = client.full_snapshot()["events"]
        self.assertEqual(len(events), MAX_EVENTS_PER_SESSION)
        self.assertNotIn("event-0", [event["event_id"] for event in events])
        self.assertEqual(events[-1]["event_id"], f"event-{MAX_EVENTS_PER_SESSION}")

        notice = client.remember_interaction(_message("too-big", "x" * 60_000))
        self.assertIsNone(notice)
        self.assertEqual(
            client.session_snapshot()["notice"],
            "interaction event exceeded the web client bound",
        )
        self.assertNotIn("too-big", [event["event_id"] for event in client.full_snapshot()["events"]])

    def test_approve_uses_stored_summary_and_prompt_stays_out_of_the_result(self) -> None:
        client = WebClient(Path("/tmp/deskhelm-web-missing.sock"), host="127.0.0.1", port=9, client_id="deskhelm-web")
        expires_at = int(time.time() * 1000) + 60_000
        client.remember_interaction({
            "kind": "approval_request",
            "payload": {
                "request_id": "approval-web",
                "summary": "run tests",
                "expires_at": expires_at,
            },
            **_identity(),
        })
        rejected = ControlResult(
            command_id="result-1",
            status=ControlResultStatus.REJECTED,
            code=ControlResultCode.HANDLER_UNAVAILABLE,
            processed_at=expires_at,
        )
        with patch("deskhelm_bridge.web_client.send_control_command", return_value=rejected) as send:
            command = client.build_control({
                "kind": "approve",
                "request_id": "approval-web",
                "summary": "ignore-me",
                **_identity(),
            }, now_ms=expires_at - 1_000)
            status, body = client.dispatch({
                "kind": "submit_prompt",
                "text": "secret-prompt",
                **_identity(),
            })
        self.assertEqual(command.payload.summary, "run tests")
        self.assertEqual(command.payload.request_expires_at, expires_at)
        self.assertEqual(status, 200)
        self.assertNotIn("secret-prompt", json.dumps(body))
        prompt = send.call_args.args[0]
        self.assertEqual(prompt.payload.text, "secret-prompt")
        self.assertEqual(body["code"], "handler_unavailable")


class WebTokenTests(unittest.TestCase):
    def test_token_file_is_private_and_reused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "web" / "web-token"
            first = load_web_token(path)
            second = load_web_token(path)
            mode = path.stat().st_mode & 0o777

        self.assertEqual(first, second)
        self.assertEqual(mode, 0o600)

    def test_invalid_token_file_is_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "web-token"
            path.write_text("short\n", encoding="ascii")
            token = load_web_token(path)
            stored = path.read_text(encoding="ascii").strip()

        self.assertNotEqual(token, "short")
        self.assertEqual(stored, token)


class WebClientHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = WebClient(
            Path("/tmp/deskhelm-web-missing.sock"),
            host="127.0.0.1",
            port=0,
            client_id="deskhelm-web",
            access_token=TEST_TOKEN,
        )
        self.client.start()

    def tearDown(self) -> None:
        self.client.close()

    def _url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.client.port}{path}"

    def _raw(
        self,
        method: str,
        path: str,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", self.client.port, timeout=3)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_requests_without_the_token_are_refused(self) -> None:
        status, headers, _ = self._raw("GET", "/api/snapshot")

        self.assertEqual(status, 401)
        self.assertIn("Bearer", headers.get("WWW-Authenticate", ""))

    def test_token_link_sets_a_private_cookie_and_drops_the_token(self) -> None:
        status, headers, _ = self._raw("GET", f"/?token={TEST_TOKEN}")
        cookie = headers.get("Set-Cookie", "")
        cookie_status, _, _ = self._raw(
            "GET", "/api/snapshot", {"Cookie": cookie.split(";", 1)[0]}
        )

        self.assertEqual(status, 303)
        self.assertEqual(headers.get("Location"), "/")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertEqual(cookie_status, 200)

    def test_token_redirect_stays_on_this_origin(self) -> None:
        status, headers, _ = self._raw("GET", f"//evil.example/?token={TEST_TOKEN}")
        location = headers.get("Location", "")

        self.assertEqual(status, 303)
        self.assertTrue(location.startswith("/"))
        self.assertFalse(location.startswith("//"))

    def test_wrong_token_and_unknown_host_are_refused(self) -> None:
        wrong, _, _ = self._raw("GET", "/?token=wrong-token-0123456789")
        foreign, _, _ = self._raw(
            "GET", "/api/snapshot", {**AUTH_HEADERS, "Host": "evil.example"}
        )

        self.assertEqual(wrong, 401)
        self.assertEqual(foreign, 403)

    def test_control_requires_json_and_a_same_origin_browser(self) -> None:
        text, _, _ = self._raw(
            "POST", "/api/control", {**AUTH_HEADERS, "Content-Type": "text/plain"}, b"{}"
        )
        cross, _, _ = self._raw(
            "POST", "/api/control", {**JSON_HEADERS, "Origin": "http://evil.example"}, b"{}"
        )
        same, _, body = self._raw(
            "POST",
            "/api/control",
            {**JSON_HEADERS, "Origin": f"http://127.0.0.1:{self.client.port}"},
            b"{",
        )

        self.assertEqual(text, 415)
        self.assertEqual(cross, 403)
        self.assertEqual(same, 400)
        self.assertEqual(body, b'{"error":"invalid_control_request"}')

    def _open(self, path: str, data: bytes | None = None, method: str | None = None) -> tuple[int, bytes]:
        headers = JSON_HEADERS if data is not None else AUTH_HEADERS
        request = urllib.request.Request(
            self._url(path), data=data, method=method, headers=headers
        )
        try:
            with _OPENER.open(request, timeout=3) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            try:
                return error.code, error.read()
            finally:
                error.close()

    def test_page_is_same_origin_and_paths_do_not_escape(self) -> None:
        status, body = self._open("/")
        self.assertEqual(status, 200)
        self.assertIn("给这个会话下一条指令".encode(), body)
        self.assertIn("审查".encode(), body)
        self.assertIn("能力".encode(), body)
        self.assertIn("应用到下一轮".encode(), body)
        self.assertIn(b'id="model-menu"', body)
        self.assertIn(b'id="model-button"', body)
        self.assertIn(b'class="dock"', body)
        self.assertNotIn(b'id="model-pill"', body)
        self.assertNotIn(b'id="capability-form"', body)
        self.assertIn(b'href="/assets/app.css"', body)
        self.assertIn(b'src="/assets/app.js"', body)
        self.assertNotIn(b"cdn.", body)
        self.assertNotIn(b"openai.com", body)
        css_status, css = self._open("/assets/app.css")
        script_status, script = self._open("/assets/app.js")
        self.assertEqual(css_status, 200)
        self.assertIn(b".review[hidden]", css)
        self.assertIn(b".capability[hidden]", css)
        self.assertEqual(script_status, 200)
        self.assertIn(b"deskhelm-web-theme", script)
        self.assertIn(b"submit_prompt", script)
        self.assertIn(b"configure_session", script)
        self.assertIn("先选择一个会话".encode(), script)
        self.assertIn("声明".encode(), body)
        self.assertIn(b'id="agent-capability-list"', body)
        self.assertIn("这个会话还不能列出能力。".encode(), script)
        self.assertIn("这次没有取到能力列表。".encode(), script)
        self.assertIn("已连接 · ".encode(), script)
        self.assertIn("这里只显示本次打开页面之后的活动。更早的对话仍在 Codex 里。".encode(), script)
        self.assertIn(b"thread-preview", script)
        missing_status, missing = self._open("/assets/../app.js")
        self.assertEqual(missing_status, 404)
        self.assertEqual(missing, b'{"error":"not_found"}')
        catalog_status, catalog_body = self._open("/api/catalog")
        self.assertEqual(catalog_status, 400)
        self.assertEqual(catalog_body, b'{"error":"invalid_catalog_request"}')
        absent_status, absent_body = self._open(
            "/api/catalog?agent_id=codex&session_id=session-web&project_id=deskhelm"
        )
        self.assertEqual(absent_status, 503)
        self.assertEqual(absent_body, b'{"error":"bridge_unavailable"}')

    def test_invalid_control_json_is_rejected(self) -> None:
        status, body = self._open("/api/control", data=b"{", method="POST")
        self.assertEqual(status, 400)
        self.assertEqual(body, b'{"error":"invalid_control_request"}')

    def test_event_stream_starts_with_a_snapshot(self) -> None:
        connection = http.client.HTTPConnection("127.0.0.1", self.client.port, timeout=3)
        connection.request("GET", "/api/events", headers=AUTH_HEADERS)
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        data = b""
        while b"\n\n" not in data and len(data) < 100_000:
            chunk = response.read(256)
            if not chunk:
                break
            data += chunk
        connection.close()
        self.assertIn(b"event: snapshot\n", data)
        self.assertNotIn(b"\n\ndata:", data.split(b"\n\n", 1)[0])


class WebClientBridgeTests(unittest.TestCase):
    def test_live_session_can_be_viewed_without_focus_and_controls_hide_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket_path = Path(directory) / "bridge.sock"
            environment = {**os.environ, "PYTHONPATH": str(ROOT / "bridge")}
            bridge = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "deskhelm_bridge",
                    "bridge",
                    "--plain",
                    "--socket",
                    str(socket_path),
                ],
                cwd=ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            client = None
            publisher = None
            try:
                deadline = time.monotonic() + 3
                while not socket_path.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(socket_path.exists(), "bridge socket was not created")
                client = WebClient(
                    socket_path,
                    host="127.0.0.1",
                    port=0,
                    client_id="deskhelm-web",
                    session_refresh_seconds=0.05,
                    access_token=TEST_TOKEN,
                )
                client.start()
                self._poll(client, lambda snapshot: snapshot["connected"] is True)
                publisher = AdapterSessionPublisher(
                    socket_path,
                    client_id="web-session-publisher",
                    additional_capabilities=(INTERACTION_EVENT_V1_CAPABILITY,),
                )
                publisher.connect()
                register = json.loads(
                    (ROOT / "tests/fixtures/protocol/adapter-session-v1/register.json").read_text(
                        encoding="utf-8"
                    )
                )
                now_ms = int(time.time() * 1000)
                register.update({
                    "session_id": "session-web",
                    "preferred_slot": 0,
                    "occurred_at": now_ms,
                    "capabilities": [
                        "interaction_events",
                        "tool_events",
                        "approval_requests",
                        "submit_prompt",
                        "interrupt",
                    ],
                })
                publisher.publish(AdapterSessionEvent.from_dict(register))
                visible = self._poll(
                    client,
                    lambda snapshot: any(
                        session["session_id"] == "session-web" for session in snapshot["sessions"]
                    ),
                )
                self.assertIsNone(visible["focused_session"])
                message = InteractionEvent(
                    kind=InteractionKind.MESSAGE,
                    agent_id="codex",
                    session_id="session-web",
                    project_id="deskhelm",
                    source="codex-app-server",
                    source_version="test",
                    sequence=1,
                    occurred_at=now_ms,
                    correlation_id="msg-web",
                    payload=MessagePayload(
                        role=MessageRole.USER,
                        phase=MessagePhase.COMPLETE,
                        text="STREAM_OK",
                    ),
                )
                approval = InteractionEvent(
                    kind=InteractionKind.APPROVAL_REQUEST,
                    agent_id="codex",
                    session_id="session-web",
                    project_id="deskhelm",
                    source="codex-app-server",
                    source_version="test",
                    sequence=2,
                    occurred_at=now_ms,
                    correlation_id="approval-web",
                    payload=ApprovalRequestPayload(
                        request_id="approval-web",
                        summary="run tests",
                        expires_at=now_ms + 60_000,
                    ),
                )
                assert publisher._client is not None
                publisher._client.sendall(encode_frame(message.to_dict()))
                publisher._client.sendall(encode_frame(approval.to_dict()))
                ready = self._poll(
                    client,
                    lambda snapshot: any(
                        event.get("payload", {}).get("text") == "STREAM_OK"
                        for event in snapshot["events"]
                    )
                    and any(session.get("approval") for session in snapshot["sessions"]),
                )
                self.assertIsNone(ready["focused_session"])
                focus_status, focus_body = self._post(client, {"kind": "focus", **_identity()})
                self.assertEqual(focus_status, 200)
                self.assertEqual(focus_body["code"], "focused")
                self.assertNotIn("STREAM_OK", json.dumps(focus_body))
                focused = self._poll(
                    client,
                    lambda snapshot: snapshot["focused_session"] is not None
                    and snapshot["focused_session"]["session_id"] == "session-web",
                )
                self.assertEqual(focused["focused_session"]["project_id"], "deskhelm")
                approve_status, approve_body = self._post(client, {
                    "kind": "approve",
                    "request_id": "approval-web",
                    "summary": "ignore-me",
                    **_identity(),
                })
                self.assertEqual(approve_status, 200)
                self.assertEqual(approve_body["code"], "handler_unavailable")
                encoded_approval = json.dumps(approve_body)
                self.assertNotIn("run tests", encoded_approval)
                self.assertNotIn("ignore-me", encoded_approval)
                secret = "secret-prompt-not-for-logs"
                prompt_status, prompt_body = self._post(client, {
                    "kind": "submit_prompt",
                    "text": secret,
                    **_identity(),
                })
                self.assertEqual(prompt_status, 200)
                self.assertEqual(prompt_body["code"], "handler_unavailable")
                self.assertNotIn(secret, json.dumps(prompt_body))
                catalog_status, catalog_body = self._get(
                    client,
                    "/api/catalog?agent_id=codex&session_id=session-web&project_id=deskhelm",
                )
                self.assertEqual(catalog_status, 200)
                self.assertFalse(catalog_body["available"])
                self.assertEqual(catalog_body["notice"], "catalog_unavailable")
                self.assertNotIn("STREAM_OK", json.dumps(catalog_body))
                missing_status, missing_body = self._get(
                    client,
                    "/api/catalog?agent_id=codex&session_id=missing&project_id=deskhelm",
                )
                self.assertEqual(missing_status, 200)
                self.assertEqual(missing_body["notice"], "target_not_found")
                invalid_status, invalid_body = self._get(client, "/api/catalog")
                self.assertEqual(invalid_status, 400)
                self.assertEqual(invalid_body["error"], "invalid_catalog_request")
                config_status, config_body = self._post(client, {
                    "kind": "configure_session",
                    "model": "gpt-6.1-sol",
                    "reasoning_effort": "medium",
                    **_identity(),
                })
                self.assertEqual(config_status, 200)
                self.assertEqual(config_body["code"], "handler_unavailable")
                self.assertNotIn("gpt-6.1-sol", json.dumps(config_body))
                self.assertNotIn(secret, json.dumps(config_body))
            finally:
                if client is not None:
                    client.close()
                if publisher is not None:
                    publisher.close()
                if bridge.poll() is None:
                    bridge.terminate()
                stdout, stderr = bridge.communicate(timeout=3)
            self.assertNotIn("secret-prompt-not-for-logs", stdout)
            self.assertNotIn("secret-prompt-not-for-logs", stderr)
            self.assertNotIn("run tests", stderr)

    def _poll(self, client: WebClient, predicate) -> dict:
        deadline = time.monotonic() + 5
        last: dict | None = None
        while time.monotonic() < deadline:
            last = self._snapshot(client)
            if predicate(last):
                return last
            time.sleep(0.05)
        raise AssertionError(last)

    def _snapshot(self, client: WebClient) -> dict:
        request = urllib.request.Request(
            f"http://127.0.0.1:{client.port}/api/snapshot",
            headers=AUTH_HEADERS,
        )
        with _OPENER.open(request, timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))

    def _get(self, client: WebClient, path: str) -> tuple[int, dict]:
        request = urllib.request.Request(
            f"http://127.0.0.1:{client.port}{path}",
            method="GET",
            headers=AUTH_HEADERS,
        )
        try:
            with _OPENER.open(request, timeout=3) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            try:
                return error.code, json.loads(error.read().decode("utf-8"))
            finally:
                error.close()

    def _post(self, client: WebClient, payload: dict) -> tuple[int, dict]:
        request = urllib.request.Request(
            f"http://127.0.0.1:{client.port}/api/control",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers=JSON_HEADERS,
        )
        with _OPENER.open(request, timeout=3) as response:
            return response.status, json.loads(response.read().decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
