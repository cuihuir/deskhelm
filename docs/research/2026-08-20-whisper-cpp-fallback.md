# whisper.cpp Fallback Research

Date: 2026-08-20

## Outcome

`whisper.cpp` remains a strong quality and licensing fallback for DeskHelm, but
this phase does not select it as the production ASR or add a native dependency
to Bridge. The next implementation step should be an isolated provider
prototype around the C API, followed by the existing provider-neutral benchmark
contract.

## Verified Upstream Facts

The facts below were retrieved from upstream sources on 2026-08-20.

| Area | Evidence | DeskHelm implication |
| --- | --- | --- |
| Runtime | `whisper.cpp` README identifies stable `v1.9.2`; the project is a C/C++ implementation with CPU, quantized, C API, and VAD support | Keep the runtime outside Bridge and pin a stable tag for a prototype |
| License | `whisper.cpp` `LICENSE` is MIT; OpenAI Whisper `LICENSE` and README also state MIT for code and model weights | Lower licensing friction than the current SenseVoice model license, subject to attribution and exact artifact provenance |
| Input boundary | `whisper.h` exposes `whisper_full()` and `whisper_full_with_state()` over raw 32-bit float PCM; model loading accepts a file or memory buffer | A provider can convert bounded S16LE frames in memory without writing WAV/PCM files |
| Cancellation | `whisper_full_params` exposes an `ggml_abort_callback`, and the encoder-begin callback can abort before encoding | Promising for bounded cancellation, but callback frequency and hard-stop behavior require a native prototype measurement |
| Concurrency | The C header says one sample context is not safe for concurrent use | Serialize one context or create explicitly bounded per-request states |
| Streaming | Upstream ships `whisper-stream` and `whisper-command` examples, while ordinary `whisper_full()` is full-window decoding | DeskHelm must define its own partial/final event boundary; do not claim provider partials from a batch call |
| Models | Upstream lists multilingual `base` (142 MiB, about 388 MB memory), `small` (466 MiB, about 852 MB), and quantized variants including `large-v3-turbo-q5_0` (547 MiB) | Start public benchmarking with multilingual `base` and `small`; reserve larger/turbo variants for a resource-qualified quality run |
| Artifacts | `models/README.md` publishes model names and SHA identifiers for converted `ggml` files | Pin model name, source URL, revision, byte size, and SHA-256 before any provider package is accepted |

## Fit With DeskHelm

### Strong fit

- Native C/C++ execution keeps PyTorch, CUDA, and Python model dependencies out
  of the Bridge process.
- The C API consumes PCM already held by the Voice Gateway, so the privacy rule
  does not require an intermediate audio file.
- MIT runtime/model terms are easier to audit than the current SenseVoice model
  agreement.
- The abort callback creates a plausible path for hard in-flight cancellation,
  unlike the current SenseVoice native decode boundary.

### Open risks

- No DeskHelm measurement exists yet for Chinese/mixed coding commands. Broad
  multilingual coverage is not evidence of code-sensitive command accuracy.
- The upstream streaming examples are reference applications, not a
  provider-neutral partial transcript contract. Windowing and endpoint policy
  must remain in the provider boundary.
- A native shared library introduces ABI, CPU-feature, and packaging risks. A
  direct `ctypes` mapping of `whisper_full_params` would be fragile because the
  struct is large and versioned with the header.
- Model mirrors and converted artifacts need exact provenance. Do not treat an
  arbitrary download URL as an OpenAI license grant.

## Recommended Prototype Boundary

1. Keep `WhisperCppAsrProvider` outside the default composition and require an
   explicit model path plus native library path.
2. Prefer a tiny C shim compiled against one pinned `whisper.cpp` tag. Expose a
   DeskHelm-specific ABI for model load, bounded float-PCM transcription,
   segment collection, and an abort flag. Avoid binding the full upstream
   parameter struct from Python.
3. Serialize access to one loaded context, cap input to the existing Gateway
   duration/byte limits, cap output at 4,096 characters, and preserve the
   existing `VoiceCancelled`, `VoiceNoTranscript`, and `voice_asr_timeout`
   semantics.
4. Use `base` and `small` multilingual models for the first public benchmark.
   Add a quantized turbo run only if CPU time and memory remain within the
   workstation budget.
5. Reuse the existing eight-sample ASR benchmark and compare CER, English WER,
   keyword accuracy, final latency, peak RSS, and cancellation latency. No
   live microphone comparison is claimed in this phase.

## Decision

Retain `whisper.cpp` as the next ASR fallback candidate. Do not silently fall
back from Paraformer or SenseVoice at runtime, do not select a production model
from this research alone, and do not add model weights or a native build to the
repository. The provider prototype is the next software phase.

## Primary Sources

- <https://github.com/ggml-org/whisper.cpp>
- <https://github.com/ggml-org/whisper.cpp/releases/tag/v1.9.2>
- <https://raw.githubusercontent.com/ggml-org/whisper.cpp/master/README.md>
- <https://raw.githubusercontent.com/ggml-org/whisper.cpp/master/LICENSE>
- <https://raw.githubusercontent.com/ggml-org/whisper.cpp/master/models/README.md>
- <https://raw.githubusercontent.com/ggml-org/whisper.cpp/master/include/whisper.h>
- <https://github.com/openai/whisper>
- <https://raw.githubusercontent.com/openai/whisper/main/LICENSE>
