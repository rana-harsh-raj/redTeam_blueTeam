# Shared brief for graph-expansion discovery agents (2026-09-07)

Project: RazorpayX Payouts functional architecture graph, repo /Users/rana.singh/rzp-payouts-architecture
(working branch/worktree: /Users/rana.singh/rzp-payouts-architecture-expansion, branch graph-expansion-adjacent-domains).
Goal of this pass: expand the graph BEYOND Payouts into adjacent business domains (candidates include, but are
not limited to: Cards, Payroll, Settlements, Pricing, Tax payments/TDS, Vendor payments/Invoices, Current-account
onboarding, Capital/lending, Reporting, Accounting integrations, Statements). Do NOT assume Cards/Payroll are best;
discover from evidence.

## Where the source is (READ-ONLY; never modify these clones)
CLONE ROOT (pristine, shallow clones, ~65 repos):
  /private/tmp/claude-502/-Users-rana-singh/cca012eb-0163-4ad1-8765-4fb6bafddfe7/scratchpad/rzp-payouts-architecture
Repos present there: accounting-integrations admin-dashboard agent-skills alert-rules api authz banking-accounts batch
business-verification-service-sdk-go cfa charge-collections config-proto dashboard data-mcp dcs devstack edge
error-mapping-module findings frontend-x fts go-foundation-v2 goutils governor governor-executor knowledge-base
kube-manifests ledger ledger-sdk markdown-docs memoir mozart payments-upi payout-links payouts pg-sdk proto
python-foundation razorpay-mcp-server recon relay rpc rzpconv security-action self-serve-analytics shield-sdk spinacode
splitz stork tejas terraform-kong ValidX vendor-experience vendor-payments virtual-account workflows x
x-account-statements x-balances x-bank-statement-sync xperience
(`findings/` there is prior-pass notes, not a repo. `api` is the PHP monolith. `x` is the RazorpayX dashboard frontend.
`xperience` is the banking dashboard BFF. `dashboard` is the main merchant dashboard.)
Full org repo listing (252 listable to this identity, name/visibility/pushed/description):
  /private/tmp/claude-502/-Users-rana-singh-rzp-payouts-architecture/97164fe3-dcad-4e09-b144-7971ad3bfa97/scratchpad/gh_repos.txt

## Existing graph artifacts (in /Users/rana.singh/rzp-payouts-architecture/reports/)
PAYOUTS_SERVICE_GRAPH.json (197 nodes/229 edges; node ids like repo:payouts svc:ledger db:ledger-pg q:kafka-fts-status
event:payout.processed ep:POST /v1/payouts role:finance-l1 flag:dcs:payout_workflows), REPOSITORY_INVENTORY.csv,
REPOSITORY_ACCESS_MATRIX.csv, NON_REPOSITORY_ACCESS_MATRIX.csv, ACCESS_DELTA.md, PAYOUTS_CLOSURE.yaml,
PAYOUTS_FLOW_CATALOG.md, EVIDENCE_INDEX.md, UNRESOLVED_QUESTIONS.md. Services already IN the graph: payouts, ledger,
fts, cfa, x-balances, banking-accounts, xperience, x, x-account-statements (xas), validx, payout-links,
vendor-payments, charge-collections, admin-dashboard, virtual-account, frontend-x, accounting-integrations,
api-monolith, edge/kong, authz, stork, mozart, dcs, splitz, shield, governor, asv, ups, vault, workflow, batch, recon,
settlements(stub node), wallet(stub), beam(stub), relay.

## Evidence rules (strict)
- Every claim cites `repo/relative/path:line` and a symbol (function, route, proto message, topic constant, table).
- Separate OBSERVED (seen in file) from INFERRED (deduced) from UNKNOWN. Never present inference as fact.
- Prefer grep/rg over reading whole files. Use `rg -n --no-heading` or `grep -rn`. Skip vendor/, node_modules/, dist/.
- When a referenced service/repo is NOT in the clone root, record the exact probable repo/service name and WHERE
  the name came from (go.mod import path, proto package, kube-manifests dir, spinacode pipeline, Kong upstream,
  CODEOWNERS, doc link), and the likely owning team (CODEOWNERS/README/Slack channel names in code).
- Write your final report as Markdown to the output file given in your task, with sections:
  1) Summary (10 lines max) 2) Findings table(s) with evidence 3) Candidate domain signals (which domains, strength)
  4) Probable repo/service names not in clone root 5) Unknowns. Keep it dense; no filler.
