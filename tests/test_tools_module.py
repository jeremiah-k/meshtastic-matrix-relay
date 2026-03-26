from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mmrelay.constants.app import SERVICE_FILENAME
from mmrelay.tools import get_sample_config_path, get_service_template_path


@pytest.mark.parametrize(
    "func,expected_filename",
    [
        (get_sample_config_path, "sample_config.yaml"),
        (get_service_template_path, SERVICE_FILENAME),
    ],
)
def test_path_functions_use_package_resource_files(func, expected_filename) -> None:
    package_root = Path("/fake/tools")
    traversable = MagicMock()
    traversable.joinpath.return_value = package_root / expected_filename

    with patch(
        "mmrelay.tools.importlib.resources.files", return_value=traversable
    ) as mock_files:
        result = func()

    assert result == str(package_root / expected_filename)
    mock_files.assert_called_once_with("mmrelay.tools")
    traversable.joinpath.assert_called_once_with(expected_filename)
