# shield-stub substitute contract

Substitutes Shield's payout rule-evaluation endpoint. Grounded in
`shield-sdk/rule_evaluation/service.go` (`getShieldEvaluateEndpoint`),
`shield-sdk/constants/rule_evaluation.go`, and
`shield-sdk/models/evaluate/payout_evaluate.go`. Payouts' call site:
`payouts/pkg/shield/shield_payout_rule_evaluate.go`
(`PrepareShieldPayoutEvaluateRequest`). Config:
`payouts/config/default.toml` `[shield]` (`host`, `timeout=200ms`,
`[shield.auth] username/password`).

## Endpoint

`POST /v1/rules/evaluate/payout` — from `getShieldEvaluateEndpoint("payout")`
= `PaymentRuleEvaluationEndpoint ("/v1/rules/evaluate") + "/" + entityType`.

Auth: HTTP Basic, `[shield.auth]` username/password pair from `secrets/`.

## Request / Response

Request: `{merchant_id, entity_type, entity_id, rulesets, skip_default_execution,
input: {PayoutID, Mode, ..., AccountNumber, MerchantID, Purpose, ...}}`
(`PayoutEvaluateRequest`, `payout_evaluate.go`). Response:
`{action, triggered_rules, status_code}` — `action` is `allow | block | review`.
Payouts only branches on `action == "block"`; `allow`/`review` are both
treated as non-blocking (confirmed gap, `findings/26:1296-1304`). `purpose
== "rzp_fees"` payouts never call Shield at all (payouts config), so the
stub should never see a request for such a payout if config is wired
correctly.

## Scripted rules — fixture-driven, not hardcoded

`seeds/shield/rules.json`, mounted at `/app/seed/rules.json`
(`SHIELD_RULES_FILE` env). Rules are evaluated top-to-bottom, first match
wins, `default` (`allow`) if none match:

1. `input.AccountNumber` ends with `9999` → `block` (task-specified
   scripted block, matches on ANY merchant).
2. `input.MerchantID == ARENAM00000001` **and** `input.Purpose ==
   "vendor_payments"` → `block`.
3. `input.MerchantID == ARENAM00000003` → `review`.
4. else → `allow`.

Matcher supports `eq`, `ends_with`, `starts_with`, `contains`, and one level
of `and` nesting (`{field, op, value, and: {field, op, value}}`) — sufficient
for the fixture above; extend `_match_one()` in `server.py` if a future
scenario needs `or`/deeper nesting.

## Latency injection

`SHIELD_LATENCY_MS` env var (default 0, task-specified name) delays every
response by that many milliseconds before evaluating rules. Payouts' own
timeout is 200ms (`[shield] timeout = 200`); setting this above 200
reproduces Shield's real fail-open contract from the payouts side — the
stub itself always eventually answers with a real verdict; fail-open is
payouts-side behaviour on timeout, not something the stub forces.

## Health

`GET /health` → 200 (unauthenticated, per shared `_common/base_stub.py`).

## Validated

`python3 -m py_compile`; ran `server.py` on port 18803, curled all 4
scenarios (account-suffix-9999 block, M1+vendor_payments block, M3 review,
default allow) — all matched the expected `action`/`triggered_rules`.
