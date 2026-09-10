# Milestone 3 fidelity statement and declared additions

Layered on the frozen Twin v1.0 (`twin-v1.0`, runtime freeze
`219ca48bbb5e51db9347b335ac0a50241656045b`) and the Milestone-2 additions
(`m2-fidelity.md`). The 76 base deviations
(`ENV2_COMPOSE/config/declared-deviations.yaml`) and the Kafka expected failures
(`TWIN_SPEC/expected-failures.yaml`) still hold. Each M3 addition is additive and,
where it touches the shared runtime, default-off so the frozen declared scope is
behaviour-neutral unless activated.

## M3 additions and their fidelity

- **M3-DEV-001 merchant-edge route policy** (`ENV2_COMPOSE/substitutes/kong-lite/route_policy.py`,
  wired in `server.py`). A source-derived allow/deny classifier
  (`RED_LOOP/registry/merchant-gateway-routes.json`, derived from
  `payouts/internal/routing/router/*.go`). When `KONG_ENFORCE_ROUTE_POLICY=1`,
  kong-lite exposes only the PUBLIC merchant routes and returns 404 for
  internal/admin/workflow routes that share the `/v1/payouts` prefix — as a real
  public Kong edge (which never registered them) does. `cred.API` is injected only
  for public payouts-api routes, never for the `/v1/balances` xbalances read
  surface, and client identity headers (`x-merchant-id`, `x-entity-id`) are
  stripped. **Default `0` = frozen Twin v1.0 behaviour (whole-prefix proxy)**, so
  the frozen verifier is behaviour-neutral; the red campaign runs with it `1`.
  Fidelity limit: the allowlist is a conservative, fail-closed reading of the
  router groups; a few ambiguous internal-app create variants
  (`internal_contact_payout`, `payouts_internal` POST) are denied at the ordinary
  merchant edge even though source places them in a passport group.

- **M3-DEV-002 Milestone-2 misattribution corrected.** The canned cross-tenant
  data seen in M2 (`fund_account_id fa_slitfa12345678`, `merchant_id
  10000000000000`, `utr 528226169544`) is NOT monolith-stub. It is the payouts-api
  in-tree TiDB mock `cannedTidbPayoutEntity()` (`payouts/internal/app/tidb/mock.go`),
  enabled by `[wda] mock = true` (`config/templates/base/payouts/arena.toml`; a
  declared deviation). It is reachable ONLY via the internal `fetch_multiple`
  route. M3 leaves the mock in place (declared low-fidelity deviation) but makes it
  **unreachable from the merchant edge** via M3-DEV-001, so it can no longer
  produce false cross-tenant evidence for a merchant.

- **M3-DEV-003 per-campaign fresh namespaces + hidden canaries**
  (`RED_LOOP/red_loop/provisioner.py`). Each campaign mints fresh unique canary
  strings inside control-merchant payouts through the normal merchant API, and the
  attacker receives a fresh idempotency namespace so no prior campaign's
  payout/idempotency record becomes a later precondition. Canary values and victim
  resource ids are JUDGE-ONLY. Fidelity limit / declared: a **fully fresh funded
  attacker merchant** is implemented (`provision_funded_merchant`, 7 datastores
  from a live-verified recipe; authenticates and its fund account resolves) but
  full payout PROCESSING for a brand-new merchant needs more seed parity (all four
  ledger sub-accounts + pricing rows), so campaigns default to the **fixture
  attacker with a fresh idempotency namespace + fresh victim canaries**, and the
  manifest records this fallback. This is the one place M3 does not fully reach the
  "fresh funded attacker" goal; it is disclosed, not hidden.

- **M3-DEV-004 judge canary oracle + minimum-evidence admission**
  (`RED_LOOP/red_loop/judge.py`). The judge independently scans the attacker's
  captured responses for victim canaries and runs a deterministic admission gate
  before any expensive reproduction: confidentiality claims need a real victim
  value/canary (a canned-mock template alone is rejected as NEEDS_HIGHER_FIDELITY);
  integrity claims need a real target resource with an attacker-attributed
  transition or an invariant violation (a 200 is never enough); availability needs
  baseline + measured degradation. Control/evidence-plane only; never reveals
  victim data to red.

- **M3-DEV-005 context compiler + model-failure handling + progress lifecycle +
  resume** (`RED_LOOP/red_loop/context.py`, `campaign.py`, `llm.py`, `state.py`).
  Context is compiled from durable records each turn (no full-transcript resend);
  blank/content-filtered turns are detected and recovered (smaller packet, then a
  distinct approved model — no safeguard bypass); the 120-turn ceiling is replaced
  by progress/stagnation/conclusion completion with a labelled high emergency
  backstop; the runner can pause/kill/restart/resume from durable JSONL.

- **M3-DEV-006 private calibration lane** (`RED_LOOP/surface/m3_calibration.py`).
  Uses the M3-DEV-001 toggle as a safely-isolated vulnerable/fixed pair to exercise
  the reproduce-and-judge machinery on a genuine unauthorized observable effect
  with a causal negative control and a different-provider reproducer. Time-isolated
  from the open campaign, marked CALIBRATION_ONLY, never counted as a finding, and
  it restores the enforcement-on profile at the end.

## Retained fidelity ceilings (unchanged from M2, still declared)
- Bank outcomes are mozart-sim (amount-keyed / forced); RBL is the only
  end-to-end channel; the API monolith is a stub. Findings hinging on real bank or
  monolith internals hit a fidelity ceiling (`known_gaps.yaml`).
- The `[wda] mock` remains true (declared) but is now merchant-unreachable.
- Fresh-funded-attacker processing parity (see M3-DEV-003) is the new declared
  ceiling introduced this milestone.

## Production reachability
Unchanged and UNKNOWN where it was UNKNOWN in M2. "Demonstrated in this captured
twin" is never translated to "affects production."
