# Milestone 3.1 fidelity declarations

Builds on `m3-fidelity.md`. Records what M3.1 changed and the ceilings that remain.

## Resolved this milestone

- **Fresh-merchant payout parity (was M3-DEV-003, the declared ceiling).**
  A runtime-provisioned merchant now completes the full Shared-payout lifecycle to
  terminal `processed`. Two provisioning defects were fixed, both in synthetic
  provisioning only — the real Payouts repository guard and `RowsAffected`
  handling are untouched:
  1. `pricing_rule_id` `char(14)` overflow: the provisioner built a 15-char plan
     id; MySQL raised 1406 and spine's `RowsAffected==0` guard masked it as
     `no_row_affected`. Fixed by a bounded 14-char hashed plan id, now reserved
     fail-closed (`RED_LOOP/red_loop/pricing_ids.py`).
  2. `provision_funded_merchant` never self-verified, so
     `provision_campaign(fund_fresh_attacker=True)` silently fell back to the M1
     fixture. It now creates+fetches a payout through kong and sets
     `verified` from that real result; a merchant that cannot transact is not
     presented as fresh/operational.
  Evidence: `reports/implementation/m31-fresh-merchant.json` (8/8 on current head,
  hardened gateway), `m31-fresh-merchant-noreuse.json` (own balance/plan/Ledger
  accounts + only the shared nodal pool).

## New calibration fixture (calibration-only, never a finding)

- **Fresh-ID tenant-isolation calibration** (`RED_LOOP/calibration/tenant-isolation/`).
  A self-contained loopback service with a regression (IDOR) / fixed (ownership-
  enforced) pair, freshly generated identities/canaries per run, an ID-free
  semantic replay bundle, a deterministic oracle, and a different-provider
  reproducer. It replaces the M3 `m3_calibration.py` lane (which reused the fixed
  `pout_1234` marker and toggled the shared gateway) for M3.1's fresh-ID
  requirement. It does not touch the real Kong/Payouts/Ledger/FTS/CFA and is
  invisible to the open campaign (outside the corpus roots; ports disjoint from the
  attacker broker). Classified `CALIBRATION_REDISCOVERY`, never
  `ACCEPTED_NEW_FINDING`.

## Retained fidelity ceilings

- **Pricing rule identity.** The provisioner seeds pricing *plans* without a
  separate rule-level id, so the monolith substitute returns a **plan id** as
  `payouts.pricing_rule_id`. This is a documented limitation, not exact production
  rule-identity parity.
- All M2/M3 ceilings stand: bank outcomes are mozart-sim; RBL is the only
  end-to-end channel; the API monolith is a stub; findings hinging on real bank or
  monolith internals hit a fidelity ceiling.

## Production reachability

Unchanged and UNKNOWN where it was UNKNOWN in M2/M3. "Demonstrated in this captured
twin" is never translated to "affects production." Zero accepted open-campaign
findings is an honest outcome and is compatible with the milestone being READY.
