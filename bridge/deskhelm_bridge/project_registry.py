from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from .session_registry import SessionKey


DEFAULT_MAX_PROJECT_WORKING_DIRECTORIES = 64
MAX_PROJECT_WORKING_DIRECTORIES = 1024


class ProjectWorkingDirectoryRegistry:
    __slots__ = ("_default", "_projects", "max_projects")

    def __init__(
        self,
        *,
        default_working_directory: Path | None = None,
        project_working_directories: Mapping[str, Path] | None = None,
        max_projects: int = DEFAULT_MAX_PROJECT_WORKING_DIRECTORIES,
    ) -> None:
        if (
            not isinstance(max_projects, int)
            or isinstance(max_projects, bool)
            or not 1 <= max_projects <= MAX_PROJECT_WORKING_DIRECTORIES
        ):
            raise ValueError("project working-directory capacity is invalid")
        projects = dict(project_working_directories or {})
        if default_working_directory is not None and projects:
            raise ValueError(
                "default and project working directories cannot be combined"
            )
        if default_working_directory is None and not projects:
            raise ValueError("at least one Agent working directory is required")
        if len(projects) > max_projects:
            raise ValueError("project working-directory capacity is exceeded")

        normalized: dict[str, Path] = {}
        for project_id, directory in projects.items():
            if not isinstance(project_id, str) or not project_id.strip():
                raise ValueError("project working-directory identity is invalid")
            if project_id in normalized:
                raise ValueError("project working-directory identity is duplicated")
            normalized[project_id] = _validated_directory(directory)

        self._default = (
            _validated_directory(default_working_directory)
            if default_working_directory is not None
            else None
        )
        self._projects = MappingProxyType(normalized)
        self.max_projects = max_projects

    @classmethod
    def single(cls, directory: Path) -> ProjectWorkingDirectoryRegistry:
        return cls(default_working_directory=directory, max_projects=1)

    @classmethod
    def configured(
        cls,
        projects: Mapping[str, Path],
        *,
        max_projects: int = DEFAULT_MAX_PROJECT_WORKING_DIRECTORIES,
    ) -> ProjectWorkingDirectoryRegistry:
        return cls(
            project_working_directories=projects,
            max_projects=max_projects,
        )

    def resolve(self, session: SessionKey) -> Path:
        if not isinstance(session, SessionKey):
            raise ValueError("Agent session identity is invalid")
        if self._default is not None:
            return self._default
        directory = self._projects.get(session.project_id)
        if directory is None:
            raise RuntimeError(
                "target project has no configured Agent working directory"
            )
        return directory

    def project_count(self) -> int:
        return len(self._projects) if self._default is None else 1

    def is_strict(self) -> bool:
        return self._default is None


def _validated_directory(directory: Path) -> Path:
    if not isinstance(directory, Path):
        raise ValueError("Agent working directory must be a Path")
    resolved = directory.expanduser().resolve()
    if not resolved.is_dir():
        raise ValueError("Agent working directory is unavailable")
    return resolved
