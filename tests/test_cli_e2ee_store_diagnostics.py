"""Store-open failures must not be diagnosed as missing crypto packages."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize("failure", ["fcntl", "other"])
def test_validation_reports_installed_store_failure(monkeypatch, capsys, failure):
    """A failed store probe receives the same distinction as environment diagnostics."""
    from mmrelay import cli
    from mmrelay.matrix import compat

    caps = SimpleNamespace(store_failure=failure)
    diagnosis = Mock(return_value="Installed crypto store cannot open")
    monkeypatch.setattr(cli, "_e2ee_dependencies_available", lambda: False)
    monkeypatch.setattr(compat, "get_matrix_capabilities", lambda: caps)
    monkeypatch.setattr(compat, "format_e2ee_unavailable_message", diagnosis)
    monkeypatch.setattr(
        compat, "format_e2ee_install_command", lambda _: "Check store permissions"
    )
    assert not cli._validate_e2ee_dependencies()
    output = capsys.readouterr().out
    assert "Installed crypto store cannot open" in output
    assert "dependencies not installed" not in output
    diagnosis.assert_called_once_with(caps)
