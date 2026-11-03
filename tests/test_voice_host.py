from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from deskhelm_client import BridgeClient  # noqa: E402
from deskhelm_core.server import BridgeServer  # noqa: E402
from deskhelm_host import BridgeLink  # noqa: E402
from deskhelm_protocol import EffectKind, FactKind, SessionKey, Tier  # noqa: E402
from deskhelm_voice_host.__main__ import main as voice_main, tts_settings  # noqa: E402
from deskhelm_voice_host.policy import VoiceHost  # noqa: E402
from helpers.v2 import FakeDriver  # noqa: E402
from voice.deskhelm_voice import (  # noqa: E402
    CapturedAudio,
    FakeAsrProvider,
    FakeCaptureProvider,
    FakePlaybackProvider,
    FakeTtsProvider,
    Transcript,
    VoiceGateway,
)


KEY = SessionKey("codex", "session-1", "demo")


def _wait(predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("condition was not met")
        time.sleep(0.02)


class VoiceHostTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "bridge.sock"
        server = BridgeServer(self.path, tick_seconds=0.05)
        server.start()
        self.addCleanup(server.close)
        self.driver = FakeDriver()
        link = BridgeLink(self.path, KEY, self.driver, client_id="host-test", reconnect_initial=0.05, reconnect_max=0.1)
        link.start()
        self.addCleanup(link.close)
        self.assertTrue(link.opened.wait(3))
        self.driver.start(link.emit)
        self.operator = BridgeClient(self.path, client_id="web")
        self.operator.start()
        self.addCleanup(self.operator.close)
        self.assertTrue(self.operator.wait_ready(3))

    def _host(self, *spoken: str) -> VoiceHost:
        self.tts = FakeTtsProvider()
        gateway = VoiceGateway(
            capture_provider=FakeCaptureProvider([CapturedAudio(b"\x00\x00" * 160, 16_000) for _ in spoken]),
            asr_provider=FakeAsrProvider([Transcript(text, text) for text in spoken]),
            tts_provider=self.tts,
            playback_provider=FakePlaybackProvider(),
        )
        self.addCleanup(gateway.close)
        holder: dict[str, VoiceHost] = {}
        client = BridgeClient(self.path, client_id="voice", tier=Tier.VOICE,
                              on_console=lambda change: holder["host"].on_console(change))
        host = holder["host"] = VoiceHost(client, gateway)
        host.start()
        self.addCleanup(host.close)
        client.start()
        self.addCleanup(client.close)
        self.assertTrue(client.wait_ready(3))
        return host

    def _focus(self) -> None:
        self.assertEqual(self.operator.command("focus", KEY).code, "focused")

    def _speak(self, host: VoiceHost) -> None:
        _wait(lambda: host.client.console().focus == KEY)
        self.assertTrue(host.press())
        time.sleep(0.05)
        self.assertTrue(host.release())

    def _approval(self, ref: str) -> None:
        self.driver.emit(
            FactKind.REQUEST_OPENED,
            {"provider_ref": ref, "kind": "approval", "summary": "Codex command execution approval",
             "expires_at": int(time.time() * 1000) + 60_000, "answerable": True},
        )

    def _heard(self, text: str) -> None:
        _wait(lambda: any(text in line for line in self.tts.requests))

    def test_a_spoken_prompt_reaches_the_focused_session(self) -> None:
        self._focus()
        host = self._host("运行测试")
        self._speak(host)
        kind, payload = self.driver.performed.get(timeout=3)

        self.assertEqual((kind, payload["text"]), (EffectKind.SUBMIT, "运行测试"))

    def test_an_approval_phrase_decides_the_single_approval_and_is_never_a_prompt(self) -> None:
        self._focus()
        self._approval("rpc-1")
        host = self._host("批准")
        _wait(lambda: bool(host.client.console().approvals(KEY)))
        self._speak(host)
        kind, payload = self.driver.performed.get(timeout=3)

        self.assertEqual((kind, payload["provider_ref"], payload["decision"]), (EffectKind.RESPOND, "rpc-1", "approve"))

    def test_an_approval_phrase_with_nothing_or_two_pending_sends_nothing(self) -> None:
        self._focus()
        host = self._host("拒绝", "批准")
        self._speak(host)
        self._heard("没有等待确认的请求")
        self._approval("rpc-1")
        self._approval("rpc-2")
        _wait(lambda: len(host.client.console().approvals(KEY)) == 2)
        self._speak(host)
        self._heard("有多个请求等待确认")

        self.assertTrue(self.driver.performed.empty())

    def test_cancel_discards_the_utterance(self) -> None:
        self._focus()
        host = self._host("取消")
        self._speak(host)
        self._heard("已取消这句话")

        self.assertTrue(self.driver.performed.empty())

    def test_without_a_focus_nothing_is_captured(self) -> None:
        host = self._host()

        self.assertFalse(host.press())
        self.assertFalse(host.release())
        self._heard("没有聚焦的会话")

    def test_notices_follow_console_transitions_only(self) -> None:
        self._approval("rpc-early")
        host = self._host()
        self.driver.emit(FactKind.TURN_STARTED, {"turn_ref": "turn-1"})
        self.driver.emit(FactKind.TURN_TERMINAL, {"status": "completed", "turn_ref": "turn-1"})
        self._heard("项目 demo，Codex 已完成工作")
        self._approval("rpc-late")
        self._heard("项目 demo，Codex 需要你的确认")
        time.sleep(0.2)

        # The approval that was open before the host attached is not spoken.
        self.assertEqual(sum("需要你的确认" in line for line in self.tts.requests), 1)
        self.assertIsNotNone(host)


class VoiceHostCommandTests(unittest.TestCase):
    def test_the_tts_directory_selects_its_provider(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "zh_CN-chaowen-medium.onnx").write_bytes(b"")
            piper = tts_settings(root)
            (root / "index").mkdir()
            (root / "index" / "config.yaml").write_text("")
            (root / "index" / "voice.wav").write_bytes(b"")
            indextts = tts_settings(root)
            with self.assertRaises(ValueError):
                tts_settings(root / "missing")

        self.assertEqual(piper["provider"], "piper")
        self.assertEqual((indextts["provider"], indextts["model"].name), ("indextts", "index"))

    def test_model_paths_are_required(self) -> None:
        names = ("DESKHELM_ALPHA_ASR_MODEL_DIRECTORY", "DESKHELM_ALPHA_TTS_DIRECTORY")
        with mock.patch.dict(os.environ, {name: "" for name in names}), self.assertRaises(SystemExit):
            voice_main(["--no-ptt", "--asr-model-directory", "/tmp/model"])


if __name__ == "__main__":
    unittest.main()
