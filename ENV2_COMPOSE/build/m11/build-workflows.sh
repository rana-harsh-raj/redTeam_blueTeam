#!/usr/bin/env bash
# M11: build the REAL razorpay/workflows service (cmd/api, cmd/workers, cmd/migration) from the pinned clone.
# The Twirp/pb bindings are generated with buf from the pinned razorpay/proto clone (the upstream Dockerfile does a
# sparse checkout of the same modules from GitHub); nothing else in the repo is modified.
set -euo pipefail
REPOS_ROOT="${REPOS_ROOT:?set REPOS_ROOT (clone root containing workflows/ and proto/)}"
OUT="${OUT:?set OUT (staging dir for binaries)}"
ARCH="${ARENA_ARCH:-arm64}"
export PATH="$HOME/go/bin:$PATH"
cd "$REPOS_ROOT/workflows"
echo "==> workflows @ $(git rev-parse HEAD)  proto @ $(git -C "$REPOS_ROOT/proto" rev-parse HEAD)"
rm -rf proto rpc && mkdir -p proto
while read -r m; do
  case "$m" in ''|'#'*|prototool.yaml) continue;; esac
  mkdir -p "proto/$m" && cp "$REPOS_ROOT/proto/$m/"*.proto "proto/$m/"
done < scripts/proto_modules
buf generate
mkdir -p "$OUT"
for t in api:cmd/api/main.go:workflows-api workers:cmd/workers/main.go:workflows-worker migration:cmd/migration/main.go:workflows-migration; do
  IFS=: read -r name src bin <<<"$t"
  echo "==> go build $bin"
  GOOS=linux GOARCH="$ARCH" CGO_ENABLED=0 GOFLAGS=-mod=mod go build -trimpath -ldflags="-s -w" -o "$OUT/$bin" "./$src"
done
# runtime config dir = the repo's own config/ (default.toml + env files) plus the arena env file rendered by the twin
mkdir -p "$OUT/config" && cp config/*.toml "$OUT/config/"
mkdir -p "$OUT/internal/database" && cp -r internal/database/migrations "$OUT/internal/database/migrations"   # cmd/migration -dir default; goose matches registered Go migrations by file name
echo "==> done: $(ls "$OUT")"
