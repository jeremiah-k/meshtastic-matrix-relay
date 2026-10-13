"""Select a real interpreter for plugin installs, including frozen Windows builds."""

import sys
import sysconfig
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def dependency_directory(plugin_root: Path) -> Path:
    """Keep native plugin wheels separate across frozen Python ABI upgrades."""
    target = plugin_root / "deps"
    if is_frozen():
        target /= f"{sys.implementation.cache_tag}-{sysconfig.get_platform()}"
    return target


def _runtime_directory() -> Path:
    bundle = getattr(sys, "_MEIPASS", None)
    if not bundle:
        raise OSError("Frozen plugin Python runtime is missing; reinstall MMRelay")
    runtime = Path(bundle) / "plugin-python"
    if not (runtime / "python.exe").is_file():
        raise OSError("Frozen plugin Python runtime is missing; reinstall MMRelay")
    return runtime


def standard_library_paths() -> list[str]:
    """Supply stdlib modules that PyInstaller's static import analysis omits."""
    if not is_frozen():
        return []
    runtime = _runtime_directory()
    return [str(runtime / "Lib"), str(runtime / "DLLs")]


def pip_install_command(target: str | None = None) -> list[str]:
    """Build an install command without ever treating mmrelay.exe as Python."""
    if is_frozen():
        if target is None:
            raise OSError("Frozen plugin installs require a writable plugin directory")
        cmd = [str(_runtime_directory() / "python.exe"), "-I", "-m", "pip"]
        cmd += ["--isolated", "install", "--only-binary=:all:", "--no-compile"]
    else:
        cmd = [sys.executable, "-m", "pip", "install"]
    cmd += ["--disable-pip-version-check", "--no-input"]
    if target is not None:
        cmd += ["--target", target]
    return cmd
