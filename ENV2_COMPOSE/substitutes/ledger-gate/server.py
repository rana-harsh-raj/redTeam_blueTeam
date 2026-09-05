#!/usr/bin/env python3
"""Transparent, arena-only Ledger transport gate with scoped failure controls.

Only the fixed internal Ledger destination is allowed. Faults run in the shared
stub dispatcher before forwarding; no Ledger response or account is invented.
"""
import json
import sys
import urllib.request
import urllib.error
sys.path.insert(0, '/app')
from _common.base_stub import serve

# Headers the gate must NOT copy to the upstream hop. Everything else is
# forwarded verbatim so the gate is transport-transparent.
#
# WHY forward everything else: in production payouts talks to ledger-live
# directly, so Ledger's request-idempotency layer runs on the payouts leg.
# ledger-sdk always emits these headers (ledger-sdk/common/constant.go:39-54:
# HeaderAccept "Accept", HeaderLedgerTenant "Ledger-Tenant", HeaderRequestID
# "Request-ID", HeaderTraceID "Trace-ID", IdempotencyKey "idempotency-key",
# LedgerIntegrationMode "Ledger-Integration-Mode", CountryCode "Country-Code";
# set on every journal call at ledger-sdk/ledger/journal/core.go:120-124,255-259
# and defaulted to a fresh UUID when empty at core.go:105-107,249-251).
# Server side, ledger/pkg/idempotency/hooks.go:17 reads "idempotency-key" off
# the request into ctx and ledger/pkg/idempotency/interceptor.go:115-118
# (isIdempotencyNotRequired) returns true -- skipping the idempotency table
# entirely -- when that key is empty. An allow-list that dropped
# "idempotency-key" therefore silently disabled a production control on the
# payouts->ledger leg only (FTS and the monolith reach ledger-api directly).
#
# Hop-by-hop / transport headers per RFC 9110 s7.6.1, plus:
#   host, content-length -- urllib recomputes both for the upstream request;
#     copying them forwards the gate's own address and a stale body length.
#   accept-encoding -- the gate decodes the upstream body and re-serialises the
#     JSON itself (see below), so it must not negotiate a compressed response.
#     Go's http.Transport adds "Accept-Encoding: gzip" on the wire even when the
#     caller never set it, so this one is load-bearing, not theoretical.
HOP_BY_HOP = frozenset((
    'host', 'content-length', 'connection', 'transfer-encoding', 'keep-alive',
    'te', 'trailer', 'trailers', 'upgrade', 'accept-encoding',
))


def forward_headers(inbound):
    """Copy every inbound header except the hop-by-hop/transport set.

    `inbound` is anything with .items() yielding (name, value) pairs
    (http.client.HTTPMessage in the server, a plain dict in tests).
    """
    out = {}
    for name, value in inbound.items():
        lowered = name.lower()
        if lowered in HOP_BY_HOP or lowered.startswith('proxy-'):
            continue
        out[name] = value
    return out


def forward(handler, body):
    headers = forward_headers(handler.headers)
    req = urllib.request.Request('http://ledger-api:8080'+handler.path,
        data=body if body else None, headers=headers, method=handler.command)
    try:
        with urllib.request.urlopen(req,timeout=20) as resp:
            return resp.status,json.loads(resp.read() or b'{}')
    except urllib.error.HTTPError as exc:
        return exc.code,json.loads(exc.read() or b'{}')
    except (urllib.error.URLError,TimeoutError,OSError):
        return 503,{'code':'unavailable','msg':'synthetic Ledger transport unavailable'}


if __name__ == '__main__':
    serve({(method,'/twirp/'):forward for method in ('GET','POST')})
