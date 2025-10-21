"""Tools and resources for MMRelay."""

import importlib.resources


def get_sample_config_path():
    """Get the path to the sample config file."""
    return str(
        importlib.resources.files("mmrelay.tools").joinpath("sample_config.yaml")
    )


def get_service_template_path():
    """Get the path to the service template file."""
    return str(importlib.resources.files("mmrelay.tools").joinpath("mmrelay.service"))
