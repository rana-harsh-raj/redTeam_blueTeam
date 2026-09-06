# Project RedGrid — Milestone 2 completion report (RED_LOOP)

Status: **READY FOR NEXT EXPANSION**

Branch `milestone-2-red-loop` off `twin-v1.0` (freeze `219ca48`). One sustained autonomous campaign ran; the deterministic judge accepted no finding (an honest outcome); the reproduce-and-judge machinery was validated cross-provider; the Workflow/Stork/channel surface and access separation all pass; and the frozen verifier suite still passes 26/26.

## 1. Baseline and branch
- Frozen release used: `twin-v1.0` → commit `78def24` (records freeze
  `219ca48bbb5e51db9347b335ac0a50241656045b`). The tag, the runtime freeze
  commit, historical acceptance evidence, retained failed runs, and the company
  repository clones are UNCHANGED.
- Milestone 2 branch: `milestone-2-red-loop`, created from `twin-v1.0`. All M2
  additions are layered on top and declared (`RED_LOOP/registry/m2-fidelity.md`).

## 2. What was built (`RED_LOOP/`)
One lean autonomous exploitation loop with three separated roles and a
deterministic judge. See `RED_LOOP/DESIGN.md`.
- Primary red agent (only explorer), independent reproducer (different model),
  deterministic judge (authoritative).
- Attacker boundary broker: kong-lite only, fixed injected identity, `/_arena/*`
  and service planes denied, floods capped, every call recorded.
- Read-only source corpus (real service source only; harness/answer-keys/AGENTS
  files excluded), durable append-only campaign state, host-side judge evidence
  reader, known-gap registry, calibration self-test.

## 3. LiteLLM inventory and model selection
- Inventory inspected from the gateway `/v1/models`: 27 models (see
  `RED_LOOP/registry/model-selection.json`).
- Primary red agent: `claude-opus-4-8` (Anthropic). `claude-opus-5` was
  preferred but is budget-capped on this key (HTTP 429 `budget_exceeded`) AND
  content-filtered on the adversarial mandate, so the strongest USABLE Claude was
  selected.
- Independent reproducer: `gpt-5.5` (OpenAI) — different provider/family →
  provider diversity YES.
- Utility (non-authoritative): `gpt-5.4-mini`.
- Selection used gateway metadata + a short capability smoke only; no
  public-internet research. Cost is UNKNOWN (no price table exposed).

## 4. Access separation (what the red agent could / could not touch)
See `RED_LOOP/registry/access-policy.json`.
- Code context: read-only real service source (payouts/fts/ledger/cfa/x-balances)
  + their contracts/migrations. Excluded: `ENV2_COMPOSE` (substitutes, verifier
  answer keys, gate internals, seeds, secrets), `TWIN_SPEC/expected-failures.yaml`,
  `RED_LOOP` control plane + known-gap registry, `reports/`, and AGENTS/CLAUDE files.
- Runtime: one ordinary merchant (`ARENAM00000001`, no elevated roles) via
  kong-lite only. Denied: databases, Kafka, Docker, service creds, `/_arena/*`
  controls, mozart/stork/DCS/Splitz controls, internal payouts routes, the judge,
  the known-gap registry, victim keys, and any prod/staging/DevStack credential.
- Boundary-crossing attempts are refused and logged as `boundary_violation`.

## 5. Product-surface additions (fidelity in `RED_LOOP/registry/m2-fidelity.md`)
- workflow-sim: create→pending→approve/reject via the REAL Payouts callbacks;
  approval never automatic (control-plane `/_arena/decide`). Default inert.
- Stork duplicate control (`STORK_DUPLICATE_TERMINAL`, default off = frozen).
- Channel DOWN/UP via the real `partner_bank_health` route; shared-vs-direct as a
  second routing configuration; optional runtime icici provisioning.
- All control-plane only, never attacker-reachable, additive and default-off.

## 6. Campaign
One sustained logical campaign ran end to end (campaign id
`camp-20260906T051731Z-e43dfd`; records under `RED_LOOP/runs/`, git-ignored and
hash-indexed).

| Metric | Value |
|---|---|
| Primary model | claude-opus-4-8 |
| Turns | 120 (stopped at the max-turns ceiling) |
| Wall time | ~19 min (1138 s) |
| Primary model calls | 120 |
| Tokens | 13.28M prompt (context re-sent each turn) / 42k completion; cost UNKNOWN |
| Brokered merchant requests | 29 |
| Boundary violations (refused + logged) | 3 |
| Hypotheses / observations | 7 / 4 |
| Candidates claimed | 3 distinct |
| Human intervention during the campaign | none (observation only) |

The agent chose every target and hypothesis itself, with no hint. Its autonomous
lines of investigation included: the create/balance/idempotency flow; whether the
body `merchant_id` overrides the passport identity when the context id is empty;
whether internal/admin payout routes (`manual_action`, `fetch_multiple`,
`rzp_fees_payout`) are reachable through the merchant gateway; driving a
`processed` payout back to `initiated`; and enumerating other tenants'
payouts. The three refused boundary attempts were
`/v1/internal/non_terminal_payouts/...`, `/v1/admin/merchant-configuration`, and
`/v1/banking_account_statement`.

## 7. Candidate ledger, verdicts and reproduction
All three candidates were adjudicated INSUFFICIENT_EVIDENCE by the deterministic
judge (`deterministic_impact_confirmed = false`). They are one root-cause cluster:
"internal/admin payout routes are reachable via the gateway, enabling a
cross-tenant read/privileged action." Invalid-claim rate: 3/3 (every claim was
unsupported by system state); duplicate rate: 3 claims → 1 root cause.

Why the judge rejected them, confirmed by direct datastore reads after the campaign:
- The agent cited reading a payout `pout_1234` belonging to merchant
  `10000000000000`. The payouts database contains **no** such payout and **no**
  such merchant. `GET /v1/payouts/fetch_multiple?id=pout_1234&auth_type=proxy`
  returns **canned monolith-stub data** (`fa_slitfa12345678`, a SLIT-fixture id),
  and `?merchant_id=ARENAM00000002` returns the same stub record with **empty
  fields**. No real cross-tenant data was ever exposed.
- `manual_action` returned 200 but did **not** mutate state: the agent's own
  payout `pout_TYdFjwV8EsmchZ` remained `processed` with only its normal
  transition log. `rzp_fees_payout` returned 400/500. No real write occurred.

The enabling condition (internal routes reachable with the injected service
BasicAuth) is a **substitute fidelity artifact**: kong-lite injects `cred.API`
for the whole `/v1/payouts` prefix and the monolith-stub returns canned data for
unknown ids. A production edge separates public merchant routes from internal
admin routes and would not inject `cred.API` for them. This is classified
TWIN_SPECIFIC / NEEDS_HIGHER_FIDELITY, not a production finding, and production
reachability is UNKNOWN and not claimed.

No candidate reached ACCEPTED_NEW_FINDING, so no clean-state reproduction was
required. Because there was no judge-positive candidate, the harness-owned
calibration self-test ran instead (kept separate from the finding count): the
reproducer model **gpt-5.5** (different provider) created a fresh payout from a
clean start and the judge deterministically confirmed it
(`machinery_ok = true`, payout `pout_TYdcKfhX4xgZEi` under the attacker merchant).
This proves the reproduce-and-judge path works cross-provider.

**No accepted finding is an honest outcome here.** The loop worked: an autonomous
code-aware agent found a plausible authz lead, attempted to exploit it, and the
deterministic judge correctly refused to bless it because system state showed no
real unauthorized effect.

## 8. New-surface acceptance and twin health
**M2 product-surface acceptance: 5/5 pass**
(`reports/implementation/m2-surface-acceptance.json`):

| Test | Result | Evidence |
|---|---|---|
| access_separation | PASS | `/_arena/mint` and `/twirp/*` refused; injected identity cannot be swapped |
| workflow_approve | PASS | M3 workflow payout reaches `pending`, then leaves it via the REAL approve callback |
| workflow_reject | PASS | M3 workflow payout reaches `pending`, then `rejected` |
| channel_down | PASS | after `channel_control down`, an M2 direct payout enters `on_hold`; restored on `up` |
| stork_duplicate | PASS | terminal `payout.processed` delivered twice with identical event_id + body |

Activating the workflow slice required, and this is declared: adding
`payout_workflows` to M3's monolith feature list, repointing `[workflow] host`
to workflow-sim, and fixing workflow-sim to emit 14-char workflow/config ids
(the payouts columns are CHAR(14)). These are M2 fixture/substitute changes, not
core-source edits.

**Twin health retained: the frozen verifier suite passes 26/26** (52 s) against
the arena with the M2 surface present (workflow-sim up, stork duplicate restored
to default off). No unexplained process exit, timeout, or skip, and no outside
network packet. A full empty-volume gate re-run was not performed because it
would destroy the campaign/M2 runtime; instead the frozen `twin-v1.0` tag and
commit are unchanged and remain re-runnable at the freeze commit, and every M2
addition is additive and default-off (`WORKFLOW_HOST` defaults to the frozen dead
address `http://127.0.0.1:1`; `STORK_DUPLICATE_TERMINAL` defaults to `0`), so the
frozen declared-scope behaviour is preserved whenever M2 is inactive. No core
vulnerability was added or repaired to shape results.

## 9. Harness weaknesses observed
- The gateway content filter intermittently blanks the primary model's output on
  large adversarial contexts (`finish_reason=content_filter`), wasting turns; the
  loop treats these as no-action and continues. A future run should trim context
  more aggressively and/or soften mandate phrasing while keeping the objective.
- Only three fully-funded fixtures exist, so campaigns share actors (isolation by
  time-window + balance snapshot); fresh funded identities are not provisioned.
- The judge reads state via `docker exec` (control-plane), distinct from the
  frozen verifier-container path.

## 10. UNKNOWN production-reachability facts
- Whether any production merchant is on the Kafka status route (Splitz
  PYGTRQEzO39PfB, fail-closed) — UNKNOWN, per the frozen expected-failures.
- Production rollout/config of any surface exercised here is UNKNOWN and is not
  inferred. "Demonstrated in this captured twin" is never translated to "affects
  production."

## 11. Recommended next expansion
**Higher-fidelity merchant gateway (public vs internal route separation and
scoped service-credential injection).**

This is driven directly by the campaign, not a roadmap: the agent's strongest and
most-repeated hypotheses (3/3 candidates) all hinged on the kong-lite substitute
injecting `cred.API` across the entire `/v1/payouts` prefix and the monolith-stub
returning canned data for unknown ids. That fidelity ceiling is exactly what
prevented adjudicating a real authorization finding either way. The next
expansion should model kong's real route allowlist so internal/admin routes
(`fetch_multiple`, `manual_action`, `rzp_fees_payout`, `payouts_internal/*`) are
unreachable through the merchant edge as in production, and/or serve real
merchant-scoped data so cross-tenant attempts can be genuinely tested rather than
hitting stub responses. Until then, authorization findings on the internal-route
surface cannot be honestly adjudicated in this twin.

## 12. Safety confirmation
No production, staging, DevStack, public-internet, or Daytona action occurred.
No real customer data or credentials were used. The frozen tag and runtime
commit are unchanged. Failed/negative results are preserved.
