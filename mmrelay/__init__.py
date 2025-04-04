import configparser
import pathlib

config = configparser.ConfigParser()
config.read(pathlib.Path(__file__).parent.parent / "setup.cfg")

__version__ = config["metadata"]["version"]
__author__ = config["metadata"]["author"]
