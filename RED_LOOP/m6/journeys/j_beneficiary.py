#!/usr/bin/env python3
"""family:beneficiary-fund-accounts -- contacts, fund accounts and the beneficiary boundary.

The beneficiary a payout pays lives in CFA (`cfa` Mongo: `contacts`, `fund_accounts`,
`hash_lookup`), is mirrored into the API monolith's fund-account record, and is read by payouts
on the route it actually calls. reports/domain/parts/money-statements.md section 7 pins the
matrix these drivers assert against:

  * `bank_account`: `ifsc` 11 chars alphanumeric, `account_number` 5-35 alnum and IMMUTABLE after
    create, beneficiary name 3-120;
  * dedup is app-level -- a SHA3-256 `hash` recorded in `hash_lookup`, with **no unique index**
    and in fact no index at all on that collection;
  * `active` (bool) is the ONLY lifecycle flag -- there is no verification state, so CFA cannot
    block an unvalidated beneficiary from a payout;
  * writes go to Mongo first, then an in-memory event -> SQS (`cfa-*-dual-write-queue`,
    `*-lazy-load`, 30s delay) -> an API-monolith worker; that is what `cfa-worker-fa` and
    `cfa-worker-contact` consume.

MEASURED ARENA LIMIT (see the `cfa_api` variant): cfa-server's grpc-gateway on :8081 exposes the
real `/v1/contacts` and `/v1/fund_accounts` routes but cannot reach its own gRPC backend --
every call answers `503 {"code":14,"message":"connection error: ... dial tcp 127.0.0.1:9:
connect: connection refused"}`, i.e. the gateway's own dial is being sent through
`HTTP_PROXY=http://127.0.0.1:9` (the discard port) which the container's `NO_PROXY` does not
cover for that target. So a merchant-facing CFA *create* is unreachable in this arena; the
create/validate/dedup legs are asserted where they ARE reachable (the documents CFA holds and the
record payouts reads), and the API leg is BLOCKED with that exact evidence.
"""
import json
import time
import uuid

import framework as F
from framework import journey

BEN = "beneficiary"
BEN_OTHER = "beneficiaryother"
CFA = "http://cfa-server:8081"


def _cfa_basic():
    return "payouts:" + F.P._pw("auth_cfa_payouts.txt")


def _fa(ctx):
    """The bare fund-account id. Descriptors carry the PUBLIC `fa_`-prefixed form; CFA, the
    monolith record and payouts.fund_account_id all use the bare one."""
    return F.bare(ctx.m["fund_account_id"])


def _cfa_docs(ctx, mid, fa_id):
    """The three documents CFA holds for this merchant's beneficiary."""
    js = ('print(JSON.stringify({'
          'contacts: db.contacts.find({merchant_id:"%(m)s"}).toArray(),'
          'fund_accounts: db.fund_accounts.find({merchant_id:"%(m)s"}).toArray(),'
          'hash_lookup: db.hash_lookup.find({entity_id:"%(fa)s"}).toArray()}))'
          % {"m": mid, "fa": fa_id})
    rc, out, err = ctx.a.mongo(js, note="CFA beneficiary documents for this merchant")
    doc = F.jload((out or "").strip().splitlines()[-1] if out.strip() else "", {}) or {}
    ctx.ev.setdefault("cfa_documents", {})[mid] = doc
    return doc


def _monolith_fa(ctx, fa_id):
    st, body = ctx.a.jhttp("GET", F.MONOLITH + "/fund_accounts_internal/" + fa_id,
                           basic=F.D._mono_basic(),
                           note="the fund-account route payouts actually reads")
    return st, body


def _bank(fa_doc):
    return (fa_doc or {}).get("bank_account") or {}


# ---------------------------------------------------------------------------
@journey("beneficiary-fund-accounts", "success", priority="P0", profile=BEN,
         title="the merchant's beneficiary is one consistent bank-account fund account across CFA, the record payouts reads, and a payout that completes to it",
         source_ref="reports/domain/parts/money-statements.md section 7 (CFA beneficiary matrix); "
                    "monolith fund_accounts_internal is the record payouts reads; "
                    "cfa/internal/fund_accounts/validate.go bank_account rules")
def beneficiary_success(ctx):
    mid, fa = ctx.m["merchant_id"], _fa(ctx)
    docs = _cfa_docs(ctx, mid, fa)
    fas = docs.get("fund_accounts") or []
    contacts = docs.get("contacts") or []
    ctx.ck("CFA_holds_exactly_one_fund_account_for_this_merchant", len(fas) == 1, fas)
    ctx.ck("CFA_holds_the_contact_it_belongs_to",
           len(contacts) == 1 and fas and fas[0].get("contact_id") == contacts[0].get("id"),
           {"contacts": contacts, "fund_account_contact_id": (fas[0] or {}).get("contact_id") if fas else None})
    if not fas:
        return
    fa_doc = fas[0]
    bank = _bank(fa_doc)
    ctx.ck("it_is_a_bank_account_fund_account_and_is_active",
           fa_doc.get("account_type") == "bank_account" and fa_doc.get("active") is True, fa_doc)
    ctx.ck("its_ifsc_is_11_alphanumeric_characters(CFA bank_account rule)",
           isinstance(bank.get("ifsc"), str) and len(bank["ifsc"]) == 11 and bank["ifsc"].isalnum(),
           bank.get("ifsc"))
    ctx.ck("its_account_number_is_5-35_alphanumeric(CFA bank_account rule)",
           isinstance(bank.get("account_number"), str)
           and 5 <= len(bank["account_number"]) <= 35 and bank["account_number"].isalnum(),
           bank.get("account_number"))
    ctx.ck("its_beneficiary_name_is_3-120_characters(CFA bank_account rule)",
           isinstance(bank.get("name"), str) and 3 <= len(bank["name"]) <= 120, bank.get("name"))
    ctx.ck("the_fund_account_carries_the_SHA-256_dedup_hash_CFA_dedups_on",
           isinstance(fa_doc.get("hash"), str) and len(fa_doc["hash"]) >= 32, fa_doc.get("hash"))
    hl = docs.get("hash_lookup") or []
    ctx.ck("CFA_holds_at_most_one_hash_lookup_dedup_row_for_it(no unique index exists to enforce it)",
           len(hl) <= 1, hl)
    if hl:
        ctx.ck("the_dedup_row_carries_the_same_hash_as_the_fund_account",
               hl[0].get("hash") == fa_doc.get("hash"),
               {"hash_lookup": hl, "fund_account_hash": fa_doc.get("hash")})
    else:
        ctx.note("GAP (reported, not fixed): the SHARED provisioning recipe "
                 "(RED_LOOP/red_loop/provisioner.py) writes the CFA `contacts` and `fund_accounts` "
                 "documents for a fresh merchant but NOT the `hash_lookup` row, which "
                 "RED_LOOP/red_loop/provisioner_direct.py does write for a Direct merchant. CFA's "
                 "dedup is app-level through exactly that collection and it carries no index at "
                 "all, so a Shared merchant's beneficiary is absent from the dedup index: a later "
                 "CFA create of the same beneficiary would mint a duplicate instead of returning "
                 "the existing id. Nothing in the payout path reads it, which is why every other "
                 "assertion here still holds.")
    ctx.ck("there_is_no_verification_state_on_it_-_active_is_the_only_lifecycle_flag(source-faithful)",
           not any(k in fa_doc for k in ("verified", "verification_status", "validation_status")),
           sorted(fa_doc))

    st, mono = _monolith_fa(ctx, fa)
    ctx.ev["monolith_fund_account"] = mono
    ctx.ck("the_record_payouts_reads_resolves_the_same_fund_account", st == 200 and bool(mono),
           {"status": st})
    mono_bank = ((mono or {}).get("fund_account") or mono or {}).get("bank_account") or {}
    ctx.ck("and_it_agrees_with_CFA_on_the_bank_account",
           str(mono_bank.get("ifsc") or mono_bank.get("ifsc_code") or "") == bank.get("ifsc")
           and str(mono_bank.get("account_number") or "") == bank.get("account_number"),
           {"cfa": {k: bank.get(k) for k in ("ifsc", "account_number", "name")},
            "monolith": mono_bank})

    # and the beneficiary actually works end to end
    ctx.a.mozart(mid, "hold")
    cr = ctx.create(4200, "bene-success")
    pid = cr["id"]
    ctx.ck("create_to_that_fund_account_200", cr["status"] == 200 and bool(pid), cr.get("raw"))
    if not pid:
        return
    ctx.ck("the_payout_entity_names_the_same_fund_account",
           F.bare((cr["json"] or {}).get("fund_account_id") or "") == fa,
           {"entity": (cr["json"] or {}).get("fund_account_id"), "expected_bare": fa})
    ctx.wait_status(pid, "initiated", timeout=60)
    ctx.a.finish_bank(pid, "success")
    row = ctx.wait_status(pid, "processed", timeout=120)
    ctx.ck("a_payout_to_that_beneficiary_completes", (row or {}).get("status") == "processed", row)
    ctx.state["bene_fa"] = fa
    ctx.state["bene_merchant"] = mid
    ctx.a.mozart(mid, clear=True)


@journey("beneficiary-fund-accounts", "failure", priority="P0", profile=BEN_OTHER,
         title="a fund account that does not exist is refused and mints nothing",
         source_ref="payouts resolves the fund account through the monolith "
                    "fund_accounts_internal route before anything else; an unresolved id is a "
                    "BAD_REQUEST_ERROR on field fund_account_id")
def beneficiary_failure(ctx):
    mid = ctx.m["merchant_id"]
    rc, before, _ = ctx.a.payouts_sql(
        "SELECT count(*) FROM payouts WHERE merchant_id='%s'" % mid, note="payout count before")
    ghost = "ARENAFAXNOSUCH"[:14]
    st1, txt1 = ctx.a.kong("POST", "/v1/payouts", ctx.auth,
                           {"fund_account_id": "fa_" + ghost,
                            "account_number": ctx.m["account_number"], "amount": 3000,
                            "currency": "INR", "mode": "IMPS", "purpose": "payout",
                            "queue_if_low_balance": False, "merchant_id": mid,
                            "narration": "m6 bene-ghost", "notes": {"m6": "bene"}},
                           {"X-Payout-Idempotency": "m6bg-" + uuid.uuid4().hex[:12]},
                           note="payout to a fund account that does not exist")
    body1 = F.jload(txt1) or {}
    err1 = (body1.get("error") or {}) if isinstance(body1, dict) else {}
    ctx.ev["unknown_fund_account"] = {"status": st1, "body": (txt1 or "")[:300]}
    ctx.ck("payout_to_an_unknown_fund_account_refused_4xx",
           isinstance(st1, int) and 400 <= st1 < 500, ctx.ev["unknown_fund_account"])
    ctx.ck("the_refusal_names_fund_account_id_as_the_offending_field",
           err1.get("field") == "fund_account_id", err1)
    ctx.ck("the_refusal_is_a_BAD_REQUEST_ERROR", err1.get("code") == "BAD_REQUEST_ERROR", err1)

    # an inactive beneficiary is also unusable -- CFA's only lifecycle flag
    st2, txt2 = ctx.a.kong("POST", "/v1/payouts", ctx.auth,
                           {"fund_account_id": "fa_ARENA00D44A220",
                            "account_number": ctx.m["account_number"], "amount": 3000,
                            "currency": "INR", "mode": "IMPS", "purpose": "payout",
                            "queue_if_low_balance": False, "merchant_id": mid,
                            "narration": "m6 bene-inactive", "notes": {"m6": "bene"}},
                           {"X-Payout-Idempotency": "m6bi-" + uuid.uuid4().hex[:12]},
                           note="payout to the fixture's INACTIVE bank fund account")
    ctx.ev["inactive_fund_account"] = {"status": st2, "body": (txt2 or "")[:300]}
    ctx.ck("payout_to_an_inactive_fund_account_refused_4xx",
           isinstance(st2, int) and 400 <= st2 < 500, ctx.ev["inactive_fund_account"])

    rc, after, _ = ctx.a.payouts_sql(
        "SELECT count(*) FROM payouts WHERE merchant_id='%s'" % mid, note="payout count after")
    ctx.ck("neither_refusal_minted_a_payout_row", before.strip() == after.strip(),
           {"before": before.strip(), "after": after.strip()})


@journey("beneficiary-fund-accounts", "tenant_isolation", priority="P1", profile=BEN_OTHER,
         title="a merchant pays ANOTHER merchant's fund account: the beneficiary boundary does not hold",
         source_ref="payouts fetches the beneficiary from the monolith `fund_accounts_internal/{id}` "
                    "route; the FundAccountResponse DTO that route serves "
                    "(payouts/pkg/api/fetch_fund_account.go, the DTO monolith-stub was built from) "
                    "carries NO owner field, so nothing at that boundary can scope the beneficiary "
                    "to the calling merchant")
def beneficiary_tenant_isolation(ctx):
    """Reported, not patched. This variant is expected to FAIL until the beneficiary boundary is
    scoped; the assertions below are written the way the boundary SHOULD behave, and the evidence
    records exactly what happened instead, including where the money went."""
    mid = ctx.m["merchant_id"]
    victim_fa = ctx.state.get("bene_fa")
    victim_mid = ctx.state.get("bene_merchant")
    if not victim_fa:
        ctx.blocked("journey:beneficiary-fund-accounts/success must run first -- it identifies the "
                    "other merchant's fund account this variant tries to pay",
                    {"then": "rerun run.py --family beneficiary-fund-accounts"})
    ctx.ck("the_two_merchants_are_different", mid != victim_mid,
           {"caller": mid, "owner_of_the_beneficiary": victim_mid})
    caller_before = ctx.a.merchant_balance(mid)
    victim_before = ctx.a.merchant_balance(victim_mid)
    ctx.a.mozart(mid, "hold")
    st, txt = ctx.a.kong("POST", "/v1/payouts", ctx.auth,
                         {"fund_account_id": "fa_" + victim_fa,
                          "account_number": ctx.m["account_number"], "amount": 3000,
                          "currency": "INR", "mode": "IMPS", "purpose": "payout",
                          "queue_if_low_balance": False, "merchant_id": mid,
                          "narration": "m6 bene-cross", "notes": {"m6": "bene"}},
                         {"X-Payout-Idempotency": "m6bc-" + uuid.uuid4().hex[:12]},
                         note="payout to ANOTHER merchant's fund account")
    body = F.jload(txt) or {}
    pid = F.bare(body.get("id") or "")
    ctx.ev["cross_merchant_attempt"] = {"caller": mid, "beneficiary_owner": victim_mid,
                                        "fund_account": victim_fa, "status": st,
                                        "payout_id": pid, "body": (txt or "")[:400]}
    ctx.ck("paying_another_merchant's_fund_account_is_refused_4xx",
           isinstance(st, int) and 400 <= st < 500, ctx.ev["cross_merchant_attempt"])
    if st != 200 or not pid:
        ctx.a.mozart(mid, clear=True)
        return
    # It was accepted. Record precisely what that means, and where the money went.
    row = ctx.wait_terminal(pid, timeout=140)
    caller_after = ctx.a.merchant_balance(mid)
    victim_after = ctx.a.merchant_balance(victim_mid)
    ctx.ev["cross_merchant_outcome"] = {
        "payout_row": row,
        "caller_balance": {"before": caller_before, "after": caller_after},
        "beneficiary_owner_balance": {"before": victim_before, "after": victim_after},
        "monolith_fund_account_record": _monolith_fa(ctx, victim_fa)[1]}
    ctx.ck("the_payout_at_least_stayed_booked_against_the_CALLER",
           (row or {}).get("merchant_id") == mid, row)
    ctx.ck("no_money_left_the_beneficiary_owner's_balance",
           victim_before is not None and victim_after is not None
           and abs(victim_before - victim_after) < 1e-6,
           {"before": victim_before, "after": victim_after})
    ctx.note("DEFECT (reported, not fixed) -- CROSS-TENANT BENEFICIARY USE. Merchant %s created "
             "payout %s (HTTP %s, reached %s) against fund account %s, which belongs to merchant "
             "%s. Mechanism: payouts resolves the beneficiary through the monolith "
             "`GET /fund_accounts_internal/{id}` route, and the FundAccountResponse record that "
             "route serves carries no owner field at all, so there is nothing at that boundary to "
             "scope it with. The debit stayed on the calling merchant and the owner's balance was "
             "untouched, so this is unauthorised USE of another tenant's beneficiary (money can be "
             "pushed to an account the caller does not own and did not register), not theft of "
             "their funds. Production reachability is UNKNOWN and is not inferred: the twin's "
             "monolith-stub was built from the payouts-side DTO "
             "(payouts/pkg/api/fetch_fund_account.go), so whether the real monolith route returns "
             "an owner that payouts ignores, or returns none either, is not decidable from here."
             % (mid, pid, st, (row or {}).get("status"), victim_fa, victim_mid))
    ctx.a.mozart(mid, clear=True)


@journey("beneficiary-fund-accounts", "idempotency", priority="P0", profile=BEN,
         title="paying the same beneficiary repeatedly never duplicates it: one fund-account document, one contact, one hash_lookup row",
         source_ref="CFA dedup is app-level (SHA3-256 hash in hash_lookup, NO unique index and no "
                    "index at all on that collection) -- money-statements.md section 7")
def beneficiary_idempotency(ctx):
    mid, fa = ctx.m["merchant_id"], _fa(ctx)
    before = _cfa_docs(ctx, mid, fa)
    n_fa, n_c = len(before.get("fund_accounts") or []), len(before.get("contacts") or [])
    n_h = len(before.get("hash_lookup") or [])
    ctx.ev["cfa_documents_before"] = {"fund_accounts": n_fa, "contacts": n_c, "hash_lookup": n_h}
    ctx.ck("the_merchant_starts_with_exactly_one_fund_account_and_one_contact",
           (n_fa, n_c) == (1, 1), ctx.ev["cfa_documents_before"])
    ctx.a.mozart(mid, "hold")
    pids = []
    for i in range(2):
        cr = ctx.create(3400 + i, "bene-idem-%d" % i)
        if cr["id"]:
            pids.append(cr["id"])
    ctx.ck("two_payouts_created_to_the_same_beneficiary", len(pids) == 2, pids)
    for pid in pids:
        ctx.a.finish_bank(pid, "success")
    bad = [p for p in pids
           if (ctx.wait_status(p, "processed", timeout=120) or {}).get("status") != "processed"]
    ctx.ck("both_completed", not bad, bad)
    after = _cfa_docs(ctx, mid, fa)
    ctx.ck("no_second_fund_account_document_was_created",
           len(after.get("fund_accounts") or []) == n_fa, after.get("fund_accounts"))
    ctx.ck("no_second_contact_was_created", len(after.get("contacts") or []) == n_c,
           after.get("contacts"))
    ctx.ck("the_hash_lookup_dedup_index_is_unchanged",
           len(after.get("hash_lookup") or []) == n_h,
           {"before": n_h, "after": len(after.get("hash_lookup") or [])})
    rc, out, _ = ctx.a.payouts_sql(
        "SELECT DISTINCT fund_account_id FROM payouts WHERE id IN (%s)"
        % ",".join("'%s'" % p for p in pids), note="both payouts point at one fund account")
    ctx.ck("both_payouts_point_at_the_same_single_fund_account_id",
           [F.bare(l.strip()) for l in out.strip().splitlines() if l.strip()] == [fa],
           {"rows": out.strip(), "expected": fa})
    ctx.a.mozart(mid, clear=True)


@journey("beneficiary-fund-accounts", "async_state", priority="P0", profile=BEN,
         title="the beneficiary is resolved on the async dispatch path, not in the create call; the CFA dual-write fabric is present and its state is recorded",
         source_ref="CFA writes Mongo first then an in-memory event -> SQS (cfa-*-dual-write-queue / "
                    "*-lazy-load, 30s delay) -> an API-monolith worker (money-statements.md section 7); "
                    "payouts-worker-fts-async-processing is what turns a created payout into an FTS transfer")
def beneficiary_async_state(ctx):
    mid, fa = ctx.m["merchant_id"], _fa(ctx)
    ctx.a.mozart(mid, "hold")
    cr = ctx.create(3900, "bene-async")
    pid = cr["id"]
    ctx.ck("create_200", cr["status"] == 200 and bool(pid), cr.get("raw"))
    if not pid:
        return
    ctx.ck("the_create_response_is_non_terminal",
           cr.get("response_status") not in ("processed", "reversed", "failed"),
           cr.get("response_status"))
    ctx.ck("no_fts_transfer_exists_at_the_moment_of_the_create_response",
           ctx.a.transfer(pid) is None or (ctx.a.transfer(pid) or {}).get("status") in
           (None, "CREATED"), ctx.a.transfer(pid))
    init = ctx.wait_status(pid, "initiated", timeout=60)
    ctx.ck("a_worker_moved_it_to_initiated", (init or {}).get("status") == "initiated", init)
    t = F.wait_until(lambda: (lambda x: x if x and x.get("attempt_id") else None)(ctx.a.transfer(pid)),
                     timeout=45, interval=2)
    ctx.ck("the_beneficiary_became_a_real_fts_transfer_only_on_the_async_path", bool(t), t)
    hits = ctx.a.worker_log_hits(pid)
    ctx.ev["worker_log_hits"] = hits
    ctx.ck("the_dispatch_is_attributable_to_a_real_payouts_worker_container",
           any(k.startswith("payouts-worker-") for k in hits), hits)
    ctx.a.finish_bank(pid, "success")
    ctx.ck("the_payout_to_that_beneficiary_completed",
           (ctx.wait_status(pid, "processed", timeout=120) or {}).get("status") == "processed", None)

    # the CFA dual-write fabric itself: containers, queues, and the API-DB target
    fabric = {}
    for svc in ("cfa-server", "cfa-worker-contact", "cfa-worker-fa"):
        fabric[svc] = ctx.a.container_state(svc)
    ctx.ck("the_cfa_server_and_both_cfa_workers_are_running_and_healthy",
           all(v["status"] == "running" and v["health"] in ("healthy", "none")
               for v in fabric.values()), fabric)
    queues = {q: ctx.a.queue_url(q) for q in ("contact-dual-write-queue", "contact-lazy-load",
                                              "fund-account-dual-write-queue",
                                              "fund-account-lazy-load")}
    ctx.ev["cfa_dual_write_queues"] = queues
    ctx.ck("every_cfa_dual-write/lazy-load_queue_exists", all(queues.values()), queues)
    rc, tables, _ = ctx.a.apidb("SHOW TABLES LIKE 'fund_accounts'; SHOW TABLES LIKE 'contacts'",
                                note="the API-DB tables CFA dual-writes into")
    ctx.ck("the_api-db_dual-write_target_tables_exist_in_mysql-apidb-stub",
           "fund_accounts" in (tables or ""), (tables or "").strip())
    rc, cnt, _ = ctx.a.apidb("SELECT (SELECT count(*) FROM fund_accounts), (SELECT count(*) FROM contacts)",
                             note="rows the CFA dual-write has landed so far")
    ctx.ev["cfa_dual_write_rows_in_apidb"] = (cnt or "").strip()
    ctx.ev["cfa_dual_write_state"] = {
        "state": "idle-by-design",
        "producer_that_would_feed_it": "CFA's own create (Mongo write -> in-memory event -> SQS, "
                                       "30s delay). The only way to trigger it is a CFA create, "
                                       "and cfa-server's grpc-gateway cannot reach its own gRPC "
                                       "backend in this arena -- see "
                                       "journey:beneficiary-fund-accounts/cfa_api.",
        "rows_in_api_db": (cnt or "").strip()}
    ctx.note("CFA dual-write fabric is present and healthy (cfa-server + cfa-worker-contact + "
             "cfa-worker-fa, all four queues, api_local.fund_accounts/contacts) but idle: its only "
             "producer is a CFA create, which is unreachable in this arena "
             "(journey:beneficiary-fund-accounts/cfa_api carries the exact dependency).")
    ctx.a.mozart(mid, clear=True)


@journey("beneficiary-fund-accounts", "cfa_api", priority="P1", profile=BEN,
         title="create a contact + bank-account fund account through the REAL CFA API (create / IFSC validation / hash_lookup dedup)",
         source_ref="cfa grpc-gateway /v1/contacts and /v1/fund_accounts on cfa-server:8081, "
                    "Basic Server.Auth.Payouts (config/templates/base/cfa/arena.toml:27-30); "
                    "cfa/internal/fund_accounts/validate.go bank_account rules")
def beneficiary_cfa_api(ctx):
    """Drives the real CFA HTTP surface. Everything this variant needs is written; the arena's
    cfa-server cannot serve it, and the probe below is the evidence."""
    basic = _cfa_basic()
    probe = {}
    st0, body0 = ctx.a.jhttp("GET", CFA + "/v1/fund_accounts?merchant_id=" + ctx.m["merchant_id"],
                             basic=basic, note="CFA fund-account list (grpc-gateway route)")
    probe["GET /v1/fund_accounts"] = {"status": st0, "body": body0}
    contact = {"contact": {"merchant_id": ctx.m["merchant_id"], "name": "M6 Journey Beneficiary",
                           "type": "vendor", "email": "m6@arena.test",
                           "reference_id": "m6-" + uuid.uuid4().hex[:10]}}
    st1, body1 = ctx.a.jhttp("POST", CFA + "/v1/contacts", contact, basic=basic,
                             note="CFA contact create (the real create route)")
    probe["POST /v1/contacts"] = {"status": st1, "body": body1}
    good_fa = {"fund_account": {"merchant_id": ctx.m["merchant_id"], "account_type": "bank_account",
                                "contact_id": "", "bank_account": {
                                    "name": "M6 Journey Beneficiary", "ifsc": "RATN0000001",
                                    "account_number": "1112220999"}}}
    st2, body2 = ctx.a.jhttp("POST", CFA + "/v1/fund_accounts", good_fa, basic=basic,
                             note="CFA bank-account fund-account create")
    probe["POST /v1/fund_accounts (valid)"] = {"status": st2, "body": body2}
    bad_fa = json.loads(json.dumps(good_fa))
    bad_fa["fund_account"]["bank_account"]["ifsc"] = "NOTANIFSC"
    st3, body3 = ctx.a.jhttp("POST", CFA + "/v1/fund_accounts", bad_fa, basic=basic,
                             note="CFA create with a malformed IFSC (validation leg)")
    probe["POST /v1/fund_accounts (bad ifsc)"] = {"status": st3, "body": body3}
    ctx.ev["cfa_api_probe"] = probe

    gateway_dead = any(str((v.get("body") or {}).get("message", "")).find("127.0.0.1:9") >= 0
                       for v in probe.values() if isinstance(v.get("body"), dict))
    if st2 != 200 or gateway_dead:
        ctx.blocked(
            "cfa-server's grpc-gateway must be able to reach its own gRPC backend. The real CFA "
            "routes ARE registered on cfa-server:8081 (an unknown path answers "
            "404 {\"code\":5,\"message\":\"Not Found\"} while /v1/contacts and /v1/fund_accounts "
            "answer 503 {\"code\":14, ...}), but every call fails with "
            "'connection error: desc = \"transport: Error while dialing: dial tcp 127.0.0.1:9: "
            "connect: connection refused\"' -- the gateway's dial to its own gRPC server "
            "(Server.ServerAddresses.Grpc = \":8080\") is being routed through the container's "
            "HTTP_PROXY=http://127.0.0.1:9 (the discard port), which NO_PROXY does not cover for a "
            "hostless \":8080\" target. cfa-server's other planes are healthy (the internal server "
            "on :8082 serves /metrics), so this is the gateway->gRPC hop alone. Needs the "
            "cfa-server compose block to stop proxying its own loopback gRPC dial (or the gateway "
            "to be pointed at 127.0.0.1:8080 explicitly) -- ENV2_COMPOSE/docker-compose.yml and "
            "config/templates/base/cfa/arena.toml, both the runtime lane's files -- plus the "
            "controlled reboot. Everything this variant asserts (contact create, bank_account "
            "create, malformed-IFSC rejection, hash_lookup dedup on a repeat create) is written "
            "and runs unmodified afterwards.",
            {"probe": probe,
             "note": "the create/validate/dedup facts are still asserted where they ARE reachable "
                     "in journey:beneficiary-fund-accounts/{success,idempotency}",
             "then": "rerun run.py --only beneficiary-fund-accounts/cfa_api"})

    ctx.ck("cfa_contact_create_200", st1 == 200, {"status": st1, "body": body1})
    ctx.ck("cfa_bank_account_fund_account_create_200", st2 == 200, {"status": st2})
    ctx.ck("cfa_rejected_the_malformed_ifsc_4xx",
           isinstance(st3, int) and 400 <= st3 < 500, {"status": st3, "body": body3})
    st4, body4 = ctx.a.jhttp("POST", CFA + "/v1/fund_accounts", good_fa, basic=basic,
                             note="CFA create replay (hash_lookup dedup leg)")
    ctx.ck("re-creating_the_same_fund_account_returns_the_same_id(hash_lookup dedup)",
           st4 == 200 and (body4 or {}).get("id") == (body2 or {}).get("id"),
           {"first": (body2 or {}).get("id"), "second": (body4 or {}).get("id")})
