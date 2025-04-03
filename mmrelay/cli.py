import argparse
import configparser
import sys
import os

def get_version():
    config = configparser.ConfigParser()
    config.read(os.path.join(os.path.dirname(__file__), "..", "setup.cfg"))
    return config["metadata"]["version"]

def parse_args():
    parser = argparse.ArgumentParser(
        description="Meshtastic Matrix Relay",
        add_help=False
    )

    parser.add_argument("-c", "--config", metavar="PATH", help="Path to config.yaml")
    parser.add_argument("-v", "--version", action="store_true", help="Show version and exit")
    parser.add_argument("-h", "--help", action="help", help="Show this help message and exit")

    args = parser.parse_args()

    if args.version:
        print(f"meshtastic-matrix-relay {get_version()}")
        sys.exit(0)

    return args
