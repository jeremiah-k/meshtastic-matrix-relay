"""Tools and resources for MMRelay."""

import importlib.resources


def get_sample_config_path() -> str:
    """
    Provide the filesystem path to the package's sample configuration file.

    Returns:
        path (str): Path to `sample_config.yaml` located in the `mmrelay.tools` package.
    """
    return str(
        importlib.resources.files("mmrelay.tools").joinpath("sample_config.yaml")
    )


def get_service_template_path() -> str:
    """
    Get the filesystem path to the mmrelay.service template bundled with the package.

    Returns:
        str: Filesystem path to the `mmrelay.service` template within the `mmrelay.tools` package.
    """
    return str(importlib.resources.files("mmrelay.tools").joinpath("mmrelay.service"))
