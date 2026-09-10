# M7 — Integration topology

Canonical branch `milestone-7-shared-ingress-integration` = `twin-m6-complete-domain` (76024ae) + preserved
starting audit + merge of `s2p-acceptance-closure-m7` (tag on 4505e62; evaluated closure commit d875910; merge
base with the Payouts line 2613f38 = `assurance-m5-autonomous-discovery`). Nothing generated was merged by hand:
the Source-to-Pay graph patch is consumed by `scripts/m7/s2p_part.py` and the functional graph, inventory, recipes
and canonical snapshot are regenerated from source by `make m7-graph`.

## Runtime shape (compose project `env2_compose`, 98 containers = 93 M6 + api-ingress + 4 S2P)

```
                 host (curl container on rzp-arena; kong-lite host bridge unchanged)
                          |
   merchant key / dashboard session / app secret + X-Razorpay-Account / admin token
                          v
     +--------------- api-ingress :8080  (sub:api-ingress, CONTRACT_FAITHFUL_REPLACEMENT) ---------------+
     |  identity + tenant authority, Route::$internalApps allow-list, merchant-scoped ownership records,  |
     |  idempotency, audit/correlation, tag-back store, SourceUpdater relay                               |
     +--------------------------------------------------------------------------------------------------+
        | cred.API + passport                 ^ GET fund_accounts_internal (X-Razorpay-Account = merchant)
        v                                     |
     payouts-api :9400 (REAL) ----------------+---- POST payouts_service/* -> api-ingress -> monolith-stub (pass-through)
        |  workers (25) -> FTS -> mozart-sim -> processed -> payout_source_updater -> payouts_service/source_update
        v                                                                               |
     ledger / x-balances / cfa (REAL)                                                    v (source_type tax_payments)
                                                                               api-ingress relay -> vp-source /_replica/status
     s2p-internal (rzp-s2p-internal, internal:true):                                    ^
        s2p-vp-source (pinned vendor-payments slice, ACTUAL_SOURCE_RUNNING) --boundary--+  (alias of api-ingress)
        s2p-kafka (Redpanda: add-tds-entry), s2p-mysql (source migrations), s2p-redis
```

`payouts [api] host = http://api-ingress:8080/v1` (was monolith-stub). `[razorx] host`, x-balances -> api and FTS
`update_fts_fund_transfer` still target monolith-stub directly (unchanged M6 seams). batch-sim posts bulk creates to
the ingress as the `batch` application (`BATCH_SIM_UPSTREAM_MODE=monolith`); per-row approve/reject still go to PS
directly (`PS_DIRECT_URL`). workflow-engine's terminal callbacks go to PS directly, which is what the source
prescribes (`payouts/pkg/workflow/workflow_create.go:20-26,286-316`: service `payouts_live`, path
`/v1/payouts/payouts_internal/{id}/approve`).

## Selected shared surface (25 routes, `substitutes/api-ingress/contract/routes.json`)

| Role | Routes (Route.php names) | Upstream |
|---|---|---|
| merchant (private) | payout_create, payout_fetch_by_id, payout_fetch_multiple, payout_cancel | PS `/v1/payouts*` |
| dashboard (proxy) | payout_create_with_otp, payout_approve, payout_reject | PS `/v1/payouts`, `/v1/payouts/payouts_internal/{id}/approve|reject/` |
| batch (proxy, app `batch`) | payout_bulk_create | PS `/v1/payouts/bulk` |
| internal apps | payout_create_internal, payout_create_on_internal_contact, payout_fetch_by_id_internal, payout_cancel_internal, payout_approve_internal, payout_reject_internal (workflows) | PS |
| internal apps (ingress-owned records) | fund_account_get_internal, fund_account_list_internal, fund_account_create_internal, contact_get_internal, contact_list_internal, contact_create_internal, banking_accounts_list_internal, payout_update_tax_payment_id, vendor_payment_verify_otp | ingress SQLite |
| admin | admin_get_free_payouts_attributes, payout_reject_admin_bulk | PS admin / reject |
| payouts_service pass-through | `payouts_service/*`, `internal/merchants/*`, on_hold_slas_internal, actor_info_internal, users_internal, internal_balances_queued, update_fts_fund_transfer, banking_account_statement/payout_update, payouts/purposes | monolith-stub (unchanged M6 substitute) |

## Overlap resolution (gate M7-08)

| Responsibility | M6 monolith-stub | S2P local boundary (`runtime/boundary/server.py`) | Now |
|---|---|---|---|
| GET fund_accounts_internal/{id} | served, owner-less (ROUTES) | served for the fixed tax FA | **api-ingress only**; stub route retired (`monolith-stub/server.py` ROUTES comment), boundary not started in the arena |
| GET contacts_internal, banking_accounts_internal, POST verify-otp, POST internalContactPayout, PATCH tax-payment-id | — | served | **api-ingress only** in the integrated arena |
| payouts_service/* callbacks, internal/merchants, pricing, credits, FTS relay | served | — | stub, reached through the ingress pass-through (unchanged behaviour, one extra hop) |

**Remaining local boundary routes:** none in the integrated arena. The standalone Source-to-Pay replica
(`DOMAIN_REPLICAS/source_to_pay/docker-compose.yml`) still starts its own `monolith-boundary` for its own
acceptance (`clean-run.py`, gate M7-05 post-integration proof); those 6 contract routes + 3 replica-only controls
(`/_evidence`, `/_control/contact`, `/_control/seed-source-payout`) exist only there. Reason: the replica's
acceptance is a namespaced, self-contained proof that must not depend on the arena. The replica-only controls are
replaced in the arena by the ingress control plane (`/_ingress/evidence`, contact registration through the real
internal routes, a real originating payout instead of a seeded one).

## Merge record

| Item | Value |
|---|---|
| Merge base | 2613f383297c7c3b5c0831c26c5c7472494b6e7b |
| Merged tag | s2p-acceptance-closure-m7 -> 4505e62dce58561793429c62174c2941c97f6eb0 |
| Merge commit | 7505783fc692b5f2cdd7fe8b4fa64aa76bc713d0 (parents 020480e, 4505e62) |
| Conflicts | none (S2P side adds `DOMAIN_REPLICAS/source_to_pay/**`, `reports/source-to-pay-replica/**`, `reports/expansion/**`) |
| Post-merge fix | `RED_LOOP/surface/m6_acceptance.py` M6-11 accepts a milestone branch descending from the M6 tag (17a3246); M6 re-evaluated 15/15 |

## Graph lanes added

`reports/domain/parts/m7-ingress.json` (ingress, identities, trust boundaries, ownership edges, families
shared-ingress / cross-domain-s2p / source-to-pay-tds) and `parts/zz-s2p-namespace.json` (projection of the
27,994-node Source-to-Pay patch: services, workers, topics/queues, datastores, 18 mandatory runtime nodes,
tax-payment states). Lane precedence: ingress lane (170) upgrades the api-monolith routes it serves from
graph_only to high_fidelity_replacement; the S2P namespace lane (90) never overrides a Payouts node.
