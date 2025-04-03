# Entry point wrapper for shiv compatibility.
# Dynamically load main.py as a module so it's included in the .pyz
import runpy
import os

def main():
    path = os.path.join(os.path.dirname(__file__), "..", "main.py")
    runpy.run_path(path, run_name="__main__")
