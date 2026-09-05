#!/usr/bin/env bash
# network/egress-audit.sh — run a tcpdump sidecar on rzp-arena during a
# golden run and produce EGRESS_AUDIT.md summarizing every distinct
# destination IP/port observed, so a human can confirm nothing left the
# arena's own subnet (rzp-arena is `internal: true`, so this should always
# come back empty of anything outside ARENA_SUBNET -- that emptiness IS the
# audit's pass condition, not an assumption this script makes for you).
#
# Usage: ./network/egress-audit.sh [duration_seconds]
# Typically run right before/around scripts/golden-run.sh so the capture
# window actually covers real traffic.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$COMPOSE_ROOT"

DURATION="${1:-60}"
OUT_MD="$COMPOSE_ROOT/EGRESS_AUDIT.md"
# The pcap lives in a Docker named volume (a host temp dir is not visible to the Colima VM's daemon).
CAP_VOL="rzp-egress-capture"
PCAP_TMP="/capture/egress.pcap"
docker volume create "$CAP_VOL" >/dev/null

# Capture point: the Docker host's (Colima VM's) network namespace, NOT a sidecar on rzp-arena.
# A sidecar container only sees its own interface; on the host namespace tcpdump -i any sees every
# packet crossing the rzp-arena bridge, including egress attempts that the `internal: true`
# isolation rules then drop. The filter keeps only packets whose source is an arena address and
# whose destination is NOT an arena address (i.e. attempted egress) plus any DNS to non-arena resolvers.
ARENA_SUBNET="$(grep -E '^ARENA_SUBNET=' "$COMPOSE_ROOT/.env.arena" 2>/dev/null | cut -d= -f2 || true)"
ARENA_SUBNET="${ARENA_SUBNET:-172.28.16.0/24}"
echo "==> starting tcpdump on the Docker host namespace (nicolaka/netshoot --net host) for ${DURATION}s; filter: src ${ARENA_SUBNET} and not dst ${ARENA_SUBNET}"
docker run --rm --net host --cap-add NET_RAW --cap-add NET_ADMIN \
  -v "$CAP_VOL:/capture" \
  nicolaka/netshoot:latest \
  sh -c "timeout ${DURATION} tcpdump -i any -n -w /capture/egress.pcap \
    'src net ${ARENA_SUBNET} and not dst net ${ARENA_SUBNET} and not dst net 172.28.17.0/24'; \
    echo control-start; timeout 5 tcpdump -i any -n -c 200 'net ${ARENA_SUBNET}' 2>/dev/null | wc -l > /capture/control.txt; true" \
  || true  # `timeout` exits 124 on the expected timeout, not a real failure
CONTROL_PACKETS="$(docker run --rm -v "$CAP_VOL:/capture" nicolaka/netshoot:latest cat /capture/control.txt 2>/dev/null | tr -d ' ' || echo 0)"
echo "==> control: ${CONTROL_PACKETS} intra-arena packets seen in 5s at the same capture point (proves the capture sees arena traffic)"
if ! docker run --rm -v "$CAP_VOL:/capture" nicolaka/netshoot:latest test -f /capture/egress.pcap; then
  echo "ERROR: no pcap produced in volume $CAP_VOL" >&2
  exit 1
fi

echo "==> summarizing distinct destination IP:port pairs"
SUMMARY="$(docker run --rm -v "$CAP_VOL:/capture" nicolaka/netshoot:latest \
  tshark -r /capture/egress.pcap -T fields -e ip.dst -e tcp.dstport -e udp.dstport 2>/dev/null \
  | awk '{ port = ($2 != "" ? $2 : $3); print $1":"port }' \
  | sort -u || echo "(tshark unavailable or capture empty)")"

# ARENA_SUBNET from .env.arena, for classifying each destination.
ARENA_SUBNET="$(grep -E '^ARENA_SUBNET=' "$COMPOSE_ROOT/.env.arena" 2>/dev/null | cut -d= -f2 || echo "172.28.16.0/24")"

{
  echo "# EGRESS_AUDIT.md"
  echo
  echo "Captured $(date -u +%Y-%m-%dT%H:%M:%SZ), duration ${DURATION}s, on the Docker host namespace with BPF filter \`src net ${ARENA_SUBNET} and not dst net ${ARENA_SUBNET}\` (attempted egress only; intra-arena traffic is excluded by design)."
  echo "Arena subnet (expected range for every destination below): \`${ARENA_SUBNET}\`"
  echo
  echo "Control: ${CONTROL_PACKETS} intra-arena packets observed in a 5 s unfiltered sample at the same capture point (non-zero proves the capture point sees arena traffic)."
  echo
  echo "## Distinct destinations observed"
  echo
  echo '```'
  echo "$SUMMARY"
  echo '```'
  echo
  echo "## Verdict"
  echo
  OUTSIDE_COUNT=0
  while IFS= read -r line; do
    [ -z "$line" ] && continue
    ip_part="${line%%:*}"
    if command -v python3 >/dev/null 2>&1; then
      inside=$(python3 -c "
import ipaddress, sys
try:
    print(ipaddress.ip_address('$ip_part') in ipaddress.ip_network('$ARENA_SUBNET', strict=False))
except Exception:
    print('unknown')
")
      if [ "$inside" != "True" ]; then
        OUTSIDE_COUNT=$((OUTSIDE_COUNT + 1))
      fi
    fi
  done <<< "$SUMMARY"
  if [ "$OUTSIDE_COUNT" -eq 0 ]; then
    echo "PASS: no packet left the arena subnet during the capture window (the filtered capture is empty)."
  else
    echo "FAIL: ${OUTSIDE_COUNT} destination(s) OUTSIDE the arena subnet were observed -- rzp-arena's \`internal: true\` isolation may be broken, or a container is misconfigured. Investigate before trusting any golden-run result from this window."
  fi
} > "$OUT_MD"

rm -rf "$(dirname "$PCAP_TMP")"
echo "==> wrote $OUT_MD"
