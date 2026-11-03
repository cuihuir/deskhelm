# Tests

Cross-component tests and fixtures. No test needs an agent CLI, a login, an
audio device, model weights, or network access.

Run the full suite from the repository root in the voice runtime, which also
has NumPy for the ASR provider tests:

```bash
uv run --project voice/runtime --locked --no-sync python -m unittest discover -s tests -v
```

The root environment runs every other test:
`uv run --locked python -m unittest discover -s tests -v`.

| Area | Tests |
|---|---|
| Protocol v2 core and socket server | `test_core`, `test_core_server` |
| Hosts and drivers | `test_host_link`, `test_drivers`, `test_replay_transcripts`, `test_simulate` |
| Codex host and adapter | `test_codex_host`, `test_codex_app_server_adapter`, `test_codex_app_server_fixtures`, `test_codex_app_server_transport`, `test_codex_tui_proxy`, `test_run_with_pty` |
| Client runtime, surfaces, and command line | `test_client`, `test_web_surface`, `test_dashboard_v2`, `test_cli_switch`, `test_user_launcher` |
| Voice host | `test_voice_host`, `test_voice_keys` |
| Voice library | `test_voice_gateway`, `test_pipewire_providers`, `test_audio_config`, `test_asr_providers`, `test_tts_providers`, `test_vad_providers`, `test_whisper_cpp_provider` |
| Voice tools and benchmarks | `test_voice_benchmark`, `test_vad_benchmark`, `test_vad_tools`, `test_local_asr_diagnostic_tool`, `test_local_voice_live_tool`, `test_whisper_cpp_benchmark_tool` |

`helpers/v2.py` provides small protocol v2 peers, `helpers/replay.py` the
replay harness, `helpers/control_host.py` a stand-in host for the Codex
app-server adapter, and `helpers/fake_pw_cat.py` a fake `pw-cat` so PipeWire
providers run without audio.

## Fixtures

Runtime evidence lives under `fixtures/adapters/<adapter>/`, and each set's
manifest records its source type, runtime version, and provenance. Official
examples and synthetic fixtures stay distinguished, and no fixture claims
local capture unless it was captured. The Codex app-server set is
schema-derived and synthetic for locally inspected Codex CLI 0.149.0; the
Claude and Grok sets are synthetic.

Replay transcripts under `fixtures/replay/<runtime>/<scenario>.ndjson` drive
the real Claude, Grok, and Codex drivers through an in-process Bridge core;
only the vendor transport is replaced. The format is `deskhelm-replay-v1`: a
header naming the runtime, scenario, `source_type`, and provenance notes,
followed by `inbound`, `outbound`, `control`, and `events` steps, as the
`helpers/replay.py` docstring describes. Every current transcript is
synthetic. A recorded transcript must use a public prompt, replace paths,
home directories, and vendor session ids with placeholders, set
`source_type` to `recorded`, name the runtime version, and be reviewed by a
person before it is committed. Adapter changes keep every transcript passing
and add one for new vendor behavior.
