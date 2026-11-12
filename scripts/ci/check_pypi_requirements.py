#!/usr/bin/env python3
"""Gate PyPI uploads on direct-reference-compatible distribution requirements.

GitHub/source releases may retain pinned VCS dependencies. PyPI rejects
artifacts whose Requires-Dist metadata includes direct URLs, even when those
URLs are immutable commit references. This gate must run on the tagged tree
before the upload action, not on the mutable default branch. Index availability
is checked separately by resolving the built wheel before upload.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from email import policy
from email.parser import BytesParser
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from packaging.requirements import Requirement


def direct_requirements(manifest: Path) -> list[str]:
    """Return direct-URL dependencies from base and all optional extras."""
    with manifest.open("rb") as handle:
        project = tomllib.load(handle).get("project")
    if not isinstance(project, dict):
        raise ValueError("project must be a table")
    dynamic = project.get("dynamic", [])
    if not isinstance(dynamic, list) or not all(
        isinstance(name, str) for name in dynamic
    ):
        raise ValueError("project.dynamic must be a list of field names")
    if set(dynamic) & {"dependencies", "optional-dependencies"}:
        raise ValueError(
            "Dynamic dependency metadata cannot be checked before build; "
            "the PyPI preflight requires static dependencies"
        )
    base = project.get("dependencies", [])
    extras = project.get("optional-dependencies", {})
    if not isinstance(extras, dict):
        raise ValueError("project.optional-dependencies must be a table")
    direct = []
    for group in (base, *extras.values()):
        if not isinstance(group, list) or not all(
            isinstance(req, str) for req in group
        ):
            raise ValueError("Each requirement group must be a list of strings")
        for text in group:
            if Requirement(text).url is not None:
                direct.append(text)
    return direct


def direct_wheel_requirements(wheel: Path) -> list[str]:
    """Read actual Requires-Dist fields from the built distribution."""
    with ZipFile(wheel) as archive:
        metadata_files = [
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        ]
        if len(metadata_files) != 1:
            raise ValueError("Wheel must contain exactly one .dist-info/METADATA")
        metadata = BytesParser(policy=policy.default).parsebytes(
            archive.read(metadata_files[0])
        )
    requirements = metadata.get_all("Requires-Dist", [])
    return [text for text in requirements if Requirement(text).url is not None]


def main() -> int:
    """Set a GitHub Actions output; never upload unsupported PyPI metadata."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pyproject", type=Path, default=Path("pyproject.toml"))
    parser.add_argument("--wheel", type=Path, help="Check the built wheel metadata too")
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--github-summary", type=Path)
    args = parser.parse_args()
    try:
        direct = direct_requirements(args.pyproject)
        if args.wheel is not None:
            direct.extend(direct_wheel_requirements(args.wheel))
    except (OSError, TypeError, ValueError, BadZipFile, tomllib.TOMLDecodeError) as exc:
        print(f"::error::Cannot validate PyPI requirements: {exc}", file=sys.stderr)
        return 1
    eligible = not direct
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as output:
            output.write(f"eligible={'true' if eligible else 'false'}\n")
    # Pre-bind: pyright cannot correlate the two `direct` guards below.
    names = ""
    if direct:
        names = ", ".join(sorted({Requirement(req).name for req in direct}))
        print(
            "::warning::Skipping PyPI upload: direct-URL Requires-Dist "
            f"not accepted by PyPI ({names}). Publish source/GitHub artifacts "
            "or replace with indexed requirements first."
        )
    else:
        print(
            "No direct-URL requirements declared; index resolution is still required."
        )
    if args.github_summary:
        with args.github_summary.open("a", encoding="utf-8") as summary:
            if direct:
                summary.write(
                    "### PyPI upload skipped\n\n"
                    f"Direct-URL dependencies in package metadata: {names}. "
                    "GitHub/source releases remain available; publish indexed "
                    "dependencies before enabling PyPI upload.\n"
                )
            else:
                summary.write(
                    "### PyPI metadata eligible\n\n"
                    "No direct-URL requirements declared; index resolution is still required.\n"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
