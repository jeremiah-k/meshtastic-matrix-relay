import os
import sys
import yaml
from yaml.loader import SafeLoader
from mmrelay.cli import parse_args

def get_app_path():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

def find_config_path(cli_arg=None):
    if cli_arg and os.path.isfile(cli_arg):
        return cli_arg
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    for path in [os.path.join(root, "config.yaml"), os.path.join(__file__, "config.yaml")]:
        if os.path.isfile(path):
            return path
    return None

def load_config():
    args = parse_args()
    path = find_config_path(args.config)
    if not path:
        print("Configuration file not found.")
        return {}
    with open(path, "r") as f:
        return yaml.load(f, Loader=SafeLoader)

relay_config = load_config()
