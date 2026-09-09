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

The `cfa_api` variant drives the REAL merchant-facing CFA HTTP surface (cfa-server's grpc-gateway
on :8081). Its request/response shapes come from the proto, not from guesswork:

  * `rzp/x/x-cfa/contact/v1/contact_api.proto:11-13` and
    `rzp/x/x-cfa/fund_account/v1/fund_account_api.proto:11-13` map both creates with
    `body:"*"`, so the JSON body is the request message's OWN TOP-LEVEL fields -- there is no
    `{"contact": {...}}` / `{"fund_account": {...}}` envelope (`contact` is a *string* field on
    `ContactCreateRequest`, the phone number, which is why an envelope answers
    `400 proto: (line 1:13): invalid value for string field contact: {`);
  * neither request message carries a `merchant_id` field at all
    (`contact.proto:32-43`, `fund_account.proto:9-26`). CFA takes the merchant from the
    `X-Merchant-Id` HEADER: `cfa/internal/constant/constant.go:15` defines it,
    `cfa/internal/server/server.go:47-62` whitelists exactly
    authorization / x-task-id / x-devstack / x-merchant-id into the gRPC metadata,
    `interceptor/tag.go:112-116` lifts it into the tags and
    `contextkey.GetMerchantID` is what both create handlers read
    (`api_controller/contact/server.go:55`, `api_controller/fund_account/server.go:55`);
  * auth is HTTP Basic against `[Server.Auth.Payouts]`
    (ENV2_COMPOSE/config/templates/base/cfa/arena.toml:33-35), i.e. the same
    `payouts` / `{{SECRET.auth_cfa_payouts}}` pair payouts itself uses
    (config/templates/base/payouts/arena.toml:809-811) -- verified in
    `cfa/internal/server/interceptor/basicauth.go:98-140`.
"""
import copy
import uuid

import framework as F
from framework import journey

BEN = "beneficiary"
BEN_OTHER = "beneficiaryother"
CFA = "http://cfa-server:8081"

# RATN0000001 -> bank code RATN, branch 000001, both present in cfa/pkg/ifsc/IFSC.json, so it
# survives the full IFSC check in cfa/internal/fund_accounts/validate.go (length 11, alphanumeric,
# then the static branch-code lookup) and CFA resolves its bank name.
CFA_IFSC = "RATN0000001"
CFA_BANK_NAME = "RBL Bank"


def _cfa_basic():
    return "payouts:" + F.P._pw("auth_cfa_payouts.txt")


def _cfa_hdr(mid):
    """CFA never reads the merchant from the body -- see the module docstring."""
    return {"X-Merchant-Id": mid}


def _cfa_err(body):
    """The rzp.common.error.v1.Error detail grpc-gateway renders for a CFA error."""
    if not isinstance(body, dict):
        return {}
    d = (body.get("details") or [{}])[0] or {}
    return {"code": d.get("code"), "reason": d.get("reason"),
            "message": body.get("message"), "description": d.get("description")}


def _cfa_bank(body):
    """CreateFundAccount renders snake_case, GetFundAccountById renders camelCase; accept both."""
    if not isinstance(body, dict):
        return {}
    b = body.get("bank_account") or body.get("bankAccount") or {}
    return {"ifsc": b.get("ifsc"),
            "name": b.get("name"),
            "bank_name": b.get("bank_name") or b.get("bankName"),
            "account_number": b.get("account_number") or b.get("accountNumber")}


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
    """The record payouts actually reads. Since M7 the monolith's GET fund_accounts_internal/{id} is served by the shared
    ingress (substitutes/api-ingress, FundAccount/Service.php fetch: findByPublicIdAndMerchant) to the payouts_service
    identity (rzp_live + [api.auth] secret) scoped by X-Razorpay-Account; monolith-stub retired that route (its
    owner-less record was the M6 D-7 cross-tenant mechanism)."""
    st, body = ctx.a.jhttp("GET", "http://api-ingress:8080/v1/fund_accounts_internal/fa_" + F.bare(fa_id),
                           headers={"X-Razorpay-Account": ctx.m["merchant_id"]}, basic=F.D._mono_basic(),
                           note="the fund-account route payouts actually reads (api-ingress, payouts_service identity)")
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
        "state": "idle -- consumers healthy, producer cannot publish",
        "producer_that_would_feed_it": "CFA's own create (Mongo write -> in-memory event -> SQS, "
                                       "30s delay). The create itself IS reachable now "
                                       "(journey:beneficiary-fund-accounts/cfa_api creates a real "
                                       "contact + fund account), but cfa-server's SQS publish "
                                       "posts to the real https://sqs.ap-south-1.amazonaws.com/ "
                                       "and is discarded by the container proxy -- the exact "
                                       "evidence and the config reason are on that variant.",
        "rows_in_api_db": (cnt or "").strip()}
    ctx.note("CFA dual-write fabric is present and healthy (cfa-server + cfa-worker-contact + "
             "cfa-worker-fa, all four queues, api_local.fund_accounts/contacts) but idle: a real "
             "CFA create now happens (journey:beneficiary-fund-accounts/cfa_api) and still "
             "publishes nothing, because cfa-server's eventsystem accepts only the plain \"sqs\" "
             "driver, which ignores [Queue.Sqs].Endpoint and posts to real AWS. That variant "
             "carries the exact dependency and the measured publish failures.")
    ctx.a.mozart(mid, clear=True)


@journey("beneficiary-fund-accounts", "cfa_api", priority="P1", profile=BEN,
         title="create a contact + bank-account fund account through the REAL CFA API (create / IFSC validation / hash_lookup dedup)",
         source_ref="cfa grpc-gateway /v1/contacts and /v1/fund_accounts on cfa-server:8081, "
                    "body:\"*\" top-level DTOs from rzp/x/x-cfa/{contact,fund_account}/v1, merchant "
                    "from the X-Merchant-Id header (cfa/internal/server/server.go:47-62), Basic "
                    "Server.Auth.Payouts (config/templates/base/cfa/arena.toml:33-35); "
                    "cfa/internal/fund_accounts/validate.go + constants.go bank_account rules")
def beneficiary_cfa_api(ctx):
    """Drives the real merchant-facing CFA HTTP surface for THIS journey's fresh merchant:
    contact create, bank-account fund-account create, the bank_account validation matrix, and the
    app-level hash_lookup dedup. Runs last in the family (VARIANT_ORDER) because it is the only
    variant that adds a beneficiary to the merchant."""
    mid = ctx.m["merchant_id"]
    basic, hdr = _cfa_basic(), _cfa_hdr(mid)
    ctx.a.hide(basic.split(":", 1)[1])

    # ---- the boundary itself: auth and the merchant header -----------------
    st, body = ctx.a.jhttp("GET", CFA + "/v1/fund_accounts", headers=hdr,
                           note="CFA list without Basic credentials")
    ctx.ck("the_real_cfa_route_refuses_a_call_without_Basic_credentials(401 CFA0000008)",
           st == 401 and _cfa_err(body).get("code") == "CFA0000008",
           {"status": st, "error": _cfa_err(body)})
    st, body = ctx.a.jhttp("GET", CFA + "/v1/fund_accounts", basic=basic,
                           note="CFA list without the X-Merchant-Id header")
    ctx.ck("and_refuses_a_call_without_the_X-Merchant-Id_header(400 CFA000001 'merchant id is required')",
           st == 400 and _cfa_err(body).get("code") == "CFA000001",
           {"status": st, "error": _cfa_err(body)})
    st, body = ctx.a.jhttp("GET", CFA + "/v1/fund_accounts", headers=hdr, basic=basic,
                           note="CFA fund-account list (grpc-gateway route, authenticated)")
    ctx.ck("the_grpc-gateway_reaches_its_own_gRPC_backend_and_serves_the_real_route",
           st == 200 and isinstance(body, dict) and body.get("entity") == "collection",
           {"status": st, "body": body})

    before = _cfa_docs(ctx, mid, _fa(ctx))
    n_fa_before = len(before.get("fund_accounts") or [])

    # ---- contact create (ContactCreateRequest, body:"*") -------------------
    # `reference_id` is deliberately NOT sent: see the recorded arena gap at the end of this driver.
    contact = {"name": "M6 Journey Beneficiary", "type": "vendor",
               "email": "m6-bene@arena.test", "contact": "9876500001"}
    st1, c1 = ctx.a.jhttp("POST", CFA + "/v1/contacts", contact, headers=hdr, basic=basic,
                          note="CFA contact create (the real create route)")
    cid = (c1 or {}).get("id")
    ctx.ck("cfa_contact_create_200", st1 == 200 and bool(cid), {"status": st1, "body": c1})
    ctx.ck("the_contact_is_minted_new_and_active",
           (c1 or {}).get("is_created") is True and (c1 or {}).get("active") is True
           and (c1 or {}).get("entity") == "contact", c1)
    st2, c2 = ctx.a.jhttp("POST", CFA + "/v1/contacts", contact, headers=hdr, basic=basic,
                          note="CFA contact create replay (contact dedup leg)")
    ctx.ck("re-posting_the_same_contact_returns_the_same_id_and_is_created=false",
           st2 == 200 and (c2 or {}).get("id") == cid and (c2 or {}).get("is_created") is False,
           {"first": cid, "second": (c2 or {}).get("id"),
            "is_created": (c2 or {}).get("is_created")})
    if not cid:
        return

    # ---- bank-account fund account (FundAccountCreateRequest, body:"*") ----
    # Deterministic per merchant, so a re-run on the same campaign dedups onto the same document
    # instead of accumulating beneficiaries.
    acct = ("M6BENE" + mid[-8:])
    good = {"contact_id": cid, "account_type": "bank_account",
            "bank_account": {"name": "M6 Journey Beneficiary", "ifsc": CFA_IFSC,
                             "account_number": acct}}
    st3, f1 = ctx.a.jhttp("POST", CFA + "/v1/fund_accounts", good, headers=hdr, basic=basic,
                          note="CFA bank-account fund-account create")
    fa_id = (f1 or {}).get("id")
    ctx.ck("cfa_bank_account_fund_account_create_200", st3 == 200 and bool(fa_id),
           {"status": st3, "body": f1})
    ctx.ck("it_is_an_active_bank_account_fund_account_bound_to_that_contact",
           (f1 or {}).get("account_type") == "bank_account" and (f1 or {}).get("active") is True
           and (f1 or {}).get("is_created") is True and (f1 or {}).get("contact_id") == cid, f1)
    ctx.ck("cfa_echoed_the_bank_account_and_resolved_its_bank_from_the_ifsc(IFSC.json lookup)",
           _cfa_bank(f1) == {"ifsc": CFA_IFSC, "name": "M6 Journey Beneficiary",
                             "bank_name": CFA_BANK_NAME, "account_number": acct},
           _cfa_bank(f1))
    if not fa_id:
        return

    # ---- the bank_account validation matrix -------------------------------
    # cfa/internal/fund_accounts/validate.go + constants.go: IFSC 11 alphanumeric AND present in
    # the bundled IFSC.json; account number 5-35 alphanumeric; beneficiary name 3-120.
    rejects = {}
    for label, field, value, want in (
            ("malformed ifsc (9 chars)", "ifsc", "NOTANIFSC", "CFA0000016"),
            ("11-char alphanumeric ifsc whose branch is not in IFSC.json", "ifsc",
             "RATN0999999", "CFA0000016"),
            ("account number shorter than 5", "account_number", "1234", "CFA0000017"),
            ("account number that is not alphanumeric", "account_number", "11-22-33", "CFA0000017"),
            ("beneficiary name shorter than 3", "name", "Jo", "CFA0000018")):
        bad = copy.deepcopy(good)
        bad["bank_account"][field] = value
        st, b = ctx.a.jhttp("POST", CFA + "/v1/fund_accounts", bad, headers=hdr, basic=basic,
                            note="CFA create with %s (validation leg)" % label)
        rejects[label] = {"status": st, "error": _cfa_err(b), "expected_code": want}
    ctx.ev["cfa_bank_account_validation_matrix"] = rejects

    bad_ifsc = rejects["malformed ifsc (9 chars)"]
    ctx.ck("cfa_rejected_the_malformed_ifsc_with_the_exact_validation_error"
           "(400 CFA0000016 'Validation error: Invalid IFSC Code')",
           bad_ifsc["status"] == 400 and bad_ifsc["error"].get("code") == "CFA0000016"
           and bad_ifsc["error"].get("message") == "Validation error: Invalid IFSC Code"
           and bad_ifsc["error"].get("reason") == "input_validation_failed", bad_ifsc)
    ctx.ck("every_bank_account_rule_in_the_matrix_is_refused_with_its_own_CFA_error_code",
           all(v["status"] == 400 and v["error"].get("code") == v["expected_code"]
               for v in rejects.values()), rejects)

    # ---- hash_lookup dedup -------------------------------------------------
    st4, f2 = ctx.a.jhttp("POST", CFA + "/v1/fund_accounts", good, headers=hdr, basic=basic,
                          note="CFA create replay (hash_lookup dedup leg)")
    ctx.ck("re-creating_the_same_fund_account_returns_the_same_id(hash_lookup dedup)",
           st4 == 200 and (f2 or {}).get("id") == fa_id,
           {"first": fa_id, "second": (f2 or {}).get("id")})
    ctx.ck("and_reports_is_created=false_-_it_returned_the_existing_document",
           (f2 or {}).get("is_created") is False, {"is_created": (f2 or {}).get("is_created")})

    after = _cfa_docs(ctx, mid, fa_id)
    mine = [d for d in (after.get("fund_accounts") or []) if d.get("id") == fa_id]
    hl = after.get("hash_lookup") or []
    ctx.ck("cfa_holds_exactly_ONE_fund_account_document_for_it_-_no_second_document_was_minted",
           len(mine) == 1, mine)
    ctx.ck("the_two_creates_plus_the_five_rejected_ones_added_exactly_one_beneficiary_to_this_merchant",
           len(after.get("fund_accounts") or []) == n_fa_before + 1,
           {"before": n_fa_before, "after": len(after.get("fund_accounts") or [])})
    ctx.ck("exactly_one_hash_lookup_row_indexes_it_and_carries_the_document's_own_hash",
           len(hl) == 1 and mine and hl[0].get("hash") == mine[0].get("hash")
           and hl[0].get("entity_type") == "fund_accounts",
           {"hash_lookup": hl, "fund_account_hash": (mine or [{}])[0].get("hash")})

    # ---- readable back through the real API --------------------------------
    st5, g = ctx.a.jhttp("GET", CFA + "/v1/fund_accounts/fa_" + fa_id, headers=hdr, basic=basic,
                         note="CFA read-back of the fund account just created")
    ctx.ck("the_created_fund_account_is_readable_back_through_the_real_cfa_api_and_agrees_with_the_create",
           st5 == 200 and (g or {}).get("id") == fa_id and (g or {}).get("active") is True
           and _cfa_bank(g) == _cfa_bank(f1), {"status": st5, "body": g})

    # ---- the record payouts reads, and the bridge that is missing ----------
    st6, mono_new = _monolith_fa(ctx, fa_id)
    st7, mono_prov = _monolith_fa(ctx, _fa(ctx))
    ctx.ev["monolith_record"] = {"cfa_created_fund_account": {"id": fa_id, "status": st6},
                                 "provisioned_fund_account": {"id": _fa(ctx), "status": st7}}
    ctx.ck("the_monolith_route_payouts_reads_still_resolves_this_merchant's_provisioned_beneficiary",
           st7 == 200 and bool(mono_prov), {"status": st7})

    sqs = ctx.a.worker_log_hits("sqs.ap-south-1.amazonaws.com", services=["cfa-server"], since="10m")
    ctx.ev["cfa_dual_write_publish_failures"] = sqs
    ctx.note(
        "GAP (recorded, not fixed) -- a beneficiary created through the REAL CFA API never reaches "
        "the record payouts reads, so it cannot yet be paid. Fund account %s exists in CFA "
        "(create 200, read-back 200, one Mongo document, one hash_lookup row) but the monolith "
        "`GET /fund_accounts_internal/%s` answers %s, while the same route resolves the "
        "provisioned beneficiary (%s). Mechanism, measured: CFA mirrors a create into the API "
        "monolith only through its dual-write event -> SQS -> cfa-worker-fa, and cfa-server's SQS "
        "publish fails in this arena -- its own eventsystem rejects any driver other than the "
        "literal \"sqs\" (cfa/internal/eventsystem/service.go, quoted in "
        "config/templates/base/cfa/arena.toml:99-109), and that driver ignores [Queue.Sqs].Endpoint "
        "and posts to the real https://sqs.ap-south-1.amazonaws.com/, which the container's proxy "
        "discards: cfa-server logged %s such 'proxyconnect tcp: dial tcp 127.0.0.1:9: connection "
        "refused' failures during this journey. On top of that the arena's monolith-stub serves "
        "/fund_accounts_internal from a JSON seed "
        "(ENV2_COMPOSE/substitutes/monolith-stub/server.py:85,269-276), not from the "
        "mysql-apidb-stub table cfa-worker-fa would write, so even a working dual-write would not "
        "surface there. The payout leg for a beneficiary is therefore asserted where it IS "
        "reachable -- journey:beneficiary-fund-accounts/success pays the provisioned one end to "
        "end. Fixing this needs the cfa-server queue/proxy wiring plus a monolith-stub read path "
        "over api_local.fund_accounts, both runtime-lane files."
        % (fa_id, fa_id, st6, st7, sqs.get("cfa-server", 0)))
    ctx.note(
        "GAP (recorded, not fixed) -- CFA contact create with a `reference_id` 500s in this arena: "
        "the handler looks the reference up in the API DB and mysql-apidb-stub's `contacts` table "
        "has no `reference_id` column (`Error 1054 (42S22): Unknown column 'contacts.reference_id' "
        "in 'where clause'` -> CONTACT_NOT_FOUND_IN_API -> SERVER_ERROR). It is an arena schema "
        "gap in the api-db stub, not CFA behaviour, so this driver creates its contact without a "
        "reference_id; every other contact field is exercised.")
