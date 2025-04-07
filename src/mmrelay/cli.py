#!/usr/bin/env python3
"""
Command-line interface for meshtastic-matrix-relay.
"""

import argparse
import asyncio
import os
import sys
import shutil
from pathlib import Path

from mmrelay import __version__
from mmrelay.main import main
from mmrelay.config import load_config, relay_config
from mmrelay.log_utils import setup_logging
from mmrelay.path_utils import get_config_path, get_log_path


def parse_args():
    """
    Parse command-line arguments.

    Returns:
        Parsed arguments
    """
    parser = argparse.ArgumentParser(description="Meshtastic Matrix Relay")
    parser.add_argument(
        "--config", "-c",
        help="Path to config file (default: auto-detected)"
    )
    parser.add_argument(
        "--logfile", "-l",
        help="Path to log file (default: auto-detected)"
    )
    parser.add_argument(
        "--version", "-v",
        action="version",
        version=f"meshtastic-matrix-relay {__version__}"
    )
    return parser.parse_args()


def run_cli():
    """
    Main CLI entry point for mmrelay.
    Sets up configuration directories and runs the main function.
    """
    # Parse command-line arguments
    args = parse_args()

    # Load configuration
    config_path = get_config_path(args.config)
    config_dir = os.path.dirname(config_path)

    # Check if config file exists, if not, create it
    if not os.path.exists(config_path):
        # Copy the default config.yaml to the config directory
        default_config = os.path.join(os.path.dirname(__file__), "config.yaml")
        if os.path.exists(default_config):
            os.makedirs(config_dir, exist_ok=True)
            shutil.copy(default_config, config_path)
            print(f"Created default configuration at {config_path}")
        else:
            print(f"Warning: Default configuration not found. Please create {config_path} manually.")

    # Load the configuration
    load_config(config_path)

    # Setup logging
    log_path = get_log_path(args.logfile)
    setup_logging(relay_config, log_path)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutting down...")
        sys.exit(0)


if __name__ == "__main__":
    run_cli()
