"""``X-Passport-JWT-V1`` header helper.

Real ``payouts-api`` validates this header as an RS256 JWT checked against a
JWKS endpoint (``goutils/passport/handler.go``: ``parseAndValidateToken``
rejects any signing method that isn't ``*jwt.SigningMethodRSA`` and requires a
``kid`` header resolvable in the fetched JWKS). There is no HS256/shared-secret
fallback in the real code, so this module cannot forge a token that would pass
against an unmodified payouts-api -- doing so would silently downgrade every
verifier that depends on it to testing a fake auth path instead of the real one.

Two supported fixture shapes, checked in order; if neither is configured this
returns ``None`` and the caller must ``pytest.skip`` naming
``PASSPORT_STATIC_JWT_<MERCHANT>`` or ``PASSPORT_SIGNER_URL`` as the missing
fixture (see VERIFIER_SPEC.md "Auth" section).

1. ``PASSPORT_STATIC_JWT_<MERCHANT_KEY>`` env var -- a pre-minted RS256 token,
   produced out-of-band (e.g. a small Go CLI built on ``goutils/passport``,
   signed with whichever private key the arena's JWKS substitute publishes).
   Used verbatim, no expiry/refresh handled here.

2. ``PASSPORT_SIGNER_URL`` (e.g. ``http://passport-signer:8090/mint``) -- a
   live minting endpoint the arena may run. POSTed the desired claim shape,
   expected to return ``{"token": "..."}``.
"""
import json
import os
import urllib.error
import urllib.request


def static_token(merchant_key):
    return os.environ.get("PASSPORT_STATIC_JWT_%s" % merchant_key.upper())


def mint_token(merchant_id, consumer_type="merchant", auth_type="private", roles=None, timeout=5):
    signer_url = os.environ.get("PASSPORT_SIGNER_URL")
    if not signer_url:
        return None
    payload = {
        "authenticated": True,
        "identified": True,
        "mode": "live",
        "roles": roles or [],
        "auth_type": auth_type,
        "consumer": {"id": merchant_id, "type": consumer_type},
    }
    req = urllib.request.Request(
        signer_url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read())
            return body.get("token")
    except (urllib.error.URLError, ValueError, TimeoutError):
        return None


def get_passport_jwt(merchant_key, merchant_id):
    """merchant_key: fixture label e.g. "M1"/"M2"/"M3". merchant_id: real merchant id to mint for."""
    token = static_token(merchant_key)
    if token:
        return token
    return mint_token(merchant_id)
