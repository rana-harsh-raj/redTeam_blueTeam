# RED_LOOP Milestone 3.1 — Clean Acceptance and Fresh Merchant Payout Parity

## Verdict

**NOT READY FOR NEXT ARCHITECTURE EXPANSION** — but only two gates remain, both now
unblocked.

The Milestone-3 blocker (two independent empty-volume clean-boot acceptances + the
unresolved verifier v17) is **resolved**, and the fresh-merchant blocker is now
**also resolved**: a freshly provisioned merchant completes the full Shared payout
lifecycle to terminal `processed`. The `no_row_affected` failure was root-caused to a
one-character column overflow (`pricing_rule_id char(14)` vs a 15-char plan id the
M3.1 provisioner generated), masked by spine's `RowsAffected==0` guard; the fix is a
provisioning change and is proven live (see
`reports/claude-review/FRESH_MERCHANT_NO_ROW_ROOT_CAUSE.md` and the 8/8
`m31-fresh-merchant.json`).

The machine gate (`reports/implementation/m3-1-acceptance.json`) now fails closed on
exactly **two** gates, neither yet executed this session:

1. **Fresh-ID calibration** — not run.
2. **The second open-ended campaign** — not run. Its premise (a fresh *fully
   operational* merchant) is now satisfied by the fix above, so it is executable.

Ten of twelve gates pass on named runtime evidence. Nothing is faked green.

## Branch and commit references

- Working branch: `milestone-3-1-clean-parity`, head **d5489e3** (created from the
  corrected M3 head 3873e96).
- No acceptance tag was created (`red-loop-m3.1` is intentionally absent — gates do
  not all pass).

### Historical references preserved (verified, unchanged)

| Reference | Commit |
|---|---|
| Frozen Twin tag `twin-v1.0` | 78def24 (annotated tag d3f9a16 → 78def24) |
| Runtime freeze | 219ca48bbb5e51db9347b335ac0a50241656045b |
| Milestone 2 head `milestone-2-red-loop` | a107612 |
| Milestone 3 head `milestone-3-gateway-hardening` | 3873e96 |

`3873e96` is confirmed the current M3 head and an ancestor of the new branch. No
history was rewritten; no M1/M2/M3 fixtures, company clones, or campaign records were
modified. Historical M3 claims are corrected only in new documents.

## What changed after commit 3873e96

Six commits (9e7df25, 160333f, bc6d98f, d339444, d5489e3, and this report):

- **A** — `RED_LOOP/archive/evidence-manifest.json`: top-level SHA-256 index binding
  all 15 campaign/clean-boot run directories (git-ignored external evidence) plus the
  committed M2/M3/M3.1 artifacts, with a self-hash + companion checksum. 1,858
  artifacts, 94.3 MiB, no secrets/DB volumes/keys included (hashes and sizes only).
- **B** — safe runtime namespacing (below).
- **E+G** — fresh-merchant create-500 root cause fixed; hardening retained; token
  reduction recomputed.
- **C** — clean-boot orchestrator + the four namespacing/portability gaps found while
  actually booting a disposable arena.
- Clean-acceptance results (two 26/0/0 boots, v17, A~B comparison, acceptance gate).

## Evidence-manifest status

Present and self-consistent: `RED_LOOP/archive/evidence-manifest.json`
(`self_sha256` set, companion `.sha256`, `missing_expected_committed: []`). It
hash-binds the complete resumed 200-turn M3 campaign, the M2 campaign, all model/
tool/hypothesis/observation/candidate ledgers and judge verdicts, calibration and
negative-control bundles, gateway acceptance, context metrics, kill/resume evidence,
both clean-boot verifier runs (+ the preserved pollution-proof run), and the
generated acceptance records. No historical claim now depends solely on a mutable
running database.

## Compose resources parameterized (Workstream B)

Every explicitly-named global Docker resource now honours `ARENA_SUFFIX` (default
empty → the live arena's names are byte-identical; verified via `docker compose
config`):

- Networks `rzp-arena`, `rzp-ingress` (names **and** distinct `/24` subnets).
- Config volumes `rzp-arena-config-{payouts,ledger,fts,cfa,xbalances,mozart-mock}`.
- Secret volumes `rzp-arena-secrets-{kong,monolith}`.
- Datastore + docker-secret volumes and container names (already project-scoped via
  `COMPOSE_PROJECT_NAME`); Kong host port (already `KONG_LITE_HOST_PORT`).

Four more hard-coded/portability spots were found **only by executing a real boot**
and fixed: `up.sh`'s post-core `--network rzp-arena` and a bash-3.2 nounset-unsafe
empty-array expansion; `secrets/materialize.py`'s hard-coded config/secret volume
names; and `network/egress_audit.py`'s hard-coded `rzp-arena`/`rzp-ingress` network
inspect (it had been auditing the *live* networks' subnets → "coverage unproven").

New tooling: `scripts/instance-plan.py` (deterministic per-instance names; renders
authoritative names via `docker compose config`; **fails closed** on any network/
volume/port overlap — proven with a decoy network); `scripts/instance-cleanup.py`
(removes only a suffixed instance's resources + a belt-and-braces suffix sweep;
refuses the live project); `scripts/clean-boot.sh` (preflight → isolated working copy
under a Docker-shared path → empty-volume boot → 26-test verifier + same-window
egress → evidence).

## Proof of no writable overlap; live-arena disposition

- Preflight recorded zero overlap for both instances (`overlap.networks/volumes = []`,
  `reuses_live_named_network = false`); the fail-closed path is proven.
- The live arena was **archived first**, then **gracefully stopped** (`compose stop`;
  67 containers + 16 volumes retained, snapshot at
  `RED_LOOP/runs/live-arena-snapshot-*.json` with the restart command), disposable
  instances booted **sequentially** (host RAM is 12.5 GB — one 66-container arena at a
  time), then the live arena was **restarted and verified**: same network IDs
  (`rzp-arena` 2c505c083d14, `rzp-ingress` 4b9fbcec821b), 66 running, kong reachable,
  enforcement restored to **0** (frozen default). No live volume or network was
  replaced or deleted. No shared named network or secret/config volume was ever used
  by a disposable instance.

## Clean boot results

| | Boot A (`m31-clean-a`) | Boot B (`m31-clean-b`) |
|---|---|---|
| Project / subnets / port | env2c_m31_clean_a / 172.28.62–63 / 18334 | env2c_m31_clean_b / 172.28.54–55 / 18247 |
| Verifier (frozen baseline, empty volumes) | **26 / 0 / 0** | **26 / 0 / 0** |
| Same-window egress | **0 outside packets**, coverage proven (36,733 control) | **0 outside packets**, coverage proven (32,496 control) |
| Unexplained exits / timeouts | none | none |

**Logical replay A vs B** (`scripts/compare-replays.py`, normalizing generated IDs/
UTRs/order only): `logical_equivalence: true`, **0 different tests**, both runs valid,
independent directories. Material outcomes match.

Boot phases were kept explicit: **frozen-baseline** (route policy **0**, original
26-test verifier, the only workload before persistent payouts) — Phase 2 hardened-
merchant acceptance was exercised separately on the live arena (Workstream E), not
presented as a byte-identical rerun of frozen Twin v1.0.

## Verifier classifications

- **v12** `inflight_reservation_total_equals_live_reservations`,
  **v13** `reservation_released_on_terminal_event`,
  **v23** `pricing_500_rejects_payout`: **state pollution, proven.** All pass on the
  empty-volume boots and fail only after the suite itself populates the instance.
- **v17** `reversal_row_committed_before_ledger_call_baseline` (and
  `reversal_survives_ledger_outage_via_async_retry`): **HEALTHY_ON_CLEAN.** Passes on
  both empty boots. The dirty-arena failure (payout observed still `initiated`) is a
  state-load/accumulated-payout effect, demonstrated by the same instance regressing
  26/0/0 → 22/26 after one verifier round. It is **not** a Milestone-3 regression, and
  is no longer described as pollution-by-assertion but as proven-by-clean-boot.

## Fresh-merchant HTTP 500 — root cause and records added

**Root cause (corrects the M3 hypothesis).** The create-500 was **not** missing
ledger/pricing rows. Payout create fetches merchant config via
`GET /v1/internal/merchants/{id}`; the fresh merchant was **absent from
`seeds/generated/monolith/merchants.json`**, so monolith-stub returned 404, payouts-
api mapped it to `invalid_status_code` (`MERCHANT_CONFIG_FETCH_FROM_API_FAILED`,
merchant/core.go:121), and the dependency errgroup 500'd — **before** pricing or
ledger were reached.

**Records added to `provisioner.py` (idempotent):**

| Record | Store | Necessary vs parity |
|---|---|---|
| Merchant-config entry (features, `pricing_plan_id`, org) | monolith `merchants.json` | **Necessary** — removes the 404 → 500 |
| Pricing plan keyed by merchant_id | `pricing.json` `plans` | Latent next-failure (non-refund payouts fail-closed without it) |
| Free-payout counter keyed by balance_id | `pricing.json` `free_payout_counters` | Parity |
| 3 more ledger `account_details` (merchant_va_vendor, commission/cash, va_gst) + `banking_account_id` entity on merchant_va | postgres-ledger | Parity with the M1 fixture (only `merchant_va` existed before) |

After the fix a fresh merchant **authenticates and creates payouts returning 200 with
correct pricing (IMPS fees 200 / tax 36 / valid `pricing_rule_id`)**.

**Success / failure / insufficient-balance / idempotency — all proven (8/8).** A
fresh merchant now reaches terminal `processed` (success), `reversed` (bank failure),
is rejected on insufficient balance, and honours idempotency (same key+body → same
id; different body → 400). Evidence: `reports/implementation/m31-fresh-merchant.json`.
No static-merchant Ledger/balance/FTS/pricing/account state is reused — the
`payout_processed` journal references only the fresh merchant's own accounts
(`ARENA<mtok>AC0001‑0004`) plus the shared nodal pool.

**`no_row_affected` — root-caused and fixed (was called a "residual ceiling").** The
pre-ledger transition's `UPDATE payouts SET …,pricing_rule_id=?,status=?,… WHERE id=?`
**errored** with `Data too long for column 'pricing_rule_id'` (the column is
`char(14)`; the M3.1 provisioner generated a 15-char plan id `"ARENAPLAN"+mid[-6:]`).
The MySQL driver reports `RowsAffected=0` on a failed statement, and spine's
`if q.RowsAffected == 0 { return NoRowAffected }` fires **before** `GetDBError(q)`,
so the real 1406 error was reported as `no_row_affected`. M1 worked only because its
plan id `ARENAPLAN00001` is exactly 14 characters. The fix keeps the plan id ≤14
chars (`provisioner.py`); the repository guard was left untouched. Full analysis:
`reports/claude-review/FRESH_MERCHANT_NO_ROW_ROOT_CAUSE.md`.

## Fresh-ID calibration and second campaign

- **Fresh-ID calibration: not run.** The historical fixed-ID `pout_1234` calibration
  is preserved unchanged and is NOT claimed as fresh-ID replay. A genuine fresh-ID
  *unauthorized* effect is not available in this twin: tenant isolation holds on the
  clean boots (v20 `test_other_merchant_cannot_fetch_or_cancel` passes), and the only
  synthetic cross-tenant effect is the fixed payouts-api TiDB mock behind the M3
  enforcement toggle. This is a fidelity ceiling, disclosed rather than papered over.
- **Second open-ended campaign: not run this session, now executable.** Its required
  premise — a *fresh, fully operational* merchant — is satisfied by the fresh-merchant
  fix (a fresh merchant now completes the full lifecycle), so the campaign can run
  with a genuinely fresh operational actor. Deferred only for session budget, not
  blocked.

## Context efficiency (recomputed from raw logs)

Recomputed from `model_calls.jsonl` (203 calls, not the previously reported figure):
**80.9%** reduction — 21,017 vs 110,000 prompt tokens/call —
`full_transcript_resent = false` across all 200 turns.

## Remaining fidelity ceilings

1. **No genuine cross-tenant vulnerability** in the twin — the only cross-tenant
   exposure is the declared fixed TiDB mock, merchant-unreachable with enforcement on
   (tenant isolation v20 passes on both clean boots). A fresh-ID calibration can now
   use fresh victim resource IDs against the enforcement-toggle effect, but the
   "vulnerability" itself remains the synthetic toggle, not a real defect.
2. Retained from M2/M3: mozart-sim bank outcomes, RBL-only end-to-end channel, the
   API monolith is a stub.

(The fresh-merchant terminal-processing `no_row_affected` issue reported earlier this
milestone is **resolved**, not a ceiling — see the fresh-merchant section.)

## Production reachability

**UNKNOWN**, exactly as in M2/M3. Nothing demonstrated in this captured twin is
translated to production impact.

## Recommendation for the next architecture family

With the fresh-merchant lifecycle working, the **next architecture family to add is
the Direct (current-account) payout path** alongside the existing Shared path: it
exercises a distinct FTS routing, ledger account shape and reversal contract, and is
the smallest expansion that materially widens the merchant-reachable attack surface
without pulling in XAS, bulk payouts, or Workflow/Cadence.

## Safety and cleanup confirmation

- All disposable M3.1 containers, networks, config/secret volumes, datastore volumes
  and generated secrets removed and **verified absent** (including the mozart-mock
  config volume the active-profile render misses); their evidence directories
  preserved.
- Live arena **restored** to its recorded starting state (matching network IDs, 66
  containers, volumes intact) with a documented restart command in the snapshot.
- Final `KONG_ENFORCE_ROUTE_POLICY` = **0** (frozen default).
- mozart-sim in-memory scenarios cleared by the restart (channel health restored); no
  Stork duplicate control left enabled.
- No production, staging, DevStack, public internet, real credentials/data, company-
  clone modification, or Daytona was involved. No unresolved outside packet was
  observed on either clean boot.
