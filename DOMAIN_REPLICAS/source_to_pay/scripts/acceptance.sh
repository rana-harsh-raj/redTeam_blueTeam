#!/bin/sh
set -eu
S2P_DIR=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
exec python3 "$S2P_DIR/scripts/acceptance.py" "$@"
