#!/usr/bin/env python3
"""
Command-line interface for meshtastic-matrix-relay.
"""

import asyncio
import os
import sys
from pathlib import Path

import platformdirs

from mmrelay.main import main


def run_cli():
    """
    Main CLI entry point for mmrelay.
    Sets up configuration directories and runs the main function.
    """
    # Create config directory if it doesn't exist
    config_dir = platformdirs.user_config_dir("mmrelay")
    os.makedirs(config_dir, exist_ok=True)
    
    # Check if config.yaml exists in the config directory
    config_path = os.path.join(config_dir, "config.yaml")
    if not os.path.exists(config_path):
        # Copy the default config.yaml to the config directory
        default_config = os.path.join(os.path.dirname(__file__), "config.yaml")
        if os.path.exists(default_config):
            import shutil
            shutil.copy(default_config, config_path)
            print(f"Created default configuration at {config_path}")
        else:
            print(f"Warning: Default configuration not found. Please create {config_path} manually.")
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutting down...")
        sys.exit(0)


if __name__ == "__main__":
    run_cli()
