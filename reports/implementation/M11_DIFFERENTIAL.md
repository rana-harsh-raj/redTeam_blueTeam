# M11 differential -- real trust path vs substitutes

Generated 2026-09-09T18:55:09.187953+00:00 by scripts/m11/differential.py

| run | dir | pass | fail | blocked | expected_failure |
|---|---|---|---|---|---|
| real | `/Users/rana.singh/.twin-factory/instances/m11-real/journeys/run-20260909T173309Z,/Users/rana.singh/.twin-factory/instances/m11-real/journeys/run-20260909T182343Z,/Users/rana.singh/.twin-factory/instances/m11-real/journeys/run-20260909T184745Z,/Users/rana.singh/.twin-factory/instances/m11-real/journeys/run-20260909T185350Z` | 113 | None | 10 | 2 |
| substitute | `/Users/rana.singh/.twin-factory/instances/m11-sub/journeys/run-20260909T173302Z,/Users/rana.singh/.twin-factory/instances/m11-sub/journeys/run-20260909T182343Z` | 107 | 5 | 12 | 1 |

journeys compared: 125, identical: 108, with differences: 17, OPEN (unclassified) differences: 0

| journey | real | substitute | differences |
|---|---|---|---|
| journey:accounting/async_state | PASS (8/8) | PASS (8/8) | identical |
| journey:accounting/cancel_or_reverse | PASS (7/7) | PASS (7/7) | identical |
| journey:accounting/failure | PASS (8/8) | PASS (8/8) | identical |
| journey:accounting/idempotency | PASS (5/5) | PASS (5/5) | identical |
| journey:accounting/success | PASS (6/6) | PASS (6/6) | identical |
| journey:approval-workflow/async_state | PASS (11/11) | PASS (11/11) | identical |
| journey:approval-workflow/cancel_or_reverse | PASS (12/12) | PASS (12/12) | identical |
| journey:approval-workflow/concurrency | PASS (10/10) | PASS (10/10) | identical |
| journey:approval-workflow/failure | PASS (13/13) | PASS (13/13) | identical |
| journey:approval-workflow/idempotency | PASS (13/13) | PASS (13/13) | identical |
| journey:approval-workflow/maker_checker | PASS (12/12) | PASS (10/10) | source-supported correction |
| journey:approval-workflow/n_of_m | PASS (17/17) | PASS (19/19) | source-supported correction |
| journey:approval-workflow/success | PASS (14/14) | PASS (14/14) | identical |
| journey:async-workers/async_state | PASS (4/4) | PASS (4/4) | identical |
| journey:async-workers/failure | PASS (6/6) | PASS (6/6) | identical |
| journey:async-workers/idempotency | PASS (8/8) | PASS (8/8) | identical |
| journey:async-workers/success | PASS (8/8) | PASS (8/8) | identical |
| journey:beneficiary-fund-accounts/async_state | PASS (10/10) | PASS (10/10) | identical |
| journey:beneficiary-fund-accounts/cfa_api | PASS (18/18) | PASS (18/18) | identical |
| journey:beneficiary-fund-accounts/failure | PASS (5/5) | PASS (5/5) | identical |
| journey:beneficiary-fund-accounts/idempotency | PASS (7/7) | PASS (7/7) | identical |
| journey:beneficiary-fund-accounts/success | PASS (15/15) | PASS (15/15) | identical |
| journey:beneficiary-fund-accounts/tenant_isolation | PASS (2/2) | PASS (2/2) | identical |
| journey:bulk-payouts/async_state | BLOCKED (0/0) | BLOCKED (0/0) | identical |
| journey:bulk-payouts/cancel_or_reverse | BLOCKED (0/0) | BLOCKED (0/0) | identical |
| journey:bulk-payouts/failure | BLOCKED (0/0) | BLOCKED (0/0) | identical |
| journey:bulk-payouts/idempotency | BLOCKED (0/0) | BLOCKED (0/0) | identical |
| journey:bulk-payouts/success | BLOCKED (0/0) | BLOCKED (0/0) | identical |
| journey:cross-domain-s2p/async_state | BLOCKED (0/0) | BLOCKED (0/0) | identical |
| journey:cross-domain-s2p/failure | BLOCKED (0/0) | BLOCKED (0/0) | identical |
| journey:cross-domain-s2p/idempotency | BLOCKED (0/0) | BLOCKED (0/0) | identical |
| journey:cross-domain-s2p/success | BLOCKED (0/0) | BLOCKED (0/0) | identical |
| journey:direct-payouts/async_state | PASS (11/11) | PASS (11/11) | identical |
| journey:direct-payouts/cancel_or_reverse | PASS (2/2) | PASS (2/2) | identical |
| journey:direct-payouts/duplicate | PASS (3/3) | PASS (3/3) | identical |
| journey:direct-payouts/failure | PASS (7/7) | PASS (7/7) | identical |
| journey:direct-payouts/failure_pending | PASS (7/7) | PASS (7/7) | identical |
| journey:direct-payouts/idempotency | PASS (8/8) | PASS (8/8) | identical |
| journey:direct-payouts/pending | PASS (7/7) | PASS (7/7) | identical |
| journey:direct-payouts/statement_first | PASS (3/3) | PASS (3/3) | identical |
| journey:direct-payouts/success | PASS (12/12) | PASS (12/12) | identical |
| journey:failure-reversal-cancellation/async_state | PASS (10/10) | PASS (10/10) | identical |
| journey:failure-reversal-cancellation/cancel_or_reverse | PASS (5/5) | PASS (5/5) | identical |
| journey:failure-reversal-cancellation/failure | PASS (7/7) | PASS (7/7) | identical |
| journey:failure-reversal-cancellation/idempotency | PASS (9/9) | PASS (9/9) | identical |
| journey:failure-reversal-cancellation/pending | PASS (14/14) | PASS (14/14) | identical |
| journey:failure-reversal-cancellation/success | PASS (11/11) | PASS (11/11) | identical |
| journey:failure-reversal-cancellation/webhook | PASS (9/9) | PASS (9/9) | identical |
| journey:fetch-list/async_state | PASS (10/10) | PASS (10/10) | identical |
| journey:fetch-list/failure | PASS (7/7) | PASS (7/7) | identical |
| journey:fetch-list/idempotency | PASS (8/8) | PASS (8/8) | identical |
| journey:fetch-list/success | PASS (17/17) | PASS (16/16) | source-supported correction |
| journey:idempotency-retries/async_state | PASS (8/8) | PASS (8/8) | identical |
| journey:idempotency-retries/duplicate | PASS (5/5) | PASS (5/5) | identical |
| journey:idempotency-retries/failure | PASS (8/8) | PASS (8/8) | identical |
| journey:idempotency-retries/idempotency | PASS (6/6) | PASS (6/6) | identical |
| journey:idempotency-retries/retry | PASS (13/13) | PASS (13/13) | twin adaptation |
| journey:idempotency-retries/success | PASS (7/7) | PASS (7/7) | identical |
| journey:on-hold/async_state | PASS (8/8) | PASS (8/8) | identical |
| journey:on-hold/cancel_or_reverse | PASS (8/8) | PASS (8/8) | identical |
| journey:on-hold/failure | PASS (6/6) | PASS (6/6) | identical |
| journey:on-hold/idempotency | PASS (14/14) | PASS (14/14) | identical |
| journey:on-hold/success | PASS (12/12) | PASS (12/12) | identical |
| journey:pricing-free-payouts/accounting | PASS (9/9) | PASS (9/9) | identical |
| journey:pricing-free-payouts/async_state | PASS (21/21) | PASS (21/21) | identical |
| journey:pricing-free-payouts/cancel_or_reverse | EXPECTED_FAILURE (4/4) | EXPECTED_FAILURE (4/4) | identical |
| journey:pricing-free-payouts/failure | PASS (10/10) | PASS (10/10) | identical |
| journey:pricing-free-payouts/free_payout | PASS (9/9) | PASS (9/9) | identical |
| journey:pricing-free-payouts/idempotency | PASS (6/6) | PASS (6/6) | identical |
| journey:pricing-free-payouts/success | PASS (8/8) | PASS (8/8) | identical |
| journey:queued-low-balance/async_state | PASS (10/10) | PASS (10/10) | identical |
| journey:queued-low-balance/cancel_or_reverse | PASS (9/9) | PASS (9/9) | identical |
| journey:queued-low-balance/failure | PASS (7/7) | PASS (7/7) | identical |
| journey:queued-low-balance/idempotency | PASS (8/8) | PASS (8/8) | identical |
| journey:queued-low-balance/success | PASS (14/14) | PASS (14/14) | identical |
| journey:scheduled-payouts/async_state | PASS (8/8) | PASS (8/8) | identical |
| journey:scheduled-payouts/cancel_or_reverse | PASS (8/8) | PASS (8/8) | identical |
| journey:scheduled-payouts/cancel_via_dashboard | PASS (7/7) | PASS (7/7) | identical |
| journey:scheduled-payouts/failure | PASS (3/3) | PASS (3/3) | identical |
| journey:scheduled-payouts/idempotency | PASS (7/7) | PASS (7/7) | identical |
| journey:scheduled-payouts/success | PASS (10/10) | PASS (10/10) | identical |
| journey:shared-ingress/admin | PASS (6/6) | PASS (6/6) | identical |
| journey:shared-ingress/approval | PASS (9/9) | PASS (9/9) | identical |
| journey:shared-ingress/async_state | PASS (9/9) | PASS (9/9) | identical |
| journey:shared-ingress/batch | BLOCKED (0/0) | BLOCKED (0/0) | identical |
| journey:shared-ingress/direct | PASS (5/5) | PASS (5/5) | identical |
| journey:shared-ingress/failure | PASS (9/9) | PASS (9/9) | identical |
| journey:shared-ingress/idempotency | PASS (6/6) | PASS (6/6) | identical |
| journey:shared-ingress/restart | PASS (6/6) | PASS (6/6) | identical |
| journey:shared-ingress/success | PASS (7/7) | PASS (7/7) | identical |
| journey:shared-ingress/tenant_isolation | PASS (6/6) | PASS (6/6) | identical |
| journey:shared-payouts/accounting | PASS (8/8) | PASS (8/8) | identical |
| journey:shared-payouts/async_state | PASS (10/10) | PASS (10/10) | identical |
| journey:shared-payouts/cancel_or_reverse | PASS (13/13) | PASS (13/13) | identical |
| journey:shared-payouts/concurrency | PASS (5/5) | PASS (5/5) | identical |
| journey:shared-payouts/duplicate | PASS (8/8) | PASS (8/8) | identical |
| journey:shared-payouts/failure | PASS (13/13) | PASS (13/13) | identical |
| journey:shared-payouts/idempotency | PASS (6/6) | PASS (6/6) | identical |
| journey:shared-payouts/pending | PASS (7/7) | PASS (7/7) | identical |
| journey:shared-payouts/restart | PASS (13/13) | PASS (13/13) | identical |
| journey:shared-payouts/success | PASS (12/12) | PASS (12/12) | identical |
| journey:source-updates/async_state | PASS (12/12) | PASS (12/12) | identical |
| journey:source-updates/failure | PASS (8/8) | PASS (8/8) | identical |
| journey:source-updates/idempotency | PASS (7/7) | PASS (7/7) | identical |
| journey:source-updates/success | PASS (9/9) | PASS (9/9) | identical |
| journey:trust-path/api_key_auth | PASS (13/13) | PASS (7/7) | twin adaptation |
| journey:trust-path/bas_lookup | PASS (6/6) | PASS (3/3) | twin adaptation |
| journey:trust-path/cross_merchant_denial | PASS (10/10) | FAIL (9/10) | source-supported correction; twin adaptation |
| journey:trust-path/dashboard_session | PASS (9/9) | PASS (8/8) | twin adaptation |
| journey:trust-path/fund_account_ownership | PASS (8/8) | PASS (8/8) | identical |
| journey:trust-path/idempotency | PASS (7/7) | FAIL (6/7) | source-supported correction |
| journey:trust-path/identity_propagation | PASS (8/8) | FAIL (3/5) | twin adaptation |
| journey:trust-path/internal_app_auth | PASS (8/8) | PASS (7/7) | twin adaptation |
| journey:trust-path/maker_checker | PASS (13/13) | FAIL (11/12) | source-supported correction |
| journey:trust-path/payouts_ext_direct | PASS (6/6) | BLOCKED (0/0) | twin adaptation |
| journey:trust-path/restart_cache_invalidation | EXPECTED_FAILURE (8/9) | BLOCKED (0/0) | twin adaptation |
| journey:trust-path/route_authorization | PASS (12/12) | FAIL (8/9) | source-supported correction; twin adaptation |
| journey:trust-path/s2s_identity | PASS (7/7) | PASS (1/1) | twin adaptation |
| journey:trust-path/shield_rules | PASS (8/8) | PASS (5/5) | twin adaptation |
| journey:webhooks/async_state | PASS (7/7) | PASS (7/7) | identical |
| journey:webhooks/duplicate | PASS (7/7) | PASS (7/7) | identical |
| journey:webhooks/failure | PASS (12/12) | PASS (12/12) | identical |
| journey:webhooks/idempotency | PASS (5/5) | PASS (5/5) | identical |
| journey:webhooks/success | PASS (11/11) | PASS (11/11) | identical |
| journey:webhooks/webhook | PASS (5/5) | PASS (5/5) | identical |

## Differences, investigated

### journey:approval-workflow/maker_checker
- substitute_only_check `a_distinct_checker_CAN_approve` (real=None, substitute=True) -- **source-supported correction**: razorpay/workflows has no maker != checker rule (internal/action, internal/workflow state machine; only a DCS-gated auto-approve when the creator's role equals the checker role). The M5 reconstruction's 403 separation_violation was an assumption. The driver asserts the real behaviour in the real variant and the reconstruction's in the substitute variant.
- real_only_check `a_second_approval_on_the_terminal_workflow_is_refused_409` (real=True, substitute=None) -- **source-supported correction**: razorpay/workflows has no maker != checker rule (internal/action, internal/workflow state machine; only a DCS-gated auto-approve when the creator's role equals the checker role). The M5 reconstruction's 403 separation_violation was an assumption. The driver asserts the real behaviour in the real variant and the reconstruction's in the substitute variant.
- real_only_check `engine_org_is_the_merchant(WFE_TENANT_KEY=owner_id)` (real=True, substitute=None) -- **source-supported correction**: razorpay/workflows has no maker != checker rule (internal/action, internal/workflow state machine; only a DCS-gated auto-approve when the creator's role equals the checker role). The M5 reconstruction's 403 separation_violation was an assumption. The driver asserts the real behaviour in the real variant and the reconstruction's in the substitute variant.
- real_only_check `payout.pending_webhook_delivered(StatusToWebhookEventMap PENDING)` (real=True, substitute=None) -- **source-supported correction**: razorpay/workflows has no maker != checker rule (internal/action, internal/workflow state machine; only a DCS-gated auto-approve when the creator's role equals the checker role). The M5 reconstruction's 403 separation_violation was an assumption. The driver asserts the real behaviour in the real variant and the reconstruction's in the substitute variant.
- real_only_check `payout_parked_at_pending_by_the_workflow_engine` (real=True, substitute=None) -- **source-supported correction**: razorpay/workflows has no maker != checker rule (internal/action, internal/workflow state machine; only a DCS-gated auto-approve when the creator's role equals the checker role). The M5 reconstruction's 403 separation_violation was an assumption. The driver asserts the real behaviour in the real variant and the reconstruction's in the substitute variant.
- real_only_check `payouts_stored_the_engine_workflow_id(workflow_entity_map, 14 chars)` (real=True, substitute=None) -- **source-supported correction**: razorpay/workflows has no maker != checker rule (internal/action, internal/workflow state machine; only a DCS-gated auto-approve when the creator's role equals the checker role). The M5 reconstruction's 403 separation_violation was an assumption. The driver asserts the real behaviour in the real variant and the reconstruction's in the substitute variant.
- real_only_check `real_workflow_service_accepts_the_makers_own_approval_(no_separation_rule_in_razorpay/workflows)` (real=True, substitute=None) -- **source-supported correction**: razorpay/workflows has no maker != checker rule (internal/action, internal/workflow state machine; only a DCS-gated auto-approve when the creator's role equals the checker role). The M5 reconstruction's 403 separation_violation was an assumption. The driver asserts the real behaviour in the real variant and the reconstruction's in the substitute variant.
- substitute_only_check `refusal_names_the_separation_rule` (real=None, substitute=True) -- **source-supported correction**: razorpay/workflows has no maker != checker rule (internal/action, internal/workflow state machine; only a DCS-gated auto-approve when the creator's role equals the checker role). The M5 reconstruction's 403 separation_violation was an assumption. The driver asserts the real behaviour in the real variant and the reconstruction's in the substitute variant.
- real_only_check `the_action_trail_records_the_approval_with_the_makers_actor_id` (real=True, substitute=None) -- **source-supported correction**: razorpay/workflows has no maker != checker rule (internal/action, internal/workflow state machine; only a DCS-gated auto-approve when the creator's role equals the checker role). The M5 reconstruction's 403 separation_violation was an assumption. The driver asserts the real behaviour in the real variant and the reconstruction's in the substitute variant.
- substitute_only_check `the_append_only_audit_trail_records_the_approval_with_its_actor_identity` (real=None, substitute=True) -- **source-supported correction**: razorpay/workflows has no maker != checker rule (internal/action, internal/workflow state machine; only a DCS-gated auto-approve when the creator's role equals the checker role). The M5 reconstruction's 403 separation_violation was an assumption. The driver asserts the real behaviour in the real variant and the reconstruction's in the substitute variant.
- real_only_check `the_engine_holds_a_pending_workflow_for_this_payout` (real=True, substitute=None) -- **source-supported correction**: razorpay/workflows has no maker != checker rule (internal/action, internal/workflow state machine; only a DCS-gated auto-approve when the creator's role equals the checker role). The M5 reconstruction's 403 separation_violation was an assumption. The driver asserts the real behaviour in the real variant and the reconstruction's in the substitute variant.
- substitute_only_check `the_maker's_own_approval_is_REFUSED_403_separation_violation` (real=None, substitute=True) -- **source-supported correction**: razorpay/workflows has no maker != checker rule (internal/action, internal/workflow state machine; only a DCS-gated auto-approve when the creator's role equals the checker role). The M5 reconstruction's 403 separation_violation was an assumption. The driver asserts the real behaviour in the real variant and the reconstruction's in the substitute variant.
- substitute_only_check `the_refused_self_approval_left_no_approval_row_in_the_trail` (real=None, substitute=True) -- **source-supported correction**: razorpay/workflows has no maker != checker rule (internal/action, internal/workflow state machine; only a DCS-gated auto-approve when the creator's role equals the checker role). The M5 reconstruction's 403 separation_violation was an assumption. The driver asserts the real behaviour in the real variant and the reconstruction's in the substitute variant.
- substitute_only_check `workflow_is_approved` (real=None, substitute=True) -- **source-supported correction**: razorpay/workflows has no maker != checker rule (internal/action, internal/workflow state machine; only a DCS-gated auto-approve when the creator's role equals the checker role). The M5 reconstruction's 403 separation_violation was an assumption. The driver asserts the real behaviour in the real variant and the reconstruction's in the substitute variant.
- real_only_check `workflow_is_approved_by_that_single_approval` (real=True, substitute=None) -- **source-supported correction**: razorpay/workflows has no maker != checker rule (internal/action, internal/workflow state machine; only a DCS-gated auto-approve when the creator's role equals the checker role). The M5 reconstruction's 403 separation_violation was an assumption. The driver asserts the real behaviour in the real variant and the reconstruction's in the substitute variant.
- substitute_only_check `workflow_still_pending_after_the_refused_self_approval` (real=None, substitute=True) -- **source-supported correction**: razorpay/workflows has no maker != checker rule (internal/action, internal/workflow state machine; only a DCS-gated auto-approve when the creator's role equals the checker role). The M5 reconstruction's 403 separation_violation was an assumption. The driver asserts the real behaviour in the real variant and the reconstruction's in the substitute variant.

### journey:approval-workflow/n_of_m
- substitute_only_check `a_repeat_approval_by_the_same_actor_does_not_reach_the_threshold` (real=None, substitute=True) -- **source-supported correction**: eligible-approver sets and per-actor de-duplication are reconstruction concepts; the real service admits any actor presenting the checker state's role. The real-variant driver records what a repeat approval does instead of assuming it.
- substitute_only_check `an_approver_outside_the_eligible_set_is_refused_403_not_eligible` (real=None, substitute=True) -- **source-supported correction**: eligible-approver sets and per-actor de-duplication are reconstruction concepts; the real service admits any actor presenting the checker state's role. The real-variant driver records what a repeat approval does instead of assuming it.
- real_only_check `repeat_approval_by_the_same_actor_is_refused_or_not_counted_(observed_real_semantics)` (real=True, substitute=None) -- **source-supported correction**: eligible-approver sets and per-actor de-duplication are reconstruction concepts; the real service admits any actor presenting the checker state's role. The real-variant driver records what a repeat approval does instead of assuming it.
- substitute_only_check `still_pending_after_the_duplicate_approval` (real=None, substitute=True) -- **source-supported correction**: eligible-approver sets and per-actor de-duplication are reconstruction concepts; the real service admits any actor presenting the checker state's role. The real-variant driver records what a repeat approval does instead of assuming it.

### journey:fetch-list/success
- real_only_check `bare_(unsigned)_id_is_refused_by_the_monolith_path_400_(PublicEntity::stripSignOrFail)` (real=True, substitute=None) -- **source-supported correction**: an unsigned payout id is refused (400) on the monolith path (PublicEntity::stripSignOrFail); kong-lite forwarded bare ids straight to the Payouts service, which accepts them

### journey:idempotency-retries/retry
- substitute_only_check `advanced_the_scheduled_retry_task_0be93f18` (real=None, substitute=True) -- **twin adaptation**: the check name carries the retry task id of that run (a different id per run); the assertion is the same in both variants and passed in both
- real_only_check `advanced_the_scheduled_retry_task_174820a4` (real=True, substitute=None) -- **twin adaptation**: the check name carries the retry task id of that run (a different id per run); the assertion is the same in both variants and passed in both

### journey:trust-path/api_key_auth
- real_only_check `credential_tags_carry_the_mode_(m~l)` (real=True, substitute=None) -- **twin adaptation**: Kong consumer/credential-store checks exist only in the real variant
- real_only_check `every_refusal_was_answered_by_the_gateway_itself_(no_upstream_call)` (real=True, substitute=None) -- **twin adaptation**: Kong consumer/credential-store checks exist only in the real variant
- real_only_check `kong_consumer_username_is_the_merchant_id_(edge common.lua extract_consumer_details)` (real=True, substitute=None) -- **twin adaptation**: Kong consumer/credential-store checks exist only in the real variant
- real_only_check `one_basic-auth-x_credential_per_key_id_hashed_sha512_(the_plaintext_secret_is_never_stored_or_returned)` (real=True, substitute=None) -- **twin adaptation**: Kong consumer/credential-store checks exist only in the real variant
- real_only_check `the_success_was_proxied_through_the_gateway_(upstream_request-id_echo;_kong.conf_headers=off)` (real=True, substitute=None) -- **twin adaptation**: Kong consumer/credential-store checks exist only in the real variant
- real_only_check `the_wrong-secret_request_never_reached_the_monolith_replacement_(no_ingress_audit_row)` (real=True, substitute=None) -- **twin adaptation**: Kong consumer/credential-store checks exist only in the real variant

### journey:trust-path/bas_lookup
- real_only_check `banking_accounts_row_exists_for_the_Direct_merchant` (real=True, substitute=None) -- **twin adaptation**: Api-Token and businesses/banking_accounts checks exist only in the real variant; the lookup during processing must happen in both
- real_only_check `details_carry_sales_team_and_business_type_(seeded_businesses_row)` (real=True, substitute=None) -- **twin adaptation**: Api-Token and businesses/banking_accounts checks exist only in the real variant; the lookup during processing must happen in both
- real_only_check `real_banking-accounts_returns_the_Direct_merchants_details_200` (real=True, substitute=None) -- **twin adaptation**: Api-Token and businesses/banking_accounts checks exist only in the real variant; the lookup during processing must happen in both

### journey:trust-path/cross_merchant_denial
- result: real=PASS substitute=FAIL -- **twin adaptation**: identical assertions in both variants
- check_outcome `the_denied_fetch_was_evaluated_under_tenant_A_(not_B)` (real=True, substitute=False) -- **source-supported correction**: kong-lite bypasses the monolith replacement, so there is no tenant-scoped ingress audit in the substitute variant; the refusal itself is asserted in both

### journey:trust-path/dashboard_session
- real_only_check `gateway_itself_refused_it` (real=True, substitute=None) -- **twin adaptation**: real-variant-only gateway/workflow-row observations

### journey:trust-path/idempotency
- result: real=PASS substitute=FAIL -- **source-supported correction**: kong-lite (substitute) proxies /v1/payouts straight to the Payouts service and short-circuits X-Payout-Idempotency itself, so the monolith replacement's idempotency state / audit never sees the call; on the real path the gateway forwards to the monolith replacement (Route.php idempotentRoutesConfig) as in production
- check_outcome `the_replay_was_served_from_idempotency_state_without_a_second_upstream_create` (real=True, substitute=False) -- **source-supported correction**: kong-lite (substitute) proxies /v1/payouts straight to the Payouts service and short-circuits X-Payout-Idempotency itself, so the monolith replacement's idempotency state / audit never sees the call; on the real path the gateway forwards to the monolith replacement (Route.php idempotentRoutesConfig) as in production

### journey:trust-path/identity_propagation
- result: real=PASS substitute=FAIL -- **twin adaptation**: edge passport (kid edgev2) checks exist only in the real variant; kong-lite forwards its own passport
- real_only_check `X-PASSPORT-USABLE_true_(passport-enabler)` (real=True, substitute=None) -- **twin adaptation**: edge passport (kid edgev2) checks exist only in the real variant; kong-lite forwards its own passport
- real_only_check `gateway_passport_verified_by_the_monolith_replacement_(RS256, kid edgev2)` (real=True, substitute=None) -- **twin adaptation**: edge passport (kid edgev2) checks exist only in the real variant; kong-lite forwards its own passport
- real_only_check `gateway_replaced_the_forged_passport_with_its_own_(consumer = caller)` (real=True, substitute=None) -- **twin adaptation**: edge passport (kid edgev2) checks exist only in the real variant; kong-lite forwards its own passport
- check_outcome `monolith_replacement_saw_the_merchant_identity_from_the_gateway_call` (real=True, substitute=False) -- **twin adaptation**: edge passport (kid edgev2) checks exist only in the real variant; kong-lite forwards its own passport
- real_only_check `passport_consumer_is_the_authenticated_merchant_in_live_mode` (real=True, substitute=None) -- **twin adaptation**: edge passport (kid edgev2) checks exist only in the real variant; kong-lite forwards its own passport
- substitute_only_check `substitute_gateway_forwarded_an_identity_the_ingress_accepted` (real=None, substitute=False) -- **twin adaptation**: edge passport (kid edgev2) checks exist only in the real variant; kong-lite forwards its own passport

### journey:trust-path/internal_app_auth
- real_only_check `gateway_itself_refused_the_app_credential` (real=True, substitute=None) -- **twin adaptation**: real-variant-only 'gateway itself refused' observation

### journey:trust-path/maker_checker
- result: real=PASS substitute=FAIL -- **source-supported correction**: real: Twirp ConfigAPI/ActionAPI + Cadence (separation section, 409 on terminal); substitute: the M5 reconstruction's admin/actor planes (404 no_pending_workflow on terminal). Both are asserted by variant.
- check_outcome `approval_config_bound_for_the_merchant_(1_checker_approval)` (real=True, substitute=False) -- **source-supported correction**: real: Twirp ConfigAPI/ActionAPI + Cadence (separation section, 409 on terminal); substitute: the M5 reconstruction's admin/actor planes (404 no_pending_workflow on terminal). Both are asserted by variant.
- real_only_check `real_workflow_service_accepts_the_creators_own_approval_(no_maker!=checker_rule_in_razorpay/workflows_-_source_supported_correction_of_the_M5_reconstruction)` (real=True, substitute=None) -- **source-supported correction**: real: Twirp ConfigAPI/ActionAPI + Cadence (separation section, 409 on terminal); substitute: the M5 reconstruction's admin/actor planes (404 no_pending_workflow on terminal). Both are asserted by variant.

### journey:trust-path/payouts_ext_direct
- result: real=PASS substitute=BLOCKED -- **twin adaptation**: BLOCKED on the substitute variant: kong-lite has no payouts-ext service/host routing
- real_only_check `anonymous_is_refused_401_at_the_edge` (real=True, substitute=None) -- **twin adaptation**: every check is real-only (the substitute variant is BLOCKED before any check)
- real_only_check `another_merchant_never_reads_the_payout_through_payouts-ext_(4xx)` (real=True, substitute=None) -- **twin adaptation**: every check is real-only (the substitute variant is BLOCKED before any check)
- real_only_check `create_200` (real=True, substitute=None) -- **twin adaptation**: every check is real-only (the substitute variant is BLOCKED before any check)
- real_only_check `payouts_service_answered_the_edge-authenticated_call_(200_or_its_own_401)` (real=True, substitute=None) -- **twin adaptation**: every check is real-only (the substitute variant is BLOCKED before any check)
- real_only_check `the_monolith_replacement_never_saw_the_payouts-ext_call_(routed_by_host_to_the_Payouts_service)` (real=True, substitute=None) -- **twin adaptation**: every check is real-only (the substitute variant is BLOCKED before any check)
- real_only_check `wrong_secret_is_refused_401_at_the_edge_(never_reaches_the_Payouts_service)` (real=True, substitute=None) -- **twin adaptation**: every check is real-only (the substitute variant is BLOCKED before any check)

### journey:trust-path/restart_cache_invalidation
- result: real=EXPECTED_FAILURE substitute=BLOCKED -- **twin adaptation**: real: EXPECTED_FAILURE F-M11-2 (a deleted basic-auth-x credential keeps authenticating on Kong 3.4.2 -- recorded finding); substitute: BLOCKED by design (Kong Admin API + Shield rule API are real-service surfaces)
- real_only_check `baseline_200` (real=True, substitute=None) -- **twin adaptation**: real-only checks
- real_only_check `credential_deleted_through_the_admin_api` (real=True, substitute=None) -- **twin adaptation**: real-only checks
- real_only_check `credential_present_before_rotation` (real=True, substitute=None) -- **twin adaptation**: real-only checks
- real_only_check `credential_re-registered` (real=True, substitute=None) -- **twin adaptation**: real-only checks
- real_only_check `edge-kong_actually_restarted_and_is_running` (real=True, substitute=None) -- **twin adaptation**: real-only checks
- real_only_check `merchant_key_still_authenticates_after_the_gateway_restart_(credentials_in_Kong_Postgres)` (real=True, substitute=None) -- **twin adaptation**: real-only checks
- real_only_check `old_secret_refused_after_the_credential_was_deleted_(cache_invalidated)` (real=False, substitute=None) -- **twin adaptation**: real-only checks
- real_only_check `re-registered_credential_authenticates_again` (real=True, substitute=None) -- **twin adaptation**: real-only checks
- real_only_check `shield_rules_for_the_merchant_survive_the_restart_(MySQL)` (real=True, substitute=None) -- **twin adaptation**: real-only checks

### journey:trust-path/route_authorization
- result: real=PASS substitute=FAIL -- **twin adaptation**: gateway route-table checks exist only in the real variant; both variants must refuse the unroutable paths with 4xx
- real_only_check `a_routable_prod-api_path_is_proxied_to_the_monolith_replacement` (real=True, substitute=None) -- **twin adaptation**: gateway route-table checks exist only in the real variant; both variants must refuse the unroutable paths with 4xx
- real_only_check `every_unroutable_path_was_answered_by_the_gateway_itself_404_no_route` (real=True, substitute=None) -- **twin adaptation**: gateway route-table checks exist only in the real variant; both variants must refuse the unroutable paths with 4xx
- real_only_check `gateway_route_table_is_the_derived_prod-api_table_(no_internal/admin_routes)` (real=True, substitute=None) -- **twin adaptation**: gateway route-table checks exist only in the real variant; both variants must refuse the unroutable paths with 4xx
- check_outcome `ps_internal_approve_route_is_not_reachable_through_the_public_gateway_(4xx)` (real=True, substitute=False) -- **source-supported correction**: kong-lite (substitute) proxies whole path prefixes, so a merchant key reaches the Payouts service's internal approve route (PS answers 500 for the unknown id); the REAL gateway has no such route (404 at the edge). This is exactly the fidelity gap the promotion closes.

### journey:trust-path/s2s_identity
- real_only_check `banking-accounts_admits_the_payouts_Api-Token_200` (real=True, substitute=None) -- **twin adaptation**: the substitute stubs carry no production credential contract; real-only checks are recorded, not silently skipped
- real_only_check `banking-accounts_refuses_anonymous_and_wrong_Api-Token_401` (real=True, substitute=None) -- **twin adaptation**: the substitute stubs carry no production credential contract; real-only checks are recorded, not silently skipped
- real_only_check `shield_admits_the_payouts_identity_and_evaluates_(action_returned)` (real=True, substitute=None) -- **twin adaptation**: the substitute stubs carry no production credential contract; real-only checks are recorded, not silently skipped
- real_only_check `shield_refuses_anonymous_and_wrong_credentials_401` (real=True, substitute=None) -- **twin adaptation**: the substitute stubs carry no production credential contract; real-only checks are recorded, not silently skipped
- real_only_check `workflow_service_admits_the_payouts_service_identity_200` (real=True, substitute=None) -- **twin adaptation**: the substitute stubs carry no production credential contract; real-only checks are recorded, not silently skipped
- real_only_check `workflow_service_refuses_anonymous_and_wrong_credentials_401` (real=True, substitute=None) -- **twin adaptation**: the substitute stubs carry no production credential contract; real-only checks are recorded, not silently skipped

### journey:trust-path/shield_rules
- real_only_check `merchant_has_the_arena_block_rule_in_the_real_Shield` (real=True, substitute=None) -- **twin adaptation**: real: the rule API / stable-rule / direct-evaluate / block checks; substitute: shield-stub carries fixture rules for the seeded merchants only, so a fresh pool merchant is allowed (checked as 'stub consulted')
- real_only_check `payout_to_the_blocked_beneficiary_is_refused_as_suspicious_transaction` (real=True, substitute=None) -- **twin adaptation**: real: the rule API / stable-rule / direct-evaluate / block checks; substitute: shield-stub carries fixture rules for the seeded merchants only, so a fresh pool merchant is allowed (checked as 'stub consulted')
- real_only_check `real_Shield_blocks_the_payouts-shaped_request_for_a_9999_beneficiary_(ruleset_payout_primary)` (real=True, substitute=None) -- **twin adaptation**: real: the rule API / stable-rule / direct-evaluate / block checks; substitute: shield-stub carries fixture rules for the seeded merchants only, so a fresh pool merchant is allowed (checked as 'stub consulted')
- substitute_only_check `shield-stub_was_consulted_for_the_fresh_merchant_(fixture_rules_bind_to_the_seeded_merchants_only)` (real=None, substitute=True) -- **twin adaptation**: real: the rule API / stable-rule / direct-evaluate / block checks; substitute: shield-stub carries fixture rules for the seeded merchants only, so a fresh pool merchant is allowed (checked as 'stub consulted')
- substitute_only_check `shield_verdict_persisted_on_the_payout_(payout_meta_permanent.shield_evaluate_response)` (real=None, substitute=True) -- **twin adaptation**: real: the rule API / stable-rule / direct-evaluate / block checks; substitute: shield-stub carries fixture rules for the seeded merchants only, so a fresh pool merchant is allowed (checked as 'stub consulted')
- real_only_check `the_block_rule_is_a_stable_rule_(no_0%_canary_feature_flag)` (real=True, substitute=None) -- **twin adaptation**: real: the rule API / stable-rule / direct-evaluate / block checks; substitute: shield-stub carries fixture rules for the seeded merchants only, so a fresh pool merchant is allowed (checked as 'stub consulted')
- real_only_check `the_real_Shield_evaluated_this_merchant_(container_log)` (real=True, substitute=None) -- **twin adaptation**: real: the rule API / stable-rule / direct-evaluate / block checks; substitute: shield-stub carries fixture rules for the seeded merchants only, so a fresh pool merchant is allowed (checked as 'stub consulted')

