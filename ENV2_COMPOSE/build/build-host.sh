#!/usr/bin/env bash
# build/build-host.sh — alternative to build.sh: build binaries on the HOST
# (using whatever git/GOPRIVATE credential setup already works in the
# developer's shell -- e.g. an existing ~/.netrc or SSH-based module proxy),
# then COPY only the compiled binaries into a credential-free runtime image.
# Useful when BuildKit secret mounts aren't available (e.g. some remote
# Docker contexts) or when iterating fast without a full Docker build per
# change.
#
# Requires: go 1.26 on PATH (payouts/x-balances/ledger want go1.25-1.26;
# fts/cfa want go1.24 -- go's own toolchain directive in each go.mod
# auto-downloads the exact version if `go` on PATH is newer, per standard Go
# 1.21+ toolchain behavior, so a single go1.26 on PATH covers all 5).
#
# Usage:
#   REPOS_ROOT=/path/to/scratchpad/rzp-payouts-architecture ./build/build-host.sh [all|payouts|...]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPOS_ROOT="${REPOS_ROOT:-}"
ARENA_TAG="${ARENA_TAG:-local}"
TARGET="${1:-all}"
OUT_DIR="$(mktemp -d)"
trap 'rm -rf "$OUT_DIR"' EXIT

if [ -z "$REPOS_ROOT" ]; then
  echo "ERROR: set REPOS_ROOT to a directory containing payouts/ ledger/ fts/ cfa/ x-balances/ clones" >&2
  exit 1
fi
if ! command -v go >/dev/null 2>&1; then
  echo "ERROR: go not found on PATH (need go1.26, or a toolchain-capable go that can fetch it)" >&2
  exit 1
fi

RUNTIME_DOCKERFILE="$COMPOSE_ROOT/build/runtime-only.Dockerfile"

build_go() {
  local repo_dir="$1" out_name="$2" cmd_path="$3" tags="${4:-}"
  # Cross-compile for the Colima/Linux runtime (host is darwin); ARENA_ARCH defaults to the host arch.
  local arch="${ARENA_ARCH:-$(uname -m | sed -e s/x86_64/amd64/ -e s/aarch64/arm64/)}"
  echo "==> go build -o $OUT_DIR/$out_name (cwd=$repo_dir, pkg=$cmd_path, GOOS=linux GOARCH=$arch tags=$tags)"
  (cd "$repo_dir" && GOOS=linux GOARCH="$arch" CGO_ENABLED=0 GOFLAGS="${GOFLAGS:--mod=mod}" go build -trimpath -ldflags="-s -w" ${tags:+-tags "$tags"} -o "$OUT_DIR/$out_name" "$cmd_path")
}

package_runtime() {
  local name="$1"; shift
  local -a bins=("$@")
  echo "==> packaging rzp-arena/${name}:${ARENA_TAG} from host-built binaries: ${bins[*]}"
  local stage_dir="$OUT_DIR/runtime-$name"
  mkdir -p "$stage_dir"
  for b in "${bins[@]}"; do
    install -m 0755 "$OUT_DIR/$b" "$stage_dir/$b"
  done
  # Runtime asset files the services open relative to WORKDIR=/app (see build/assets.Dockerfile.md).
  if declare -f "stage_assets_${name}" >/dev/null; then "stage_assets_${name}" "$stage_dir"; fi
  # Only binaries and explicitly admitted public assets enter this context.
  # Source copies may be 0600 and callers may use umask 077; appuser must
  # still traverse every packaged directory. Never change source permissions.
  find "$stage_dir" -type d -exec chmod 0755 {} +
  docker build -f "$RUNTIME_DOCKERFILE" -t "rzp-arena/${name}:${ARENA_TAG}" "$stage_dir"
  rm -rf "$stage_dir"
}

stage_public_json() {
  local destination="$1"; shift
  mkdir -p "$destination"
  # Bank-directory metadata is public runtime reference data, not config or
  # credentials. Set creation permissions explicitly instead of preserving
  # the restrictive modes of the admitted source copy.
  install -m 0644 "$@" "$destination/"
}

# payouts: github.com/razorpay/ifsc opens $WORKDIR/github.com/razorpay/ifsc/v2@<ver>/src/*.json at boot
#
# ARENA FIX (milestone 1, TWIN_V1_AUDIT_AND_NEXT_STEP.md §5.2/§3, lane_C_patches.md §3 "files/ not staged"):
# prod's build/docker/prod/Dockerfile.api:80 also does `COPY --from=builder /src/files/ /app/files/`; that
# directory was never staged here. helpers.ReadErrorFile (internal/helpers/read_file.go:18-33) resolves
# $WORKDIR/files/error/<payout_error|fav_error>.json (WORKDIR=/app in docker-compose.yml) and silently returns
# nil on a missing file (read_file.go:22-25, no error) -- every bank error-code -> public failure-reason mapping
# (internal/app/payoutStatusDetails/statusProcessor.go:112, internal/app/payouts/payout_error.go:63,
# internal/app/favStatusDetails/core.go:141) was degrading to the fallback with no visible failure. Both JSON
# files are public bank error-code -> {source,reason,description} maps (grepped for
# password/secret/api_key/private_key/token/credential -- zero matches; "VAULT_TOKEN_*" hits are error-code
# *keys*, not values) and are staged public/0644 like the IFSC data above.
stage_assets_payouts() {
  local stage="$1" ver; ver="$(grep -o 'razorpay/ifsc/v2 v[0-9.]*' "$REPOS_ROOT/payouts/go.mod" | awk '{print $2}')"
  stage_public_json "$stage/github.com/razorpay/ifsc/v2@${ver}/src" "$(go env GOMODCACHE)/github.com/razorpay/ifsc/v2@${ver}/src/"*.json
  stage_public_json "$stage/files/error" "$REPOS_ROOT/payouts/files/error/"*.json
}

# cfa: its own pkg/ifsc/*.json plus the ifsc module files, and the socat Mongo-forward entrypoint
stage_assets_cfa() {
  local stage="$1" ver; ver="$(grep -o 'razorpay/ifsc/v2 v[0-9.]*' "$REPOS_ROOT/cfa/go.mod" 2>/dev/null | awk '{print $2}' || true)"
  stage_public_json "$stage/github.com/razorpay/cfa/pkg/ifsc" "$REPOS_ROOT/cfa/pkg/ifsc/"*.json
  if [ -n "$ver" ]; then   # only when cfa depends on the ifsc module (it vendors pkg/ifsc/go itself today)
    stage_public_json "$stage/github.com/razorpay/ifsc/v2@${ver}/src" "$(go env GOMODCACHE)/github.com/razorpay/ifsc/v2@${ver}/src/"*.json
  fi
  install -m 0755 "$COMPOSE_ROOT/build/cfa-entry.sh" "$stage/cfa-entry.sh"
}

build_payouts() {
  build_go "$REPOS_ROOT/payouts" payouts-api ./cmd/api boot
  build_go "$REPOS_ROOT/payouts" payouts-workers ./cmd/workers boot
  build_go "$REPOS_ROOT/payouts" payouts-kafka-consumer ./cmd/kafkaConsumers boot
  build_go "$REPOS_ROOT/payouts" payouts-migration ./cmd/migration
  package_runtime payouts payouts-api payouts-workers payouts-kafka-consumer payouts-migration
}

build_ledger() {
  build_go "$REPOS_ROOT/ledger" ledger-api ./cmd/api
  build_go "$REPOS_ROOT/ledger" ledger-worker ./cmd/worker
  build_go "$REPOS_ROOT/ledger" ledger-scheduler ./cmd/scheduler
  build_go "$REPOS_ROOT/ledger" ledger-migration ./cmd/migration
  package_runtime ledger ledger-api ledger-worker ledger-scheduler ledger-migration
}

build_fts() {
  build_go "$REPOS_ROOT/fts" fts-web ./cmd/web
  build_go "$REPOS_ROOT/fts" fts-worker ./cmd/worker
  build_go "$REPOS_ROOT/fts" fts-migrate ./cmd/migration
  build_go "$REPOS_ROOT/fts" fts-mock-server ./cmd/mock-server
  package_runtime fts fts-web fts-worker fts-migrate fts-mock-server
}

build_cfa() {
  build_go "$REPOS_ROOT/cfa" cfa-server ./cmd/server
  build_go "$REPOS_ROOT/cfa" cfa-worker ./cmd/worker
  build_go "$REPOS_ROOT/cfa" cfa-migration ./cmd/migration
  package_runtime cfa cfa-server cfa-worker cfa-migration
}

build_xbalances() {
  build_go "$REPOS_ROOT/x-balances" xbalances-server ./cmd/server
  build_go "$REPOS_ROOT/x-balances" xbalances-worker ./cmd/worker
  build_go "$REPOS_ROOT/x-balances" xbalances-migration ./cmd/migration
  package_runtime xbalances xbalances-server xbalances-worker xbalances-migration
}

case "$TARGET" in
  payouts) build_payouts ;;
  ledger) build_ledger ;;
  fts) build_fts ;;
  cfa) build_cfa ;;
  xbalances) build_xbalances ;;
  all)
    build_payouts
    build_ledger
    build_fts
    build_cfa
    build_xbalances
    ;;
  *)
    echo "usage: $0 [payouts|ledger|fts|cfa|xbalances|all]" >&2
    exit 1
    ;;
esac

echo "==> build-host.sh done"
