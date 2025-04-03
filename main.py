#!/usr/bin/env python3
"""
Backwards-compatible main.py that imports and runs the mmrelay package main function.
This maintains compatibility with users running `python main.py` from the root directory.
"""

import asyncio
from mmrelay.main import main

if __name__ == "__main__":
    asyncio.run(main())