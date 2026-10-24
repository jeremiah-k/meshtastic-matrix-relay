#!/usr/bin/env python3
"""Verify the experimental Matrix SDK selected by this branch's install extra."""

from __future__ import annotations

import json
import re
import tomllib
from importlib.metadata import distribution
from pathlib import Path


def main() -> None:
    """Reject a release wheel or unrelated source masquerading as the candidate."""
    metadata = Path(__file__).resolve().parents[1] / "pyproject.toml"
    project = tomllib.loads(metadata.read_text(encoding="utf-8"))["project"]
    entries = project["optional-dependencies"]["integration"]
    if len(entries) != 1:
        raise ValueError("integration must select exactly one SDK source")
    match = re.fullmatch(
        r"mindroom-nio\[e2e\] @ git\+(https://github\.com/jeremiah-k/mindroom-nio\.git)@([0-9a-f]{40}) ; python_version >= '3\.12'",
        entries[0],
    )
    if match is None:
        raise ValueError("integration SDK must be a full SHA from the configured fork")
    installed = distribution("mindroom-nio")
    source = json.loads(installed.read_text("direct_url.json") or "{}")
    vcs = source.get("vcs_info", {})
    if (
        source.get("url") != match[1]
        or vcs.get("vcs") != "git"
        or vcs.get("commit_id") != match[2]
    ):
        raise RuntimeError("installed Matrix SDK does not match the integration pin")
    from nio.crypto import ENCRYPTION_ENABLED

    if not ENCRYPTION_ENABLED:
        raise RuntimeError("installed Matrix SDK cryptography is disabled")
    print(f"Matrix SDK {installed.version}: {match[2]}; cryptography enabled")


if __name__ == "__main__":
    main()
