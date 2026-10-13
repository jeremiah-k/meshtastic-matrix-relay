"""Check native installer YAML quoting and UTF-8 writing on Windows."""

import os
import re
import subprocess  # nosec B404 - invoke only the pinned compiler and probe
import tempfile
from pathlib import Path

import yaml


def verify_installer_yaml() -> None:
    """Compile and execute the production Pascal quoting/writing functions."""
    action_root = (
        Path(os.environ["GITHUB_WORKSPACE"]).resolve().parents[1]
        / "_actions"
        / "sharktide"
        / "iscc-cli"
    )
    compilers = list(action_root.glob("*/src/ISCC.exe"))
    if len(compilers) != 1:
        raise RuntimeError(
            f"Cannot identify the pinned Inno compiler under {action_root}"
        )
    source = Path("scripts/mmrelay.iss").read_text(encoding="utf-8")
    functions = []
    for name in ("YamlString", "YamlSecret", "SaveUtf8Text"):
        match = re.search(rf"(?ms)^function {name}\(.*?^end;", source)
        if match is None:
            raise RuntimeError(f"Installer function {name} is missing")
        functions.append(match.group())
    values = {"path": 'C:\\Users\\名字\\"relay"', "password": "p'ass\\word#台灣"}

    def literal(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    with tempfile.TemporaryDirectory(prefix="MMRelay YAML probe ") as temporary:
        directory = Path(temporary)
        document = directory / "probe.yaml"
        script = directory / "probe.iss"
        script.write_text(
            "[Setup]\nAppName=MMRelay YAML probe\nAppVersion=1\n"
            "DefaultDirName={tmp}\\MMRelay YAML probe\nCreateAppDir=no\n"
            "Uninstallable=no\nPrivilegesRequired=lowest\n"
            f"OutputDir={directory}\nOutputBaseFilename=probe\n[Code]\n"
            + "\n".join(functions)
            + "\nprocedure InitializeWizard;\nbegin\n"
            + f"  if not SaveUtf8Text({literal(str(document))}, "
            + f"'path: ' + YamlString({literal(values['path'])}) + #13#10 + "
            + f"'password: ' + YamlSecret({literal(values['password'])})) then\n"
            + "    RaiseException('Could not write YAML probe');\nend;\n",
            encoding="utf-8-sig",
        )
        subprocess.run(  # nosec B603 - pinned compiler and generated test script
            [str(compilers[0]), str(script)], check=True, timeout=120
        )
        subprocess.run(  # nosec B603 - generated test artifact, no shell
            [
                str(directory / "probe.exe"),
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
            ],
            check=True,
            timeout=120,
        )
        if yaml.safe_load(document.read_text(encoding="utf-8-sig")) != values:
            raise RuntimeError(
                "Installer YAML changed quoted, backslash, or Unicode values"
            )
    print("Native installer YAML checks passed")


if __name__ == "__main__":
    verify_installer_yaml()
