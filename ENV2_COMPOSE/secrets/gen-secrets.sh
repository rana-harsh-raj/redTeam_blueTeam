#!/usr/bin/env bash
# secrets/gen-secrets.sh — generate every credential this arena needs, fresh,
# into secrets/*.txt (plaintext, gitignored, one value per file, exactly one
# trailing newline). Nothing here is a real credential -- every value is
# freshly random per invocation. Run again any time to rotate everything
# (idempotent: always overwrites).
#
# config/generate.py reads these files (token SECRET.<filename-without-ext>)
# and inlines their values directly into generated/<svc>/*.toml -- see that
# script's header comment for why credentials are inlined into generated
# config rather than passed as container env vars or docker secrets for the
# 5 custom Go services (docker secrets ARE used, separately, for the 6
# official datastore images -- see docker-compose.yml's top-level `secrets:`
# block, which references six of the *_root_password files this script
# writes below).
#
# secrets/destroy.sh removes everything this script writes.
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

rand_password() {
  # 32 bytes of randomness, base64url-ish (no padding/slashes) so it's safe
  # to embed unquoted-ish in TOML strings without escaping surprises.
  # M1 / FD-003: never start with '-' or '_'. A leading '-' made mongosh treat the
  # CFA seed password as an option (usage text, seed step failed, boot aborted:
  # reports/implementation/runs/m1-20260905T165255Z/profile-kafka/up.log). The
  # first character is drawn from [A-Za-z0-9]; the remaining 43 stay as before.
  local body head
  body="$(openssl rand -base64 33 | tr '+/' '-_' | tr -d '=\n' | cut -c2-)"
  head="$(openssl rand -base64 12 | tr -dc 'A-Za-z0-9' | cut -c1)"
  printf '%s%s' "${head:-A}" "$body"
}

write_secret() {
  local name="$1" value="$2"
  printf '%s\n' "$value" > "${name}.txt"
  chmod 600 "${name}.txt"
}

echo "==> generating datastore root/app passwords"
write_secret mysql_payouts_root_password   "$(rand_password)"
write_secret mysql_fts_root_password       "$(rand_password)"
write_secret mysql_xbalances_root_password "$(rand_password)"
write_secret mysql_apidb_root_password     "$(rand_password)"
write_secret postgres_ledger_password      "$(rand_password)"
write_secret mongo_cfa_root_password       "$(rand_password)"

echo "==> generating service-to-service Basic-Auth pairs (usernames match what each repo's config expects, see config/templates/*.toml.tmpl for exactly where each is used)"
write_secret auth_payouts_ledger    "$(rand_password)"   # username payouts_key
write_secret auth_fts_ledger        "$(rand_password)"   # username fts_key
write_secret auth_xbalances_ledger  "$(rand_password)"   # username x_balances_key
write_secret auth_fts_payouts       "$(rand_password)"   # username fts
write_secret auth_xbalances_payouts "$(rand_password)"   # username x_balances
write_secret auth_fastcron_payouts  "$(rand_password)"   # username fast_cron
write_secret auth_monolith_shared   "$(rand_password)"   # username rzp_live
write_secret auth_api_payouts       "$(rand_password)"   # username api -- cred.API, inbound on payouts-api's /v1/payouts group (kong-lite's service Basic-Auth), see payouts.toml.tmpl [auth.api]
write_secret auth_monolith_payouts_db "$(rand_password)"  # SELECT on the synthetic payout identity row
write_secret auth_monolith_balance_db "$(rand_password)"  # synthetic API balance mirror only
write_secret auth_dcs_payouts       "$(rand_password)"   # username payouts
write_secret auth_dcs_xbalances     "$(rand_password)"   # username x_balances
write_secret auth_splitz_payouts    "$(rand_password)"   # username payouts
write_secret auth_shield_payouts    "$(rand_password)"   # username payouts
write_secret auth_mozart_shared     "$(rand_password)"   # username payouts (fts/xbalances/payouts all share -- see generate.py MOZART.* tokens)
write_secret auth_asv_shared        "$(rand_password)"   # username payouts / xBalances
write_secret auth_workflow_payouts  "$(rand_password)"   # username rzp_live -- payouts [auth.workflow]; the credential the Workflow substitute (workflow-engine / workflow-sim) presents on the approve/reject callback into payouts. M6: materialized into the secrets-workflow volume so those two containers can actually read it (see secrets/materialize.py).
# M6: admin-plane token for substitutes/workflow-engine (POST /admin/orgs|actors|policies,
# GET /admin/workflows). NEVER handed to a campaign/attacker worker -- that separation is
# the whole point of the M5 engine's two auth planes, which is why this lives in its own
# narrow secrets-workflow volume and not in secrets-kong.
write_secret wfe_admin_token        "$(rand_password)"   # bearer token, workflow-engine admin plane only
write_secret auth_stork_payouts     "$(rand_password)"   # username payouts
write_secret auth_vault_payouts     "$(rand_password)"   # username payouts_user ([hvault]/[vault] Mock -- see templates' TODO on this section)
write_secret auth_cfa_payouts       "$(rand_password)"   # username payouts
write_secret auth_pricing_payouts   "$(rand_password)"   # username payouts

echo "==> generating passport signer RSA keypair + minimal JWKS"
TMP_KEY_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_KEY_DIR"' EXIT
openssl genrsa -out "$TMP_KEY_DIR/passport_private.pem" 2048 >/dev/null 2>&1
openssl rsa -in "$TMP_KEY_DIR/passport_private.pem" -pubout -out "$TMP_KEY_DIR/passport_public.pem" >/dev/null 2>&1
write_secret passport_private_key "$(cat "$TMP_KEY_DIR/passport_private.pem")"
write_secret passport_public_key  "$(cat "$TMP_KEY_DIR/passport_public.pem")"

# Minimal JWKS derived from the public key's modulus/exponent, stdlib-python
# style base64url encoding via openssl + python3 (python3 is a reasonable
# dependency for a shell script even though config/generate.py itself must
# stay stdlib-only Python -- this is a bash script, not that file).
python3 - "$TMP_KEY_DIR/passport_public.pem" <<'PYEOF' > jwks.json
import base64
import json
import subprocess
import sys

pubkey_path = sys.argv[1]
# Extract modulus (n) and exponent (e) via openssl's text output rather than
# pulling in a crypto library -- stays within "what's already on the host
# running this shell script", not a new pip dependency.
text = subprocess.check_output(["openssl", "rsa", "-pubin", "-in", pubkey_path, "-noout", "-modulus"]).decode()
modulus_hex = text.strip().split("=", 1)[1]
n_bytes = bytes.fromhex(modulus_hex)
n_b64 = base64.urlsafe_b64encode(n_bytes).rstrip(b"=").decode()
e_b64 = base64.urlsafe_b64encode((65537).to_bytes(3, "big")).rstrip(b"=").decode()

jwks = {"keys": [{"kty": "RSA", "use": "sig", "alg": "RS256", "kid": "arena-passport-1", "n": n_b64, "e": e_b64}]}
print(json.dumps(jwks, indent=2))
PYEOF
chmod 600 jwks.json

echo "==> generating synthetic merchant key secrets (public key ids like rzp_test_ARENAM00000001 are in seeds/merchants.json, not secret; only the secret half lives here -- see substitutes/kong-lite/CONTRACT.md)"
write_secret merchant_arena_m1_secret "$(rand_password)"
write_secret merchant_arena_m2_secret "$(rand_password)"
write_secret merchant_arena_m3_secret "$(rand_password)"
mkdir -p merchant-keys
cp merchant_arena_m?_secret.txt merchant-keys/
# Generated per-test merchants have independent API secrets.
python3 - <<'PYGEN'
import json,secrets
from pathlib import Path
seed=Path('../seeds/generated/merchants.json')
if seed.exists():
    for merchant in json.loads(seed.read_text())['merchants'].values():
        name=merchant['secret_file']
        if not name.replace('_','').isalnum():
            raise SystemExit('invalid generated secret file name')
        p=Path('merchant-keys')/(name+'.txt')
        if name not in ('merchant_arena_m1_secret','merchant_arena_m2_secret','merchant_arena_m3_secret'): p.write_text(secrets.token_urlsafe(33)+'\n')
        p.chmod(0o600)
PYGEN

echo "==> writing verifier-bridge/ (user:pass files at the exact slugs verifier/helpers/creds.py's resolve_basic_auth() looks for under SECRETS_DIR=/run/secrets -- see docker-compose.yml verifier service's secrets list). Best-effort mapping onto the auth_* pairs above; TODO confirm each prefix's real intended caller identity against VERIFIER_SPEC.md if results look wrong under a real run -- these are reasonable guesses, not confirmed against that spec (not available to this workstream)."
mkdir -p verifier-bridge
write_bridge() {
  local slug="$1" user="$2" pass_file="$3"
  printf '%s:%s\n' "$user" "$(cat "${pass_file}.txt")" > "verifier-bridge/${slug}"
  chmod 600 "verifier-bridge/${slug}"
}
write_bridge ledger      "payouts_key" auth_payouts_ledger
write_bridge fts         "ps_payouts"  auth_fts_payouts   # fts-web [users.ps] identity (payouts-api's FTS client)
write_bridge monolith    "rzp_live"    auth_monolith_shared
write_bridge ps-fastcron "fast_cron"   auth_fastcron_payouts
write_bridge ps-service  "api"         auth_api_payouts   # cred.API family guards /v1/payouts (kong-lite forwards the same pair)

echo "==> writing .env.secrets (non-TOML consumers, e.g. scripts/cron-driver, that read env vars instead of a mounted config file)"
{
  echo "CRON_BASIC_AUTH_USER=fast_cron"
  echo "CRON_BASIC_AUTH_PASS=$(cat auth_fastcron_payouts.txt)"
} > .env.secrets
chmod 600 .env.secrets

echo "==> done. $(ls -1 *.txt jwks.json .env.secrets 2>/dev/null | wc -l | tr -d ' ') secret files written under $SCRIPT_DIR"
echo "    Run config/generate.py next to render these into generated/<svc>/*.toml."
