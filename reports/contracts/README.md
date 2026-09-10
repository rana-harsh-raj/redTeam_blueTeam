# reports/contracts — service API contracts used by the Payouts Twin

Index of the contracts that substitutes, seeds and verifiers must honour. Substitute-side contracts (what a stub must
implement) live in `TWIN_SPEC/substitute-contracts/`; this directory holds the **real services' inbound contracts** that
substitutes and verifiers call.

| File | Service | Content |
|---|---|---|
| `payouts-service-inbound.md` | Payouts Service (REAL) | route groups, auth per group, key request/response DTOs, cron routes |
| `fts-inbound.md` | FTS (REAL) | `/v1/transfer` create, status routes, ART attempts routes, users/app auth |
| `ledger-inbound.md` | Ledger (REAL) | Journal/Account/LedgerConfig Twirp endpoints, request body, discovery rules |
| `TWIN_SPEC/substitute-contracts/*.md` | substitutes | monolith, kong-lite, mozart-sim, workflow-service, batch, stork, xas/recon/repair, dcs/splitz/pricing/shield |

All line references are against the commits listed in `TWIN_SPEC/architecture.yaml`.
