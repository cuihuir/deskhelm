# Tools

Development, manufacturing, firmware packaging, and validation utilities.

Voice benchmark preparation and runners are documented in
[`voice/README.md`](../voice/README.md). TTS uses
`prepare-tts-benchmark.py` and `run-tts-benchmark.py`; both keep downloaded
models, generated speech, environments, and raw results outside Git.

`run-whisper-cpp-benchmark.py` is the explicit fallback runner. It requires a
verified external whisper.cpp `v1.9.2` shim and a pinned `base` or `small`
`ggml` model, then reuses the ASR observation contract without changing the
default provider composition.

`deskhelm-user-launcher` is the source-checkout user command. Link it once as
`~/.local/bin/deskhelm`; it resolves the checkout from its real path,
preserves the invoking working directory, and runs the synchronized root
environment without installing anything. It never injects a socket or signals
a process; the session hosts own their sockets. Only `codex --voice` and
`voice` run under this checkout's encrypted profile, which injects the local
voice settings without storing their values.

`run-with-pty.py` is the relay behind `deskhelm codex`. It relays terminal
input, output, resizing, and exit status using only the standard library. Its
bounded Ctrl-C guard maps the first raw or Kitty-protocol Ctrl-C to
clear-input and forwards only a second press within the confirmation window
to Codex. With voice it also writes output directly to the controlling
terminal and redacts the injected settings across chunk boundaries. It stops
waiting after the primary child exits even if a descendant keeps the PTY
open.

`check-web-page.py` drives the web page in Chrome against a real Bridge and
scripted session hosts, and checks replies, tool rows, approvals, prompt
outcomes, streaming, interrupt, focus, session switching, and the theme. Run
it from the repository root after page work:

```bash
uv run --locked --with playwright python tools/check-web-page.py \
  [--chrome /usr/bin/google-chrome] [--screenshots DIR]
```

It needs no agent, model, or network, and Playwright downloads no browser.
