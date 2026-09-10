#!/usr/bin/env bash
# M11: build the REAL razorpay/banking-accounts service (main.go) from the pinned clone + a static goose CLI for its
# SQL migrations (internal/database/migrations/*.sql), exactly what build/entrypoint_migrations.sh runs in production.
set -euo pipefail
REPOS_ROOT="${REPOS_ROOT:?}"; OUT="${OUT:?}"; ARCH="${ARENA_ARCH:-arm64}"
cd "$REPOS_ROOT/banking-accounts"
echo "==> banking-accounts @ $(git rev-parse HEAD)"
mkdir -p "$OUT"
GOOS=linux GOARCH="$ARCH" CGO_ENABLED=0 GOFLAGS=-mod=mod go build -trimpath -ldflags="-s -w" -o "$OUT/banking-accounts-api" .
GOOS=linux GOARCH="$ARCH" CGO_ENABLED=0 go install github.com/pressly/goose/v3/cmd/goose@v3.24.1
cp "$(go env GOPATH)/bin/linux_${ARCH}/goose" "$OUT/goose" 2>/dev/null || cp "$(go env GOPATH)/bin/goose" "$OUT/goose"
mkdir -p "$OUT/config" "$OUT/migrations" && cp config/*.toml "$OUT/config/" && cp internal/database/migrations/*.sql "$OUT/migrations/"
echo "==> done: $(ls "$OUT")"
