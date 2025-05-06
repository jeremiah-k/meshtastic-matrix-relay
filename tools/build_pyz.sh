#!/bin/bash
# Build a PYZ file for mmrelay with native extensions included
# This script is designed to be run on the target architecture (e.g., ARMv7)

set -e

# Get version from setup.cfg
grep "version =" setup.cfg | sed 's/.*= //' >/tmp/version.txt || true
VERSION=$(cat /tmp/version.txt)
ARCH=$(uname -m)
OUT="mmrelay-${VERSION}-${ARCH}.pyz"

echo "Building ${OUT} for architecture ${ARCH}..."

# Clean up previous builds
rm -rf build wheelhouse dist
mkdir -p wheelhouse dist

# Install build dependencies
pip install --upgrade pip setuptools wheel build shiv

# Download all required wheels, including compiled ones
echo "Downloading dependencies..."
pip download . -r requirements.txt --only-binary :all: -d wheelhouse || true

# Build mmrelay and any missing binary wheels locally
echo "Building wheels..."
pip wheel . -w wheelhouse

# Build the PYZ file with the correct entry point and include site-packages
echo "Building PYZ file..."
python -m shiv \
	--compressed \
	--compile-pyc \
	--site-packages wheelhouse \
	--reproducible \
	--entry-point mmrelay.cli:main \
	--output-file "dist/${OUT}" \
	.

# Verify the build contains native modules
echo "Verifying PYZ contents..."
python -m zipfile -l "dist/${OUT}" >/tmp/pyz_contents.txt
grep -i "\.so" /tmp/pyz_contents.txt || echo "Warning: No .so files found in PYZ"

# Make the PYZ file executable
chmod +x "dist/${OUT}"

# Test the PYZ file
echo "Testing PYZ file..."
"dist/${OUT}" --version || echo "PYZ test failed"

echo "✅ Built ${OUT}"
CURRENT_DIR=$(pwd)
echo "The PYZ file is located at: ${CURRENT_DIR}/dist/${OUT}"
echo "SHA256 hash:"
sha256sum "dist/${OUT}"
