# ./mmrelay/__main__.py:
"""
Entry point for running the package via `python -m mmrelay`.
"""

import asyncio
import sys # Import sys for exception handling if needed

# Import the main function from your main module
try:
    from mmrelay.main import main
except ImportError as e:
     # This might happen if dependencies aren't installed correctly
     # or if there's a circular import during startup.
     print(f"Error importing main function: {e}", file=sys.stderr)
     print("Please ensure all dependencies are installed and the package structure is correct.", file=sys.stderr)
     sys.exit(1)

if __name__ == "__main__":
    try:
        # asyncio.run handles the event loop setup and shutdown
        asyncio.run(main())
    except KeyboardInterrupt:
        # Gracefully handle Ctrl+C if needed, although main() likely handles shutdown.
        print("\nShutdown requested via KeyboardInterrupt.", file=sys.stderr)
    except Exception as e:
        # Catch other potential exceptions during startup or runtime
        print(f"\nAn unexpected error occurred: {e}", file=sys.stderr)
        # Optionally add more detailed logging here
        sys.exit(1) # Exit with error status