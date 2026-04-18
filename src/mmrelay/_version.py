"""
Version helpers with source-checkout fallback support.
"""

from __future__ import annotations

import re
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as metadata_version
from pathlib import Path
from typing import Final

_PACKAGE_NAME: Final[str] = "mmrelay"
_UNKNOWN_VERSION: Final[str] = "0+unknown"
_PROJECT_VERSION_RE: Final[re.Pattern[str]] = re.compile(
    r"""^version\s*=\s*["'](?P<version>[^"']+)["']\s*(?:#.*)?$"""
)

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback path
    tomllib = None  # type: ignore[assignment]


def _version_from_metadata() -> str | None:
    """
    Return installed package version metadata, if available.
    """
    try:
        return metadata_version(_PACKAGE_NAME)
    except PackageNotFoundError:
        return None


def _find_pyproject_toml() -> Path | None:
    """
    Find pyproject.toml by walking upward from this module.
    """
    this_file = Path(__file__).resolve()
    for parent in this_file.parents:
        candidate = parent / "pyproject.toml"
        if candidate.is_file():
            return candidate
    return None


def _version_from_pyproject() -> str | None:
    """
    Read project.version from pyproject.toml in source checkouts.
    """
    pyproject = _find_pyproject_toml()
    if pyproject is None:
        return None

    try:
        if tomllib is not None:
            with pyproject.open("rb") as handle:
                data = tomllib.load(handle)
            version = data.get("project", {}).get("version")
            if isinstance(version, str):
                normalized = version.strip()
                return normalized or None
            return None

        # Python 3.10 fallback: parse only [project] version entry.
        in_project_section = False
        for line in pyproject.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("[") and stripped.endswith("]"):
                in_project_section = stripped == "[project]"
                continue
            if not in_project_section:
                continue
            match = _PROJECT_VERSION_RE.match(stripped)
            if match:
                normalized = match.group("version").strip()
                return normalized or None
    except (OSError, UnicodeError, ValueError):
        return None

    return None


def get_version() -> str:
    """
    Return application version with stable fallbacks.

    Precedence:
    1) Installed package metadata
    2) pyproject.toml from source checkout
    3) 0+unknown sentinel
    """
    metadata_value = _version_from_metadata()
    if metadata_value:
        return metadata_value

    pyproject_value = _version_from_pyproject()
    if pyproject_value:
        return pyproject_value

    return _UNKNOWN_VERSION


__version__: Final[str] = get_version()
