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
`~/.local/bin/deskhelm`; it resolves the checkout from its real path, preserves
the invoking working directory, and runs the synchronized root environment.
Default `deskhelm codex` joins the user bridge and does not receive a private
`--socket`. `deskhelm alpha` is the same command. If this directory still has a Codex socket, a terminal rebuild
stops that app-server only. It does not signal the user bridge.
Core `deskhelm codex --tui` does not require `project-secrets`, audio models, or
hardware. Every real TUI path uses `run-with-pty.py`, which relays terminal
input, output, resizing, and exit status using only the Python standard library.
Its bounded Ctrl-C guard maps the first raw or Kitty-protocol Ctrl-C to
clear-input and forwards only a second press within the confirmation window to
Codex. `--voice` dynamically injects only the required local-voice settings
from this checkout's encrypted profile; it never stores their values in the
launcher. In voice mode the same relay also writes live output directly to the
controlling terminal and performs boundary-safe streaming redaction. It stops
waiting after the primary child exits even if a descendant temporarily keeps
the PTY open.
