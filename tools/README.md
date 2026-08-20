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
