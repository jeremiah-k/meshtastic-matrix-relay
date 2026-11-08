#!/usr/bin/env python3
"""Gate PyPI uploads on index-resolvable distribution requirements.

GitHub/source releases may retain pinned VCS dependencies. PyPI rejects
artifacts whose Requires-Dist metadata includes direct URLs, even when those
URLs are immutable commit references. This gate must run on the tagged tree
before the upload action, not on the mutable default branch.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

_DIRECT_REFERENCE = re.compile(r"^\s*[A-Za-z0-9_.-]+(?:\[[^]]+\])?\s*@\s*\S")


def direct_requirements(manifest: Path) -> list[str]:
    """Return direct-URL dependencies from base and all optional extras."""
    with manifest.open("rb") as handle:
        project = tomllib.load(handle)["project"]
    requirements = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        requirements.extend(extra)
    if not all(isinstance(value, str) for value in requirements):
        raise ValueError("All project requirements must be strings")
    return [req for req in requirements if _DIRECT_REFERENCE.match(req)]


def main() -> int:
    """Set a GitHub Actions output; never upload unsupported PyPI metadata."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pyproject", type=Path, default=Path("pyproject.toml"))
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    try:
        direct = direct_requirements(args.pyproject)
    except (KeyError, OSError, TypeError, ValueError, tomllib.TOMLDecodeError) as exc:
        print(f"::error::Cannot validate PyPI requirements: {exc}", file=sys.stderr)
        return 1
    eligible = not direct
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as output:
            output.write(f"eligible={'true' if eligible else 'false'}\n")
    if direct:
        names = ", ".join(req.split("@", 1)[0].strip() for req in direct)
        print(
            "::warning::Skipping PyPI upload: direct-URL Requires-Dist "
            f"not accepted by PyPI ({names}). Publish source/GitHub artifacts "
            "or replace with indexed requirements first."
        )
    else:
        print("PyPI requirements are index-resolvable; upload permitted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
