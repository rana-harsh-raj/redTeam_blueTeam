#!/usr/bin/env bash
# Env2 arena -- CFA (contacts + fund accounts) seed, via CFA's own HTTP API.
#
# ALTERNATIVE to cfa.js (mongosh direct-insert). Per the research pass's verdict, direct-mongo
# seeding is correct and sufficient (no unique index exists on any CFA collection, and the hash
# algorithm has no secret/server-side input) -- cfa.js is the recommended default. Use this script
# instead only if the arena's policy requires provisioning exclusively through service APIs, never
# direct datastore writes.
#
# Confirmed HTTP paths from the grpc-gateway registration: cfa/rpc/x/x-cfa/contact/v1/contact_api.pb.gw.go
# ("/v1/contacts"), cfa/rpc/x/x-cfa/fund_account/v1/fund_account_api.pb.gw.go ("/v1/fund_accounts").
#
# WHY A MONGOSH PATCH STEP FOLLOWS EACH CREATE: CFA's create endpoints generate their own "id"
# server-side (no request field to force a chosen ID). This script (a) creates each entity via the
# real API, letting the server compute a correct hash and hash_lookup row, then (b) renames the
# document's "id" field to the fixed ARENA id that payouts.sql's fund_accounts.account_id and
# SYNTHETIC_FIXTURE_SPEC.md Sec 8 already hardcode -- matching cfa.js's id/merchant assignments
# exactly, so the two seeding paths are interchangeable. The hash itself is untouched by the rename
# (computed from account fields, not from the id), so hash/hash_lookup consistency is preserved.
# active=false for FA4 is also applied here, since CreateFundAccountRequest has no "active" field.
#
# Usage: CFA_BASE_URL=http://cfa-server:8081 MONGO_URI="mongodb://cfa:<pass>@127.0.0.1:27117/cfa_arena" \
#        ./cfa_via_api.sh
#
# Requires: curl, jq, mongosh (mongosh only needed for the id-rename + inactive-flip steps).

set -euo pipefail

CFA_BASE_URL="${CFA_BASE_URL:?set CFA_BASE_URL, e.g. http://cfa-server:8081}"
MONGO_URI="${MONGO_URI:?set MONGO_URI, e.g. mongodb://cfa:<pass>@127.0.0.1:27117/cfa_arena}"

create_contact() {
  curl -sS -X POST "${CFA_BASE_URL}/v1/contacts" -H "Content-Type: application/json" -d "$1"
}
create_fund_account() {
  curl -sS -X POST "${CFA_BASE_URL}/v1/fund_accounts" -H "Content-Type: application/json" -d "$1"
}
rename_id() {
  # $1=collection $2=real_id $3=arena_id -- renames the document's own "id" field.
  mongosh "$MONGO_URI" --quiet --eval \
    "db.${1}.updateOne({id: '${2}'}, {\$set: {id: '${3}'}})"
}

echo "== Contacts (one per fund account, matching cfa.js) =="

C1_RAW=$(create_contact '{"name":"Arena Vendor One","email":"vendor1@arena.test","contact":"+919900000001","type":"vendor","reference_id":"ARENA-VENDOR-001","merchant_id":"ARENAM00000001"}')
C1_ID=$(echo "$C1_RAW" | jq -r '.id')
rename_id contacts "$C1_ID" ARENACO0000001

C2_RAW=$(create_contact '{"name":"Arena Vendor Two","email":"vendor2@arena.test","contact":"+919900000002","type":"vendor","reference_id":"ARENA-VENDOR-002","merchant_id":"ARENAM00000002"}')
C2_ID=$(echo "$C2_RAW" | jq -r '.id')
rename_id contacts "$C2_ID" ARENACO0000002

C3_RAW=$(create_contact '{"name":"Arena Vendor Three VPA","email":"vendor3@arena.test","contact":"+919900000003","type":"vendor","reference_id":"ARENA-VENDOR-003","merchant_id":"ARENAM00000003"}')
C3_ID=$(echo "$C3_RAW" | jq -r '.id')
rename_id contacts "$C3_ID" ARENACO0000003

C4_RAW=$(create_contact '{"name":"Arena Vendor Inactive","email":"vendor4@arena.test","contact":"+919900000004","type":"vendor","reference_id":"ARENA-VENDOR-004","merchant_id":"ARENAM00000001"}')
C4_ID=$(echo "$C4_RAW" | jq -r '.id')
rename_id contacts "$C4_ID" ARENACO0000004

echo "== Fund accounts (2 bank, 1 VPA, 1 inactive) =="

# FA1 -- M1's active bank fund account. Referenced by payouts.sql fund_accounts.account_id = ARENAFAX000001.
FA1_RAW=$(create_fund_account '{
  "merchant_id": "ARENAM00000001", "contact_id": "ARENACO0000001", "account_type": "bank_account",
  "bank_account": {"name": "Arena Vendor One", "ifsc": "ARNA0000001", "account_number": "2323230000000101"}
}')
FA1_ID=$(echo "$FA1_RAW" | jq -r '.id')
rename_id fund_accounts "$FA1_ID" ARENAFAX000001

# FA2 -- M2's active bank fund account. Referenced as ARENAFAX000002.
FA2_RAW=$(create_fund_account '{
  "merchant_id": "ARENAM00000002", "contact_id": "ARENACO0000002", "account_type": "bank_account",
  "bank_account": {"name": "Arena Vendor Two", "ifsc": "ARNA0000001", "account_number": "2323230000000201"}
}')
FA2_ID=$(echo "$FA2_RAW" | jq -r '.id')
rename_id fund_accounts "$FA2_ID" ARENAFAX000002

# FA3 -- M3's active VPA fund account. Referenced as ARENAFAX000003.
FA3_RAW=$(create_fund_account '{
  "merchant_id": "ARENAM00000003", "contact_id": "ARENACO0000003", "account_type": "vpa",
  "vpa": {"address": "arena.user@arenabank"}
}')
FA3_ID=$(echo "$FA3_RAW" | jq -r '.id')
rename_id fund_accounts "$FA3_ID" ARENAFAX000003

# FA4 -- M1's INACTIVE bank fund account, ending 9999 -- also the Shield scripted-block beneficiary
# (shield_fixtures.json's arena_block_bene_acct_suffix_9999 rule). Referenced as ARENAFAX000004.
FA4_RAW=$(create_fund_account '{
  "merchant_id": "ARENAM00000001", "contact_id": "ARENACO0000004", "account_type": "bank_account",
  "bank_account": {"name": "Arena Vendor Inactive", "ifsc": "ARNA0000001", "account_number": "2323230000009999"}
}')
FA4_ID=$(echo "$FA4_RAW" | jq -r '.id')
rename_id fund_accounts "$FA4_ID" ARENAFAX000004
mongosh "$MONGO_URI" --quiet --eval "db.fund_accounts.updateOne({id: 'ARENAFAX000004'}, {\$set: {active: false}})"

echo "Done. Verify with: mongosh \"\$MONGO_URI\" --eval 'db.contacts.find({id:/^ARENA/}).pretty(); db.fund_accounts.find({id:/^ARENA/}).pretty()'"
echo "NOTE: hash_lookup.entity_id still points at the ORIGINAL server-generated id for each entity,"
echo "not the renamed ARENA id (this script does not rewrite hash_lookup, to avoid corrupting the"
echo "hash the server computed correctly) -- a lookup by the CFA-native id still resolves; a lookup"
echo "by the ARENA id relies only on contacts.id/fund_accounts.id, not hash_lookup. Flag as"
echo "TODO(confirm) if a verifier specifically queries hash_lookup by the renamed id."
