#!/usr/bin/env python3
"""Unit tests for the STORK_DUPLICATE_TERMINAL control in server.py.

Runs on the host with STDLIB only. server.py has an ``if __name__ == "__main__"``
guard (it only calls serve() there), so importing it does NOT start a server --
we import the module and drive _process_event directly, patching the underlying
urllib POST to record each delivery (headers + raw body) instead of hitting the
network.

server.py does ``sys.path.insert(0, "/app")`` then ``from _common.base_stub
import ...`` -- so we prepend the substitutes dir (which contains _common/) to
sys.path before importing, making that resolve on the host too.
"""
import os
import re
import sys
import types
import unittest
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
_SUBSTITUTES = os.path.dirname(_HERE)  # .../substitutes  (holds _common/)
sys.path.insert(0, _SUBSTITUTES)
sys.path.insert(0, _HERE)

import server  # noqa: E402


class _FakeResp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class DuplicateTerminalTest(unittest.TestCase):
    OWNER = "ARENAM0TEST01"
    URL = "http://merchant-webhook-sink:8080/webhook/test"
    SECRET = "arena_test_secret_min5"

    def setUp(self):
        # Record every POST the module makes, without touching the network.
        self.posts = []

        def fake_urlopen(req, *args, **kwargs):
            self.posts.append({
                "url": req.full_url,
                "body": req.data,  # raw bytes actually sent
                "event_id": req.headers.get("X-razorpay-event-id"),
                "request_id": req.headers.get("Request-id"),
            })
            return _FakeResp()

        self._orig_urlopen = urllib.request.urlopen
        urllib.request.urlopen = fake_urlopen

        # Snapshot / reset module state we mutate.
        self._orig_dup = server.DUPLICATE_TERMINAL
        self._orig_events = server.DUPLICATE_EVENTS
        self._orig_reorder = server.REORDER
        self._orig_webhooks = server.WEBHOOKS
        self._orig_captured = server.CAPTURED_EVENTS
        self._orig_log = server._append_events_log

        server.REORDER = False  # keep deliveries synchronous for assertions
        server.WEBHOOKS = {self.OWNER: [{
            "id": "wh_TEST01",
            "service": "rx-live",
            "owner_id": self.OWNER,
            "owner_type": "merchant",
            "url": self.URL,
            "secret": self.SECRET,
            "subscriptions": ["payout.processed", "payout.initiated"],
            "disabled": False,
        }]}
        server.CAPTURED_EVENTS = []
        server._append_events_log = lambda record: None  # no disk writes in tests

    def tearDown(self):
        urllib.request.urlopen = self._orig_urlopen
        server.DUPLICATE_TERMINAL = self._orig_dup
        server.DUPLICATE_EVENTS = self._orig_events
        server.REORDER = self._orig_reorder
        server.WEBHOOKS = self._orig_webhooks
        server.CAPTURED_EVENTS = self._orig_captured
        server._append_events_log = self._orig_log

    def _fire(self, event_name):
        payload = '{"entity":"event","event":"%s","account_id":"acc_x"}' % event_name
        body = ('{"event": {"owner_id": "%s", "owner_type": "merchant", '
                '"name": "%s", "payload": %s}}' % (self.OWNER, event_name,
                                                   __import__("json").dumps(payload)))
        status, resp = server._process_event(None, body.encode("utf-8"))
        self.assertEqual(status, 200)

    def test_flag_off_single_delivery(self):
        server.DUPLICATE_TERMINAL = False
        self._fire("payout.processed")
        self.assertEqual(len(self.posts), 1, "flag off => exactly one delivery")
        # event_id minted inside _deliver exactly as before: ev_ARENA<6 digits>
        eid = self.posts[0]["event_id"]
        self.assertIsNotNone(eid)
        self.assertRegex(eid, r"^ev_ARENA\d{6}$")
        # Event-Id and Request-Id share the single minted id (legacy behaviour).
        self.assertEqual(self.posts[0]["event_id"], self.posts[0]["request_id"])

    def test_flag_on_terminal_duplicates_identically(self):
        server.DUPLICATE_TERMINAL = True
        self._fire("payout.processed")
        self.assertEqual(len(self.posts), 2, "flag on + terminal => two deliveries")
        a, b = self.posts
        self.assertEqual(a["event_id"], b["event_id"], "identical X-Razorpay-Event-Id")
        self.assertEqual(a["request_id"], b["request_id"], "identical Request-Id")
        self.assertEqual(a["body"], b["body"], "byte-identical body")
        self.assertRegex(a["event_id"], r"^ev_ARENA\d{6}$")

    def test_flag_on_nonterminal_single_delivery(self):
        server.DUPLICATE_TERMINAL = True
        self._fire("payout.initiated")  # subscribed but NOT terminal
        self.assertEqual(len(self.posts), 1,
                         "flag on + non-terminal => one delivery")

    def test_default_event_set_is_the_three_terminals(self):
        self.assertEqual(
            set(server._DEFAULT_DUPLICATE_EVENTS.split(",")),
            {"payout.processed", "payout.failed", "payout.reversed"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
