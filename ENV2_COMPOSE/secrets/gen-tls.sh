#!/usr/bin/env bash
# M11: a twin-local CA + a server certificate for the production DCS hostnames the REAL banking-accounts service dials.
# razorpay/banking-accounts pkg/dcs/dcs.go hardcodes WithMock(false) and goutils/dcs config/uri.go maps [dcs].env to
# https://dcs-{live,test}.<env>.razorpay.in -- there is no plain-HTTP or host override in the source. The twin therefore
# answers those hostnames itself: dcs-stub carries them as network aliases and serves TLS with this certificate, and
# banking-accounts trusts the twin CA through Go's SSL_CERT_FILE. The service binary and its DCS client run unmodified.
# Idempotent per call (a fresh keypair every run, like gen-secrets.sh); never commit the outputs.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
write_secret() { printf '%s\n' "$2" > "$1.txt"; chmod 600 "$1.txt"; }
openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj "/CN=rzp-arena twin CA" \
  -keyout "$TMP/ca.key" -out "$TMP/ca.crt" >/dev/null 2>&1
openssl req -newkey rsa:2048 -nodes -subj "/CN=dcs-live.dev.razorpay.in" \
  -keyout "$TMP/dcs.key" -out "$TMP/dcs.csr" >/dev/null 2>&1
printf 'subjectAltName=DNS:dcs-live.dev.razorpay.in,DNS:dcs-test.dev.razorpay.in,DNS:dcs-stub\nextendedKeyUsage=serverAuth\n' > "$TMP/ext.cnf"
openssl x509 -req -in "$TMP/dcs.csr" -CA "$TMP/ca.crt" -CAkey "$TMP/ca.key" -CAcreateserial -days 3650 \
  -extfile "$TMP/ext.cnf" -out "$TMP/dcs.crt" >/dev/null 2>&1
write_secret arena_tls_ca_cert "$(cat "$TMP/ca.crt")"
write_secret dcs_tls_cert "$(cat "$TMP/dcs.crt")"
write_secret dcs_tls_key "$(cat "$TMP/dcs.key")"
echo "==> twin TLS: CA + DCS server certificate (SAN dcs-live/dcs-test.dev.razorpay.in) written"
