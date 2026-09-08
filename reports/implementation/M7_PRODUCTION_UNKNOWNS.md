# M7 — Production unknowns (exact)

Everything below is unknown from the pinned sources and the twin; none of it is inferred. The machine-readable
list is `reports/architecture/M7_CANONICAL_SNAPSHOT.json` -> `production_unknowns` (PU-1..PU-8) and the
Source-to-Pay baseline `DOMAIN_REPLICAS/source_to_pay/spec/unresolved-production-unknowns.yaml`.

| Id | Topic | What is unknown | What the twin does instead | Affected components |
|---|---|---|---|---|
| PU-1 | Payout create route selection | Whether a merchant's `POST /v1/payouts` is served Edge->PS directly or Edge->API->PS (live routing + `isMonolithProxyForPayoutServiceMerchant` per-balance migration flag; PS `PROXY_CUTOVER_DECISION` / `X-Payouts-Service-Proxy`). | Both entry points exist: kong-lite (Edge->PS) and api-ingress (API->PS); journeys drive the latter. | route:api-monolith/POST payouts, sub:api-ingress, sub:kong-lite |
| PU-2 | Beneficiary ownership on the PS-direct path | Whether any production configuration lets PS resolve a beneficiary without the monolith's merchant-scoped `fund_accounts_internal` hop (composite create, cache pre-warm, VA-to-VA). | Ownership enforced at the ingress on every selected path; PS cache is per merchant. | GET fund_accounts_internal/{id}, table:api-ingress/resources |
| PU-3 | Tax-payment tag-back scoping | `PayoutsDetails/Core.php updateTaxPayment` is id-only in the pinned source; whether another production layer scopes it to the merchant. | Source semantics reproduced; caller app + tenant recorded in evidence. | PATCH payouts_internal/{id}/tax-payment-id |
| PU-4 | Internal-application authentication | Production apps may authenticate via Passport-issued app auth (`BasicAuth::isValidPassportForAppAuth`) or the key-blank secret branch; live app secrets/config unknown. | Key-blank appAuth branch with fresh random per-boot app secrets (vendor_payments fixed to the S2P replica's synthetic constant). | identity:ingress-app-secret |
| PU-5 | Dashboard session and OTP | Real sessions (dashboard) and OTPs (Raven) and their policies. | Synthetic single-use OTP store and session tokens issued by the ingress control plane. | identity:dashboard-session, identity:dashboard-otp |
| PU-6 | SourceUpdater transport & vendor-payments auth | Live value of `source_updater_sns_enabled`/Splitz experiment; Twirp authentication of vendor-payments `PayoutStatusChange`; retry policy. | HTTP `payouts_service/source_update` (pinned default) relayed by the ingress to the source driver's callback adapter, no Twirp auth. | route:vendor-payments PayoutStatusChange, queue:prod-api-payout-source-updater-live |
| PU-7 | Source-to-Pay deployment | Deployed process set, replica counts, Kafka partitioning/retention/DLQ, MySQL/Redis topology, flags, secrets, bank destination values, the upstream producer of `add-tds-entry`. | One Redpanda broker, one MySQL, one Redis, synthetic bank constants, the fixture producer. | svc:s2p/* |
| PU-8 | Batch approval hop | Whether Batch's per-row approve/reject reaches PS through the monolith `bulk_approve` route or a PS-direct path in production. | batch-sim: create via the ingress (monolith proxy route), approve/reject PS-direct (collapsed hop). | sub:batch-sim |
| PU-9 | Monolith dashboard approve for PS payouts | `approveFundAccountPayout` for PS-owned payouts goes through `handlePayoutServicePayoutForWorkflowAction` (Splitz `WORKFLOW_ACTION_WITH_DB_DUAL_WRITE_PAYOUTS_SERVICE`); live variant unknown. | Ingress dashboard approve/reject forwards to PS `payouts_internal/{id}/approve|reject/` (`Workflow.php` client shape). | payout_approve, payout_reject |
| PU-10 | Purposes, permissions, rate limits | `Route::$routePermission` (dashboard role permissions), throttling, merchant purpose lists served by `payouts/purposes`. | Not modelled (pass-through to the stub where PS asks). | route:api-monolith/* |

Not fabricated anywhere in M7: bank settlement, challan, tax filing, `paid` status, accounting-payout events,
production credentials, production hosts (arena networks are `internal: true`; preflight scans every rendered
config for production hostnames), customer data.

Closing any row requires evidence from the owning production systems: deployed manifests and flag snapshots,
Edge/Kong route configuration, Passport application registrations, authenticated integration tests against the
real API-monolith/Payouts boundary, vendor-payments Twirp auth policy, broker/datastore operating parameters.
