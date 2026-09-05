#!/usr/bin/env bash
# build/build.sh — build the 5 core service images + mozart-mock via
# BuildKit, using a per-invocation `.netrc` supplied ONLY as a BuildKit
# secret (never a build arg, never a layer, never left on disk longer than
# this script's own run).
#
# Usage:
#   REPOS_ROOT=/path/to/scratchpad/rzp-payouts-architecture \
#   MOZART_REPO=/path/to/mozart/clone \
#   ./build/build.sh [payouts|ledger|fts|cfa|xbalances|mozart|all]
#
# REPOS_ROOT must contain payouts/, ledger/, fts/, cfa/, x-balances/ clones
# (matching this scaffold's own reference paths). MOZART_REPO is optional --
# if unset, mozart-mock is skipped (scripts/up.sh falls back to mozart-sim).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

REPOS_ROOT="${REPOS_ROOT:-}"
MOZART_REPO="${MOZART_REPO:-}"
ARENA_TAG="${ARENA_TAG:-local}"
TARGET="${1:-all}"

if [ -z "$REPOS_ROOT" ]; then
  echo "ERROR: set REPOS_ROOT to a directory containing payouts/ ledger/ fts/ cfa/ x-balances/ clones" >&2
  exit 1
fi

export DOCKER_BUILDKIT=1

# --- produce a per-invocation netrc from `gh auth token`, use it as a
#     BuildKit secret, then delete it. Nothing here is written into an image
#     layer or left behind after this script exits (even on error, via trap).
NETRC_TMP="$(mktemp -d)/netrc"
cleanup() {
  rm -f "$NETRC_TMP"
  rmdir "$(dirname "$NETRC_TMP")" 2>/dev/null || true
}
trap cleanup EXIT

make_netrc() {
  if ! command -v gh >/dev/null 2>&1; then
    echo "ERROR: gh CLI not found; required to mint a short-lived GitHub token for private go modules." >&2
    echo "       Install: https://cli.github.com/ , then \`gh auth login\`." >&2
    exit 1
  fi
  local token
  token="$(gh auth token)"
  cat > "$NETRC_TMP" <<EOF
machine github.com
login x-access-token
password ${token}
EOF
  chmod 600 "$NETRC_TMP"
}

build_one() {
  local name="$1" dockerfile="$2" context="$3"
  echo "==> building rzp-arena/${name}:${ARENA_TAG} from ${dockerfile} (context: ${context})"
  docker build \
    -f "$dockerfile" \
    --secret "id=netrc,src=${NETRC_TMP}" \
    -t "rzp-arena/${name}:${ARENA_TAG}" \
    "$context"
}

make_netrc

case "$TARGET" in
  payouts) build_one payouts "$COMPOSE_ROOT/build/payouts.Dockerfile" "$REPOS_ROOT/payouts" ;;
  ledger)  build_one ledger  "$COMPOSE_ROOT/build/ledger.Dockerfile"  "$REPOS_ROOT/ledger" ;;
  fts)     build_one fts     "$COMPOSE_ROOT/build/fts.Dockerfile"     "$REPOS_ROOT/fts" ;;
  cfa)     build_one cfa     "$COMPOSE_ROOT/build/cfa.Dockerfile"     "$REPOS_ROOT/cfa" ;;
  xbalances) build_one xbalances "$COMPOSE_ROOT/build/xbalances.Dockerfile" "$REPOS_ROOT/x-balances" ;;
  mozart)
    if [ -z "$MOZART_REPO" ]; then
      echo "ERROR: set MOZART_REPO to build mozart-mock" >&2
      exit 1
    fi
    build_one mozart "$COMPOSE_ROOT/build/mozart.Dockerfile" "$MOZART_REPO"
    ;;
  all)
    build_one payouts "$COMPOSE_ROOT/build/payouts.Dockerfile" "$REPOS_ROOT/payouts"
    build_one ledger  "$COMPOSE_ROOT/build/ledger.Dockerfile"  "$REPOS_ROOT/ledger"
    build_one fts     "$COMPOSE_ROOT/build/fts.Dockerfile"     "$REPOS_ROOT/fts"
    build_one cfa     "$COMPOSE_ROOT/build/cfa.Dockerfile"     "$REPOS_ROOT/cfa"
    build_one xbalances "$COMPOSE_ROOT/build/xbalances.Dockerfile" "$REPOS_ROOT/x-balances"
    if [ -n "$MOZART_REPO" ]; then
      build_one mozart "$COMPOSE_ROOT/build/mozart.Dockerfile" "$MOZART_REPO"
    else
      echo "==> MOZART_REPO not set, skipping mozart-mock (mozart-sim will be used as fallback)"
    fi
    ;;
  *)
    echo "usage: $0 [payouts|ledger|fts|cfa|xbalances|mozart|all]" >&2
    exit 1
    ;;
esac

echo "==> build.sh done"
