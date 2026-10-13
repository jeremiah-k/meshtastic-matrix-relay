"""Private Windows runtime assembly excludes unrelated site packages."""

import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts import build_plugin_python


def test_assemble_relocatable_runtime_and_pip(tmp_path, monkeypatch):
    prefix = tmp_path / "python"
    prefix.mkdir()
    version = f"{sys.version_info.major}{sys.version_info.minor}"
    for name in (
        "python.exe",
        f"python{version}.dll",
        "python3.dll",
        "vcruntime140.dll",
        "LICENSE.txt",
    ):
        (prefix / name).write_bytes(b"runtime")
    (prefix / "DLLs").mkdir()
    (prefix / "DLLs" / "_ssl.pyd").write_bytes(b"native")
    library = prefix / "Lib"
    library.mkdir()
    (library / "ssl.py").write_text("# stdlib", encoding="utf-8")
    for ignored in ("site-packages", "__pycache__", "test"):
        (library / ignored).mkdir()
        (library / ignored / "unwanted.py").touch()
    pip_source = library / "site-packages" / "pip"
    pip_source.mkdir()
    (pip_source / "__init__.py").touch()
    metadata = Path("pip-1.0.dist-info") / "METADATA"
    (prefix / metadata.parent).mkdir()
    (prefix / metadata).write_text("Name: pip\n", encoding="utf-8")
    distribution = Mock(files=[metadata])
    distribution.locate_file.side_effect = lambda file: prefix / file
    monkeypatch.setattr(
        build_plugin_python.importlib.metadata, "distribution", lambda _: distribution
    )

    output = tmp_path / "runtime"
    assert build_plugin_python.build_runtime(prefix, output, pip_source) == (
        output / "python.exe"
    )
    assert (output / "DLLs" / "_ssl.pyd").read_bytes() == b"native"
    assert (output / "Lib" / "ssl.py").is_file()
    assert (output / "site-packages" / "pip" / "__init__.py").is_file()
    assert (output / "site-packages" / metadata).is_file()
    assert (output / f"python{version}._pth").read_text(encoding="utf-8") == (
        ".\nLib\nDLLs\nsite-packages\nimport site\n"
    )
    assert not (output / "Lib" / "site-packages").exists()
    assert not (output / "Lib" / "test").exists()
    with pytest.raises(FileExistsError):
        build_plugin_python.build_runtime(prefix, output, pip_source)
