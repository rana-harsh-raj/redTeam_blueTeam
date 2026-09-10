# EGRESS_AUDIT.md

Captured 2026-09-04T17:58:48Z, duration 120s, on the Docker host namespace with BPF filter `src net 172.28.16.0/24 and not dst net 172.28.16.0/24` (attempted egress only; intra-arena traffic is excluded by design).
Arena subnet (expected range for every destination below): `172.28.16.0/24`

Control: 200 intra-arena packets observed in a 5 s unfiltered sample at the same capture point (non-zero proves the capture point sees arena traffic).

## Distinct destinations observed

```

```

## Verdict

PASS: no packet left the arena subnet during the capture window (the filtered capture is empty).
