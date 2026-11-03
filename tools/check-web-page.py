"""Drive the web page in Chrome against a real Bridge and scripted sessions.

From the repository root:

    uv run --locked --with playwright python tools/check-web-page.py \\
        [--chrome /usr/bin/google-chrome] [--screenshots DIR]

It starts an in-process Bridge, three session hosts whose vendor is this
script, and the real web surface, then checks what the page shows and sends:
Markdown replies, tool rows, approvals, prompt outcomes, streaming, interrupt,
focus, session switching, and the theme. No agent, model, or network is
needed; Playwright drives the given Chrome and downloads no browser.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
from queue import Empty, Queue
import sys
import tempfile
import time

from deskhelm_core.server import BridgeServer
from deskhelm_host import BridgeLink, SessionState
from deskhelm_protocol import (
    ApprovalOverflow,
    ApprovalSupport,
    CapabilityMatrix,
    EffectKind,
    EffectOutcome,
    FactKind,
    InputSupport,
    PromptWhileBusy,
    RuntimeIdentity,
    SessionKey,
)
from deskhelm_web import WebSurface
from playwright.sync_api import Page, sync_playwright

TOKEN = "check-web-page-token-0123456789"
CODEX = SessionKey("codex", "local-3f2a9c", "deskhelm")
CLAUDE = SessionKey("claude", "local-77b1e0", "website")
GROK = SessionKey("grok", "local-0c9d41", "deskhelm")
CATALOG = {
    "model": "gpt-5.5-codex",
    "reasoning_effort": "medium",
    "models": [
        {"id": "gpt-5.5-codex", "name": "GPT-5.5 Codex", "is_default": True, "default_effort": "medium",
         "efforts": [{"id": "low"}, {"id": "medium"}, {"id": "high"}]},
        {"id": "gpt-5.5-codex-mini", "name": "GPT-5.5 Codex Mini", "efforts": [{"id": "low"}]},
    ],
    "mcp_servers": [{"name": "github", "status": "connected"}],
    "skills": [{"name": "review", "enabled": True}],
}
REPLY = """The failing test expects a `ValueError`:

```python
def parse(text):
    if not text:
        return None
```

1. Raise for **empty** input.
2. Re-run the suite.

| Case | Result |
|---|---|
| empty | `ValueError` |
"""


class ScriptedDriver:
    """A session host driver whose vendor is this script."""

    runtime = RuntimeIdentity("deskhelm-check", "1", "check", "1")

    def __init__(self, capabilities: CapabilityMatrix) -> None:
        self.capabilities = capabilities
        self.performed: Queue[tuple[EffectKind, dict]] = Queue()
        self.outcome = EffectOutcome.ACCEPTED
        self.silent = False

    def start(self, emit) -> None:
        self.emit = emit

    def perform(self, kind: EffectKind, payload: dict) -> EffectOutcome:
        self.performed.put((kind, payload))
        if self.silent:
            # The Bridge reports dispatch_failed at its deadline.
            raise RuntimeError("no receipt")
        return self.outcome

    def state(self) -> SessionState:
        return SessionState(ready=True)

    def wait(self) -> int:
        return 0

    def close(self) -> None:
        return None

    def take(self, kind: EffectKind, timeout: float = 5.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                performed, payload = self.performed.get(timeout=0.2)
            except Empty:
                continue
            if performed is kind:
                return payload
        raise AssertionError(f"the page did not send {kind.value}")


class Check:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def __call__(self, label: str, ok: bool) -> None:
        print(("PASS " if ok else "FAIL ") + label, flush=True)
        if not ok:
            self.failures.append(label)


def capabilities(**changes) -> CapabilityMatrix:
    base = CapabilityMatrix(
        version=1, prompt_while_busy=PromptWhileBusy.STEER, interrupt=True, approvals=ApprovalSupport.ANSWERABLE,
        approval_overflow=ApprovalOverflow.DECLINE, user_input=InputSupport.NATIVE_UI, resume=False,
    )
    return replace(base, **changes)


def host(path: Path, key: SessionKey, driver: ScriptedDriver) -> BridgeLink:
    link = BridgeLink(path, key, driver, client_id=f"check-{key.agent_id}", reconnect_initial=0.05, reconnect_max=0.1)
    link.start()
    if not link.opened.wait(3):
        raise RuntimeError("the session did not open")
    driver.start(link.emit)
    return link


def user_message(driver: ScriptedDriver, command_id: str, text: str) -> None:
    meta = {"message_ref": command_id, "role": "user", "phase": "complete", "command_id": command_id}
    driver.emit(FactKind.MESSAGE, meta, {"text": text})


def announced(page: Page, text: str, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if page.locator("#live").text_content() == text:
            return True
        page.wait_for_timeout(50)
    return False


def send(page: Page, text: str) -> None:
    page.fill("#prompt", text)
    page.press("#prompt", "Enter")


def pending(page: Page, text: str):
    return page.locator(".tail .msg.user", has_text=text)


def run(page: Page, drivers: dict[str, ScriptedDriver], check: Check, shots: Path | None) -> None:
    codex, claude = drivers["codex"], drivers["claude"]
    page.locator("#session-list .session", has_text="Codex").click()
    page.locator("#prompt:not([disabled])").wait_for(timeout=10_000)
    check("the selected session names the agent", page.locator("#topbar h1").text_content() == "Codex")
    page.locator(".chip .chip-label", has_text="GPT-5.5 Codex · 中").wait_for(timeout=5000)
    codex.emit(FactKind.CATALOG, content={"catalog": {**CATALOG, "reasoning_effort": "high"}})
    page.locator(".chip .chip-label", has_text="GPT-5.5 Codex · 高").wait_for(timeout=5000)
    check("the model chip follows each catalog the host pushes", True)

    # Markdown and tools from the replay window.
    check("a reply renders Markdown code, lists, and tables",
          page.locator(".msg.assistant .md .code pre").count() == 1
          and page.locator(".msg.assistant .md ol li").count() == 2
          and page.locator(".msg.assistant .md table td").count() == 2)
    check("a tool row shows its command line without the shell wrapper",
          page.locator(".tool .tool-title").first.text_content() == "uv run pytest")
    page.locator(".tool.is-failed .tool-head").click()
    check("a failed tool opens to its output", "1 failed" in page.locator(".tool.is-failed pre").text_content())
    codex.emit(FactKind.TOOL, {"tool_ref": "t1", "label": "command", "phase": "complete", "exit_code": 1}, {"text": ""})
    page.wait_for_timeout(300)
    check("an open tool stays open across an update", page.locator(".tool.is-failed.is-open").count() == 1)

    # Streaming.
    codex.emit(FactKind.TURN_STARTED, {"turn_ref": "x2"})
    page.locator(".composer .stop").wait_for(timeout=5000)
    for piece in ("Look", "ing at ", "it"):
        codex.emit(FactKind.MESSAGE, {"message_ref": "a2", "role": "assistant", "phase": "delta"}, {"text": piece})
    page.locator(".msg.assistant.is-streaming", has_text="Looking at it").wait_for(timeout=5000)
    check("a streaming reply grows in place", page.locator(".msg.assistant.is-streaming").count() == 1)
    codex.emit(FactKind.MESSAGE, {"message_ref": "a2", "role": "assistant", "phase": "complete"}, {"text": "Looking at it now."})
    page.locator(".msg.assistant:not(.is-streaming)", has_text="Looking at it now.").wait_for(timeout=5000)
    check("the finished reply replaces the stream", page.locator(".msg.assistant.is-streaming").count() == 0)

    # Interrupt while working.
    page.click(".composer .stop")
    codex.take(EffectKind.INTERRUPT)
    check("stop sends an interrupt", True)
    codex.emit(FactKind.TURN_TERMINAL, {"status": "interrupted", "reusable": True, "turn_ref": "x2"}, {})
    page.locator(".turn", has_text="这一轮已中断").wait_for(timeout=5000)
    check("the interrupted turn is marked", page.locator(".composer .stop").is_hidden())

    # Prompt outcomes.
    send(page, "first prompt")
    command_id = codex.take(EffectKind.SUBMIT)["command_id"]
    pending(page, "first prompt").wait_for(timeout=5000)
    user_message(codex, command_id, "first prompt")
    pending(page, "first prompt").wait_for(state="detached", timeout=5000)
    check("a sent prompt settles into one user message by command id",
          page.locator(".items .msg.user", has_text="first prompt").count() == 1)
    codex.silent = True
    send(page, "maybe arrived")
    pending(page, "maybe arrived").locator(".msg-state", has_text="结果未知").wait_for(timeout=12_000)
    check("dispatch_failed is labelled unknown", pending(page, "maybe arrived").get_attribute("class").count("is-unknown") == 1)
    codex.silent = False
    codex.outcome = EffectOutcome.UNDELIVERED
    send(page, "not delivered")
    pending(page, "not delivered").locator(".msg-state", has_text="Agent 当前无法接收这个操作").wait_for(timeout=5000)
    check("an undelivered prompt returns to the composer", page.input_value("#prompt") == "not delivered")
    page.fill("#prompt", "")
    codex.outcome = EffectOutcome.ACCEPTED
    page.route("**/api/control", lambda route: route.abort())
    send(page, "lost response")
    pending(page, "lost response").locator(".msg-state", has_text="结果未知").wait_for(timeout=5000)
    check("a lost response is labelled unknown", announced(page, "页面没有收到回应，结果未知"))
    page.unroute("**/api/control")

    # Approval.
    codex.emit(FactKind.REQUEST_OPENED, {"provider_ref": "r1", "kind": "approval", "summary": "Run git push",
                                         "expires_at": int(time.time() * 1000) + 120_000, "answerable": True})
    approve = page.locator(".approval.is-live .button.primary")
    approve.wait_for(timeout=5000)
    check("a pending approval shows a countdown", "有效" in page.locator(".approval-time").text_content())
    check("the session list counts the approval", page.locator(".session .badge").first.text_content() == "1")
    check("the tab title counts the approval", page.title() == "(1) DeskHelm")
    if shots:
        page.screenshot(path=str(shots / "approval.png"))
    approve.click()
    decision = codex.take(EffectKind.RESPOND)
    check("approve sends the vendor reference", (decision["provider_ref"], decision["decision"]) == ("r1", "approve"))
    codex.emit(FactKind.REQUEST_RESOLVED, {"provider_ref": "r1", "by": "deskhelm"})
    page.locator(".approval-state", has_text="已在这里批准").wait_for(timeout=5000)
    check("a decided approval says who decided it", page.locator(".approval.is-live").count() == 0)

    # Focus, details, and switching.
    page.click("#topbar .button.ghost")
    page.locator("#topbar .button.ghost.is-on").wait_for(timeout=5000)
    check("focus marks the session for voice", page.locator(".session .focus-mark").count() == 1)
    page.click("#topbar [aria-label='会话详情']")
    page.locator(".details .catalog .name", has_text="github").wait_for(timeout=5000)
    check("details list the MCP servers", True)
    page.keyboard.press("Escape")
    page.locator("#session-list .session", has_text="Claude").click()
    page.locator(".msg.user", has_text="Summarize the pricing page").wait_for(timeout=5000)
    check("switching sessions switches the transcript", page.locator("#topbar h1").text_content() == "Claude")
    claude.emit(FactKind.MESSAGE, {"message_ref": "c2", "role": "assistant", "phase": "complete"}, {"text": "Second note."})
    page.locator(".msg.assistant", has_text="Second note.").wait_for(timeout=5000)
    drivers["grok"].emit(FactKind.MESSAGE, {"message_ref": "g1", "role": "assistant", "phase": "complete"}, {"text": "Grok news."})
    page.locator("#session-list .session", has_text="Grok").locator(".unseen").wait_for(timeout=5000)
    check("new activity elsewhere is marked unseen", page.locator("#session-list .session", has_text="Claude").locator(".unseen").count() == 0)
    page.reload()
    page.locator("#topbar h1", has_text="Claude").wait_for(timeout=5000)
    check("the selection survives a reload", page.locator(".msg.assistant", has_text="Second note.").count() == 1)

    # Theme.
    before = page.locator("html").get_attribute("data-theme")
    page.click("#theme-toggle")
    page.reload()
    page.locator("#topbar h1", has_text="Claude").wait_for(timeout=5000)
    check("the theme choice persists", page.locator("html").get_attribute("data-theme") != before)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--chrome", default="/usr/bin/google-chrome")
    parser.add_argument("--screenshots", type=Path, default=None)
    args = parser.parse_args()
    if args.screenshots:
        args.screenshots.mkdir(parents=True, exist_ok=True)
    check = Check()
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "bridge.sock"
        server = BridgeServer(path, tick_seconds=0.05)
        server.start()
        drivers = {
            "codex": ScriptedDriver(capabilities(configure=frozenset({"model", "effort"}),
                                                 catalog=frozenset({"models", "mcp", "skills"}))),
            "claude": ScriptedDriver(capabilities()),
            "grok": ScriptedDriver(capabilities()),
        }
        links = [host(path, key, drivers[key.agent_id]) for key in (CODEX, CLAUDE, GROK)]
        codex, claude = drivers["codex"], drivers["claude"]
        codex.emit(FactKind.CATALOG, content={"catalog": CATALOG})
        codex.emit(FactKind.TURN_STARTED, {"turn_ref": "x1"})
        codex.emit(FactKind.TOOL, {"tool_ref": "t1", "label": "command", "phase": "start"},
                   {"text": "/usr/bin/zsh -lc 'uv run pytest'"})
        codex.emit(FactKind.TOOL, {"tool_ref": "t1", "label": "command", "phase": "output", "stream": "stdout"},
                   {"text": "..F\n1 failed\n"})
        codex.emit(FactKind.TOOL, {"tool_ref": "t1", "label": "command", "phase": "complete", "exit_code": 1}, {"text": ""})
        codex.emit(FactKind.MESSAGE, {"message_ref": "a1", "role": "assistant", "phase": "complete"}, {"text": REPLY})
        codex.emit(FactKind.TURN_TERMINAL, {"status": "completed", "reusable": True, "turn_ref": "x1"}, {})
        claude.emit(FactKind.MESSAGE, {"message_ref": "c1", "role": "user", "phase": "complete"},
                    {"text": "Summarize the pricing page"})
        web = WebSurface(path, host="127.0.0.1", port=0, access_token=TOKEN)
        web.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=args.chrome, headless=True)
                page = browser.new_page(viewport={"width": 1280, "height": 860})
                errors: list[str] = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("console", lambda message: message.type == "error" and errors.append(message.text))
                page.goto(f"http://127.0.0.1:{web.port}/?token={TOKEN}")
                try:
                    run(page, drivers, check, args.screenshots)
                finally:
                    if args.screenshots:
                        page.screenshot(path=str(args.screenshots / "last.png"))
                # A lost response is reported by the browser as one failed request.
                errors = [error for error in errors if "Failed to load resource" not in error]
                check("the page logged no errors", not errors)
                for error in errors:
                    print("  ", error)
                browser.close()
        finally:
            web.close()
            for link in links:
                link.close()
            server.close()
    print("RESULT", "ok" if not check.failures else f"{len(check.failures)} failed")
    return 1 if check.failures else 0


if __name__ == "__main__":
    sys.exit(main())
