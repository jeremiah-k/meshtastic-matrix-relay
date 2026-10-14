"""Check native installer YAML output and configuration-page selection on Windows."""

import os
import re
import shutil
import subprocess  # nosec B404 - invoke only the pinned compiler and probe
import tempfile
from pathlib import Path

import yaml


def find_inno_compiler() -> Path:
    """Find the pinned action compiler, or a standard local Inno Setup install."""
    explicit = os.environ.get("ISCC_PATH")
    if explicit:
        compiler = Path(explicit)
        if not compiler.is_file():
            raise RuntimeError(f"ISCC_PATH does not identify a file: {compiler}")
        return compiler

    workspace = os.environ.get("GITHUB_WORKSPACE")
    if workspace:
        action_root = (
            Path(workspace).resolve().parents[1] / "_actions" / "sharktide" / "iscc-cli"
        )
        compilers = list(action_root.glob("*/src/ISCC.exe"))
        if len(compilers) == 1:
            return compilers[0]

    on_path = shutil.which("ISCC.exe")
    if on_path:
        return Path(on_path)
    for root in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        if root:
            for version in ("Inno Setup 6", "Inno Setup 7"):
                compiler = Path(root) / version / "ISCC.exe"
                if compiler.is_file():
                    return compiler
    raise RuntimeError(
        "Cannot locate Inno Setup ISCC.exe; set ISCC_PATH or install Inno Setup"
    )


def verify_installer_yaml() -> None:
    """Execute production YAML helpers and page selection in a native installer."""
    compiler = find_inno_compiler()
    source = Path("scripts/mmrelay.iss").read_text(encoding="utf-8")
    functions = []
    for name in ("YamlString", "YamlSecret", "SaveUtf8Text", "ShouldSkipPage"):
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
            "var\n  OverwriteConfig, OptionsPage: TInputOptionWizardPage;\n"
            "  MeshtasticPage, MatrixPage, MatrixMeshtasticPage: TInputQueryWizardPage;\n"
            + "\n".join(functions)
            + "\nprocedure InitializeWizard;\nvar\n  Mode, PageID: Integer;\nbegin\n"
            + "  OverwriteConfig := CreateInputOptionPage(wpWelcome, 'Probe', 'Probe', '', False, False);\n"
            + "  OverwriteConfig.Add('Generate configuration');\n"
            + "  MeshtasticPage := CreateInputQueryPage(OverwriteConfig.ID, 'Probe', 'Probe', '');\n"
            + "  MatrixPage := CreateInputQueryPage(MeshtasticPage.ID, 'Probe', 'Probe', '');\n"
            + "  MatrixMeshtasticPage := CreateInputQueryPage(MatrixPage.ID, 'Probe', 'Probe', '');\n"
            + "  OptionsPage := CreateInputOptionPage(MatrixMeshtasticPage.ID, 'Probe', 'Probe', '', False, False);\n"
            + "  for Mode := 0 to 1 do\n  begin\n"
            + "    OverwriteConfig.Values[0] := Mode = 1;\n"
            + "    for PageID := wpWelcome to wpFinished do\n"
            + "      if ShouldSkipPage(PageID) then\n"
            + "        RaiseException('Standard installer page must not be skipped');\n"
            + "    if ShouldSkipPage(OverwriteConfig.ID) then\n"
            + "      RaiseException('Configuration choice must remain available');\n"
            + "    if (ShouldSkipPage(MeshtasticPage.ID) <> (Mode = 0)) or\n"
            + "       (ShouldSkipPage(MatrixPage.ID) <> (Mode = 0)) or\n"
            + "       (ShouldSkipPage(MatrixMeshtasticPage.ID) <> (Mode = 0)) or\n"
            + "       (ShouldSkipPage(OptionsPage.ID) <> (Mode = 0)) then\n"
            + "      RaiseException('Configuration page selection is incorrect');\n"
            + "  end;\n"
            + f"  if not SaveUtf8Text({literal(str(document))}, "
            + f"'path: ' + YamlString({literal(values['path'])}) + #13#10 + "
            + f"'password: ' + YamlSecret({literal(values['password'])})) then\n"
            + "    RaiseException('Could not write YAML probe');\nend;\n",
            encoding="utf-8-sig",
        )
        subprocess.run(  # nosec B603 - pinned compiler and generated test script
            [str(compiler), str(script)], check=True, timeout=120
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
    print("Native installer YAML and page-selection checks passed")


if __name__ == "__main__":
    verify_installer_yaml()
