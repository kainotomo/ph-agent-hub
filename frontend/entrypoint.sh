#!/bin/sh
# =============================================================================
# PH Agent Hub — Frontend Entrypoint
# =============================================================================
# Substitutes env vars into the nginx config template at container startup.
# This allows runtime configuration of CSP frame-ancestors for the embed widget
# without rebuilding the image.
# =============================================================================
set -e

# Substitute WIDGET_ALLOWED_ORIGINS placeholder in nginx config
# Default to 'self' if env var is unset (same-origin only, secure default)
ALLOWED="${WIDGET_ALLOWED_ORIGINS:-'self'}"
sed "s|__WIDGET_ALLOWED_ORIGINS__|${ALLOWED}|g" \
  /etc/nginx/conf.d/default.conf.template \
  > /etc/nginx/conf.d/default.conf

exec nginx -g 'daemon off;'
