import hashlib
import json
from pathlib import Path
import tempfile
from threading import Event
import unittest
import wave

from voice.deskhelm_voice import (
    PcmChunk,
    PcmStreamFormat,
    VadEventKind,
    VadRunManifest,
    WebRtcVadProvider,
    load_prepared_vad_samples,
)


MANIFEST_PATH = Path("voice/benchmarks/vad-external-v1.json")


class _FakeWebRtcDetector:
    def __init__(self, mode: int, decisions: list[bool]) -> None:
        self.mode = mode
        self._decisions = iter(decisions)

    def is_speech(self, data: bytes, sample_rate_hz: int) -> bool:
        return next(self._decisions)


class VadProviderTests(unittest.TestCase):
    def test_external_manifest_has_pinned_unique_sources_and_scenarios(self) -> None:
        manifest = VadRunManifest.load(MANIFEST_PATH)

        self.assertEqual(manifest.sample_rate_hz, 16000)
        self.assertEqual(manifest.chunk_ms, 20)
        self.assertEqual(len(manifest.sources), 6)
        self.assertEqual(len(manifest.scenarios), 7)
        self.assertTrue(
            all(len(source.revision) == 40 for source in manifest.sources)
        )

    def test_prepared_samples_require_matching_checksum_and_exact_chunks(self) -> None:
        manifest = VadRunManifest.load(MANIFEST_PATH)
        scenario = manifest.scenarios[0]
        frame_count = 3200
        pcm = b"\x01\x00" * frame_count
        with tempfile.TemporaryDirectory() as directory:
            prepared = Path(directory)
            wav_path = prepared / f"{scenario.scenario_id}.wav"
            with wave.open(str(wav_path), "wb") as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(16000)
                stream.writeframes(pcm)
            checksum = hashlib.sha256(wav_path.read_bytes()).hexdigest()
            entries = [
                {
                    "sample_id": item.scenario_id,
                    "file_name": f"{item.scenario_id}.wav",
                    "sha256": checksum,
                    "total_frames": frame_count,
                    "reference_segments": [
                        {
                            "start_frame": 320 + index * 1280,
                            "end_frame": 1600 + index * 1280,
                        }
                        for index in range(
                            sum(
                                part.source_id is not None
                                for part in item.parts
                            )
                        )
                    ],
                }
                for item in manifest.scenarios
            ]
            for entry in entries[1:]:
                (prepared / entry["file_name"]).write_bytes(wav_path.read_bytes())
            (prepared / "index.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "manifest_name": manifest.name,
                        "dataset_revision": manifest.dataset_revision,
                        "samples": entries,
                    }
                ),
                encoding="utf-8",
            )

            samples = load_prepared_vad_samples(MANIFEST_PATH, prepared)
            self.assertEqual(len(samples), 7)
            self.assertEqual(len(samples[0].chunks), 10)
            self.assertEqual(samples[0].reference_segments[0].start_frame, 320)
            wav_path.write_bytes(wav_path.read_bytes() + b"tamper")
            with self.assertRaisesRegex(ValueError, "checksum"):
                load_prepared_vad_samples(MANIFEST_PATH, prepared)

    def test_webrtc_adapter_applies_bounded_start_and_end_hysteresis(self) -> None:
        decisions = [False, True, True, False, True] + [False] * 8
        detector = _FakeWebRtcDetector(2, decisions)
        provider = WebRtcVadProvider(vad_factory=lambda mode: detector)
        stream_format = PcmStreamFormat(sample_rate_hz=16000)
        chunks = tuple(
            PcmChunk(b"\x00\x00" * 320, stream_format, index * 320)
            for index in range(len(decisions))
        )

        events = []
        with provider.open_session(stream_format) as session:
            for chunk in chunks:
                events.extend(session.process(chunk, Event()))
            events.extend(session.finish(Event()))

        self.assertEqual([event.kind for event in events], [
            VadEventKind.SPEECH_STARTED,
            VadEventKind.SPEECH_ENDED,
        ])
        self.assertEqual(events[0].frame_index, 320)
        self.assertEqual(events[1].frame_index, 1600)

    def test_webrtc_adapter_buffers_partial_frames_and_rejects_format(self) -> None:
        provider = WebRtcVadProvider(
            vad_factory=lambda mode: _FakeWebRtcDetector(mode, [False])
        )
        with self.assertRaisesRegex(ValueError, "mono S16LE"):
            provider.open_session(PcmStreamFormat(sample_rate_hz=44100))
        stream_format = PcmStreamFormat(sample_rate_hz=16000)
        with provider.open_session(stream_format) as session:
            self.assertEqual(
                session.process(
                    PcmChunk(b"\x00\x00" * 160, stream_format, 0),
                    Event(),
                ),
                (),
            )
            self.assertEqual(
                session.process(
                    PcmChunk(b"\x00\x00" * 160, stream_format, 160),
                    Event(),
                ),
                (),
            )


if __name__ == "__main__":
    unittest.main()
