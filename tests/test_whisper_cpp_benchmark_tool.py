import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "run_whisper_cpp_benchmark",
    ROOT / "tools/run-whisper-cpp-benchmark.py",
)
assert SPEC is not None and SPEC.loader is not None
TOOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOL)


class WhisperCppBenchmarkToolTests(unittest.TestCase):
    def test_parser_bounds_and_model_choices(self) -> None:
        parser = TOOL._parser()
        args = parser.parse_args(
            [
                "--model",
                "base",
                "--model-path",
                "/model.bin",
                "--library-path",
                "/shim.so",
                "--prepared",
                "/prepared",
                "--observations",
                "/observations.ndjson",
                "--summary",
                "/summary.json",
            ]
        )
        self.assertEqual(args.model, "base")
        self.assertEqual(args.repetitions, 3)
        self.assertEqual(args.cpu_threads, 4)
        self.assertEqual(args.language, "auto")

    def test_model_verification_rejects_unpinned_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.bin"
            path.write_bytes(b"not-a-model")
            with self.assertRaisesRegex(ValueError, "size"):
                TOOL._verify_model(path, "base")

    def test_identity_records_pinned_model_and_shim(self) -> None:
        metadata = TOOL.MODEL_METADATA["small"] | {
            "sha256": "a" * 64,
        }
        identity = TOOL._benchmark_identity("small", 4, metadata)
        self.assertEqual(identity.provider_name, "whisper.cpp-shim")
        self.assertEqual(identity.provider_version, "v1.9.2/deskhelm_whisper_shim_v1")
        self.assertEqual(identity.model_version, metadata["upstream_sha1"])
        self.assertEqual(identity.model_license, "MIT")


if __name__ == "__main__":
    unittest.main()
