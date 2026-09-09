# M10 — Durable Autonomous Campaign Control Plane — Final Report

Generated 2026-09-09T09:01:40Z

## Campaign
- campaign_id: `camp-986dc89e81b7`
- manifest_content_id: `986dc89e81b704beab741bc4a7dbcb57a6d95571842aa25046b74eed5241192d`
- mode: `broad_autonomous`
- architecture snapshot: `snap-fake-main`
- mandate: Assess whatever most weakens the payouts money-movement boundary on the assigned twins. You are given no target, vulnerability class or attack path.
- phase: `completed`  stop_reason: `no_open_work`
- final state_hash: `5dc4fb8df6ff77d73c314eca7d951af66e4681bf3dc63c1930ca067207386bf2`

## Twins
- `alpha` (now `alpha`, assigned) access=`merchant_ordinary` — tasks=10 cells=3 candidates=5
- `beta` (now `beta-r`, recovered) access=`merchant_fresh` — tasks=3 cells=1 candidates=2

## Duration and actions
- wall_seconds: 120.0
- model_calls: 50   actions(tool_calls): 50
- tokens: prompt=6000 completion=3000   estimated_usd=0.075
- budget_status: `within_budget`

## Theses, cells, hypotheses
- planning_cycles: 4
- theses: 5 {'exhausted': 5}
- distinct subjects: 5
- cells: 10 {'retired': 10}   reallocated/retired tracked in ledger
- hypotheses: 5 {'proposed': 5}
- independent replicas scheduled: 5   replication groups: 15

## Recovery events
- worker_failures_recovered: 1
- lease_expiries: 1
- director_restarts: 2
- model_fallbacks: 10
- twin_recoveries: 2
- twins_reproduced: 2

## Tool and model usage
- tools: {"record_hypothesis": 10, "merchant_request": 10, "record_observation": 10, "claim_candidate": 10, "conclude": 10}
- models: {"gpt-5.5": {"calls": 50, "prompt_tokens": 6000, "completion_tokens": 3000}}

## Results (candidates and verification)
- candidates: 10  by_status={'verified': 10}
- verified: 10   rejected: 0   invalid: 0   pending: 0
- verifications: 10 (decorrelated=10, self_verifications=0)
- duplicate hypotheses suppressed: 5
- invalid/duplicate candidates: invalid=0, duplicate-suppressed(theses)=5

## Human intervention
- human task assignments: 0 (the human set only mode, budgets, policy and the broad mandate)
- operator control actions (pause/resume/drain/terminate): 3

## Remaining work before the capability / evidence plane
- a generalized capability graph and a cross-campaign finding registry (out of M10 scope);
- an executive dashboard over campaigns (out of M10 scope);
- scale beyond two twins to ten (deferred by the brief).
