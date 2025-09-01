"""Tools and resources for MMRelay."""

import importlib.resources
import pathlib


def get_sample_config_path():
    """Get the path to the sample config file."""
    try:
        # For Python 3.9+
        return str(
            importlib.resources.files("mmrelay.tools").joinpath("sample_config.yaml")
        )
    except AttributeError:
        # Fallback for older Python versions
        return str(pathlib.Path(__file__).parent / "sample_config.yaml")


def get_service_template_path():
    """Get the path to the service template file."""
    try:
        # For Python 3.9+
        return str(
            importlib.resources.files("mmrelay.tools").joinpath("mmrelay.service")
        )
    except AttributeError:
        # Fallback for older Python versions
        return str(pathlib.Path(__file__).parent / "mmrelay.service")
