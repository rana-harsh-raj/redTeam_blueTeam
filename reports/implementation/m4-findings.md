# M4 verified findings

Findings that meet the mandate's bar for a verified issue: an observable unauthorized effect or
violated business invariant, deterministic evidence, a negative control, and clean-state reproduction
with fresh IDs. Discovered inside the authorized local synthetic twin (`env2_compose`). Production
reachability is assessed separately and, where it cannot be established from the pinned source corpus,
is stated as UNKNOWN. These are NOT claims about production behaviour.

## F-M4-001 — Cross-tenant free-payout attribute disclosure (IDOR)  [VERIFIED in twin; production reachability UNKNOWN]

- **Class:** Broken object-level authorization (cross-tenant information disclosure).
- **Route:** `GET /v1/payouts/free_payout/{balance_id}` (on the public merchant allowlist per T06).
- **Source:** accepted payouts clone, `GetFreePayoutAttributes(ctx, balanceId)` (`freePayout/core.go:678-684`)
  resolves the banking account by `balance_id` alone with **no merchant scoping**; the handler does not
  re-check that the authenticated merchant owns that `balance_id`.
- **Observable effect (deterministic):** attacker merchant A, authenticated as A (A's own key; A supplied
  no valid secret for B), requested B's `balance_id` and received **HTTP 200 carrying merchant B's
  distinct free-payout counter value** (a planted canary), not A's own value and not a canned default.
- **Negative control:** with B holding the default counter value, A's identical request returned no
  distinctive value → causality established. The scoped `GET /v1/payouts/{payout_id}` route DENIES the
  analogous cross-tenant fetch (400 "id does not exist"), so the gap is specific to this route.
- **Independent reproduction:** reproduced on a second, independently provisioned fresh A/B pair via the
  attacker Broker's gateway path (different mechanism, no model in the loop) — the originating path did
  not self-certify.
- **Evidence:** `reports/implementation/m4-direct-e2e-replay.json` (candidate H-D3), run dirs under
  `RED_LOOP/runs/m4-replay-*` (booleans/ids only; no raw canary/secret values stored).
- **Production reachability: UNKNOWN.** In the twin, kong-lite mints the passport and the handler performs
  no ownership check. Whether a production edge/monolith ingress scopes `balance_id` to the caller before
  this handler is out of the pinned corpus and was not established. Do NOT present this as a confirmed
  production vulnerability.
- **Recommended remediation (source):** scope `GetFreePayoutAttributes` to the authenticated merchant
  (join banking account → merchant and reject a `balance_id` the caller does not own), or enforce that
  ownership at the gateway before the handler. Twin source is preserved unpatched (mandate: do not patch
  source to go green); this finding is recorded, not silenced.

## Non-findings (recorded for honesty)

- **H-D4 — cross-merchant fund_account_id on create → BENIGN.** Quoting merchant B's `fund_account_id`
  on create returns 200 but the payout binds to the CALLER (A) and draws A's balance; B's balance is
  untouched. Money isolation holds. The 200-vs-400 is a CFA-substitute fidelity gap, not a payouts bug.
- **F-T10-1 — statement-first guard route asymmetry → SOURCE-FAITHFUL.** The `VerifyPayoutFailedTransaction`
  guard fires on the FTS-direct `/transfer_status_webhook` route (HTTP 500, payout stays `initiated`) but
  the monolith-relay route moved the same link-before-terminal payout to `failed`. Faithful to accepted PS
  source (`HandlePayoutStatusUpdateViaFTS` has no such guard, T02 §8/T05); the relay endpoint is the
  monolith-stub substitute and the production monolith's own upstream guard is out of corpus. Not a
  verified issue (no unauthorized effect; a route-scoped guard difference).
