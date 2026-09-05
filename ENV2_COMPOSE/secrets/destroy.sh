#!/usr/bin/env bash
# secrets/destroy.sh — remove every credential secrets/gen-secrets.sh wrote,
# and the generated config that had them inlined (generated/ is regenerated
# from scratch next time anyway, but deleting it here means no credential
# survives teardown in EITHER location). Called by scripts/down.sh.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "==> destroying secrets/*.txt, secrets/jwks.json, secrets/.env.secrets"
# `find -exec shred` reports success on macOS even when shred is absent.
# Unlink generated files portably; overwriting cannot promise secure erasure
# on APFS/SSD snapshots. Encrypted host storage remains the storage boundary.
find "$SCRIPT_DIR" -maxdepth 1 -type f -name '*.txt' -delete
rm -f "$SCRIPT_DIR/jwks.json" "$SCRIPT_DIR/.env.secrets"
rm -rf "$SCRIPT_DIR/verifier-bridge" "$SCRIPT_DIR/merchant-keys"

if [ -d "$COMPOSE_ROOT/generated" ]; then
  echo "==> destroying $COMPOSE_ROOT/generated (rendered config had credentials inlined)"
  rm -rf "$COMPOSE_ROOT/generated"
fi

python3 "$SCRIPT_DIR/materialize.py" --destroy

echo "==> secrets destroyed"
