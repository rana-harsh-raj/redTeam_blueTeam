#!/usr/bin/env bash
# M11: build the REAL razorpay/shield service (main.go + migrations/) from the pinned clone.
# ONE adaptation, labelled: github.com/razorpay/fingerprint-sdk (an external device-fingerprint vendor SDK) returns
# 404 to the build identity, so it is replaced with the 3-function stub in fingerprint-sdk-stub/ through a SEPARATE
# -modfile (the clone's go.mod/go.sum are never edited). Every other module (goutils/*, config-proto, governor-executor,
# error-mapping-module, i18nify) is the real one.
set -euo pipefail
REPOS_ROOT="${REPOS_ROOT:?}"; OUT="${OUT:?}"; ARCH="${ARENA_ARCH:-arm64}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPOS_ROOT/shield"
echo "==> shield @ $(git rev-parse HEAD)"
MODDIR="$(mktemp -d)"; trap 'rm -rf "$MODDIR"' EXIT
cp go.mod "$MODDIR/arena.go.mod"; cp go.sum "$MODDIR/arena.go.sum"
go mod edit -modfile="$MODDIR/arena.go.mod" -replace "github.com/razorpay/fingerprint-sdk=$HERE/fingerprint-sdk-stub"
mkdir -p "$OUT"
export GOFLAGS="-mod=mod -modfile=$MODDIR/arena.go.mod"
GOOS=linux GOARCH="$ARCH" CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o "$OUT/shield" .
GOOS=linux GOARCH="$ARCH" CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o "$OUT/shield-migrate" ./migrations
mkdir -p "$OUT/conf" "$OUT/resources/maxmind-db" && cp conf/*.toml "$OUT/conf/"
unzip -o -q ip2location/db/IP2LOCATION-LITE-DB3.IPV6.BIN.zip -d "$OUT/resources/maxmind-db/" && rm -f "$OUT"/resources/maxmind-db/LICENSE_LITE.TXT "$OUT"/resources/maxmind-db/README_LITE.TXT
mkdir -p "$OUT/migrations" "$OUT/settings" && cp migrations/*.go "$OUT/migrations/" && cp settings/* "$OUT/settings/" && cp prestoCert.crt "$OUT/"   # goose collects Go migrations by file name in -dir
[ -d templates ] && cp -r templates "$OUT/templates" || true
[ -d resources ] && rsync -a --exclude maxmind-db resources/ "$OUT/resources/" || true
echo "==> done: $(ls "$OUT")"
