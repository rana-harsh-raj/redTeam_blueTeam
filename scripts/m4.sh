#!/usr/bin/env bash
# scripts/m4.sh — Milestone 4 one-command entry-point dispatcher.
#
# Every subcommand ECHOES what it will do, then runs a REAL project script.
# All subcommands are rerunnable. This dispatcher never mutates the LIVE arena
# destructively: `m4-clean` refuses to touch the live compose project.
#
# Usage: scripts/m4.sh <subcommand> [args...]
#   boot            bring up / verify the full Env2 arena (clean-boot per README)
#   self-check      provision + self_check a FRESH Direct merchant (disposable)
#   test            verifier golden-run + boundary + BAS/XAS journey drivers
#   assurance-run   4-context autonomous run (gateway-backed) + rollup
#   replay          clean-state replay driver (STUB; coordinator fills)
#   acceptance      evaluate all 74 gates -> m4-direct-e2e-acceptance.json
#   evidence-verify build+verify the M4 evidence manifest (G68/G69)
#   clean           safe teardown of a DISPOSABLE instance (never the live arena)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV2="$REPO/ENV2_COMPOSE"
SURFACE="$REPO/RED_LOOP/surface"
IMPL="$REPO/reports/implementation"

say() { printf '\n\033[1;36m[m4:%s]\033[0m %s\n' "$1" "$2"; }

cmd="${1:-help}"; shift || true

case "$cmd" in
  boot)
    say boot "Bring up the full Env2 arena. Prefer the clean-boot per README (isolated, empty-volume)."
    say boot "Disposable clean boot:  ENV2_COMPOSE/scripts/clean-boot.sh <instance-id>"
    say boot "Live arena (in place):   ENV2_COMPOSE/scripts/up.sh"
    if [ "${1:-}" = "--live" ]; then
      say boot "Running live up.sh (in-place arena boot)."
      exec bash "$ENV2/scripts/up.sh"
    fi
    inst="${1:-m4-boot-$(date -u +%Y%m%dT%H%M%SZ)}"
    say boot "Running clean-boot.sh for disposable instance '$inst' (empty volumes, isolated networks)."
    exec bash "$ENV2/scripts/clean-boot.sh" "$inst"
    ;;

  self-check)
    say self-check "Provision a FRESH Direct merchant + self-check + payout proof via surface/m4_direct_provision.py."
    campaign="${1:-m4selfcheck-$(date -u +%Y%m%dT%H%M%SZ)}"
    say self-check "campaign=$campaign  ->  reports/implementation/m4-direct-provision.json (summary.all_green)"
    exec python3 "$SURFACE/m4_direct_provision.py" all --count "${COUNT:-2}" --campaign "$campaign"
    ;;

  test)
    say test "Run the verifier golden-run, the boundary suite, and the BAS/XAS journey drivers."
    say test "1/4 verifier golden-run (26/0/0 baseline):  ENV2_COMPOSE/scripts/golden-run.sh"
    ( cd "$ENV2" && bash scripts/golden-run.sh "$@" )
    say test "2/4 boundary/identity/concurrency suite:  ENV2_COMPOSE/verifier/m4_boundary.py --no-build"
    ( cd "$ENV2" && python3 verifier/m4_boundary.py --no-build )
    say test "3/4 BAS ingestion journey:  RED_LOOP/surface/m4_bas_ingest.py"
    python3 "$SURFACE/m4_bas_ingest.py" || true
    say test "4/4 XAS + Ledger DA journey:  RED_LOOP/surface/m4_xas_ledger.py"
    python3 "$SURFACE/m4_xas_ledger.py" || true
    say test "done. Journey evidence in reports/implementation/m4-{boundary-results,bas-ingest,xas-ledger}.json"
    ;;

  assurance-run)
    say assurance-run "4-context gateway-backed autonomous run (fresh Direct merchants) via RED_LOOP/run.py contexts."
    say assurance-run "-> reports/implementation/m4-assurance-run.json ; hypothesis lifecycle rollup + soak are separate."
    exec python3 "$REPO/RED_LOOP/run.py" contexts "$@"
    ;;

  replay)
    say replay "Clean-state replay driver (STUB). Writes reports/implementation/m4-direct-e2e-replay.json (status=pending)."
    say replay "Coordinator fills: clean-boot -> fresh-ID provision -> regression arm -> negative control -> judge."
    exec python3 "$SURFACE/m4_replay.py" "$@"
    ;;

  acceptance)
    say acceptance "Evaluate all 74 mandatory gates against present evidence."
    say acceptance "-> reports/implementation/m4-direct-e2e-acceptance.json (accepted / pending_gates / unmet_gates)."
    exec python3 "$SURFACE/m4_acceptance.py" "$@"
    ;;

  evidence-verify)
    say evidence-verify "Build the M4 evidence manifest, then verify every hash (backs G68/G69)."
    python3 "$SURFACE/m4_evidence_manifest.py" build
    say evidence-verify "Verifying recomputed hashes + self_sha256 + companion checksum..."
    exec python3 "$SURFACE/m4_evidence_manifest.py" verify
    ;;

  clean)
    inst="${1:?usage: m4.sh clean <disposable-instance-id>   (NEVER the live arena)}"
    say clean "Safe teardown of DISPOSABLE instance '$inst' via ENV2_COMPOSE/scripts/instance-cleanup.py."
    say clean "instance-cleanup refuses the live default plan (project env2_compose / empty suffix)."
    exec python3 "$ENV2/scripts/instance-cleanup.py" "$inst" "${@:2}"
    ;;

  help|*)
    awk 'NR>1 && /^#/ {sub(/^# ?/,""); print; next} NR>1 {exit}' "${BASH_SOURCE[0]}"
    [ "$cmd" = help ] && exit 0 || { echo "unknown subcommand: $cmd" >&2; exit 2; }
    ;;
esac
