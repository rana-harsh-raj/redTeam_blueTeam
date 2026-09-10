# Lane brief — RazorpayX Payouts Twin fidelity investigation (2026-09-05)

You are one investigation lane for the "Payouts Twin" specification. Your job is to
VERIFY AND CORRECT the existing reports against the now-readable repositories, and to
describe production behaviour precisely enough that an engineer can (a) decide whether
the current local twin reproduces it and (b) implement a faithful substitute or seed.

## Locations (all read-only unless stated)
- Pristine shallow clones (depth 1, current master): `REPOS=/private/tmp/claude-502/-Users-rana-singh/cca012eb-0163-4ad1-8765-4fb6bafddfe7/scratchpad/rzp-payouts-architecture/<repo>`
  Available: api authz terraform-kong edge kube-manifests spinacode mozart stork dcs splitz governor
  recon proto goutils workflows batch payouts fts ledger cfa x-balances banking-accounts
  x-account-statements x-bank-statement-sync ledger-sdk shield-sdk config-proto rpc alert-rules
  knowledge-base dashboard frontend-x x admin-dashboard payout-links payments-upi devstack
  error-mapping-module go-foundation-v2 python-foundation relay virtual-account vendor-payments.
- Existing reports: `/Users/rana.singh/rzp-payouts-architecture/reports/` — start from the lane
  findings named in your task (`raw-findings/NN_*.md`), `ARCHITECTURE_DELTA.md`, `UNRESOLVED_QUESTIONS.md`,
  `SYNTHETIC_FIXTURE_SPEC.md`, `EFFECTIVE_CONFIG_GAPS.md`. They were partly written BEFORE the repos
  were readable: treat them as hypotheses to check, not as truth.
- Current local twin ("arena", Env 2): `/Users/rana.singh/rzp-payouts-architecture/ENV2_COMPOSE/`
  (docker-compose.yml, config/templates, config/generate.py, seeds/, substitutes/<name>/server.py,
  verifier/, scripts/). Bring-up log of what it took: `reports/raw-findings/31_env2_bringup_notes.md`.
  Verifier outcome: `reports/ENV2_BUILD_STATUS.md`.

## Hard rules
1. READ-ONLY on every repository clone and on ENV2_COMPOSE. Do not modify, do not create branches,
   do not push, do not run services. `git` in read mode only. No network calls except, where a task
   explicitly allows it, `gh api` GET calls for commit history of razorpay repos.
2. Never copy real credentials, tokens, account numbers, IFSC+account pairs, VPA, phone numbers,
   real merchant ids/emails, production hostnames with credentials, bank payloads with customer data,
   or production DB rows. If you meet one, record only "type + redacted file:line". Synthetic values
   from repo test fixtures may be quoted when they are obviously fake (e.g. `10000000000000`), and say so.
3. Every claim carries provenance as `repo/path:line` (or `path:line-line`). Mark each claim
   CONFIRMED (you read it in source), INFERRED (derived, say from what), or UNKNOWN (not derivable;
   name the exact missing artifact, likely owner, and the minimal retrieval request).
4. Do not silently present an assumption as production behaviour. Where the existing report says X
   and source says Y, record "CORRECTION: report <file> §.. says X; source <path:line> says Y".
5. Write ONLY your assigned output file under `/Users/rana.singh/rzp-payouts-architecture/reports/fidelity/raw/`.
   Nothing else on disk (scratch notes may go under
   `/private/tmp/claude-502/-Users-rana-singh/cca012eb-0163-4ad1-8765-4fb6bafddfe7/scratchpad/lanes/<your-lane>/`).

## Required structure of your output file
1. `## Scope and sources read` — repos/paths actually opened.
2. `## Production behaviour` — the facts, organised by path/flow, each with provenance. Include
   exact route strings, HTTP methods, auth mechanism, request/response field lists (name, type,
   required, allowed values), state transitions (from → to, guard, actor), config keys (name, type,
   default, where read), queue/topic names, retry/timeout/cadence values.
3. `## Corrections to existing reports` — table: report, claim, what source says, evidence.
4. `## Twin comparison` — table per component/path in your lane:
   `component | production mechanism | twin mechanism (file) | verdict REAL / CONTRACT-FAITHFUL / REPRESENTATIVE / INCORRECT / MISSING | what differs | does it control auth, tenant, approval, routing, balance/ledger, payout/transfer state, idempotency, reversal, repair, or merchant-visible state?`
5. `## Recommendation: real vs substitute` — for each component in your lane: run REAL (repo buildable?
   external deps?) or SUBSTITUTE; if substitute, the exact protocol + behavioural contract it must
   implement (routes, bodies, status codes, error shapes, timing, side effects, callbacks).
6. `## Synthetic data` — every field/record family your lane touches:
   `family/table | field | source evidence | type+length | constraints | allowed values | FK/relationships | state rules | distribution matters? | generation rule | EXACT / REPRESENTATIVE / ASSUMED`.
   Prefer evidence from migrations, ORM models, validators, enums, DTOs/protos, test fixtures, ITF/E2E fixtures.
7. `## Cannot be derived from repositories` — list: missing artifact, why it matters (which behaviour is
   not production-representative without it), likely owner/team, minimal precise request
   (schema-only DDL for table X / sanitized config export / contract fixture / cron definition), and
   whether a schema-only or sanitized export is sufficient.
8. `## Fidelity tier verdicts` — one line per material component: REAL / CONTRACT-FAITHFUL SUBSTITUTE /
   REPRESENTATIVE SUBSTITUTE / UNKNOWN-BLOCKED, with the single most important reason.

Be exhaustive on facts and terse on prose. Prefer tables. Quote code only where the exact
expression matters (guards, maps, defaults). Line numbers are mandatory.
