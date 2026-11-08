#!/usr/bin/env python3
"""Gate PyPI uploads on index-resolvable distribution requirements.

GitHub/source releases may retain pinned VCS dependencies. PyPI rejects
artifacts whose Requires-Dist metadata includes direct URLs, even when those
URLs are immutable commit references. This gate must run on the tagged tree
before the upload action, not on the mutable default branch.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

from packaging.requirements import Requirement


def direct_requirements(manifest: Path) -> list[str]:
    """Return direct-URL dependencies from base and all optional extras."""
    with manifest.open("rb") as handle:
        project = tomllib.load(handle).get("project")
    if not isinstance(project, dict):
        raise ValueError("project must be a table")
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


def main() -> int:
    """Set a GitHub Actions output; never upload unsupported PyPI metadata."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pyproject", type=Path, default=Path("pyproject.toml"))
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--github-summary", type=Path)
    args = parser.parse_args()
    try:
        direct = direct_requirements(args.pyproject)
    except (OSError, TypeError, ValueError, tomllib.TOMLDecodeError) as exc:
        print(f"::error::Cannot validate PyPI requirements: {exc}", file=sys.stderr)
        return 1
    eligible = not direct
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as output:
            output.write(f"eligible={'true' if eligible else 'false'}\n")
    names = ", ".join(Requirement(req).name for req in direct)
    if direct:
        print(
            "::warning::Skipping PyPI upload: direct-URL Requires-Dist "
            f"not accepted by PyPI ({names}). Publish source/GitHub artifacts "
            "or replace with indexed requirements first."
        )
    else:
        print("No direct-URL requirements declared; PyPI upload permitted.")
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
                    "### PyPI upload eligible\n\n"
                    "No direct-URL requirements declared.\n"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
