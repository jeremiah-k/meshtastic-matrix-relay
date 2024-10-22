import yaml

try:
    with open("config.yaml", "r") as f:
        relay_config = yaml.safe_load(f)
except FileNotFoundError:
    raise FileNotFoundError("Configuration file 'config.yaml' not found.")
except yaml.YAMLError as e:
    raise ValueError(f"Error parsing 'config.yaml': {e}")
