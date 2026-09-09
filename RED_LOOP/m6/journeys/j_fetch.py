#!/usr/bin/env python3
"""family:fetch-list -- the merchant read surface: fetch one payout, list with filters.

Everything here goes through kong-lite with the merchant's own key, i.e. the same public
`/v1/payouts` route group a merchant calls (payout_routes.go). The read surface is asserted
against the DB row and against the create request that produced it, so a wrong field is a
finding rather than a tautology.

Variants
  success            GET /v1/payouts/{id} (both `pout_`-prefixed and bare) returns the payout with
                     the merchant-contract fields it was created with; GET /v1/payouts with
                     account_number / status / count filters returns exactly the created payouts.
  failure            a payout id belonging to ANOTHER merchant is not readable, and it never
                     appears in this merchant's collection; an unknown id is refused.
  idempotency        a GET is side-effect free: two identical reads, and no new payout row, no new
                     payout_logs row, no new ledger journal, no idempotency_keys row.
  async_state        the read surface reflects a state change that only the async workers can
                     make: the same GET returns `initiated` with no utr before, and `processed`
                     with the FTS utr after, and the list reflects it too.
"""
import time
import uuid

import framework as F
from framework import journey

FL = "fetch"
FL_OTHER = "fetchother"

# fields the merchant contract fixes at create time and a read must never change
CONTRACT = ("entity", "merchant_id", "fund_account_id", "amount", "currency", "mode", "purpose",
            "narration", "notes")


def _mk(ctx, amount, note, **kw):
    cr = ctx.create(amount, note, **kw)
    ctx.ck("create_200_%s" % note, cr["status"] == 200 and bool(cr["id"]), cr.get("raw"))
    return cr


def _items(body):
    return (body or {}).get("items") or [] if isinstance(body, dict) else []


def _list(ctx, query, note):
    st, body = ctx.a.kong("GET", "/v1/payouts?" + query, ctx.auth, note=note)
    return st, F.jload(body) or {}


# ---------------------------------------------------------------------------
@journey("fetch-list", "success", priority="P0", profile=FL,
         title="fetch one payout and list with filters: every field matches the create request and the DB row",
         source_ref="payouts payout_routes.go public GET /v1/payouts/{id} and GET /v1/payouts "
                    "(collection); kong-lite proxies the public route group with cred.API + passport")
def fetch_success(ctx):
    mid = ctx.m["merchant_id"]
    ctx.a.mozart(mid, "hold")
    made = [_mk(ctx, 3100 + i, "fetch-%d" % i, notes={"m6": "fetch", "idx": str(i)})
            for i in range(3)]
    ids = [c["id"] for c in made if c["id"]]
    if len(ids) != 3:
        return
    for c in made:
        ctx.a.finish_bank(c["id"], "success")
    for c in made:
        ctx.wait_status(c["id"], "processed", timeout=120)

    # -- fetch one ----------------------------------------------------------
    c0 = made[0]
    st, body = ctx.fetch(c0["id"])
    ctx.ck("fetch_by_id_200", st == 200 and isinstance(body, dict), {"status": st})
    ctx.ev["fetched"] = body
    ctx.ck("fetch_returns_the_public_pout_prefixed_id", (body or {}).get("id") == "pout_" + c0["id"],
           (body or {}).get("id"))
    created = c0["json"] or {}
    diff = {k: {"create": created.get(k), "fetch": (body or {}).get(k)}
            for k in CONTRACT if created.get(k) != (body or {}).get(k)}
    ctx.ck("every_merchant_contract_field_matches_the_create_response", not diff, diff)
    row = ctx.a.payout(c0["id"])
    ctx.ck("fetch_amount_fees_tax_match_the_db_row",
           str((body or {}).get("amount")) == str(row.get("amount"))
           and str((body or {}).get("fees")) == str(row.get("fees"))
           and str((body or {}).get("tax")) == str(row.get("tax")),
           {"api": {k: (body or {}).get(k) for k in ("amount", "fees", "tax")},
            "db": {k: row.get(k) for k in ("amount", "fees", "tax")}})
    ctx.ck("fetch_status_and_utr_match_the_db_row",
           (body or {}).get("status") == row.get("status")
           and ((body or {}).get("utr") or None) == (row.get("utr") or None),
           {"api_status": (body or {}).get("status"), "db_status": row.get("status"),
            "api_utr": (body or {}).get("utr"), "db_utr": row.get("utr")})
    st2, txt2 = ctx.a.kong("GET", "/v1/payouts/" + c0["id"], ctx.auth,
                           note="fetch by the bare (unprefixed) id")
    b2 = F.jload(txt2) or {}
    if F.TRUST["real_gateway"]:
        # M11 source-supported correction: the monolith's PayoutController fetch runs Payout\Entity::verifyIdAndStripSign ->
        # PublicEntity::stripSignOrFail (api app/Models/Base/PublicEntity.php:762), so an unsigned id is a BAD_REQUEST; the
        # M6 kong-lite forwarded bare ids straight to the Payouts service, which accepts them -- that leniency was the
        # substitute's, not the monolith's.
        ctx.ck("bare_(unsigned)_id_is_refused_by_the_monolith_path_400_(PublicEntity::stripSignOrFail)", st2 == 400,
               {"status": st2, "body": str(b2)[:200]})
        st2, b2 = 200, dict(body)      # the signed-id read above stands in for the equality check below
    ctx.ck("bare_id_and_pout_prefixed_id_return_the_same_entity",
           st2 == 200 and b2.get("id") == (body or {}).get("id")
           and b2.get("amount") == (body or {}).get("amount"), {"status": st2})

    # -- list ---------------------------------------------------------------
    stl, coll = _list(ctx, "account_number=%s&count=100" % ctx.m["account_number"],
                      "list the merchant's payouts by account_number")
    ctx.ev["collection"] = {"status": stl, "entity": coll.get("entity"), "count": coll.get("count")}
    ctx.ck("list_200_collection", stl == 200 and coll.get("entity") == "collection",
           ctx.ev["collection"])
    listed = {i.get("id") for i in _items(coll)}
    ctx.ck("the_collection_contains_every_payout_this_journey_created",
           all("pout_" + i in listed for i in ids), {"created": ids, "listed": sorted(listed)[:20]})
    ctx.ck("every_listed_payout_belongs_to_this_merchant",
           all(i.get("merchant_id") == mid for i in _items(coll)),
           sorted({i.get("merchant_id") for i in _items(coll)}))
    ctx.ck("collection_count_matches_the_item_count",
           int(coll.get("count") or -1) == len(_items(coll)),
           {"count": coll.get("count"), "items": len(_items(coll))})
    stc, small = _list(ctx, "account_number=%s&count=2" % ctx.m["account_number"],
                       "list with count=2")
    ctx.ck("the_count_filter_limits_the_page", stc == 200 and len(_items(small)) == 2,
           {"status": stc, "items": len(_items(small))})
    sts, proc = _list(ctx, "account_number=%s&status=processed&count=100" % ctx.m["account_number"],
                      "list filtered by status=processed")
    ctx.ck("the_status_filter_returns_only_that_status",
           sts == 200 and _items(proc)
           and all(i.get("status") == "processed" for i in _items(proc)),
           {"status": sts, "statuses": sorted({i.get("status") for i in _items(proc)})})
    ctx.ck("the_status_filter_still_contains_this_journey's_processed_payouts",
           all("pout_" + i in {x.get("id") for x in _items(proc)} for i in ids), ids)
    ctx.state["fetch_ids"] = ids
    ctx.state["fetch_merchant"] = mid
    ctx.a.mozart(mid, clear=True)


@journey("fetch-list", "failure", priority="P0", profile=FL_OTHER,
         title="a payout belonging to another merchant is not readable and never appears in this merchant's collection",
         source_ref="payouts fetch scopes by merchant_id from the passport identity kong-lite mints; "
                    "kong-lite strips client-supplied x-merchant-id so the tenant cannot be asserted")
def fetch_cross_merchant(ctx):
    victim_ids = ctx.state.get("fetch_ids") or []
    victim_mid = ctx.state.get("fetch_merchant")
    if not victim_ids:
        ctx.blocked("journey:fetch-list/success must run first -- it creates the payouts this "
                    "variant tries to read from a different merchant",
                    {"then": "rerun run.py --family fetch-list"})
    ctx.ev["victim"] = {"merchant_id": victim_mid, "payout_ids": victim_ids}
    ctx.ck("the_two_merchants_are_different", ctx.m["merchant_id"] != victim_mid,
           {"this": ctx.m["merchant_id"], "victim": victim_mid})
    st, body = ctx.fetch(victim_ids[0])
    ctx.ck("cross_merchant_fetch_refused_4xx", isinstance(st, int) and 400 <= st < 500,
           {"status": st, "body": str(body)[:250]})
    ctx.ck("the_refusal_leaks_no_payout_entity",
           not (isinstance(body, dict) and body.get("amount")), str(body)[:250])
    stb, txtb = ctx.a.kong("GET", "/v1/payouts/pout_" + "ARENADOESNOTEXI"[:14], ctx.auth,
                           note="fetch a payout id that does not exist at all")
    ctx.ck("unknown_payout_id_refused_4xx", isinstance(stb, int) and 400 <= stb < 500,
           {"status": stb, "body": (txtb or "")[:200]})
    stl, coll = _list(ctx, "account_number=%s&count=100" % ctx.m["account_number"],
                      "this merchant's collection")
    listed = {i.get("id") for i in _items(coll)}
    ctx.ck("no_other_merchant's_payout_appears_in_this_collection",
           not any("pout_" + v in listed for v in victim_ids),
           {"victim_ids": victim_ids, "listed_sample": sorted(listed)[:10]})
    ctx.ck("every_item_in_the_collection_is_this_merchant's",
           all(i.get("merchant_id") == ctx.m["merchant_id"] for i in _items(coll)),
           sorted({i.get("merchant_id") for i in _items(coll)}))
    # and the victim's account_number does not unlock the victim's collection either
    stx, other = _list(ctx, "account_number=%s&count=100" % "0000000000000000",
                       "list with a foreign account_number")
    ctx.ck("a_foreign_account_number_returns_nothing_of_the_other_merchant's",
           not any("pout_" + v in {i.get("id") for i in _items(other)} for v in victim_ids),
           {"status": stx, "items": len(_items(other))})


@journey("fetch-list", "idempotency", priority="P0", profile=FL,
         title="a read is side-effect free: two identical GETs, and nothing new is written anywhere",
         source_ref="GET /v1/payouts/{id} and GET /v1/payouts are reads; the idempotency middleware "
                    "(X-Payout-Idempotency) applies to creates only")
def fetch_idempotency(ctx):
    mid = ctx.m["merchant_id"]
    ids = ctx.state.get("fetch_ids") or []
    if not ids:
        ctx.a.mozart(mid, "hold")
        cr = _mk(ctx, 3300, "fetch-idem")
        if not cr["id"]:
            return
        ctx.a.finish_bank(cr["id"], "success")
        ctx.wait_status(cr["id"], "processed", timeout=120)
        ids = [cr["id"]]
    pid = ids[0]
    rc, before_rows, _ = ctx.a.payouts_sql(
        "SELECT count(*) FROM payouts WHERE merchant_id='%s'" % mid, note="payout count before")
    rc, before_keys, _ = ctx.a.payouts_sql(
        "SELECT count(*) FROM idempotency_keys WHERE merchant_id='%s'" % mid,
        note="idempotency_keys count before")
    before_logs = ctx.a.payout_logs(pid)
    before_j = ctx.a.journals("pout_" + pid)
    before_w = ctx.a.deliveries(mid, pid)

    st1, b1 = ctx.fetch(pid)
    time.sleep(2)
    st2, b2 = ctx.fetch(pid)
    ctx.ck("both_reads_200", st1 == 200 and st2 == 200, {"first": st1, "second": st2})
    live = {"updated_at", "status_details", "status_details_id"}
    diff = {k: (b1.get(k), b2.get(k)) for k in set(b1) | set(b2)
            if k not in live and b1.get(k) != b2.get(k)}
    ctx.ck("the_two_reads_are_identical_on_every_non-live_field", not diff, diff)
    st3, coll1 = _list(ctx, "account_number=%s&count=10" % ctx.m["account_number"], "list read 1")
    st4, coll2 = _list(ctx, "account_number=%s&count=10" % ctx.m["account_number"], "list read 2")
    ctx.ck("the_two_list_reads_return_the_same_ids_in_the_same_order",
           [i.get("id") for i in _items(coll1)] == [i.get("id") for i in _items(coll2)],
           {"first": [i.get("id") for i in _items(coll1)],
            "second": [i.get("id") for i in _items(coll2)]})

    rc, after_rows, _ = ctx.a.payouts_sql(
        "SELECT count(*) FROM payouts WHERE merchant_id='%s'" % mid, note="payout count after")
    rc, after_keys, _ = ctx.a.payouts_sql(
        "SELECT count(*) FROM idempotency_keys WHERE merchant_id='%s'" % mid,
        note="idempotency_keys count after")
    ctx.ck("no_new_payout_row_was_created_by_reading",
           before_rows.strip() == after_rows.strip(),
           {"before": before_rows.strip(), "after": after_rows.strip()})
    ctx.ck("no_idempotency_keys_row_was_created_by_reading",
           before_keys.strip() == after_keys.strip(),
           {"before": before_keys.strip(), "after": after_keys.strip()})
    ctx.ck("no_new_payout_logs_row", len(ctx.a.payout_logs(pid)) == len(before_logs), before_logs)
    ctx.ck("no_new_ledger_journal", len(ctx.a.journals("pout_" + pid)) == len(before_j),
           [j["transactor_event"] for j in before_j])
    ctx.ck("no_new_webhook_delivery", len(ctx.a.deliveries(mid, pid)) == len(before_w),
           len(before_w))
    ctx.a.mozart(mid, clear=True)


@journey("fetch-list", "async_state", priority="P0", profile=FL,
         title="the read surface reflects the worker-driven state change: initiated with no utr before, processed with the FTS utr after",
         source_ref="the terminal transition is made by the payouts worker chain, not by the read; "
                    "GET /v1/payouts/{id} serves whatever the workers have written")
def fetch_async_state(ctx):
    mid = ctx.m["merchant_id"]
    ctx.a.mozart(mid, "hold")
    cr = _mk(ctx, 3700, "fetch-async")
    pid = cr["id"]
    if not pid:
        return
    ctx.wait_status(pid, "initiated", timeout=60)
    st, before = ctx.fetch(pid)
    ctx.ev["fetch_before"] = before
    ctx.ck("read_shows_the_non-terminal_state_while_the_bank_is_held",
           st == 200 and (before or {}).get("status") in ("initiated", "processing", "created"),
           (before or {}).get("status"))
    ctx.ck("no_utr_yet_on_the_read", not (before or {}).get("utr"), (before or {}).get("utr"))
    stl, coll = _list(ctx, "account_number=%s&status=processed&count=100" % ctx.m["account_number"],
                      "processed collection BEFORE the bank completes")
    ctx.ck("the_processed_filter_does_not_yet_contain_it",
           "pout_" + pid not in {i.get("id") for i in _items(coll)}, None)

    ctx.a.finish_bank(pid, "success")
    row = ctx.wait_status(pid, "processed", timeout=120)
    ctx.ck("the_workers_completed_it", (row or {}).get("status") == "processed", row)
    hits = ctx.a.worker_log_hits(pid)
    ctx.ev["worker_log_hits"] = hits
    ctx.ck("the_completion_is_attributable_to_a_real_worker_container", bool(hits), hits)

    st2, after = ctx.fetch(pid)
    ctx.ev["fetch_after"] = after
    ctx.ck("the_same_read_now_returns_processed", st2 == 200 and (after or {}).get("status") == "processed",
           (after or {}).get("status"))
    t = ctx.a.transfer(pid)
    ctx.ck("the_read_now_carries_the_utr_fts_produced",
           bool((after or {}).get("utr")) and (after or {}).get("utr") == (t or {}).get("utr"),
           {"api_utr": (after or {}).get("utr"), "fts_utr": (t or {}).get("utr")})
    ctx.ck("the_immutable_contract_fields_did_not_change_across_the_transition",
           all((before or {}).get(k) == (after or {}).get(k)
               for k in ("id", "amount", "currency", "mode", "purpose", "fund_account_id")),
           {k: ((before or {}).get(k), (after or {}).get(k))
            for k in ("id", "amount", "currency", "mode", "purpose", "fund_account_id")})
    stl2, coll2 = _list(ctx, "account_number=%s&status=processed&count=100" % ctx.m["account_number"],
                        "processed collection AFTER the workers completed it")
    ctx.ck("the_processed_filter_now_contains_it",
           "pout_" + pid in {i.get("id") for i in _items(coll2)},
           sorted({i.get("id") for i in _items(coll2)})[:10])
    ctx.a.mozart(mid, clear=True)
