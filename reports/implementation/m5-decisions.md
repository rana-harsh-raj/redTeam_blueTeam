# M5 decisions

Key engineering decisions for "Autonomous discovery on maker-checker workflows",
with rationale.

## D1 — Reconstruct the engine, do not stub
The real Workflows/Cadence engine repo was unavailable. Rather than extend the
thin `workflow-sim` stub, we built a durable, DB-backed decision engine
(`ENV2_COMPOSE/substitutes/workflow-engine`) that reproduces the interface,
identity, policy, approval, retry, idempotency, concurrency, and callback
semantics. Labelled a RECONSTRUCTION (`FIDELITY.md`), grounded in real payouts
source (`m5-source-map.md`). We deliberately did not chase Cadence internals.

## D2 — SQLite + stdlib HTTP, loopback only
Portability + true restart recovery + no new heavy container in a
memory-constrained arena (host ~11.65 GiB, ~67 containers). The DB file is the
single source of truth; undelivered callbacks resume on boot. Everything binds
127.0.0.1.

## D3 — Invariants as marked checks; defects as surgical patches
Every invariant is a `# [CHECK:...]` in `core.py`, so a benchmark mutant
disables exactly one property (`mutations.py`) while the valid workflow keeps
working. The builder asserts each anchor still matches, so core drift fails the
build loudly instead of producing a non-defective "mutant".

## D4 — Strict plane separation for a blind benchmark
Campaign workers receive ONLY a public manifest (base URL + their own actor
tokens). The admin token, policy, answer key, and verifier live on the control
plane. Environments are shuffled with opaque ids, so a worker cannot tell fixed
from mutant except by behaviour.

## D5 — Model-originated candidates, independent adjudication
The Director surfaces raw runtime observations (including "an action a correct
engine must forbid nonetheless succeeded") but never labels a defect. The MODEL
forms the hypothesis and raises the candidate. A separate hidden verifier then
reproduces it twice with fresh IDs, runs a negative control against the fixed
reference, and consults the answer key for root-cause agreement. A fixed-env
candidate is structurally unconfirmable (answer key + negative control both fail).

## D6 — Exploration discipline to avoid the M4 failure
The M4 loop resent the same invalid request many times. Here the Director dedups
hypotheses (semantic key) and experiments (env|tool|org|role|status|error
signature); a repeated signature is suppressed and the worker is pushed to a new
probe. A two-phase turn (propose→interpret) forces the model to conclude each
experiment before starting another.

## D7 — Multi-model, cross-provider reproduction
Primary kimi-k3 (Moonshot) + gpt-5.5 (OpenAI) are decorrelated providers; glm-5p2
adds diversity. Every environment is probed by every model, so a finding
reproduced by ≥2 independent model families is recorded as such, on top of the
verifier's own mechanical reproduction.

## D8 — Standalone benchmark vs full-arena integration
The maker-checker business journey and the campaign run against the standalone
engine + a `payout-sink` callback receiver, so callback identity / idempotency /
retry are observable without the full 66-container arena. The create + callback
surfaces are byte-compatible with the real payouts routes, so the same engine
can be wired into the arena; full-arena integration is documented in
`m5-known-limits.md`.
