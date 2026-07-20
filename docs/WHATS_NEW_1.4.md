# What's New in 1.4.0

MMRelay 1.4.0 improves mesh observability and BLE recovery while raising the
minimum supported Python version to 3.11.

## Mesh observability

The built-in node listing is more useful and less noisy:

- `!nodes` shows the 10 most recently heard nodes by default.
- `!nodes <count>` selects a custom limit.
- `!nodes full` or `!nodes all` returns the complete NodeDB.
- Entries include canonical node IDs for follow-on diagnostics.

A new Matrix-only traceroute command uses mtjk's structured traceroute API:

```text
!traceroute <node-id-or-name> [--hops 1-7]
!trace <node-id-or-name> [--hops 1-7]
```

Results include outbound and reverse paths plus per-link SNR when firmware
provides complete measurements. Requests are serialized so multiple Matrix
users cannot accidentally create competing traceroutes over the same radio.

Both plugins are enabled in the sample configuration. Existing installations
can opt in by adding or enabling the corresponding `nodes` and `traceroute`
plugin sections.

## BLE recovery

MMRelay now counts only abandoned BLE constructor workers that are still alive.
If a timed-out constructor later exits, its orphan capacity is released and the
executor can recover automatically instead of remaining permanently degraded
until the process is restarted.

The release requires mtjk 2.7.11.post2, which also classifies a closed BlueZ
D-Bus stream as a typed BLE transport error. This gives MMRelay the structured
failure information it needs for targeted retry and diagnostics.

## Python 3.11 minimum

Matplotlib 3.11 requires Python 3.11 or newer. MMRelay uses Matplotlib for
telemetry graphs, so retaining Python 3.10 in the package metadata would
advertise an environment that cannot install MMRelay's required dependencies.
The map plugin is unaffected; it uses py-staticmaps and Pillow rather than
Matplotlib.

Python 3.10 is also in security-fix-only maintenance and reaches end of life in
October 2026. Moving the floor now keeps MMRelay aligned with its actively
maintained dependency stack and removes Python 3.10-only compatibility code.

### Upgrade guidance

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
- The exact mtjk dependency is updated to 2.7.11.post2.
