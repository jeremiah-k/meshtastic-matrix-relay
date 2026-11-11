"""Serial path validation keeps stable aliases and rejects trailing input."""

from unittest.mock import patch

import pytest

from mmrelay.cli import _is_valid_serial_port


@pytest.mark.parametrize(
    "system,port",
    [
        ("Linux", "/dev/serial/by-id/usb-Lab_Radio-if00"),
        ("Linux", "/dev/serial/by-path/pci-0000:00:14.0-usb-0:2:1.0"),
        ("Linux", "/dev/ttyACM0"),
        ("Linux", "/dev/radio-container"),
        ("Windows", "COM12"),
    ],
)
def test_serial_validation_accepts_device_paths(system, port):
    with patch("platform.system", return_value=system):
        assert _is_valid_serial_port(port)


@pytest.mark.parametrize(
    "system,port",
    [
        ("Linux", "/dev/serial/by-id/"),
        ("Linux", "/dev/serial/by-id/nested/radio"),
        ("Linux", "/dev/serial/by-id/radio\n"),
        ("Linux", "/dev/ttyACM0\n"),
        ("Linux", "/dev/radio\x00port"),
        ("Linux", "/dev/foo/nested"),
        ("Windows", "COM12\n"),
    ],
)
def test_serial_validation_rejects_incomplete_or_trailing_input(system, port):
    with patch("platform.system", return_value=system):
        assert not _is_valid_serial_port(port)
