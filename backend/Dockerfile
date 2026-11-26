# =============================================================================
# PH Agent Hub — Backend Dockerfile
# =============================================================================
# Multi-stage build:
#   1. builder — installs all Python dependencies
#   2. final — minimal runtime image with only what's needed
# =============================================================================

# ── Builder stage ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /app

# Install build dependencies (weasyprint + playwright system deps)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc pkg-config libcairo2-dev libpango1.0-dev \
    libgdk-pixbuf-2.0-dev libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
# Use increased timeout (1000s) and retries for large packages like claude_agent_sdk (~75MB)
COPY requirements.txt .
RUN pip install --no-cache-dir --timeout=1000 --retries=5 \
    --user \
    -r requirements.txt

# ── Final stage ────────────────────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Install runtime system dependencies for:
# - weasyprint (libpango, libcairo, libgdk-pixbuf, libffi)
# - playwright (libnss3, libnspr4, libdbus-1-3, libatk-1.0-0, libatk-bridge2.0-0,
#   libcups2, libdrm2, libxkbcommon0, libxcomposite1, libxdamage1, libxrandr2,
#   libgbm1, libpango-1.0-0, libasound2, libatspi2.0-0)
# - timeout command for code_interpreter
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangocairo-1.0-0 libcairo2 \
    libgdk-pixbuf-2.0-0 libffi8 libnss3 libnspr4 libdbus-1-3 \
    libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 \
    libxcomposite1 libxdamage1 libxrandr2 libgbm1 libasound2 \
    libatspi2.0-0 libx11-6 libx11-xcb1 libxcb1 libxfixes3 libxext6 \
    coreutils \
    && rm -rf /var/lib/apt/lists/*

# Copy only installed packages from builder (avoids build tools in final image)
COPY --from=builder /root/.local /root/.local

# Make sure scripts in .local are usable
ENV PATH=/root/.local/bin:$PATH

# Install Playwright Chromium browser (headless shell)
RUN playwright install chromium && \
    playwright install-deps chromium 2>/dev/null || true

# Copy application code
COPY . .

# Build identity (Issue #659) — the commit is stamped into the image by
# infrastructure/build.sh (the .git directory is not copied into the build
# context), so a session export can name the build that produced it.
ARG GIT_COMMIT=""
ENV GIT_COMMIT=${GIT_COMMIT}

EXPOSE 8000

# CMD is overridden in docker-compose.yml:
#   alembic upgrade head && uvicorn src.main:app --host 0.0.0.0 --port 8000
