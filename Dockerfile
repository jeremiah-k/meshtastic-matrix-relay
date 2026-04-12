# Build stage
FROM python:3.11-slim-bookworm@sha256:9c6f90801e6b68e772b7c0ca74260cbf7af9f320acec894e26fccdaccfbe3b47 AS builder

# Install build dependencies with pinned versions
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential=12.9 \
    git=1:2.39.5-0+deb12u2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python build tools with pinned versions
RUN pip install --no-cache-dir --upgrade pip==25.1.1 setuptools==80.9.0 wheel==0.45.1

# Copy source files
COPY pyproject.toml MANIFEST.in setup.py README.md LICENSE ./
COPY src/ ./src/

# Install dependencies and application package with E2EE support
# hadolint ignore=DL3013
RUN python -m pip install --no-cache-dir --timeout=300 --retries=3 \
    --prefix=/install ".[e2e]"

# Runtime stage
FROM python:3.11-slim-bookworm@sha256:9c6f90801e6b68e772b7c0ca74260cbf7af9f320acec894e26fccdaccfbe3b47

# Create non-root user for security
RUN groupadd --gid 1000 mmrelay && \
    useradd --uid 1000 --gid mmrelay --shell /bin/bash --create-home mmrelay

# Install only runtime dependencies with pinned versions
RUN apt-get update && apt-get install -y --no-install-recommends \
    git=1:2.39.5-0+deb12u2 \
    procps=2:4.0.2-3 \
    && (apt-get install -y --no-install-recommends bluez=5.66-1+deb12u2 || echo "Warning: bluez package not found for this architecture. BLE support will be unavailable.") \
    && rm -rf /var/lib/apt/lists/*

# Note: User will be set via docker-compose user directive

# Set working directory
WORKDIR /app

# Copy installed Python packages and scripts from builder stage.
# Using a fixed prefix keeps this independent of pythonX.Y site-packages paths.
COPY --from=builder /install /usr/local

# Create app and data directories and set ownership
RUN mkdir -p /app /data && chown -R mmrelay:mmrelay /app /data

# Add container metadata labels
ARG BUILD_DATE
ARG VCS_REF
ARG VERSION
LABEL org.opencontainers.image.title="Meshtastic Matrix Relay" \
      org.opencontainers.image.description="A bridge between Meshtastic mesh networks and Matrix chat rooms, enabling seamless communication across different platforms with support for encryption, plugins, and real-time message relay." \
      org.opencontainers.image.url="https://github.com/jeremiah-k/meshtastic-matrix-relay" \
      org.opencontainers.image.source="https://github.com/jeremiah-k/meshtastic-matrix-relay" \
      org.opencontainers.image.documentation="https://github.com/jeremiah-k/meshtastic-matrix-relay/blob/main/README.md" \
      org.opencontainers.image.licenses="GPL-3.0-or-later" \
      org.opencontainers.image.version="${VERSION:-dev}" \
      org.opencontainers.image.revision="${VCS_REF:-unknown}" \
      org.opencontainers.image.created="${BUILD_DATE:-unknown}"

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV MPLCONFIGDIR=/tmp/matplotlib
ENV PATH=/usr/local/bin:/usr/bin:/bin
ENV MMRELAY_HOME=/data
ENV MMRELAY_READY_FILE=/tmp/mmrelay-ready

# Switch to non-root user
USER mmrelay

# Health check - verifies ready-file freshness.
# The ready file is created when the app is running and healthy.
# Users who don't want ready-file health checks should omit HEALTHCHECK entirely.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD find "$MMRELAY_READY_FILE" -mmin -2 | grep -q .

# Default command
# MMRELAY_HOME is set via ENV, so runtime paths resolve under /data by default.
# mmrelay will automatically search for config.yaml in /data then in the current directory (/app).
CMD ["mmrelay"]
