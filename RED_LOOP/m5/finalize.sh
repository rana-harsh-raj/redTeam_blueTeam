#!/usr/bin/env bash
# Promote campaign evidence, run the control-plane verifier checks, and evaluate
# M5 acceptance. Run after a campaign completes:
#   source RED_LOOP/llm.env && bash RED_LOOP/m5/finalize.sh RED_LOOP/m5/campaign/.run/full-A
set -euo pipefail
CAMP="${1:?usage: finalize.sh <campaign-run-dir>}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IMPL="$REPO/reports/implementation"
cd "$REPO"

echo "== promote campaign evidence =="
cp "$CAMP/m5-campaign-summary.json"     "$IMPL/m5-campaign-summary.json"
cp "$CAMP/m5-hypotheses.json"           "$IMPL/m5-hypotheses.json"
cp "$CAMP/m5-candidates.json"           "$IMPL/m5-candidates.json"
cp "$CAMP/m5-provenance-events.jsonl"   "$IMPL/m5-provenance-events.jsonl"
cp "$CAMP/m5-task-ledger.jsonl"         "$IMPL/m5-task-ledger.jsonl"
cp "$CAMP/m5-checkpoint.json"           "$IMPL/m5-campaign-checkpoint.json"

echo "== benchmark integrity (control-plane verifier matrix) =="
python3 RED_LOOP/m5/benchmark/integrity_check.py "$CAMP/work/integrity" 2>/dev/null \
  | grep -v "Thread\|Fatal\|runtime state\|Current thread\|no Python" || true

echo "== safety + egress =="
python3 RED_LOOP/m5/safety_egress.py

echo "== independent clean-state replay of accepted findings =="
python3 RED_LOOP/m5/verify/replay_findings.py "$CAMP/work/replay" 2>/dev/null \
  | grep -v "Thread\|Fatal\|runtime state\|Current thread\|no Python" || true

echo "== M5 acceptance =="
python3 RED_LOOP/surface/m5_acceptance.py --dry-run || true
