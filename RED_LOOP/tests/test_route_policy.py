#!/usr/bin/env python3
"""Deterministic unit tests for the M3 merchant-edge route classifier.

Imports the SAME route_policy module kong-lite ships (single source of truth,
no drift). Run: python3 RED_LOOP/tests/test_route_policy.py
"""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "ENV2_COMPOSE" / "substitutes" / "kong-lite"))
import route_policy as rp  # noqa: E402

PAYOUTS = "http://payouts-api:9400"
XBAL = "http://xbalances-server:8080"


class TestRoutePolicy(unittest.TestCase):
    def _allow(self, m, p, up=None, inj=None):
        dec, u, i = rp.classify_request(m, p)
        self.assertEqual(dec, "allow", "%s %s should be allowed, got %s" % (m, p, dec))
        if up is not None:
            self.assertEqual(u, up)
        if inj is not None:
            self.assertEqual(i, inj)

    def _deny(self, m, p):
        dec, _, _ = rp.classify_request(m, p)
        self.assertEqual(dec, "deny", "%s %s should be denied, got %s" % (m, p, dec))

    def test_public_merchant_routes_allowed(self):
        self._allow("POST", "/v1/payouts", PAYOUTS, True)
        self._allow("GET", "/v1/payouts", PAYOUTS, True)
        self._allow("GET", "/v1/payouts?count=10&skip=0", PAYOUTS, True)
        self._allow("GET", "/v1/payouts/pout_TYabc123", PAYOUTS, True)
        self._allow("POST", "/v1/payouts/cancel_payout/pout_TYabc", PAYOUTS, True)
        self._allow("GET", "/v1/payouts/free_payout/ARENABAL000001", PAYOUTS, True)
        self._allow("GET", "/v1/payouts/payouts_status_reason_map", PAYOUTS, True)
        self._allow("GET", "/v1/payouts/_meta/summary", PAYOUTS, True)
        self._allow("GET", "/v1/payouts/schedule/timeslots", PAYOUTS, True)
        self._allow("PATCH", "/v1/payouts/pout_TYabc/attachments", PAYOUTS, True)
        self._allow("POST", "/v2/payouts", PAYOUTS, True)
        self._allow("POST", "/v1/fund_accounts/validations", PAYOUTS, True)
        self._allow("GET", "/v1/fund_accounts/validations/fav_123", PAYOUTS, True)

    def test_balances_allowed_without_api_cred(self):
        # xbalances read surface: routed to xbalances, cred.API NOT injected
        self._allow("GET", "/v1/balances", XBAL, False)
        self._allow("GET", "/v1/balances/ARENABAL000001", XBAL, False)

    def test_internal_routes_denied(self):
        for p in ["/v1/payouts/fetch_multiple", "/v1/payouts/manual_action",
                  "/v1/payouts/rzp_fees_payout", "/v1/payouts/analytics",
                  "/v1/payouts/retry", "/v1/payouts/on_hold/process",
                  "/v1/payouts/batch/process", "/v1/payouts/consistency_checker",
                  "/v1/payouts/bulk", "/v1/payouts/attachments",
                  "/v1/payouts/status_details/pout_x", "/v1/payouts/internal_contact_payout",
                  "/v1/payouts/payout_internal", "/v1/inflight_reservations"]:
            self._deny("POST", p)
            self._deny("GET", p)

    def test_canned_mock_path_denied(self):
        # The exact Milestone-2 false-positive path must be unreachable
        self._deny("GET", "/v1/payouts/fetch_multiple?id=pout_1234&auth_type=proxy")

    def test_internal_app_create_variants_denied(self):
        self._deny("POST", "/v1/payouts/payouts_internal")
        self._deny("POST", "/v2/payouts/payouts_internal")
        self._deny("POST", "/v1/payouts/payouts_internal/pout_x/approve")
        self._deny("POST", "/v1/payouts/payouts_internal/pout_x/reject")
        self._deny("GET", "/v1/payouts/payouts_internal/pout_x")

    def test_admin_workflow_and_other_internal_denied(self):
        for p in ["/v1/admin", "/v1/admin/merchant-configuration", "/v1/admin/foo/bar",
                  "/v1/workflow/state", "/v1/internal/non_terminal_payouts/ARENABAL000002",
                  "/v1/cron/process_queued_payouts", "/v1/notify/health/update",
                  "/v1/banking_account_statement", "/v1/elasticsearch/query",
                  "/v1/payloadcrypt/encrypt", "/v1/merchant/update_on_hold_slas",
                  "/v1/fund_accounts/validations/update/vpa", "/twirp/ledger/x"]:
            self._deny("POST", p)

    def test_not_implemented_prefixes_denied(self):
        for p in ["/v1/contacts", "/v1/contacts/cont_x", "/v1/transactions",
                  "/v1/payout_links", "/v1/payouts_batch", "/v1/fund_accounts"]:
            self._deny("GET", p)

    def test_default_deny_unknown(self):
        self._deny("GET", "/v1/payoutstranslate_account_number_to_balance_id")
        self._deny("DELETE", "/v1/payouts/pout_TYabc")  # method not in allow set
        self._deny("GET", "/random/path")

    def test_deny_wins_over_generic_id_rule(self):
        # fetch_multiple/analytics share the /v1/payouts/{seg} shape but must deny
        self.assertEqual(rp.classify_request("GET", "/v1/payouts/fetch_multiple")[0], "deny")
        self.assertEqual(rp.classify_request("GET", "/v1/payouts/pout_realid")[0], "allow")


if __name__ == "__main__":
    unittest.main(verbosity=2)
