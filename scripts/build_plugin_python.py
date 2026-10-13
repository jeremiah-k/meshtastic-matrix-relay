"""Assemble an isolated Windows pip interpreter from the packaging interpreter.

No extra runtime download or system Python installation is required. Keeping
the executable, DLLs and stdlib from one interpreter guarantees the same ABI as
the frozen application. The ._pth file excludes registry and user-site paths:
https://docs.python.org/3/library/sys_path_init.html#pth-files
"""

import argparse
import importlib.metadata
import shutil
import subprocess  # nosec B404 - validate the runtime assembled from trusted CI Python
import sys
from pathlib import Path


def build_runtime(prefix: Path, output: Path, pip_source: Path) -> Path:
    """Copy only CPython's runtime and pip, never the application's site-packages."""
    # An existing destination could retain wheels or DLLs from a different ABI.
    output.mkdir(parents=True, exist_ok=False)
    version = f"{sys.version_info.major}{sys.version_info.minor}"
    for filename in ("python.exe", f"python{version}.dll", "python3.dll"):
        shutil.copy2(prefix / filename, output / filename)
    for dll in prefix.glob("vcruntime*.dll"):
        shutil.copy2(dll, output / dll.name)
    shutil.copy2(prefix / "LICENSE.txt", output / "LICENSE.txt")
    shutil.copytree(prefix / "DLLs", output / "DLLs")
    shutil.copytree(
        prefix / "Lib",
        output / "Lib",
        ignore=shutil.ignore_patterns(
            "site-packages", "__pycache__", "test", "idlelib", "tkinter", "turtledemo"
        ),
    )
    shutil.copytree(
        pip_source,
        output / "site-packages" / "pip",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    # Keep pip's license and metadata alongside its vendored dependency licenses.
    distribution = importlib.metadata.distribution("pip")
    for file in distribution.files or ():
        if file.parts[0].endswith(".dist-info"):
            destination = output / "site-packages" / file
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(distribution.locate_file(file)), destination)
    (output / f"python{version}._pth").write_text(
        ".\nLib\nDLLs\nsite-packages\nimport site\n", encoding="utf-8"
    )
    return output / "python.exe"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("The plugin runtime must be assembled on Windows")
    pip_source = Path(str(importlib.metadata.distribution("pip").locate_file("pip")))
    interpreter = build_runtime(
        Path(sys.base_prefix), args.output.resolve(), pip_source
    )
    # Fail the packaging step if relocation, DLL loading, SSL, or pip is broken.
    subprocess.run(  # nosec B603 - fixed probes using the assembled CI interpreter
        [
            str(interpreter),
            "-I",
            "-c",
            "import ssl, ctypes, pip; print(ssl.OPENSSL_VERSION)",
        ],
        check=True,
        timeout=60,
    )
    subprocess.run(  # nosec B603 - fixed probes using the assembled CI interpreter
        [str(interpreter), "-I", "-m", "pip", "--version"],
        check=True,
        timeout=60,
    )


if __name__ == "__main__":
    main()
