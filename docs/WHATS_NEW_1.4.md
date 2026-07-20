# What's New in 1.4.0

MMRelay 1.4.0 raises the minimum supported Python version to 3.11.

## Why the Python floor changed

Matplotlib 3.11 requires Python 3.11 or newer. MMRelay uses Matplotlib for
telemetry graphs, so retaining Python 3.10 in the package metadata would
advertise an environment that cannot install MMRelay's required dependencies.
The map plugin is unaffected; it uses py-staticmaps and Pillow rather than
Matplotlib.

Python 3.10 is also in security-fix-only maintenance and reaches end of life in
October 2026. Moving the floor now keeps MMRelay aligned with its actively
maintained dependency stack and removes Python 3.10-only compatibility code.

## Upgrade guidance

Before installing MMRelay 1.4.0 or newer, upgrade the runtime to Python 3.11 or
newer and recreate the virtual environment or pipx installation so compiled
packages are installed for the new interpreter.

Python 3.10 systems can remain on the final MMRelay 1.3.x release. Operators who
need a custom dependency set may continue to install and maintain an older
source checkout, but that environment is outside the supported 1.4 release
matrix.

## Maintainer notes

- Package metadata, runtime checks, mypy, Windows guidance, and CI all use the
  same Python 3.11 minimum.
- Matplotlib is updated to 3.11.1 after the Python floor change.
- The Python 3.10 fallback parser in the source-checkout version helper is
  removed in favor of the Python 3.11 standard-library `tomllib` module.
