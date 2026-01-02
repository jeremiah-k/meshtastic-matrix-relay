"""
Alternative entry point for MMRelay that doesn't rely on setuptools console scripts.

This can be used as a fallback on Windows systems where the setuptools-generated
console script fails due to missing pkg_resources.

Usage:
    python -m mmrelay [args...]
"""

import sys

if __name__ == "__main__":
    try:
        from mmrelay.cli import main

        sys.exit(main())
    except ImportError as e:
        print(f"Error importing MMRelay CLI: {e}", file=sys.stderr)
        print("Please ensure MMRelay is properly installed.", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        sys.exit(130)
    except SystemExit:
        raise
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        sys.exit(1)
