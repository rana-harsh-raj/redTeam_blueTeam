#!/bin/sh
set -eu
. "$(dirname "$0")/_common.sh"
exec python3 "$S2P_DIR/tests/integration/golden_journey.py"
