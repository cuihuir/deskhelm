from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from deskhelm_bridge.cli import (
    _parse_agent_project_directories,
    main,
)
from deskhelm_bridge.project_registry import ProjectWorkingDirectoryRegistry
from deskhelm_bridge.session_registry import SessionKey


class ProjectWorkingDirectoryRegistryTests(unittest.TestCase):
    def test_single_directory_preserves_legacy_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = ProjectWorkingDirectoryRegistry.single(Path(directory))

            first = registry.resolve(SessionKey("codex", "one", "project-a"))
            second = registry.resolve(SessionKey("codex", "two", "project-b"))

            self.assertEqual(first, Path(directory).resolve())
            self.assertEqual(second, first)
            self.assertFalse(registry.is_strict())

    def test_strict_registry_maps_opaque_project_ids(self) -> None:
        with (
            tempfile.TemporaryDirectory() as first_directory,
            tempfile.TemporaryDirectory() as second_directory,
        ):
            registry = ProjectWorkingDirectoryRegistry.configured(
                {
                    "project-a": Path(first_directory),
                    "project-b": Path(second_directory),
                }
            )

            self.assertEqual(
                registry.resolve(SessionKey("codex", "one", "project-a")),
                Path(first_directory).resolve(),
            )
            self.assertEqual(
                registry.resolve(SessionKey("codex", "two", "project-b")),
                Path(second_directory).resolve(),
            )
            self.assertEqual(registry.project_count(), 2)
            self.assertTrue(registry.is_strict())

    def test_strict_registry_rejects_unmapped_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = ProjectWorkingDirectoryRegistry.configured(
                {"project-a": Path(directory)}
            )

            with self.assertRaisesRegex(RuntimeError, "no configured"):
                registry.resolve(SessionKey("codex", "two", "project-b"))

    def test_registry_rejects_missing_directories_and_capacity_overflow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "unavailable"):
                ProjectWorkingDirectoryRegistry.configured(
                    {"project-a": Path(directory) / "missing"}
                )
            with self.assertRaisesRegex(ValueError, "capacity"):
                ProjectWorkingDirectoryRegistry.configured(
                    {
                        "project-a": Path(directory),
                        "project-b": Path(directory),
                    },
                    max_projects=1,
                )

    def test_cli_mapping_is_strict_bounded_and_mutually_exclusive(self) -> None:
        with (
            tempfile.TemporaryDirectory() as first_directory,
            tempfile.TemporaryDirectory() as second_directory,
        ):
            registry = _parse_agent_project_directories(
                [
                    f"project-a={first_directory}",
                    f"project-b={second_directory}",
                ],
                max_projects=2,
            )
            self.assertEqual(registry.project_count(), 2)

            with self.assertRaisesRegex(ValueError, "duplicate"):
                _parse_agent_project_directories(
                    [
                        f"project-a={first_directory}",
                        f"project-a={second_directory}",
                    ],
                    max_projects=2,
                )

            with patch("deskhelm_bridge.cli.run_bridge", return_value=0) as run:
                result = main(
                    [
                        "bridge",
                        "--plain",
                        "--agent-provider",
                        "codex",
                        "--agent-project",
                        f"project-a={first_directory}",
                        "--agent-project",
                        f"project-b={second_directory}",
                        "--agent-project-records",
                        "2",
                    ]
                )

            self.assertEqual(result, 0)
            self.assertIsNone(run.call_args.kwargs["agent_working_directory"])
            configured = run.call_args.kwargs["agent_working_directory_registry"]
            self.assertEqual(configured.project_count(), 2)

            with patch("deskhelm_bridge.cli.run_bridge") as run:
                result = main(
                    [
                        "bridge",
                        "--agent-provider",
                        "codex",
                        "--agent-workdir",
                        first_directory,
                        "--agent-project",
                        f"project-a={first_directory}",
                    ]
                )
            self.assertEqual(result, 1)
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
