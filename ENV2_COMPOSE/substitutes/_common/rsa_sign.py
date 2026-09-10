#!/usr/bin/env python3
"""rsa_sign.py — pure-stdlib RSASSA-PKCS1-v1_5 SHA-256 signing (RS256), for
minting the X-Passport-JWT-V1 header in kong-lite. No `cryptography`/`pyjwt`
dependency: the substitutes image is stdlib-only Python (see
substitutes/Dockerfile), and this environment's own toolchain doesn't have
either package installed either -- rather than add a pip dependency to the
image just for one signing call, this module implements the handful of
primitives RS256 needs directly:

  - a minimal ASN.1 DER reader (SEQUENCE/INTEGER/OCTET STRING/BIT STRING/
    OBJECT IDENTIFIER tags only -- everything a PKCS#1 or PKCS#8 RSA key
    needs), to pull (n, d) out of the PEM private key gen-secrets.sh writes
    (openssl 3.x's `genrsa` emits PKCS#8 "BEGIN PRIVATE KEY", confirmed by
    inspecting secrets/passport_private_key.txt's header locally -- this
    module also accepts PKCS#1 "BEGIN RSA PRIVATE KEY" for robustness in
    case a different openssl build is used to regenerate secrets).
  - RSASSA-PKCS1-v1_5 padding + modexp (Python's built-in 3-arg `pow` does
    efficient big-integer modexp; a 2048-bit RSA sign completes in low
    single-digit milliseconds, no C extension needed).

This module also parses a PEM PUBLIC KEY (SubjectPublicKeyInfo) and can
verify a signature it produced -- used only for this module's own
self-test (see __main__ block), not on kong-lite's runtime path.
"""
import base64
import hashlib
import struct


# --- minimal ASN.1 DER reader -------------------------------------------

def _read_length(data, pos):
    first = data[pos]
    pos += 1
    if first < 0x80:
        return first, pos
    n_bytes = first & 0x7F
    length = int.from_bytes(data[pos:pos + n_bytes], "big")
    return length, pos + n_bytes


def _read_tlv(data, pos):
    tag = data[pos]
    pos += 1
    length, pos = _read_length(data, pos)
    value = data[pos:pos + length]
    return tag, value, pos + length


def _read_sequence_elements(seq_value):
    """Given the VALUE bytes of a SEQUENCE, return a list of (tag, value) for
    each top-level element it contains."""
    elements = []
    pos = 0
    while pos < len(seq_value):
        tag, value, pos = _read_tlv(seq_value, pos)
        elements.append((tag, value))
    return elements


TAG_INTEGER = 0x02
TAG_BIT_STRING = 0x03
TAG_OCTET_STRING = 0x04
TAG_SEQUENCE = 0x30


def _int_from_der(value_bytes):
    return int.from_bytes(value_bytes, "big")


def pem_to_der(pem_text):
    lines = [l.strip() for l in pem_text.strip().splitlines()
             if l.strip() and not l.startswith("-----")]
    return base64.b64decode("".join(lines))


def parse_rsa_private_key_der(der):
    """Accepts either a PKCS#1 RSAPrivateKey DER or a PKCS#8 PrivateKeyInfo
    DER (whose privateKey OCTET STRING contains a PKCS#1 RSAPrivateKey).
    Returns (n, d, key_size_bytes)."""
    tag, outer_value, _ = _read_tlv(der, 0)
    assert tag == TAG_SEQUENCE, "expected a top-level SEQUENCE"
    elements = _read_sequence_elements(outer_value)

    # elements[0] is always the version INTEGER. If elements[1] is a
    # SEQUENCE, this is PKCS#8 (AlgorithmIdentifier) and elements[2] is the
    # OCTET STRING wrapping the real PKCS#1 key. If elements[1] is an
    # INTEGER, this is already a bare PKCS#1 RSAPrivateKey (modulus).
    if elements[1][0] == TAG_SEQUENCE:
        octet_tag, octet_value = elements[2]
        assert octet_tag == TAG_OCTET_STRING, "expected OCTET STRING wrapping the RSA key"
        return parse_rsa_private_key_der(octet_value)

    # Bare PKCS#1 RSAPrivateKey: version, modulus(n), publicExponent(e),
    # privateExponent(d), prime1, prime2, exponent1, exponent2, coefficient.
    n = _int_from_der(elements[1][1])
    d = _int_from_der(elements[3][1])
    key_size_bytes = (n.bit_length() + 7) // 8
    return n, d, key_size_bytes


def parse_rsa_public_key_der(der):
    """Accepts a SubjectPublicKeyInfo DER (PEM 'PUBLIC KEY'). Returns (n, e)."""
    tag, outer_value, _ = _read_tlv(der, 0)
    assert tag == TAG_SEQUENCE
    elements = _read_sequence_elements(outer_value)
    # elements[0] = AlgorithmIdentifier SEQUENCE, elements[1] = BIT STRING
    bit_tag, bit_value = elements[1]
    assert bit_tag == TAG_BIT_STRING
    # BIT STRING: first byte is the "unused bits" count (0 for DER-aligned content)
    inner_der = bit_value[1:]
    tag2, inner_value, _ = _read_tlv(inner_der, 0)
    assert tag2 == TAG_SEQUENCE
    inner_elements = _read_sequence_elements(inner_value)
    n = _int_from_der(inner_elements[0][1])
    e = _int_from_der(inner_elements[1][1])
    return n, e


# --- RSASSA-PKCS1-v1_5 SHA-256 ------------------------------------------

# DER prefix for a DigestInfo wrapping a SHA-256 digest (RFC 8017 Appendix
# A.2.4's well-known constant for id-sha256 + NULL params + OCTET STRING(32)).
_SHA256_DIGESTINFO_PREFIX = bytes.fromhex(
    "3031300d060960864801650304020105000420"
)


def _pkcs1_v15_pad(digest_info, key_size_bytes):
    padding_len = key_size_bytes - len(digest_info) - 3
    if padding_len < 8:
        raise ValueError("RSA key too small for PKCS#1 v1.5 SHA-256 padding")
    return b"\x00\x01" + (b"\xff" * padding_len) + b"\x00" + digest_info


def rsa_sign_pkcs1v15_sha256(message, n, d, key_size_bytes):
    digest = hashlib.sha256(message).digest()
    digest_info = _SHA256_DIGESTINFO_PREFIX + digest
    em = _pkcs1_v15_pad(digest_info, key_size_bytes)
    m_int = int.from_bytes(em, "big")
    sig_int = pow(m_int, d, n)
    return sig_int.to_bytes(key_size_bytes, "big")


def rsa_verify_pkcs1v15_sha256(message, signature, n, e, key_size_bytes):
    sig_int = int.from_bytes(signature, "big")
    em_int = pow(sig_int, e, n)
    em = em_int.to_bytes(key_size_bytes, "big")
    digest = hashlib.sha256(message).digest()
    digest_info = _SHA256_DIGESTINFO_PREFIX + digest
    expected = _pkcs1_v15_pad(digest_info, key_size_bytes)
    return em == expected


def b64url(raw_bytes):
    return base64.urlsafe_b64encode(raw_bytes).rstrip(b"=").decode("ascii")


if __name__ == "__main__":
    # Self-test: generate nothing (no openssl dependency here); instead
    # exercise round-trip sign+verify against whatever PEM files are passed
    # as argv, so this can be curl/python-tested exactly like every other
    # stub per the task's validation convention.
    import sys
    import json
    if len(sys.argv) != 3:
        print("usage: rsa_sign.py <private_key.pem> <public_key.pem>")
        sys.exit(1)
    with open(sys.argv[1]) as f:
        priv_pem = f.read()
    with open(sys.argv[2]) as f:
        pub_pem = f.read()
    n, d, ksz = parse_rsa_private_key_der(pem_to_der(priv_pem))
    n2, e = parse_rsa_public_key_der(pem_to_der(pub_pem))
    assert n == n2, "public/private key modulus mismatch"
    header = {"typ": "JWT", "alg": "RS256", "kid": "arena-passport-1"}
    payload = {"iss": "https://edge.razorpay.com", "sub": "https://payouts.razorpay.com",
               "identified": True, "authenticated": True, "mode": "test"}
    signing_input = (b64url(json.dumps(header, separators=(",", ":")).encode()) + "." +
                      b64url(json.dumps(payload, separators=(",", ":")).encode())).encode()
    sig = rsa_sign_pkcs1v15_sha256(signing_input, n, d, ksz)
    ok = rsa_verify_pkcs1v15_sha256(signing_input, sig, n2, e, ksz)
    jwt = signing_input.decode() + "." + b64url(sig)
    print("verify_ok=%s" % ok)
    print("jwt=%s" % jwt)
    sys.exit(0 if ok else 1)
