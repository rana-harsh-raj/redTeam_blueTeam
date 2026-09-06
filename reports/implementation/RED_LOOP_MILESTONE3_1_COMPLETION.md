# RED_LOOP Milestone 3.1 — Clean Acceptance and Fresh Merchant Payout Parity

## Verdict

**NOT READY FOR NEXT ARCHITECTURE EXPANSION.**

The milestone's headline blocker from Milestone 3 — the two independent
empty-volume clean-boot acceptances and the unresolved verifier v17 — is now
**resolved**. The milestone remains NOT READY strictly because three gates could not
be met with genuine runtime evidence this session:

1. **Fresh-merchant full payout success** is blocked by a precisely-characterised
   compiled-service ceiling (pre-ledger `no_row_affected`), so a freshly provisioned
   merchant cannot reach terminal `processed`.
2. **Fresh-ID calibration** was not run — the twin has no genuine cross-tenant
   vulnerability to exercise with fresh resource IDs (see Remaining ceilings).
3. **The second open-ended campaign** was not run this session; its premise (a fresh
   *fully operational* merchant) depends on (1).

A machine-generated gate (`reports/implementation/m3-1-acceptance.json`) fails closed
on exactly these three; the other eight gates pass on named runtime evidence.

Zero of these three is faked green. This is the honest, evidence-backed state.

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

**Success / failure / insufficient-balance / idempotency.** Idempotency is **proven**
(same key+body → same id; same key + different body → 400). Successful payout, bank
failure/reversal, and insufficient-balance are all **blocked by one residual
ceiling** and therefore not demonstrated end-to-end.

**Residual ceiling (characterised to SQL, not resolved).** Runtime-provisioned fresh
payouts stall at `create_request_submitted`: the pre-ledger transition
(`payoutStateProcessingBeforeLedgerServiceCall`) calls spine `repo.Update`, whose
full-model `UPDATE payouts SET …,status=?,… WHERE id=?` returns `no_row_affected`
(0 rows) for a fresh merchant but 1 row for the boot-seeded M1 fixture. Ruled out:
merchant-config (fixed), pricing (correct), ledger parity (4 rows match M1), payouts
`merchant_configurations`/`settings` (empty for M1 too), id validity (the row is
manually updatable by its id), a payouts-api cache (restart did not help), and warm-
up/race (deterministic across 3 merchants × 3 consecutive payouts). Because the
Shared-account balance check is enforced in the ledger debit
(`balance + amount >= 0`), which never runs, the success/reversal/insufficient
behaviours all depend on this one ceiling. It is a deterministic compiled-service
behaviour for runtime-provisioned merchants, beyond seed-data fixes.

## Fresh-ID calibration and second campaign

- **Fresh-ID calibration: not run.** The historical fixed-ID `pout_1234` calibration
  is preserved unchanged and is NOT claimed as fresh-ID replay. A genuine fresh-ID
  *unauthorized* effect is not available in this twin: tenant isolation holds on the
  clean boots (v20 `test_other_merchant_cannot_fetch_or_cancel` passes), and the only
  synthetic cross-tenant effect is the fixed payouts-api TiDB mock behind the M3
  enforcement toggle. This is a fidelity ceiling, disclosed rather than papered over.
- **Second open-ended campaign: not run this session.** Its required premise — a
  *fresh, fully operational* merchant — is blocked by the residual ceiling above; a
  campaign with the fixture attacker would only replicate the M3 zero-finding result
  and would not satisfy the requirement. Deferred pending the terminal-processing fix.

## Context efficiency (recomputed from raw logs)

Recomputed from `model_calls.jsonl` (203 calls, not the previously reported figure):
**80.9%** reduction — 21,017 vs 110,000 prompt tokens/call —
`full_transcript_resent = false` across all 200 turns.

## Remaining fidelity ceilings

1. **Fresh-merchant terminal processing** (`no_row_affected`) — new this milestone;
   the blocker for a fully operational fresh merchant.
2. **No genuine cross-tenant vulnerability** in the twin — the only cross-tenant
   exposure is the declared fixed TiDB mock, now merchant-unreachable with enforcement
   on. Fresh-ID exploit calibration is therefore not demonstrable here.
3. Retained from M2/M3: mozart-sim bank outcomes, RBL-only end-to-end channel, the
   API monolith is a stub.

## Production reachability

**UNKNOWN**, exactly as in M2/M3. Nothing demonstrated in this captured twin is
translated to production impact.

## Recommendation for the next architecture family

Resolve the fresh-merchant terminal-processing ceiling first (it gates every
lifecycle proof and any credible fresh-merchant campaign): instrument the payouts-api
`spine.Repo.Update` path to capture the exact bound `id`/changed-column set for a
runtime-provisioned merchant vs a boot-seeded one — the single remaining unknown.
Once a fresh merchant can complete a payout, the **next architecture family to add is
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
