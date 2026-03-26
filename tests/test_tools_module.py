from pathlib import Path
from unittest.mock import MagicMock, patch

from mmrelay.tools import get_sample_config_path, get_service_template_path


def test_get_sample_config_path_uses_package_resource_files() -> None:
    package_root = Path("/fake/tools")
    traversable = MagicMock()
    traversable.joinpath.return_value = package_root / "sample_config.yaml"

    with patch(
        "mmrelay.tools.importlib.resources.files", return_value=traversable
    ) as mock_files:
        result = get_sample_config_path()

    assert result == str(package_root / "sample_config.yaml")
    mock_files.assert_called_once_with("mmrelay.tools")
    traversable.joinpath.assert_called_once_with("sample_config.yaml")


def test_get_service_template_path_uses_package_resource_files() -> None:
    package_root = Path("/fake/tools")
    traversable = MagicMock()
    traversable.joinpath.return_value = package_root / "mmrelay.service"

    with patch(
        "mmrelay.tools.importlib.resources.files", return_value=traversable
    ) as mock_files:
        result = get_service_template_path()

    assert result == str(package_root / "mmrelay.service")
    mock_files.assert_called_once_with("mmrelay.tools")
    traversable.joinpath.assert_called_once_with("mmrelay.service")
