# ./mmrelay/__main__.py:
"""
Entry point for running the package via `python -m mmrelay`.
Invokes the command-line entry point function.
"""
import sys

# Import the main entry_point function from the cli module
try:
    # Point to the entry_point in cli.py
    from mmrelay.cli import entry_point
except ImportError as e:
     print(f"Error importing entry_point function from mmrelay.cli: {e}", file=sys.stderr)
     print("Please ensure all dependencies are installed and the package structure is correct.", file=sys.stderr)
     sys.exit(1)
except Exception as e:
     # Catch potential issues during import itself
     print(f"Unexpected error during import: {e}", file=sys.stderr)
     sys.exit(1)

if __name__ == "__main__":
    # Call the single entry point function which handles args, setup, and running
    entry_point()
