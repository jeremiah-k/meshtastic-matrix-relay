import os
import re

from setuptools import find_packages, setup

# Read version from __init__.py without importing the package
with open(
    os.path.join(os.path.dirname(__file__), "src", "mmrelay", "__init__.py"),
    encoding="utf-8",
) as f:
    content = f.read()
    match = re.search(r'__version__\s*=\s*["\']([^"\']*)["\']', content)
    if match:
        __version__ = match.group(1)
    else:
        raise RuntimeError("Version string not found in src/mmrelay/__init__.py")

# Read README file with proper resource management
with open("README.md", encoding="utf-8") as f:
    long_description = f.read()

setup(
    name="mmrelay",
    version=__version__,
    author="Geoff Whittington, Jeremiah K., and contributors",
    author_email="jeremiahk@gmx.com",
    description="Bridge between Meshtastic mesh networks and Matrix chat rooms",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/jeremiah-k/meshtastic-matrix-relay",
    project_urls={
        "Bug Tracker": "https://github.com/jeremiah-k/meshtastic-matrix-relay/issues"
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)",
        "Operating System :: OS Independent",
        "Development Status :: 4 - Beta",
        "Topic :: Communications",
    ],
    python_requires=">=3.10",
    install_requires=[
        "meshtastic>=2.7.4",
        "Pillow==12.1.1",
        "matrix-nio==0.25.2",
        "matplotlib==3.10.8",
        "requests==2.32.5",
        "markdown==3.10.2",
        "bleach==6.3.0",
        "haversine==2.9.0",
        "schedule==1.2.2",
        "platformdirs==4.9.2",
        "py-staticmaps>=0.4.0",
        "psutil>=5.8.0",
        "rich==14.3.2",
        "setuptools>=80.9.0",  # Required for console script entry points and Windows compatibility
    ],
    extras_require={
        "dev": [
            "pyright==1.1.408",
            "mypy==1.19.1",
            "types-PyYAML==6.0.12.20250915",
            "types-Markdown==3.10.0.20251106",
        ],
        "e2e": [
            "matrix-nio[e2e]==0.25.2",
            "python-olm",
        ],
    },
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    include_package_data=True,
    package_data={"mmrelay.tools": ["sample_config.yaml", "mmrelay.service"]},
    entry_points={"console_scripts": ["mmrelay = mmrelay.cli:main"]},
)
