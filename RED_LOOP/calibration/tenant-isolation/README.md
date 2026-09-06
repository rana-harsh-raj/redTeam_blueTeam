# Tenant-isolation calibration (Milestone 3.1)

A **private, calibration-only** fresh-identity regression/fixed pair that validates
the harness's reproduce-and-judge machinery on a genuine, causally-attributable
cross-tenant disclosure effect. It is **never** counted as a Razorpay finding
(classification is `CALIBRATION_PASS` / `CALIBRATION_REDISCOVERY`, never
`ACCEPTED_NEW_FINDING`), and it is invisible to the open campaign.

This supersedes the earlier note that calibration was "not implemented". It is
implemented, fresh-ID, and executed; see `reports/implementation/m31-calibration-freshid.json`.

## What it is

A self-contained stdlib HTTP service (`service.py`) modelling a tenant-scoped
resource store, with two profiles chosen by the calibration-only env flag
`CALIB_PROFILE` (default **fixed**, the safe behaviour):

- **regression** — an object-level authorization flaw (IDOR): a resource is
  returned by id *without* checking that the authenticated caller owns it, so a
  freshly generated attacker tenant reads a freshly generated victim tenant's
  resource and its hidden canary.
- **fixed** — ownership is enforced; the same cross-tenant fetch returns `404`
  with no victim field. This is the causal **negative control**.

It does **not** touch the real Kong gateway, Payouts, Ledger, FTS or CFA; it binds
to loopback on a dedicated port and is torn down after each phase.

## Components

- `generator.py`  — mints a fresh attacker/victim/resource/canary/evidence-namespace
  per run (`ARENACALATK*` / `ARENACALVIC*`; no `pout_1234`, no M1/M2 merchant).
- `service.py`    — the two-profile calibration endpoint (loopback only).
- `replay.py`     — the **ID-free semantic bundle**, its resolver, and a
  deterministic (no-model) requester.
- `oracle.py`     — the deterministic judge oracle + machine-readable impact
  certificate. The oracle, not any model, decides PASS/FAIL.
- `run_calibration.py` — orchestrates the gateway-independent gates:
  fresh fixture, deterministic replay across **two disjoint** fixtures, and the
  fixed-profile negative control.
- `cross_provider.py`  — the **different-provider** reproducer (default `gpt-5.5`,
  decorrelated from the Anthropic primary) via the LiteLLM gateway; needs
  `LITELLM_BASE_URL` / `LITELLM_API_KEY`.

## Isolation boundary

- The calibration code lives **outside** the primary corpus roots
  (`.local/twin-repos/accepted/{payouts,fts,ledger,cfa,x-balances}`), so the
  primary agent's `code_read`/`code_search` cannot reach it.
- The service listens on 19099/191xx; the attacker Broker only reaches Kong
  (`127.0.0.1:18080`) and denies `/_arena`. The ports are disjoint.
- The requester/reproducer receives only its attacker credential (injected), the
  public route/method, the resolved target reference and the endpoint — never the
  victim credential, the canary, the profile, or the oracle rules.
- Machine proof: `reports/implementation/m31-campaign-isolation.json`.

## Run

```
# gateway-independent (fixture + deterministic replay + negative control)
python3 RED_LOOP/calibration/tenant-isolation/run_calibration.py

# different-provider replay (requires the LiteLLM env)
python3 RED_LOOP/calibration/tenant-isolation/cross_provider.py
```

## Fidelity limitation (preserved from the pricing fix)

The provisioner seeds pricing plans without separate rule IDs, so the monolith
substitute returns a **plan ID** as `payouts.pricing_rule_id` (`CHAR(14)`). This
is a documented fidelity limitation, not production rule-identity parity. The core
`RowsAffected` handling is intact; the bounded 14-char plan mapping and the
fail-closed reservation in `RED_LOOP/red_loop/pricing_ids.py` are retained.
Offline check: `python3 -m unittest RED_LOOP.tests.test_pricing_ids`.
