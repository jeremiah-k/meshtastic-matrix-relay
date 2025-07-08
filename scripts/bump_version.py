#!/usr/bin/env python3
"""
Version bumping script for MMRelay.

This script updates version numbers in all relevant locations throughout the codebase.

NOTE: This is a temporary solution. When connection issues are resolved, we should
reduce the number of locations where version numbers need to be maintained.

Current version locations:
- setup.py (line 9)
- src/mmrelay/__init__.py (line 17, fallback)
- .github/workflows/build-pyz-armv7.yml (fallback)
- .github/workflows/docker-publish.yml (fallback)

Usage:
    python scripts/bump_version.py <new_version>

Example:
    python scripts/bump_version.py 1.2.0
"""

import argparse
import re
import sys
from pathlib import Path


def update_file(file_path: Path, pattern: str, replacement: str, description: str):
    """Update a file with the new version using regex pattern."""
    try:
        content = file_path.read_text()
        new_content = re.sub(pattern, replacement, content)

        if content != new_content:
            file_path.write_text(new_content)
            print(f"✅ Updated {description}: {file_path}")
            return True
        else:
            print(f"⚠️  No changes needed in {description}: {file_path}")
            return False
    except Exception as e:
        print(f"❌ Error updating {description} in {file_path}: {e}")
        return False


def bump_version(new_version: str):
    """Update version in all relevant files."""
    print(f"🚀 Bumping version to {new_version}")

    # Validate version format (basic semver check)
    if not re.match(r"^\d+\.\d+\.\d+$", new_version):
        print("❌ Version must be in format X.Y.Z (e.g., 1.2.0)")
        return False

    root_dir = Path(__file__).parent.parent
    updates_made = 0

    # Update setup.py
    setup_py = root_dir / "setup.py"
    if update_file(
        setup_py, r'version="[^"]*"', f'version="{new_version}"', "setup.py version"
    ):
        updates_made += 1

    # Update __init__.py fallback version
    init_py = root_dir / "src/mmrelay/__init__.py"
    if update_file(
        init_py,
        r'__version__ = "[^"]*"',
        f'__version__ = "{new_version}"',
        "__init__.py fallback version",
    ):
        updates_made += 1

    # Update GitHub workflow fallbacks
    armv7_workflow = root_dir / ".github/workflows/build-pyz-armv7.yml"
    if update_file(
        armv7_workflow,
        r'echo "1\.[0-9]+\.[0-9]+"',
        f'echo "{new_version}"',
        "ARMv7 workflow fallback version",
    ):
        updates_made += 1

    if update_file(
        armv7_workflow,
        r'VERSION="1\.[0-9]+\.[0-9]+"',
        f'VERSION="{new_version}"',
        "ARMv7 workflow default version",
    ):
        updates_made += 1

    docker_workflow = root_dir / ".github/workflows/docker-publish.yml"
    if update_file(
        docker_workflow,
        r'echo "1\.[0-9]+\.[0-9]+"',
        f'echo "{new_version}"',
        "Docker workflow fallback version",
    ):
        updates_made += 1

    if update_file(
        docker_workflow,
        r'VERSION="1\.[0-9]+\.[0-9]+"',
        f'VERSION="{new_version}"',
        "Docker workflow default version",
    ):
        updates_made += 1

    print(f"\n🎉 Version bump complete! Made {updates_made} updates.")

    if updates_made > 0:
        print("\n📝 Next steps:")
        print("1. Review the changes with: git diff")
        print("2. Test the build: make build")
        print(
            "3. Commit the changes: git add . && git commit -m 'Bump version to {}'".format(
                new_version
            )
        )
        print("4. Create a tag: git tag v{}".format(new_version))
        print("5. Push changes: git push && git push --tags")

    return updates_made > 0


def main():
    parser = argparse.ArgumentParser(description="Bump version in all MMRelay files")
    parser.add_argument("version", help="New version number (e.g., 1.2.0)")

    args = parser.parse_args()

    success = bump_version(args.version)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
