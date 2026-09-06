# M4 contradictions log

Format: C-nnn | claim A (source) | claim B (source) | status | resolution evidence

(none yet)
| C-001 | M3.1 completion report says live-arena enforcement was "restored to 0" (RED_LOOP_MILESTONE3_1_COMPLETION.md) | Accepted `hardened_gateway` gate requires enforcement=1 and the live arena runs with KONG_ENFORCE_ROUTE_POLICY=1 (T01 observation) | resolved | Live state + m3-1-acceptance.json win (source-of-truth order: artifacts > review docs). The report line describes an intermediate state before the second campaign. |
| C-002 | M3.1 acceptance evaluator `historical_integrity` hardcodes M3.1 branch names | On branch milestone-4-direct-reconciliation the same commit checks pass but the gate flips false | resolved | Evaluator-scope limitation (T01 scratch re-evaluation); the M4 gate G03 will re-evaluate commit checks branch-independently. |
