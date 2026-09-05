# pricing-stub substitute contract

## Is this stub used?

**No, not by any client config rendered in this arena** — kept only as a
thin, documented alias. Grounding:

- `payouts/config/default.toml`'s `[ccSdk]` section is the only place a
  "pricing engine" style client (Governor + Charge Collections, via
  `payouts/pkg/ccSdk`) is configured. `findings/28_build_spike_payouts.md`
  ("Required fixes" table) confirms `ccsdk.mock` must be set `true` for a
  local/arena boot to avoid dialing real `governor.razorpay.com` /
  `charge-collections.dev.razorpay.in` hosts — `pkg/ccSdk.Initialize`: when
  `Mock` is true, it builds **only** `NewMockPayoutFeeCalculatorClient()` —
  no HTTP client, no host, ever constructed. `config/templates/payouts.toml.tmpl`
  does not render a `[ccSdk]` section at all (no host to point at this
  stub even if we wanted to), consistent with that finding.
- The pricing figures payouts actually uses in this arena come from
  `payouts/pkg/api/fetch_pricing.go`'s `FetchPricing` client
  (`POST /payouts_service/fetch_pricing_info`), which is a live,
  always-called HTTP client (`[api]` section, points at `monolith-stub`) —
  see `substitutes/monolith-stub/CONTRACT.md`. **`monolith-stub` is the
  real pricing source for this arena, not this stub.**

## What this stub does anyway

Reads the same `seeds/pricing.json` fixture `monolith-stub` uses (mounted
at `/app/seed/pricing.json`, `PRICING_SEED_FILE` env) and answers
`POST /fetch_pricing_info` with `{fee, tax, pricing_rule_id}` — so that if
a future patched payouts binary (or a manual test) ever does dial a
`[ccSdk]`-shaped config pointed at this stub, the numbers it gets back are
consistent with `monolith-stub`'s, not a second, drifting hardcoded table.
Same override precedence as `monolith-stub` (`purpose in
{rzp_fees,refund}` or `fee_type == free_payout` → 0/0).

## Validated

`python3 -m py_compile`; ran on port 18806, curled `fetch_pricing_info`
for M2/NEFT — matches `seeds/pricing.json` (fee 200, tax 36,
`pricing_rule_id: ARENAPLAN000002`).
