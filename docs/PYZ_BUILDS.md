# MMRelay PYZ Builds

This document explains how to use and build the standalone `.pyz` executables for MMRelay.

## What is a PYZ file?

A `.pyz` file is a self-contained Python application that includes all dependencies. It's similar to a `.zip` file but is executable. This makes it easy to distribute and run MMRelay without installing Python packages.

## Using the Pre-built PYZ

For ARMv7 devices (like Raspberry Pi), we provide pre-built `.pyz` executables:

1. Download the latest `mmrelay-x.x.x-armv7.pyz` from [Releases](https://github.com/geoffwhittington/meshtastic-matrix-relay/releases)
2. Make it executable: `chmod +x mmrelay-x.x.x-armv7.pyz`
3. Run it: `./mmrelay-x.x.x-armv7.pyz`

The PYZ file supports all the same command-line options as the regular installation:

```bash
./mmrelay-x.x.x-armv7.pyz --generate-config
./mmrelay-x.x.x-armv7.pyz --config ~/.mmrelay/config.yaml
```

## Building Your Own PYZ

If you need to build a PYZ file for your specific architecture, we provide a build script:

```bash
# Clone the repository
git clone https://github.com/geoffwhittington/meshtastic-matrix-relay.git
cd meshtastic-matrix-relay

# Make the build script executable
chmod +x tools/build_pyz.sh

# Run the build script
./tools/build_pyz.sh
```

The built PYZ file will be in the `dist/` directory.

## Troubleshooting

### Missing Native Extensions

If you see an error like `ModuleNotFoundError: No module named 'rpds.rpds'`, it means the PYZ file is missing native extensions. This can happen if:

1. You're using a PYZ built for a different architecture
2. The PYZ was built without including native extensions

Solution: Use the pre-built PYZ for your architecture or build your own using the provided script.

### Permission Denied

If you see `Permission denied` when trying to run the PYZ, make sure it's executable:

```bash
chmod +x mmrelay-x.x.x-armv7.pyz
```

### Other Issues

If you encounter other issues with the PYZ file, please report them on our [GitHub Issues](https://github.com/geoffwhittington/meshtastic-matrix-relay/issues) page.

## How It Works

The PYZ file is built using [shiv](https://github.com/linkedin/shiv), which creates a self-contained Python application. The build process:

1. Downloads all dependencies, including native extensions
2. Builds wheels for any missing dependencies
3. Packages everything into a single executable file

This ensures that all dependencies, including native extensions like `rpds.rpds`, are included in the PYZ file.
