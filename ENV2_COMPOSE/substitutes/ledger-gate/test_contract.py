"""Offline contract tests for the Ledger transport gate. No container, no network.

Proves the gate is header-transparent: the ledger-sdk header set (notably
`idempotency-key`, which ledger/pkg/idempotency/interceptor.go:115-118 uses to
decide whether the idempotency table is consulted at all) reaches ledger-api,
while hop-by-hop/transport headers are dropped.
"""
import importlib.util
from email.message import Message
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "substitutes"))
spec = importlib.util.spec_from_file_location("ledger_gate", Path(__file__).with_name("server.py"))
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def http_message(pairs):
    """Build an http.client-style header container from (name, value) pairs."""
    msg = Message()
    for name, value in pairs:
        msg[name] = value
    return msg


class FakeHandler:
    command = "POST"
    path = "/twirp/rzp.ledger.journal.v1.JournalAPI/Create"

    def __init__(self, headers):
        self.headers = headers


class Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return b'{"id":"jrnl_arena_1"}'


# Exactly what ledger-sdk puts on a journal create (common/constant.go:39-54),
# plus the transport headers Go's http.Transport and any proxy would add.
SDK_HEADERS = [
    ("Host", "ledger-gate:8080"),
    ("Content-Length", "412"),
    ("Content-Type", "application/json"),
    ("Accept", "application/json"),
    ("Accept-Encoding", "gzip"),
    ("Connection", "keep-alive"),
    ("Proxy-Connection", "keep-alive"),
    ("Transfer-Encoding", "chunked"),
    ("Authorization", "Basic cnpwX2xpdmU6c2VjcmV0"),
    ("Ledger-Tenant", "X"),
    ("Request-ID", "0f7f2a1e-2d1c-4f0a-9d0a-4a1a2b3c4d5e"),
    ("Trace-ID", "7d2f1c9b8a6e5d4c"),
    ("Country-Code", "IN"),
    ("Ledger-Integration-Mode", "sync"),
    ("idempotency-key", "0a1b2c3d-4e5f-6071-8293-a4b5c6d7e8f9"),
    ("X-Request-Id", "arena-req-1"),
    ("X-Task-Id", "arena-task-1"),
]

FORWARDED = {
    "content-type", "accept", "authorization", "ledger-tenant", "request-id",
    "trace-id", "country-code", "ledger-integration-mode", "idempotency-key",
    "x-request-id", "x-task-id",
}
DROPPED = {
    "host", "content-length", "accept-encoding", "connection",
    "proxy-connection", "transfer-encoding",
}


class HeaderPassthrough(unittest.TestCase):
    def sent_headers(self):
        captured = {}

        def capture(req, **_):
            captured.update({k.lower(): v for k, v in req.header_items()})
            return Response()

        handler = FakeHandler(http_message(SDK_HEADERS))
        with patch.object(gate.urllib.request, "urlopen", side_effect=capture):
            status, payload = gate.forward(handler, b'{"transactor_id":"pout_1"}')
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"id": "jrnl_arena_1"})
        return captured

    def test_idempotency_key_is_forwarded(self):
        sent = self.sent_headers()
        self.assertEqual(sent.get("idempotency-key"),
                         "0a1b2c3d-4e5f-6071-8293-a4b5c6d7e8f9")

    def test_full_ledger_sdk_header_set_is_forwarded(self):
        sent = self.sent_headers()
        for name in sorted(FORWARDED):
            self.assertIn(name, sent, "gate dropped %s" % name)

    def test_hop_by_hop_and_transport_headers_are_not_forwarded(self):
        sent = self.sent_headers()
        for name in sorted(DROPPED):
            self.assertNotIn(name, sent, "gate forwarded hop-by-hop header %s" % name)

    def test_host_is_the_upstream_not_the_gate(self):
        # urllib derives Host from the destination URL; the inbound
        # "ledger-gate:8080" value must not survive.
        sent = self.sent_headers()
        self.assertNotEqual(sent.get("host"), "ledger-gate:8080")

    def test_filter_is_case_insensitive_and_covers_proxy_star(self):
        out = gate.forward_headers({
            "HOST": "x", "Content-LENGTH": "1", "PROXY-Authorization": "b",
            "Idempotency-Key": "k", "TE": "trailers",
        })
        self.assertEqual({k.lower() for k in out}, {"idempotency-key"})

    def test_fault_registry_is_untouched_by_the_gate(self):
        # The gate registers only the two Twirp routes; delay/drop/status
        # injection stays entirely in the shared dispatcher
        # (_common/base_stub.py -> faults.control / faults.apply).
        import _common.faults as faults
        self.assertTrue(hasattr(faults, "control") and hasattr(faults, "apply"))
        self.assertNotIn("faults", gate.forward.__code__.co_names)


if __name__ == "__main__":
    unittest.main()
