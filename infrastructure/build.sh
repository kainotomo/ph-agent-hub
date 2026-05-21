#!/usr/bin/env bash
# =============================================================================
# PH Agent Hub — Build & Push Script
# =============================================================================
# Builds backend and frontend Docker images and pushes them to the registry.
#
# Usage:
#   ./build.sh                    # Build + push with tag from package.json
#   ./build.sh -t v1.2.3          # Override the tag
#   ./build.sh --no-push          # Build only, skip registry push
#   ./build.sh --no-cache         # Disable Docker layer cache (clean build)
#   ./build.sh --no-push --no-cache  # Combine overrides
#
# Prerequisites:
#   - Docker installed and running
#   - Logged into the registry (docker login)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# ── Configuration ──────────────────────────────────────────────────────────
REGISTRY="phalouvas"           # Docker Hub user or full registry URL
BACKEND_IMAGE="${REGISTRY}/ph-agent-hub-backend"
FRONTEND_IMAGE="${REGISTRY}/ph-agent-hub-frontend"

# ── Auto-detect version from package.json ──────────────────────────────────
APP_VERSION="$(grep '"version"' "${PROJECT_DIR}/frontend/package.json" | head -1 | sed 's/.*"version": *"\(.*\)".*/\1/')"
echo "Detected app version: ${APP_VERSION}"

# ── Defaults (production-first) ────────────────────────────────────────────
TAG="${APP_VERSION:-latest}"   # Auto-tag from package.json, fallback: latest
PUSH=true                       # Push by default; --no-push to skip
NO_CACHE=""                     # Use cache by default; --no-cache to opt out

# ── Help ───────────────────────────────────────────────────────────────────
usage() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Options:
  -t, --tag TAG       Override the image tag (default: auto-detected from package.json)
  --no-push           Build images but skip pushing to registry
  --no-cache          Disable Docker layer cache (clean build)
  -h, --help          Show this help
EOF
  exit 0
}

# ── Parse arguments ────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    -t|--tag) TAG="$2"; shift 2 ;;
    --no-push) PUSH=false; shift ;;
    --no-cache) NO_CACHE="--no-cache"; shift ;;
    -h|--help) usage ;;
    *) echo "Unknown option: $1"; usage ;;
  esac
done

# ── Build backend ──────────────────────────────────────────────────────────
echo "========================================"
echo " Building backend: ${BACKEND_IMAGE}:${TAG}"
echo "========================================"
docker build ${NO_CACHE} \
  -t "${BACKEND_IMAGE}:${TAG}" \
  -t "${BACKEND_IMAGE}:latest" \
  -f "${PROJECT_DIR}/backend/Dockerfile" \
  "${PROJECT_DIR}/backend"

# ── Build frontend ─────────────────────────────────────────────────────────
echo ""
echo "========================================"
echo " Building frontend: ${FRONTEND_IMAGE}:${TAG}"
echo "========================================"
docker build ${NO_CACHE} \
  -t "${FRONTEND_IMAGE}:${TAG}" \
  -t "${FRONTEND_IMAGE}:latest" \
  -f "${PROJECT_DIR}/frontend/Dockerfile.prod" \
  "${PROJECT_DIR}/frontend"

# ── Push ───────────────────────────────────────────────────────────────────
if $PUSH; then
  echo ""
  echo "========================================"
  echo " Pushing images to ${REGISTRY} ..."
  echo "========================================"
  docker push "${BACKEND_IMAGE}:${TAG}"
  docker push "${FRONTEND_IMAGE}:${TAG}"
  if [ "${TAG}" != "latest" ]; then
    docker push "${BACKEND_IMAGE}:latest"
    docker push "${FRONTEND_IMAGE}:latest"
    echo ""
    echo "Done — pushed tags '${TAG}' and 'latest' (app version: ${APP_VERSION})."
  else
    echo ""
    echo "Done — pushed tag 'latest'."
  fi
else
  echo ""
  echo "Done — images built with tag '${TAG}' (push skipped with --no-push)."
fi