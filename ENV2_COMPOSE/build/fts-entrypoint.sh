#!/bin/sh
# build/fts-entrypoint.sh — default ENTRYPOINT for the fts runtime image.
#
# fts-web (docker-compose.yml `command: ["web"]`): starts the Go binary
# (cmd/web) bound to 127.0.0.1:8080 internally per fts config's
# LISTEN_IP/LISTEN_PORT, then execs nginx in the foreground so nginx's PID
# is PID 1 (container stop signals go to nginx, which is what real fts does
# per build/nginx.conf's two-listener shape -- :80 public, :8081
# metrics-only, both proxying to the Go binary on :8080).
#
# fts-worker (`command: ["worker"]`): just execs the worker binary, no nginx.
set -eu

MODE="${1:-web}"

case "$MODE" in
  web)
    /app/fts-web &
    GO_PID=$!
    trap 'kill -TERM "$GO_PID" 2>/dev/null || true' TERM INT
    # give the Go process a moment to bind before nginx starts proxying to it
    for i in $(seq 1 30); do
      nc -z 127.0.0.1 8080 && break
      sleep 0.5
    done
    exec nginx -g "daemon off;"
    ;;
  worker)
    exec /app/fts-worker
    ;;
  *)
    echo "unknown fts entrypoint mode: $MODE (expected web|worker)" >&2
    exit 1
    ;;
esac
