"""
Entry point for running the package as a module.
"""

import asyncio
from mmrelay.main import main

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
