"""M3 merchant-edge route policy (source-derived, stdlib-only, importable).

Source of truth: RED_LOOP/registry/merchant-gateway-routes.json. Compiled here
so kong-lite stays self-contained and so the classifier can be unit-tested on
the host without kong-lite's /app runtime deps.

Classification is derived from payouts/internal/routing/router/*.go: a route is
PUBLIC_MERCHANT only when its group layers a merchant (private/proxy/privilege)
passport on top of the service BasicAuth; groups with service BasicAuth and no
passport (or admin-only passport) are internal/admin and are NOT exposed on the
merchant edge. Default is fail-closed (deny).
"""
import re

PAYOUTS_UP = "http://payouts-api:9400"
XBAL_UP = "http://xbalances-server:8080"

# DENY: internal/admin/workflow/not-implemented paths (any method). Ordered first.
DENY = [
    # payoutInternalRoutes (svc-cred only, no passport) — payout_internal_routes.go:12-190
    r"^/v1/payouts/manual_action$", r"^/v1/payouts/update_payouts_with_fts$",
    r"^/v1/payouts/update_payouts_details_with_fts$", r"^/v1/payouts/update_credit_transfer_payout$",
    r"^/v1/payouts/scheduled/process$", r"^/v1/payouts/balance_update_event$",
    r"^/v1/payouts/retry$", r"^/v1/payouts/retry/source_update$",
    r"^/v1/payouts/payouts_internal/[^/]+/approve$", r"^/v1/payouts/payouts_internal/[^/]+/reject$",
    r"^/v1/payouts/payouts_internal/[^/]+$", r"^/v1/payouts/payout_internal$",
    r"^/v1/payouts/analytics$", r"^/v1/payouts/bene_bank_status_update$",
    r"^/v1/payouts/on_hold/process$", r"^/v1/payouts/free_payout_migration$",
    r"^/v1/payouts/consistency_checker$", r"^/v1/payouts/batch/process$",
    r"^/v1/payouts/shield/evaluate$", r"^/v1/payouts/duplicate_payout_evaluate$",
    r"^/v1/payouts/banking_account_statement/payout_update$", r"^/v1/payouts/rzp_fees_payout$",
    r"^/v1/payouts/set_pricing_rule_info$", r"^/v1/payouts/mapped_vpa/[^/]+$",
    r"^/v1/payouts/transfer_status_webhook$", r"^/v1/payouts/fetch_multiple$",
    r"^/v1/payouts/elasticsearch/backfill_index$", r"^/v1/payouts/status_details/[^/]+$",
    r"^/v1/payouts/internal_contact_payout$",
    # bulk / attachments internal (payout_internal_routes_with_passport.go, payout_bulk_routes.go)
    r"^/v1/payouts/bulk$", r"^/v1/payouts/bulk/validate$", r"^/v1/payouts/attachments$",
    # internal-app create variants (fail closed on the ordinary merchant edge)
    r"^/v1/payouts/payouts_internal$", r"^/v2/payouts/payouts_internal$",
    r"^/v2/payouts/internal_contact_payout$",
    r"^/v1/inflight_reservations$",
    # admin / control-plane / workflow / other internal service groups
    r"^/v1/admin(/.*)?$", r"^/v1/workflow(/.*)?$", r"^/v1/internal(/.*)?$",
    r"^/v1/cron(/.*)?$", r"^/v1/notify(/.*)?$", r"^/v1/banking_account_statement(/.*)?$",
    r"^/v1/elasticsearch(/.*)?$", r"^/v1/fund-management-payout(/.*)?$",
    r"^/v1/merchant/.*$", r"^/v1/payloadcrypt(/.*)?$", r"^/v1/idempotency-keys(/.*)?$",
    r"^/v1/fund_accounts/validations/update/.*$",
    # not-implemented-as-inbound (monolith-only or absent): deny on the edge
    r"^/v1/contacts(/.*)?$", r"^/v1/transactions(/.*)?$", r"^/v1/payout_links(/.*)?$",
    r"^/v1/payouts_batch(/.*)?$", r"^/twirp/.*$",
]

# ALLOW: (regex, {methods}, upstream, inject_cred_api). Evaluated after DENY.
ALLOW = [
    (r"^/v1/payouts$", {"POST", "GET"}, PAYOUTS_UP, True),
    (r"^/v1/payouts/pout_[A-Za-z0-9]+$", {"GET"}, PAYOUTS_UP, True),
    (r"^/v1/payouts/cancel_payout/[A-Za-z0-9_]+$", {"POST"}, PAYOUTS_UP, True),
    (r"^/v1/payouts/free_payout/[A-Za-z0-9_]+$", {"GET"}, PAYOUTS_UP, True),
    (r"^/v1/payouts/payouts_status_reason_map$", {"GET"}, PAYOUTS_UP, True),
    (r"^/v1/payouts/_meta/summary$", {"GET"}, PAYOUTS_UP, True),
    (r"^/v1/payouts/schedule/timeslots$", {"GET"}, PAYOUTS_UP, True),
    (r"^/v1/payouts/pout_[A-Za-z0-9]+/attachments$", {"PATCH"}, PAYOUTS_UP, True),
    (r"^/v2/payouts$", {"POST"}, PAYOUTS_UP, True),
    (r"^/v2/payouts/pout_[A-Za-z0-9]+$", {"GET"}, PAYOUTS_UP, True),
    (r"^/v1/fund_accounts/validations$", {"POST"}, PAYOUTS_UP, True),
    (r"^/v1/fund_accounts/validations/[A-Za-z0-9_]+$", {"GET"}, PAYOUTS_UP, True),
    (r"^/v1/balances(/.*)?$", {"GET"}, XBAL_UP, False),
]

STRIP_IDENTITY_HEADERS = {"x-merchant-id", "x-entity-id"}

_DENY_RE = [re.compile(p) for p in DENY]
_ALLOW_RE = [(re.compile(p), m, up, inj) for (p, m, up, inj) in ALLOW]


def classify_request(method, path):
    """Return (decision, upstream, inject_api_cred). decision in {allow, deny}.
    First matching DENY wins; then ALLOW (method-bound); default deny."""
    base = path.split("?", 1)[0]
    for rx in _DENY_RE:
        if rx.match(base):
            return "deny", None, False
    for rx, methods, up, inj in _ALLOW_RE:
        if rx.match(base) and method in methods:
            return "allow", up, inj
    return "deny", None, False
