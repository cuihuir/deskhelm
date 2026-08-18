# Voice Benchmarks

Versioned, provider-neutral benchmark inputs for local VAD, ASR, and TTS
selection. The corpus contains public synthetic text only; it does not contain
captured user audio, credentials, source code, or private prompts.

## Corpus

`utterances-v1.json` covers Chinese, English, mixed language, commands, paths,
URLs, version numbers, symbols, negation, repetition, and longer recovery
instructions. `utterance_id` is stable within version 1. Change reference text
or keyword expectations only by creating a new corpus version.

`vad-external-v1.json` is the first public-audio manifest. It pins six FSDD
source recordings by commit and SHA-256 and defines seven deterministic
speech/silence scenarios. The source and prepared audio remain outside Git;
only provenance, license identity, checksums, and recipes are committed. A
future private capture set must record speaker consent, microphone and room
metadata, sample format, checksum, and redistribution terms.

## Observation Format

VAD, ASR, and TTS runs write UTF-8 NDJSON with one versioned observation per
line. Each record includes run/provider/model/device identity, an utterance or
sample ID, provider and model license identifiers, an anonymous system profile,
repetition, status, latency, process CPU time, optional peak RSS/VRAM, and a
fixed error code. ASR success records include the transcript; failed records
never include provider exception text. TTS records include only output size,
not generated audio. VAD records contain only audio format, duration, chunk
bound, derived speech overlap/error durations, segment count, and timing/resource
metrics; they never contain raw PCM or provider exceptions. Use an exact SPDX
identifier when verified and the literal `unverified` when a provider or model
license still needs review.

Records are limited to 1 MiB each, 64 MiB per file, and 10,000 observations per
run. Model weights, generated audio, raw microphone captures, and local
benchmark results belong outside Git unless their provenance and redistribution
terms are clear.

Score an ASR observation file:

```bash
PYTHONPATH=voice python3 -m deskhelm_voice.benchmark score-asr \
  --corpus voice/benchmarks/utterances-v1.json \
  --observations /path/to/asr-observations.ndjson
```

Summarize a TTS observation file:

```bash
PYTHONPATH=voice python3 -m deskhelm_voice.benchmark summarize-tts \
  --corpus voice/benchmarks/utterances-v1.json \
  --observations /path/to/tts-observations.ndjson
```

Summarize a VAD observation file:

```bash
PYTHONPATH=voice python3 -m deskhelm_voice.benchmark summarize-vad \
  --observations /path/to/vad-observations.ndjson
```

ASR summaries report CER, English-only WER, keyword accuracy, final/partial
latency, CPU time, and real-time factor when audio duration is known. TTS
summaries currently cover batch synthesis latency, CPU time, and output size;
streaming first-audio and interruption timing enter the provider benchmark when
that contract is implemented. VAD summaries report frame-weighted speech
precision, recall and F1, total false-positive/false-negative duration,
first-speech detection delay, processing latency, CPU time, and real-time factor.

## Streaming VAD Inputs

VAD runners consume complete-frame `PcmChunk` values with one immutable format
and contiguous absolute frame positions starting at zero. Each chunk is limited
to 1 MiB. Reference speech segments are non-overlapping frame intervals. Each
in-memory sample is limited to 100,000 chunks, 256 speech segments, and 64 MiB
PCM. Offline replay feeds chunks without sleeping, so processing latency and
frame-relative detection delay are reported separately from future live
end-to-end measurements.

Prepare the external set with `tools/prepare-vad-benchmark.py`. It verifies
source checksums, converts to 16 kHz mono S16LE, pads to complete 20 ms chunks,
and writes a local checksum/index file. Run candidates with
`tools/run-vad-benchmark.py`; both tools require an explicit ignored artifact
directory and do not add optional runtimes to Bridge.
