#!/usr/bin/env python3
"""M11: render the banking-accounts seed SQL (businesses + banking_accounts rows) for every Direct/current-account
merchant of the arena from the monolith merchant seed (seeds/generated/monolith/merchants.json). Loaded into the REAL
banking-accounts MySQL after its own goose migrations (schema: internal/database/migrations). The columns the payouts
path reads (FetchMerchantDetailsForShield joins banking_accounts.account_number/business_id -> businesses.merchant_id,
constitution; credentials JSON for the RBL statement worker's fetch_banking_creds) carry the same synthetic values the
retired bankingaccounts-stub answered.

  python3 render_seed.py [--merchants FILE] [--out FILE] [--merchant <id> --account-number <n> --balance-id <b>]
"""
import argparse
import hashlib
import json
import time


def bid(mid):
    return "BAS" + hashlib.sha256(("business:" + mid).encode()).hexdigest()[:11].upper()


def baid(mid, acct):
    return "BAA" + hashlib.sha256(("account:%s:%s" % (mid, acct)).encode()).hexdigest()[:11].upper()


def q(v):
    return "NULL" if v is None else "'" + str(v).replace("\\", "\\\\").replace("'", "\\'") + "'"


def rows(mid, m):
    merchant = m.get("merchant") or {}
    detail = m.get("merchant_detail") or {}
    acct = m.get("account_number")
    now = int(time.time())
    creds = {"auth_username": "arena", "auth_password": "arena-rbl-pass", "client_id": "arena-rbl-client", "client_secret": "arena-rbl-secret",
             "corp_id": m.get("corp_id") or "ARENACORP"}
    out = []
    out.append("INSERT IGNORE INTO businesses (id, created_at, updated_at, merchant_id, name, email_id, constitution, registered_address_city, registered_address_state, registered_address_country, registered_address_pin_code, category) VALUES (%s);" %
               ", ".join(q(x) for x in (bid(mid), now, now, mid, detail.get("business_name") or merchant.get("name") or mid, merchant.get("email"),
                                        detail.get("business_type") or "private_limited", detail.get("business_registered_city"), detail.get("business_registered_state"),
                                        detail.get("business_registered_country") or "IN", detail.get("business_registered_pin"), merchant.get("category"))))
    if acct:
        out.append("INSERT IGNORE INTO banking_accounts (id, created_at, updated_at, business_id, account_number, status, account_type, partner_bank, balance_id, account_currency, ifsc, corp_id, user_id, urn, credentials) VALUES (%s);" %
                   ", ".join(q(x) for x in (baid(mid, acct), now, now, bid(mid), acct, "activated", "current", (m.get("channel") or "rbl").upper(), m.get("balance_id"),
                                            "INR", m.get("ifsc") or "RATN0000156", creds["corp_id"], m.get("bank_user_id") or "arena", "", json.dumps(creds))))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--merchants", default="seeds/generated/monolith/merchants.json")
    ap.add_argument("--out", default="seeds/generated/trustpath/bas.sql")
    ap.add_argument("--merchant"); ap.add_argument("--account-number"); ap.add_argument("--balance-id"); ap.add_argument("--channel", default="rbl")
    a = ap.parse_args()
    lines = ["-- M11 banking-accounts seed (rendered by trustpath/bankingaccounts/render_seed.py)", "SET NAMES utf8mb4;"]
    n = 0
    if a.merchant:
        lines += rows(a.merchant, {"account_number": a.account_number, "balance_id": a.balance_id, "channel": a.channel, "merchant": {"name": a.merchant}})
        n = 1
    else:
        doc = json.load(open(a.merchants))
        for mid, m in doc.get("merchants", {}).items():
            if (m.get("account_type") or "") != "direct" and not m.get("account_number"):
                continue
            lines += rows(mid, m); n += 1
    import os
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    open(a.out, "w").write("\n".join(lines) + "\n")
    print("wrote %s (%d merchants)" % (a.out, n))


if __name__ == "__main__":
    main()
