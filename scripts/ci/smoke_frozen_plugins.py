"""Exercise offline pure/native wheel installs in a real frozen Windows process."""

import argparse
import importlib.util
import os
import sys
import sysconfig
import zipfile
from pathlib import Path


def prepare(home: Path) -> None:
    """Create local test wheels without fetching packages or executing a build."""
    wheels = home / "wheels"
    wheels.mkdir(parents=True)
    abi = f"cp{sys.version_info.major}{sys.version_info.minor}"
    platform = sysconfig.get_platform().replace("-", "_")
    spec = importlib.util.find_spec("_elementtree")
    if spec is None or spec.origin is None or not spec.origin.endswith(".pyd"):
        raise RuntimeError("The native wheel fixture requires Windows _elementtree.pyd")
    native = Path(spec.origin)
    for name, tag, files, dependencies in (
        (
            "frozen_probe_native",
            f"{abi}-{abi}-{platform}",
            {
                "frozen_probe_native/__init__.py": b"",
                "frozen_probe_native/_elementtree.pyd": native.read_bytes(),
            },
            "",
        ),
        (
            "frozen_probe_pure",
            "py3-none-any",
            {"frozen_probe_pure.py": b"VALUE = 42\n"},
            "Requires-Dist: frozen-probe-native==1.0\n",
        ),
    ):
        metadata = f"{name}-1.0.dist-info"
        files[f"{metadata}/METADATA"] = (
            f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n{dependencies}"
        ).encode()
        files[f"{metadata}/WHEEL"] = (
            f"Wheel-Version: 1.0\nRoot-Is-Purelib: {'true' if name.endswith('pure') else 'false'}\nTag: {tag}\n"
        ).encode()
        files[f"{metadata}/RECORD"] = (
            "".join(f"{path},,\n" for path in files) + f"{metadata}/RECORD,,\n"
        ).encode()
        with zipfile.ZipFile(wheels / f"{name}-1.0-{tag}.whl", "w") as wheel:
            for path, content in files.items():
                wheel.writestr(path, content)
    plugin = home / "probe"
    plugin.mkdir()
    (plugin / "requirements.txt").write_text(
        f'--no-index\n--find-links "{wheels.as_posix()}"\nfrozen-probe-pure==1.0\n',
        encoding="utf-8",
    )
    (plugin / "probe.py").write_text(
        "from frozen_probe_pure import VALUE\n"
        "from frozen_probe_native._elementtree import Element\n"
        "import wsgiref.validate\n"
        "class Plugin:\n    value = VALUE\n    native = Element('probe').tag\n",
        encoding="utf-8",
    )


def verify(home: Path, verify_only: bool) -> None:
    if not getattr(sys, "frozen", False):
        raise RuntimeError("The smoke check must run as a frozen executable")
    os.environ["MMRELAY_HOME"] = str(home)
    # These must not redirect the isolated helper to the runner's Python.
    os.environ["PYTHONHOME"] = str(home / "not-python")
    os.environ["PYTHONPATH"] = str(home / "not-python")
    os.environ["PIP_TARGET"] = str(home / "not-the-target")
    os.environ["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")

    # Import the production entrypoint so the fixture uses the app's import graph.
    import mmrelay.main  # noqa: F401
    from mmrelay import plugin_loader, plugin_runtime

    plugin_loader.config = {"security": {"allow_untrusted_dependencies": True}}
    deps = Path(plugin_loader._PLUGIN_DEPS_DIR or "")
    if not deps.is_relative_to(home / "plugins" / "deps"):
        raise RuntimeError(f"Unexpected persistent dependency target: {deps}")
    plugin = home / "probe"
    if not verify_only:
        if not plugin_loader._install_requirements_for_repo(
            str(plugin), "probe", plugin_type=plugin_loader.PLUGIN_TYPE_COMMUNITY
        ):
            raise RuntimeError("Frozen wheel install failed")
    plugins = plugin_loader.load_plugins_from_directory(str(plugin))
    if len(plugins) != 1 or plugins[0].value != 42 or plugins[0].native != "probe":
        raise RuntimeError("Installed pure/native plugin imports failed")
    interpreter = plugin_runtime.pip_install_command(str(deps))[0]
    if interpreter == sys.executable:
        raise RuntimeError("The frozen application was selected as the interpreter")
    print(f"Frozen plugin check passed (verify_only={verify_only}, target={deps})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    if args.prepare:
        prepare(args.home.resolve())
    else:
        verify(args.home.resolve(), args.verify_only)


if __name__ == "__main__":
    main()
