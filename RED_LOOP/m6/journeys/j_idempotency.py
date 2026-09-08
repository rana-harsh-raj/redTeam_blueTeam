#!/usr/bin/env python3
"""family:idempotency-retries -- the X-Payout-Idempotency contract and bank retries.

Idempotency is enforced by internal/routing/middleware IdempotencyKey (wrapping
PostFundAccountPayout on both /v1/payouts and /v1/payouts/payouts_internal): the key is
stored with a request hash in payouts.idempotency_keys scoped by merchant_id, so
  same key + same body      -> the SAME payout is returned, nothing new is created
  same key + different body -> rejected, the original payout is untouched
  same key, DIFFERENT merchant -> a separate payout (the key is tenant-scoped)

The retry half of the family is the bank-level retry: mozart-sim `insufficient_funds`
returns INSUFFICIENT_FUND, which FTS maps to a RETRYABLE internal error for Shared
accounts (fts internal/providers/mozart/error_code.go StatusInsufficientFund), which is
what the fts-worker-retry-transfer chain exists to re-attempt.
"""
import time
import uuid

import framework as F
from framework import journey

SH = "shared"


@journey("idempotency-retries", "success", priority="P0", profile=SH,
         title="same key + same body returns the identical payout entity; only one payout and one key row exist",
         source_ref="middleware.IdempotencyKey; verifiers/test_v01")
def idem_success(ctx):
    key = "m6i-" + uuid.uuid4().hex[:12]
    tag = "idem-success-" + key[-6:]
    ctx.a.mozart(ctx.m["merchant_id"], "hold")
    a = ctx.create(5500, tag, idem=key)
    ctx.ck("first_create_200", a["status"] == 200 and bool(a["id"]), a.get("raw"))
    if not a["id"]:
        return
    b = ctx.create(5500, tag, idem=key)
    ctx.ck("replay_200", b["status"] == 200, b.get("raw"))
    ctx.ck("replay_returns_the_same_payout_id", a["id"] == b["id"], {"a": a["id"], "b": b["id"]})
    ja, jb = a["json"] or {}, b["json"] or {}
    contract = ("id", "entity", "merchant_id", "fund_account_id", "amount", "currency", "mode",
                "purpose", "narration", "notes", "fees", "tax", "created_at")
    diff = {k: (ja.get(k), jb.get(k)) for k in contract if ja.get(k) != jb.get(k)}
    ctx.ck("replay_entity_matches_the_first_response_on_every_merchant_contract_field", not diff, diff)
    live = {"updated_at", "status", "utr", "status_details", "status_details_id",
            "internal_status", "reference_id", "failure_reason", "fee_type"}
    extra = {k: (ja.get(k), jb.get(k)) for k in set(ja) | set(jb)
             if k not in contract and ja.get(k) != jb.get(k)}
    ctx.ev["replay_non_contract_field_diff"] = extra
    if set(extra) - live:
        ctx.note("OBSERVATION: the idempotent replay response differs from the original create "
                 "response outside the live-status fields: %s" % sorted(set(extra) - live))
    elif extra:
        ctx.note("OBSERVATION (benign, recorded not asserted): the replay serialises fields the "
                 "original create response left null because they are written asynchronously: %s"
                 % {k: v for k, v in extra.items()})
    rows = ctx.a.idempotency_row(key, ctx.m["merchant_id"])
    ctx.ck("exactly_one_idempotency_keys_row", len(rows) == 1, rows)
    ctx.ck("the_key_row_points_at_the_payout", rows and rows[0]["source_id"] == a["id"], rows)
    rc, cnt, _ = ctx.a.payouts_sql(
        "SELECT count(*) FROM payouts WHERE merchant_id='%s' AND narration='m6 %s'"
        % (ctx.m["merchant_id"], tag), note="one payout for two identical requests (run-unique narration)")
    ctx.ck("only_one_payout_row_for_the_two_requests", cnt.strip() == "1", cnt.strip())
    ctx.a.finish_bank(a["id"], "success")
    ctx.wait_status(a["id"], "processed", timeout=90)
    ctx.state["idem_key"] = key
    ctx.state["idem_pid"] = a["id"]
    ctx.a.mozart(ctx.m["merchant_id"], clear=True)


@journey("idempotency-retries", "failure", priority="P0", profile=SH,
         title="same key + different body is rejected and leaves the original payout untouched",
         source_ref="middleware.IdempotencyKey request-hash mismatch; verifiers/test_v02")
def idem_failure(ctx):
    key = "m6if-" + uuid.uuid4().hex[:12]
    tag = "idem-conflict-" + key[-6:]
    ctx.a.mozart(ctx.m["merchant_id"], "hold")
    a = ctx.create(5200, tag, idem=key)
    ctx.ck("first_create_200", a["status"] == 200 and bool(a["id"]), a.get("raw"))
    if not a["id"]:
        return
    IMMUTABLE = ("id", "merchant_id", "balance_id", "amount", "fees", "tax", "mode", "purpose")
    before = ctx.a.payout(a["id"])
    for label, kwargs in (("different_amount", {"amount": 5201}),
                          ("different_mode", {"amount": 5200, "mode": "NEFT"}),
                          ("different_purpose", {"amount": 5200, "purpose": "refund"})):
        c = ctx.create(kwargs.get("amount", 5200), tag, idem=key,
                       mode=kwargs.get("mode", "IMPS"), purpose=kwargs.get("purpose", "payout"))
        ctx.ck("conflict_%s_rejected_4xx" % label,
               isinstance(c["status"], int) and 400 <= c["status"] < 500,
               {"status": c["status"], "body": c.get("raw")})
    after = ctx.a.payout(a["id"])
    # Compare the fields a rejected replay must never touch. `status`/`updated_at` are
    # deliberately excluded: the original payout is legitimately still moving through its
    # normal async lifecycle (created -> initiated) while the conflicting replays are refused.
    diff = {k: (before.get(k), after.get(k)) for k in IMMUTABLE
            if (before or {}).get(k) != (after or {}).get(k)}
    ctx.ck("original_payout_untouched_by_the_conflicts(identity, amount, fee, mode, purpose)",
           before and after and not diff, {"diff": diff, "before": before, "after": after})
    ctx.ck("original_payout_still_progressing_normally(not failed or cancelled)",
           (after or {}).get("status") not in ("failed", "cancelled", "rejected"), after)
    ctx.ck("still_exactly_one_idempotency_keys_row",
           len(ctx.a.idempotency_row(key, ctx.m["merchant_id"])) == 1, None)
    rc, cnt, _ = ctx.a.payouts_sql(
        "SELECT count(*) FROM payouts WHERE merchant_id='%s' AND narration='m6 %s'"
        % (ctx.m["merchant_id"], tag), note="conflicting replays created no payouts (run-unique narration)")
    ctx.ck("no_extra_payout_row_created_by_the_conflicts", cnt.strip() == "1", cnt.strip())
    ctx.a.finish_bank(a["id"], "success")
    ctx.wait_status(a["id"], "processed", timeout=90)
    ctx.a.mozart(ctx.m["merchant_id"], clear=True)


@journey("idempotency-retries", "idempotency", priority="P0", profile="queued",
         title="the idempotency key is tenant-scoped: a different merchant reusing the same key gets its own payout",
         source_ref="idempotency_keys is keyed (idempotency_key, merchant_id); verifiers/test_v20 tenant isolation")
def idem_tenant_scope(ctx):
    """Runs on the second (queued-family) merchant and reuses the key minted by
    journey:idempotency-retries/success on the first merchant."""
    key = ctx.state.get("idem_key")
    other_pid = ctx.state.get("idem_pid")
    if not key:
        key = "m6it-" + uuid.uuid4().hex[:12]
        ctx.note("journey:idempotency-retries/success was not run first; minted a local key instead, "
                 "so this journey asserts scoping without a cross-merchant collision")
    ctx.a.mozart(ctx.m["merchant_id"], "hold")
    a = ctx.create(5500, "idem-success", idem=key)
    ctx.ck("second_merchant_create_with_the_same_key_200", a["status"] == 200 and bool(a["id"]), a.get("raw"))
    if not a["id"]:
        return
    if other_pid:
        ctx.ck("second_merchant_got_a_DIFFERENT_payout_id", a["id"] != other_pid,
               {"tenant_a_payout": other_pid, "tenant_b_payout": a["id"]})
    rows_here = ctx.a.idempotency_row(key, ctx.m["merchant_id"])
    ctx.ck("this_merchant_has_its_own_idempotency_row", len(rows_here) == 1, rows_here)
    ctx.ck("the_row_is_owned_by_this_merchant",
           rows_here and rows_here[0]["merchant_id"] == ctx.m["merchant_id"], rows_here)
    rc, out, _ = ctx.a.payouts_sql(
        "SELECT merchant_id,source_id FROM idempotency_keys WHERE idempotency_key='%s'" % key,
        note="all tenants holding this key")
    pairs = [l.split("\t") for l in out.strip().splitlines() if l.strip()]
    ctx.ck("each_merchant_holding_the_key_has_its_own_source_payout",
           len({p[0] for p in pairs}) == len(pairs), pairs)
    if other_pid:
        st, body = ctx.fetch(other_pid)
        ctx.ck("this_merchant_cannot_fetch_the_other_tenant's_payout",
               isinstance(st, int) and st >= 400, {"status": st, "body": str(body)[:200]})
    ctx.cancel(a["id"], "m6 cleanup")
    ctx.a.mozart(ctx.m["merchant_id"], clear=True)


@journey("idempotency-retries", "duplicate", priority="P0", profile=SH,
         title="a duplicate terminal FTS status stimulus is absorbed: no state change, no extra journal, no extra webhook",
         source_ref="fts_transfer_status_webhook.go terminal guard; verifiers/test_v15")
def idem_duplicate(ctx):
    mid = ctx.m["merchant_id"]
    ctx.a.mozart(mid, "hold")
    cr = ctx.create(3000, "idem-dup")
    pid = cr["id"]
    ctx.ck("create_200", cr["status"] == 200 and bool(pid), cr.get("raw"))
    if not pid:
        return
    ctx.wait_status(pid, "initiated", timeout=45)
    ctx.a.finish_bank(pid, "success")
    row = ctx.wait_status(pid, "processed", timeout=90)
    ctx.wait_journal("pout_" + pid, "payout_processed", timeout=80)
    ctx.wait_delivery(pid, {"payout.processed"}, timeout=60)

    def snapshot():
        return {"row": ctx.a.payout(pid),
                "journals": [j["transactor_event"] for j in ctx.a.journals("pout_" + pid)],
                "deliveries": len(ctx.a.deliveries(mid, pid)),
                "updated_at": (ctx.a.payout(pid) or {}).get("updated_at")}

    # settle first: require two identical consecutive readings so the comparison below
    # measures the duplicate stimulus, not the tail of the normal async completion.
    snap = snapshot()
    for _ in range(12):
        time.sleep(5)
        again = snapshot()
        if again == snap:
            break
        snap = again
    ctx.ev["settled_snapshot"] = snap
    t = ctx.a.transfer(pid)
    body = {"fund_transfer_id": int(t["id"]), "source_id": pid, "source_type": "payout",
            "status": "processed", "utr": (row or {}).get("utr"),
            "source_account_id": int(t["source_account_id"]),
            "bank_account_type": t.get("sa_bank_account_type")}
    codes = []
    for _ in range(3):
        st, _txt = ctx.a.http("POST", F.PAYOUTS + "/v1/payouts/transfer_status_webhook", body,
                              basic=F.bridge("ps-service"), note="duplicate terminal stimulus")
        codes.append(st)
    ctx.ck("every_duplicate_stimulus_answered_without_a_server_error",
           all(isinstance(c, int) and c < 500 for c in codes), codes)
    time.sleep(8)
    after = snapshot()
    ctx.ck("journals_unchanged", after["journals"] == snap["journals"],
           {"before": snap["journals"], "after": after["journals"]})
    ctx.ck("delivery_count_unchanged", after["deliveries"] == snap["deliveries"],
           {"before": snap["deliveries"], "after": after["deliveries"]})
    mutable = {"updated_at"}
    row_diff = {k: (snap["row"].get(k), after["row"].get(k))
                for k in (snap["row"] or {}) if k not in mutable
                and (snap["row"] or {}).get(k) != (after["row"] or {}).get(k)}
    ctx.ck("every_material_payout_field_unchanged(status, amount, fees, utr, transaction_id, ...)",
           not row_diff, row_diff)
    if snap["updated_at"] != after["updated_at"]:
        ctx.ev["duplicate_stimulus_updated_at_bump"] = {
            "before": snap["updated_at"], "after": after["updated_at"], "material_diff": row_diff}
        ctx.note("OBSERVATION: the terminal guard absorbs the duplicate FTS status webhook for "
                 "state, ledger and webhooks, but payouts still rewrites the row -- `updated_at` "
                 "moved from %s to %s with no material field change. Reported for the source lane; "
                 "it is a redundant write, not an incorrect state."
                 % (snap["updated_at"], after["updated_at"]))
    ctx.a.mozart(mid, clear=True)


def _retry_probe(ctx, note):
    """Drive a retryable bank error (INSUFFICIENT_FUND) and report what the FTS retry
    chain actually did. Returns (pid, transfer, first_attempts, attempts_fn)."""
    mid = ctx.m["merchant_id"]
    ctx.a.mozart(mid, "insufficient_funds")
    cr = ctx.create(2200, note)
    ctx.ck("create_200", cr["status"] == 200 and bool(cr["id"]), cr.get("raw"))
    pid = cr["id"]
    if not pid:
        return None, None, [], (lambda: [])
    t = F.wait_until(lambda: (lambda x: x if x and x.get("attempt_id") else None)(ctx.a.transfer(pid)),
                     timeout=45, interval=2)
    ctx.ck("fts_transfer_created", bool(t), t)

    def attempts():
        rc, out, _ = ctx.a.fts_sql(
            "SELECT id,status,bank_status_code FROM attempts WHERE transfer_id=%s ORDER BY id"
            % (t or {}).get("id"), note="FTS attempts for this transfer")
        return [dict(zip(("id", "status", "bank_status_code"), l.split("\t")))
                for l in out.strip().splitlines() if l.strip()]

    first = F.wait_until(lambda: (lambda a: a if a and a[0]["status"] != "CREATED" else None)(attempts()),
                         timeout=60, interval=3) or attempts()
    ctx.ev["first_attempts"] = first
    ctx.ck("bank_returned_the_retryable_INSUFFICIENT_FUND",
           bool(first) and any(a["bank_status_code"] == "INSUFFICIENT_FUND" for a in first), first)
    ctx.ev["fts_attempts_helper"] = "attempts() closure bound to transfer %s" % (t or {}).get("id")
    return pid, t, first, attempts


@journey("idempotency-retries", "retry", priority="P1", profile=SH,
         title="a retryable bank error (INSUFFICIENT_FUND) is re-attempted by the FTS retry-transfer chain",
         source_ref="fts internal/providers/mozart/error_code.go StatusInsufficientFund -> RetriableError; "
                    "fts-worker-retry-transfer / -preprocessor / -source-update containers")
def idem_retry(ctx):
    """M6: REDIS_QUEUE_HOST now points at the arena redis, so FTS's machinery broker is real.
    The retry is scheduled as a delayed task (`retry_transfer.v2` on `fts_retry_transfer`) whose
    ETA is ~30 minutes out; this journey proves the scheduling, then brings that ONE task's ETA
    forward so the real worker executes it now instead of after the suite has finished."""
    pid, t, first, attempts = _retry_probe(ctx, "idem-retry")
    if not pid:
        return
    tid = str((t or {}).get("id"))
    sched = F.wait_until(
        lambda: ctx.a.machinery_delayed(name="retry_transfer.v2",
                                        contains='"Value":%s}' % tid) or None,
        timeout=90, interval=5) or []
    ctx.ev["scheduled_retry_tasks"] = [{k: v for k, v in s.items() if k != "member"} for s in sched]
    if not sched:
        ctx.a.mozart(ctx.m["merchant_id"], clear=True)
        ctx.blocked(
            "FTS retry scheduling. The bank returned the retryable INSUFFICIENT_FUND and FTS "
            "persisted it, but no `retry_transfer.v2` delayed task for transfer %s appeared on the "
            "arena redis ZSET `delayed_tasks` within 90s. The three fts-worker-retry-transfer* "
            "containers now run with REDIS_QUEUE_HOST=redis / REDIS_QUEUE_PORT=6379 (M6 fix), so "
            "the broker itself is reachable; what is missing is FTS enqueuing the retry at all."
            % tid,
            {"transfer": t, "first_attempts": first,
             "delayed_tasks_seen": ctx.a.machinery_delayed(name="retry_transfer.v2")[:5],
             "then": "rerun run.py --only idempotency-retries/retry"})
    ctx.ck("fts_scheduled_a_retry_transfer_task_on_the_arena_redis_broker", True,
           ctx.ev["scheduled_retry_tasks"])
    ctx.ck("the_scheduled_task_is_routed_to_the_fts_retry_transfer_queue",
           all(s["routing_key"] == "fts_retry_transfer" for s in sched), ctx.ev["scheduled_retry_tasks"])
    ctx.ck("the_scheduled_task_carries_this_transfer_id",
           all(any(str(a.get("Value")) == tid for a in (s["args"] or [])) for s in sched),
           ctx.ev["scheduled_retry_tasks"])
    ctx.note("FTS schedules the retry with an ETA of %s (about 30 minutes out in this arena); the "
             "journey advances that one task's score to now so the REAL "
             "fts-worker-retry-transfer container executes the REAL task without a 30-minute wait."
             % sched[0]["eta"])
    # the bank recovers before the retry runs, so the re-attempt is the one that succeeds
    ctx.a.mozart(ctx.m["merchant_id"], "success")
    for s in sched:
        uuid_ = (F.jload(s["member"], {}) or {}).get("UUID")
        ctx.ck("advanced_the_scheduled_retry_task_%s" % (uuid_ or "?")[:8],
               ctx.a.machinery_advance(uuid_ or s["member"]) == "1", uuid_)
    grown = F.wait_until(lambda: (lambda a: a if len(a) > len(first) else None)(attempts()),
                         timeout=180, interval=6)
    after = grown or attempts()
    ctx.ev["attempts_after_retry_window"] = after
    ctx.ck("the_retry_worker_created_a_SECOND_fts_attempt", len(after) > len(first),
           {"before": first, "after": after})
    hits = ctx.a.worker_log_hits(tid, services=(
        "fts-worker-retry-transfer", "fts-worker-retry-transfer-preprocessor",
        "fts-worker-retry-transfer-source-update", "fts-worker-initiate-transfer"), since="30m")
    ctx.ev["fts_retry_worker_hits"] = hits
    ctx.ck("a_real_fts_retry_worker_container_handled_the_transfer",
           "fts-worker-retry-transfer" in hits, hits)
    if len(after) > len(first):
        ok = F.wait_until(lambda: (lambda a: a if a and a[-1]["status"] == "PROCESSED" else None)(attempts()),
                          timeout=90, interval=5)
        ctx.a.fts_check(tid)
        final = ok or attempts()
        ctx.ck("the_re-attempt_is_the_one_that_succeeded_at_the_bank",
               final[-1]["status"] == "PROCESSED" and final[0]["bank_status_code"] == "INSUFFICIENT_FUND",
               final)
        row = ctx.wait_status(pid, "processed", timeout=150)
        ctx.ck("payout_processed_after_the_retry", (row or {}).get("status") == "processed", row)
        ctx.ck("the_payout_has_exactly_one_processed_ledger_journal",
               len(ctx.wait_journal("pout_" + pid, "payout_processed", timeout=90)) == 1, None)
        ctx.ck("the_payout_carries_the_utr_of_the_successful_re-attempt",
               bool((row or {}).get("utr")) and (row or {}).get("utr") == (ctx.a.transfer(pid) or {}).get("utr"),
               {"payout_utr": (row or {}).get("utr"), "fts_utr": (ctx.a.transfer(pid) or {}).get("utr")})
    ctx.a.mozart(ctx.m["merchant_id"], clear=True)


@journey("idempotency-retries", "async_state", priority="P0", profile=SH,
         title="the idempotency guard holds across the ASYNC completion: replaying the key after the payout has been completed by the workers still returns that same payout",
         source_ref="middleware.IdempotencyKey stores source_id; state_machine.go terminal transitions are worker-driven")
def idem_async_state(ctx):
    mid = ctx.m["merchant_id"]
    key = "m6ia-" + uuid.uuid4().hex[:12]
    ctx.a.mozart(mid, "hold")
    a = ctx.create(2800, "idem-async", idem=key)
    pid = a["id"]
    ctx.ck("create_200", a["status"] == 200 and bool(pid), a.get("raw"))
    if not pid:
        return
    ctx.ck("create_response_is_non_terminal", a.get("response_status") not in
           ("processed", "reversed", "failed"), a.get("response_status"))
    ctx.wait_status(pid, "initiated", timeout=45)
    ctx.a.finish_bank(pid, "success")
    row = ctx.wait_status(pid, "processed", timeout=90)
    ctx.ck("workers_completed_the_payout", (row or {}).get("status") == "processed", row)
    hits = ctx.a.worker_log_hits(pid)
    ctx.ev["worker_log_hits"] = hits
    ctx.ck("a_real_async_worker_container_completed_it", bool(hits), hits)
    b = ctx.create(2800, "idem-async", idem=key)
    ctx.ck("post_completion_replay_200", b["status"] == 200, b.get("raw"))
    ctx.ck("post_completion_replay_returns_the_same_payout", b["id"] == pid, {"a": pid, "b": b["id"]})
    ctx.ck("post_completion_replay_reports_the_completed_status",
           (b["json"] or {}).get("status") in ("processed", "processing"),
           {"replay_status": (b["json"] or {}).get("status")})
    ctx.ck("replay_created_no_second_payout_and_no_second_journal",
           len(ctx.a.idempotency_row(key, mid)) == 1
           and sum(1 for j in ctx.a.journals("pout_" + pid)
                   if j["transactor_event"] == "payout_processed") == 1, None)
    ctx.a.mozart(mid, clear=True)
