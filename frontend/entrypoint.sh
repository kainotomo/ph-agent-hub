#!/bin/sh
# =============================================================================
# PH Agent Hub — Frontend Entrypoint
# =============================================================================
# Substitutes env vars into the nginx config template at container startup.
# This allows runtime configuration of CSP frame-ancestors for the embed widget
# without rebuilding the image.
#
# 'self' is hardcoded in the nginx template. The WIDGET_ALLOWED_ORIGINS env var
# holds only additional origins (single value, no spaces) to avoid Docker
# Compose env_file parsing issues.
# =============================================================================
set -e

# Substitute WIDGET_ALLOWED_ORIGINS placeholder in nginx config
# 'self' is already in the template — this adds extra origins with a leading space
EXTRA="${WIDGET_ALLOWED_ORIGINS:-}"
if [ -n "$EXTRA" ]; then
  EXTRA=" ${EXTRA}"
fi
sed "s|__WIDGET_ALLOWED_ORIGINS__|${EXTRA}|g" \
  /etc/nginx/conf.d/default.conf.template \
  > /etc/nginx/conf.d/default.conf

exec nginx -g 'daemon off;'
