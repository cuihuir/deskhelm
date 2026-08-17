import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
PYTHONPATH = str(ROOT / "bridge")


class BridgeEndToEndTests(unittest.TestCase):
    def test_emit_updates_bridge_slot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket_path = Path(directory) / "bridge.sock"
            environment = {**os.environ, "PYTHONPATH": PYTHONPATH}
            bridge = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "deskhelm_bridge",
                    "bridge",
                    "--plain",
                    "--max-events",
                    "1",
                    "--socket",
                    str(socket_path),
                ],
                cwd=ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                deadline = time.monotonic() + 3
                while not socket_path.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(socket_path.exists(), "bridge socket was not created")

                emitted = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "deskhelm_bridge",
                        "emit",
                        "--socket",
                        str(socket_path),
                        "--agent-id",
                        "demo:backend",
                        "--slot",
                        "1",
                        "--state",
                        "running_tool",
                        "--label",
                        "backend",
                    ],
                    cwd=ROOT,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                self.assertEqual(emitted.returncode, 0, emitted.stderr)
                stdout, stderr = bridge.communicate(timeout=3)
            finally:
                if bridge.poll() is None:
                    bridge.terminate()
                    bridge.wait(timeout=3)

            self.assertEqual(bridge.returncode, 0, stderr)
            self.assertIn("slot=1", stdout)
            self.assertIn("state=running_tool", stdout)


if __name__ == "__main__":
    unittest.main()
