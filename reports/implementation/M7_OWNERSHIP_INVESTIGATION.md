# M7 — Beneficiary (fund-account) ownership investigation

Question carried over from M6 (defect D-7, `journey:beneficiary-fund-accounts/tenant_isolation`, retained as a
reproducible FAIL): Merchant A could create a payout using Merchant B's fund account, because the substituted
monolith record returned by `GET /fund_accounts_internal/{id}` carried no owner. This document records what the
pinned sources say, what the twin now does, and how the previous result is classified. The previous evidence is
preserved unchanged (`reports/implementation/m6-journeys.json` at tag `twin-m6-complete-domain`, evidence bundle
`journey-beneficiary-fund-accounts-tenant_isolation.json` of run `m6-journeys-20260908T015704Z`).

Pinned sources: `api` 2d665f918b60e917ec92be648fb5816d247f72b1, `payouts` 4bf3dbf9239feadea6d65ca90c893a988e116173,
`cfa` d488558e162c08e9fa04dff005f27d846ce88ad2, `vendor-payments` 20c4f4d59970471067388afea8b1d65ac39ee126.

## 1. Where fund-account ownership is created

| Layer | Evidence | Finding |
|---|---|---|
| API monolith | `api/app/Models/FundAccount/Entity.php` (merchant relation, `merchant_id` column via `PublicEntity`), `api/app/Models/FundAccount/Service.php:433,480` (`findByPublicIdAndMerchant` on contact/customer at create) | A fund account is created for the authenticated merchant; the row carries `merchant_id`. |
| CFA (contacts & fund accounts service) | `cfa/internal/fund_accounts/model.go`, `cfa/internal/contacts/service.go` (M6 seed generator `build_cfa` uses `owner_map[fa] -> merchant`) | CFA documents carry the merchant; the M6 seed generator already computed the owner for the CFA seed but did NOT write it into the monolith substitute's seed (`seeds/generated/monolith/fund_accounts.json` had `merchant_id` on 36 of 196 records: only the provisioner-added ones). |
| Payouts service | `payouts/internal/app/fundAccountCache/core.go:199`, `:694` (`cacheKeyFundAccount` = merchant + fund-account id) | Payouts never stores an owner of its own; it caches the monolith's answer **per merchant**. |

## 2. Where merchant ownership is stored and enforced

- `api/app/Models/FundAccount/Service.php:210-212`:
  `fetch($id)` -> `$this->entityRepo->findByPublicIdAndMerchant($id, $this->merchant, $input)`.
- `api/app/Base/RepositoryFetch.php:1431-1441`: `findByPublicIdAndMerchant` -> `verifyIdAndStripSign` -> `findByIdAndMerchant`
  (query scoped by `merchantId`); miss -> `ArchivedCore::throwInvalidIdException` -> `BadRequestException(BAD_REQUEST_INVALID_ID)`
  -> HTTP 400 "The id provided does not exist" (`api/app/Error/PublicErrorDescription.php:65`).
- The merchant on an internal-app request comes only from `X-Razorpay-Account`
  (`BasicAuth::appAuth` -> `checkAndSetAccountScope`, `BasicAuth.php:1145-1200, 2578-2604`), never from the body.
- Payouts sends exactly that header: `payouts/pkg/api/fetch_fund_account.go:439` `request.Header.Set(XRxAccount, req.MerchantID)`,
  with `req.MerchantID` the merchant of the payout being created (`fundAccountCache/core.go:199`).

**Conclusion:** ownership is created in the monolith/CFA record and enforced by the monolith's merchant-scoped
repository lookup on the internal fetch route. The twin's M6 substitute answered that route by id only.

## 3. Does the internal response contain ownership?

No. `api/app/Services/PayoutService/Create.php:521-560 generateFundAccountResponseForPayoutsService` builds the
payouts-facing record from `id, entity, contact_id, account_type, active, batch_id, created_at` plus bank/card/VPA/
wallet details and the contact; there is no `merchant_id`. `toArrayPublic` (other callers) does not expose it either.
The payouts DTO (`pkg/api/fetch_fund_account.go:21-34 FundAccountResponse`) has no owner field. **Ownership is
enforced by scoping the lookup, not disclosed in the response** — which is exactly why an id-only substitute
silently removed the check.

## 4. Which layer is responsible for authorization

| Path | Authorization of the beneficiary | Evidence |
|---|---|---|
| Classic (`POST /v1/payouts`, private auth, monolith proxy) | Monolith `fundAccountPayout` -> `createPayoutViaPayoutsService` forwards to PS; PS resolves the beneficiary through `GET fund_accounts_internal` with the caller's merchant -> monolith scopes by merchant | `Payout/Service.php:562-700`, `Payout/Processor/Base.php:597-610`, `fundAccountCache/core.go:199` |
| Classic, older monolith flow (`createPayoutViaMicroservice`) | Monolith fetches the fund account itself, merchant-scoped, and ships `fund_account_info` to PS | `Payout/Processor/Base.php:5150-5175`, `FundAccount\Core::fetchFundAccountForPayoutService($merchantId)` |
| Direct / current account | Same create route; the *account number* is additionally scoped: payouts `TranslateAccountNumberToBalanceId` resolves within the merchant (`core.go:437`) — and the ingress-level control in `journey:shared-ingress/direct` shows a foreign account number refused | same as classic + `payouts/internal/app/payouts/core.go:437` |
| Batch (`payouts/bulk`) | PS bulk create resolves each row's beneficiary through the same cache/monolith path; merchant from the proxy passport / `X-Entity-Id` | `payout_internal_routes_with_passport.go:23`, `request_context.go:18-51` |
| Internal apps (`payouts_internal`, `internalContactPayout`) | Monolith `fundAccountPayoutOnInternalContact` (`Payout/Service.php:348`) itself calls `findByPublicIdAndMerchant` before forwarding; PS re-checks the contact type through the fetch | `Payout/Service.php:330-362`, `payouts/internal/app/contact/type.go:57-83` |
| Admin | Admin routes act on existing payouts (reject/attributes); no beneficiary resolution | `payout_admin_routes.go` |
| Tax-payment tag-back (`PATCH payouts_internal/{id}/tax-payment-id`) | **No merchant scoping in the pinned source**: `PayoutsDetails/Core.php:378-406 updateTaxPayment` -> `Repository::updatePayoutDetails([$payoutId])` (`whereIn payout_id`), returns SUCCESS whether or not a row matched | `api/app/Models/PayoutsDetails/{Core,Repository}.php` |

So every create path that reaches the beneficiary passes through the monolith's merchant-scoped lookup; the paths
differ only in *who* performs the first lookup (monolith or PS), not in whether it is scoped.

## 5. Unknown production configuration or route selection

- Whether a merchant's `POST /v1/payouts` reaches PS through the monolith (`isMonolithProxyForPayoutServiceMerchant`,
  live-mode migration flag per balance) or directly from Edge (`payout_routes.go:104-120` "Edge-direct requests arrive
  with no service BasicAuth") is a live routing/flag decision. On both paths the beneficiary is still resolved through
  the monolith's internal route by PS, so the ownership check is reached either way **unless** PS already holds the
  record in its per-merchant cache; the cache key includes the merchant, so a cached record of B cannot serve A.
- Composite payouts (`fund_account` object instead of `fund_account_id`) and the VA-to-VA path are outside the
  selected surface.
- The tag-back route's lack of scoping is a pinned-source fact; whether another production layer compensates is unknown.

## 6. What the twin now does (highest-fidelity source-supported behaviour)

The shared ingress (`ENV2_COMPOSE/substitutes/api-ingress`) serves `GET /v1/fund_accounts_internal/{id}` exactly like
`FundAccount/Service.php fetch()`: it resolves the record **within the calling tenant** from explicit ownership
records (`resources(kind,id,merchant_id,...)`, seeded from the generator, which now writes `merchant_id` on every
generated fund account, or created by the internal routes) and answers 400 `BAD_REQUEST_INVALID_ID` otherwise. The
response keeps the PS-facing shape (no owner field). Payouts' `[api] host` points at the ingress, so PS's beneficiary
fetch goes through it; the monolith-stub's owner-less route is retired. `internalContactPayout` performs the monolith's
own scoped lookup before forwarding. The tag-back route follows the source (id-only) and records the calling tenant
and app so the evidence shows who tagged what.

Result: `journey:shared-ingress/tenant_isolation` — A's create with B's fund account is refused (4xx from PS after the
ingress denied the lookup to tenant A), no payout row, no balance movement on either side, the denial is in the
ingress audit for tenant A; and the M6 journey `beneficiary-fund-accounts/tenant_isolation` now PASSes on the same
arena. Direct/current-account misuse of another merchant's account number is refused by PS itself.

## 7. Classification of the previous M6 result

Classification: **REPRODUCED_AND_RESOLVED_BY_MISSING_INGRESS_CHECK**

Basis: the M6 failure reproduced exactly the mechanism predicted in §2-§3 (an id-only substitute for a route whose
source is merchant-scoped); the pinned source enforces ownership at that route on every selected path; restoring
the source's scoping at the ingress removes the failure with no change to Payouts. This is a twin-substitution
defect, not a production finding. Production reachability of the *mechanism* would require a production route in
which PS resolves a beneficiary without the monolith's scoped lookup; none was found in the pinned sources, and
the live routing/flag state that would settle it is unknown (PU-1, PU-2 in `M7_PRODUCTION_UNKNOWNS.md`). It is
**not** called a production vulnerability.

Residual, separately recorded: the tag-back route is unscoped in the pinned source (PU-3) — a source observation,
reproduced faithfully, not a twin defect and not a production claim.
