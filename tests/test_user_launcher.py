from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

LAUNCHER = Path(__file__).resolve().parents[1] / "tools" / "deskhelm-user-launcher"
_ECHO = """#!/usr/bin/env bash
printf '%s\\n' "$(basename "$0") $PWD $*"
"""


class UserLauncherTests(unittest.TestCase):
    """The launcher only adds voice settings; the v2 hosts own the rest."""

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "tools").mkdir()
        (self.root / ".venv" / "bin").mkdir(parents=True)
        (self.root / "bin").mkdir()
        (self.root / "project").mkdir()
        shutil.copy(LAUNCHER, self.root / "tools" / "deskhelm-user-launcher")
        for path in (self.root / ".venv" / "bin" / "deskhelm", self.root / "bin" / "project-secrets"):
            path.write_text(_ECHO)
            path.chmod(0o755)

    def _run(self, *arguments: str) -> str:
        completed = subprocess.run(
            ["bash", str(self.root / "tools" / "deskhelm-user-launcher"), *arguments],
            cwd=self.root / "project",
            env={**os.environ, "PATH": f"{self.root / 'bin'}:{os.environ['PATH']}"},
            capture_output=True,
            text=True,
            check=True,
        )
        return completed.stdout.strip()

    def test_commands_reach_the_runner_unchanged_from_the_same_directory(self) -> None:
        project = self.root / "project"

        self.assertEqual(self._run("codex", "--tui"), f"deskhelm {project} codex --tui")
        self.assertEqual(self._run("web"), f"deskhelm {project} web")
        self.assertEqual(self._run("voice", "--help"), f"deskhelm {project} voice --help")

    def test_voice_runs_under_the_encrypted_profile(self) -> None:
        project = self.root / "project"
        runner = self.root / ".venv" / "bin" / "deskhelm"
        names = "DESKHELM_ALPHA_VOICE_SOURCE DESKHELM_ALPHA_ASR_MODEL_DIRECTORY DESKHELM_ALPHA_TTS_DIRECTORY"

        self.assertEqual(
            self._run("codex", "--tui", "--voice"),
            f"project-secrets {project} --project {self.root} run --env default --require {names} -- "
            f"{runner} codex --tui --voice",
        )
        self.assertTrue(self._run("voice", "--default-source").endswith(f"{runner} voice --default-source"))


if __name__ == "__main__":
    unittest.main()
