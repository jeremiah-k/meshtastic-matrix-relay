# Build stage
FROM python:3.11-slim-bookworm AS builder

# Install build dependencies with pinned versions
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential=12.9 \
    git=1:2.39.5-0+deb12u2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python build tools with pinned versions
RUN pip install --no-cache-dir --upgrade pip==25.1.1 setuptools==80.9.0 wheel==0.45.1

# Copy source files
COPY requirements.txt requirements-e2e.txt setup.py ./
COPY README.md ./
COPY src/ ./src/

# Install dependencies and application package with E2EE support (consolidated)
RUN python -m pip install --no-cache-dir --timeout=300 --retries=3 -r requirements.txt \
    && python -m pip install --no-cache-dir --timeout=300 --retries=3 -r requirements-e2e.txt \
    && python -m pip install --no-cache-dir --no-deps .

# Runtime stage
FROM python:3.11-slim-bookworm

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

# Copy installed packages from builder stage
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages

# Copy scripts to the correct location
COPY --from=builder /usr/local/bin/mmrelay /usr/local/bin/mmrelay

# Create app directory and set ownership
RUN mkdir -p /app && chown -R mmrelay:mmrelay /app

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
ENV MMRELAY_CREDENTIALS_PATH=/app/data/credentials.json

# Switch to non-root user
USER mmrelay

# Health check - ready file when configured, otherwise process detection
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD if [ -n "$MMRELAY_READY_FILE" ]; then test -f "$MMRELAY_READY_FILE"; else pgrep -f mmrelay >/dev/null 2>&1; fi

# Default command - uses config.yaml from volume mount
CMD ["mmrelay", "--config", "/app/config.yaml", "--base-dir", "/app", "--logfile", "/app/data/logs/mmrelay.log"]
