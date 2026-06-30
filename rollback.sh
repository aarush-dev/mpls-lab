#!/bin/bash
# Switch back to main branch and redeploy Grafana without the mockup plugin.
set -e
REPO="$(cd "$(dirname "$0")" && pwd)"
git -C "$REPO" checkout main
docker compose -f "$REPO/telemetry/docker-compose.yml" restart grafana
echo "Switched to main + restarted Grafana. Mockup plugin removed."
