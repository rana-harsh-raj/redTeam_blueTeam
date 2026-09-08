#!/usr/bin/env python3
"""family:bulk-payouts -- batch creation and bulk approval through batch-sim.

Driven entirely through lane I2's RED_LOOP/m6/journeys/bulk_client.py (that module is
I2's file and is never modified from here) against ENV2_COMPOSE/substitutes/batch-sim:

    bulk_client.payout_row(...) / approval_row(...)   real payout.json / payout_approval.json headers
    bulk_client.create_payout_batch(rows, merchant_id)
    bulk_client.create_approval_batch(payout_ids, merchant_id)
    bulk_client.wait_batch(batch_id)                  terminal BatchStatus
    bulk_client.payout_ids(batch_id)                  the payout ids PS actually created
    bulk_client.entry_errors(batch_id)                per-row PS errors
    bulk_client.reprocess(batch_id)                   resend every row with its original key
    bulk_client.arena_batch/arena_calls               evidence plane

batch-sim collapses the Batch -> monolith -> payouts hop and calls the REAL payouts routes
`POST /v1/payouts/bulk` (cred.API Basic + passport, headers x-batch-id / X-Entity-Id /
x-creator-id / x-creator-type) and `POST /v1/payouts/payouts_internal/{payout_id}/{approve|
reject}`, deriving each entry's idempotency key as `"batch_" + BatchEntry.id`
(CONTRACT.md section 4.1). Anything reproduced only because of batch-sim's behaviour is a
twin-specific lead, not a production finding (CONTRACT.md section 0).

Each driver probes the live arena first and BLOCKs with exactly what is missing.
"""
import time
import uuid

import framework as F
from framework import journey

BULK = "bulk"


def _client():
    try:
        import bulk_client  # noqa: F401  (lane I2 module; never overwritten from here)
        return bulk_client, None
    except Exception as exc:  # noqa: BLE001
        return None, "%s: %s" % (type(exc).__name__, str(exc)[:200])


def _probe(ctx):
    probe = {}
    client, err = _client()
    probe["bulk_client_importable"] = client is not None
    probe["bulk_client_import_error"] = err
    if client is not None:
        probe["bulk_client_api"] = sorted(
            n for n in ("payout_row", "approval_row", "create_payout_batch", "create_approval_batch",
                        "wait_batch", "payout_ids", "entry_errors", "reprocess", "arena_batch",
                        "arena_calls", "available", "health") if hasattr(client, n))
        try:
            probe["batch_sim_base_url"] = client.base_url()
        except Exception as exc:  # noqa: BLE001
            probe["batch_sim_base_url_error"] = str(exc)[:200]
        try:
            probe["batch_sim_health"] = client.health()
            probe["batch_sim_available"] = True
        except Exception as exc:  # noqa: BLE001
            probe["batch_sim_health_error"] = str(exc)[:300]
            probe["batch_sim_available"] = False
    import subprocess
    out = subprocess.run(["docker", "ps", "-a", "--format", "{{.Names}} {{.Status}}", "--filter",
                          "name=%s-batch-sim" % F.P.compose_project()],
                         capture_output=True, text=True, timeout=30)
    probe["batch_sim_container"] = out.stdout.strip() or "(no batch-sim container in the compose project)"
    ctx.ev["capability_probe"] = probe
    return probe


def _ready(ctx):
    """Return (client, probe) when the whole chain is live; otherwise BLOCK with the gap."""
    probe = _probe(ctx)
    client, _err = _client()
    if client is not None and probe.get("batch_sim_available") and ctx.m is not None:
        if str((probe.get("batch_sim_health") or {}).get("passport")) not in ("ready", "None"):
            ctx.note("batch-sim reports passport=%r; PS will 400 every bulk create without "
                     "X-Passport-JWT-V1 (bulk_client.health() docstring)"
                     % (probe.get("batch_sim_health") or {}).get("passport"))
        return client, probe
    missing = []
    if client is None:
        missing.append("RED_LOOP/m6/journeys/bulk_client.py (lane I2) is not importable: %s" % _err)
    if not probe.get("batch_sim_available"):
        missing.append("batch-sim is not reachable on the arena network -- compose service absent "
                       "(docker ps: %s); ENV2_COMPOSE/substitutes/batch-sim exists in the tree with "
                       "its CONTRACT.md, so what remains is the compose service plus the controlled "
                       "reboot (topology lane)" % probe["batch_sim_container"])
    if ctx.m is None:
        missing.append("a provisioned merchant for the bulk profile")
    ctx.blocked("; ".join(missing),
                {"probe": probe, "then": "rerun run.py --family bulk-payouts"})


def _rows(ctx, client, n, base=1200):
    return [client.payout_row(ctx.m["account_number"], base + i, ctx.m["fund_account_id"],
                              mode="IMPS", purpose="payout",
                              narration="m6 bulk %d" % i, notes={"m6": "bulk", "row": str(i)})
            for i in range(n)]


@journey("bulk-payouts", "success", priority="P1", profile=BULK,
         title="a payout batch creates one real payout per row and every row reaches processed",
         source_ref="batch-sim CONTRACT.md 3.1 POST /v1/payouts/bulk; bulk_client.create_payout_batch/wait_batch/payout_ids")
def bulk_success(ctx):
    client, probe = _ready(ctx)
    ctx.a.mozart(ctx.m["merchant_id"], "hold")
    batch = client.create_payout_batch(_rows(ctx, client, 5), ctx.m["merchant_id"])
    ctx.ev["batch"] = batch
    ctx.ck("batch_accepted", bool(batch and batch.get("id")), batch)
    done = client.wait_batch(batch["id"])
    ctx.ev["batch_terminal"] = done
    ctx.ck("batch_reached_a_terminal_status", done.get("status") in ("COMPLETED", "PROCESSED"), done)
    ctx.ck("every_row_succeeded",
           int(done.get("success_count") or 0) == 5 and int(done.get("failure_count") or 0) == 0, done)
    pids = [F.bare(p) for p in client.payout_ids(batch["id"])]
    ctx.ev["payout_ids"] = pids
    ctx.ck("one_real_payout_per_row", len(pids) == 5, pids)
    for pid in pids:
        ctx.a.finish_bank(pid, "success")
    bad = [p for p in pids
           if (ctx.wait_status(p, "processed", timeout=120) or {}).get("status") != "processed"]
    ctx.ck("every_batch_payout_reached_processed", not bad, bad)
    for pid in pids:
        ctx.wait_journal("pout_" + pid, "payout_processed", timeout=90)
    unbalanced = [{"payout": p,
                   "events": sorted(j["transactor_event"] for j in ctx.a.journals("pout_" + p))}
                  for p in pids
                  if sorted(j["transactor_event"] for j in ctx.a.journals("pout_" + p))
                  != ["payout_initiated", "payout_processed"]]
    ctx.ck("every_batch_payout_has_exactly_its_two_ledger_journals", not unbalanced, unbalanced)
    nowh = [p for p in pids if len(ctx.wait_delivery(p, {"payout.processed"}, timeout=60)) != 1]
    ctx.ck("every_batch_payout_delivered_exactly_one_payout.processed", not nowh, nowh)
    ctx.ev["batch_sim_calls"] = client.arena_calls()[-10:]
    ctx.state["bulk_pids"] = pids
    ctx.a.mozart(ctx.m["merchant_id"], clear=True)


@journey("bulk-payouts", "failure", priority="P1", profile=BULK,
         title="a batch with bad rows fails only those rows; the batch reports partial success and the good rows still land",
         source_ref="batch-sim CONTRACT.md 7 BatchEntry statuses + counters, 8 error handling; bulk_client.entry_errors")
def bulk_failure(ctx):
    client, probe = _ready(ctx)
    ctx.a.mozart(ctx.m["merchant_id"], "hold")
    rows = _rows(ctx, client, 3, base=1400)
    rows.append(client.payout_row(ctx.m["account_number"], 1000, "fa_ARENADOESNOTEX", mode="IMPS"))
    rows.append(client.payout_row(ctx.m["account_number"], 0, ctx.m["fund_account_id"], mode="IMPS"))
    batch = client.create_payout_batch(rows, ctx.m["merchant_id"])
    done = client.wait_batch(batch["id"])
    ctx.ev["batch_terminal"] = done
    errors = client.entry_errors(batch["id"])
    ctx.ev["entry_errors"] = errors
    ctx.ck("batch_reports_partial_success",
           int(done.get("success_count") or 0) == 3 and int(done.get("failure_count") or 0) == 2, done)
    ctx.ck("two_failed_rows_recorded", len(errors) == 2, errors)
    ctx.ck("each_failed_row_carries_the_real_payouts_error",
           all(e.get("code") or e.get("description") for e in errors), errors)
    ctx.ck("each_failed_row_carries_its_batch_derived_idempotency_key",
           all(str(e.get("idempotency_key") or "").startswith("batch_") for e in errors), errors)
    pids = [F.bare(p) for p in client.payout_ids(batch["id"])]
    ctx.ck("only_the_good_rows_minted_payouts", len(pids) == 3, pids)
    for pid in pids:
        ctx.a.finish_bank(pid, "success")
    bad = [p for p in pids
           if (ctx.wait_status(p, "processed", timeout=120) or {}).get("status") != "processed"]
    ctx.ck("the_good_rows_still_complete_despite_the_bad_ones", not bad, bad)
    ctx.a.mozart(ctx.m["merchant_id"], clear=True)


@journey("bulk-payouts", "idempotency", priority="P1", profile=BULK,
         title="reprocessing a batch resends every row with its original 'batch_<entry_id>' key and creates no duplicate payout",
         source_ref="batch-sim CONTRACT.md 4.1 idempotency-key derivation; bulk_client.reprocess; "
                    "payouts bulkPayoutsProcessor/core.go duplicate-key path")
def bulk_idempotency(ctx):
    client, probe = _ready(ctx)
    mid = ctx.m["merchant_id"]
    ctx.a.mozart(mid, "hold")
    batch = client.create_payout_batch(_rows(ctx, client, 3, base=1600), mid)
    done = client.wait_batch(batch["id"])
    first = sorted(F.bare(p) for p in client.payout_ids(batch["id"]))
    ctx.ev["first_payout_ids"] = first
    ctx.ck("three_payouts_created", len(first) == 3, first)
    rc, before, _ = ctx.a.payouts_sql(
        "SELECT count(*) FROM payouts WHERE merchant_id='%s'" % mid, note="payout count before reprocess")
    ctx.ev["reprocess"] = client.reprocess(batch["id"])
    again = client.wait_batch(batch["id"])
    second = sorted(F.bare(p) for p in client.payout_ids(batch["id"]))
    ctx.ev["second_payout_ids"] = second
    ctx.ck("reprocessing_returns_the_same_payout_ids", first == second, {"first": first, "second": second})
    rc, after, _ = ctx.a.payouts_sql(
        "SELECT count(*) FROM payouts WHERE merchant_id='%s'" % mid, note="payout count after reprocess")
    ctx.ck("no_new_payout_row_created_by_the_reprocess", before.strip() == after.strip(),
           {"before": before.strip(), "after": after.strip()})
    keys = [e.get("idempotency_key") for e in client.get_entries(batch["id"])]
    ctx.ev["idempotency_keys"] = keys
    ctx.ck("every_entry_key_is_batch_<entry_id>",
           keys and all(str(k or "").startswith("batch_") for k in keys), keys)
    # The bulk route keeps its own key table: payouts' bulkPayoutsProcessor writes
    # `bulk_idempotency_keys` (id, idempotency_key, merchant_id, batch_id, source_id, source_type),
    # NOT the single-create `idempotency_keys` table the middleware uses. Verified live: no
    # `batch_*` key ever appears in idempotency_keys, while every one appears exactly once in
    # bulk_idempotency_keys linked to its payout and batch.
    dupes = [k for k in keys if len(ctx.a.bulk_idempotency_row(k, mid)) != 1]
    ctx.ck("each_batch_key_has_exactly_one_bulk_idempotency_keys_row", not dupes, dupes)
    rows = [r for k in keys for r in ctx.a.bulk_idempotency_row(k, mid)]
    ctx.ck("each_bulk_key_row_points_at_one_of_this_batch's_payouts",
           rows and sorted(r["source_id"] for r in rows) == first, rows)
    ctx.ck("each_bulk_key_row_carries_the_batch_id",
           rows and len({r["batch_id"] for r in rows}) == 1, rows)
    stray = [k for k in keys if ctx.a.idempotency_row(k, mid)]
    ctx.ck("the_bulk_route_does_not_write_the_single-create_idempotency_keys_table",
           not stray, stray)
    ctx.note("bulk creates are deduplicated through payouts' `bulk_idempotency_keys` table, not "
             "the middleware `idempotency_keys` table used by POST /v1/payouts")
    for pid in first:
        ctx.a.finish_bank(pid, "success")
    ctx.a.mozart(mid, clear=True)


@journey("bulk-payouts", "async_state", priority="P1", profile=BULK,
         title="the batch is executed by batch-sim's own worker and each payout completes through the payouts workers",
         source_ref="batch-sim CONTRACT.md 6 (worker_alive) + 8 chunking; payouts-worker-* containers")
def bulk_async_state(ctx):
    client, probe = _ready(ctx)
    mid = ctx.m["merchant_id"]
    ctx.a.mozart(mid, "hold")
    batch = client.create_payout_batch(_rows(ctx, client, 5, base=1800), mid)
    ctx.ev["batch_at_create"] = batch
    ctx.ck("create_returns_before_the_batch_is_terminal",
           str(batch.get("status") or "").upper() in ("CREATED", "PROCESSING", "QUEUED"), batch)
    done = client.wait_batch(batch["id"])
    ctx.ev["batch_terminal"] = done
    pids = [F.bare(p) for p in client.payout_ids(batch["id"])]
    for pid in pids:
        ctx.a.finish_bank(pid, "success")
    bad = [p for p in pids
           if (ctx.wait_status(p, "processed", timeout=120) or {}).get("status") != "processed"]
    ctx.ck("every_batch_payout_completed_through_the_workers", pids and not bad, {"pids": pids, "bad": bad})
    hits = {p: ctx.a.worker_log_hits(p) for p in pids[:2]}
    ctx.ev["worker_log_hits"] = hits
    ctx.ck("each_sampled_payout_is_attributable_to_a_real_worker_container", all(hits.values()), hits)
    ctx.ev["batch_sim_evidence"] = client.arena_batch(batch["id"])
    ctx.ck("batch_sim_recorded_the_payouts_responses_per_entry",
           bool((ctx.ev["batch_sim_evidence"] or {}).get("entries")), ctx.ev["batch_sim_evidence"])
    calls = client.arena_calls()
    ctx.ev["batch_sim_calls"] = calls[-10:]
    ctx.ck("batch_sim_called_the_real_/v1/payouts/bulk_route",
           any("/v1/payouts/bulk" in str(c.get("url") or "") for c in calls), calls[-3:])
    ctx.a.mozart(mid, clear=True)


@journey("bulk-payouts", "cancel_or_reverse", priority="P1", profile=BULK,
         title="bulk approval batch: approve a set of workflow-pending payouts in one batch",
         source_ref="batch-sim CONTRACT.md 2.2 payout_approval + 3.2 approve/reject callbacks; "
                    "bulk_client.create_approval_batch; workflow-engine ARENA.md sections 1-3")
def bulk_approval(ctx):
    import j_approval as A
    client, probe = _ready(ctx)
    A._require_engine(ctx)
    ctx.ev["workflow_applicability"] = ctx.a.make_workflow_applicable(ctx.m["merchant_id"], True)
    try:
        _bulk_approval_body(ctx, client, A)
    finally:
        # bulk/async_state runs after this variant on the SAME merchant and must not have its
        # payouts parked for approval, so the feature is switched back off (and the cached
        # MerchantConfig dropped again) before leaving.
        ctx.ev["workflow_disabled_again"] = ctx.a.make_workflow_applicable(ctx.m["merchant_id"], False)
        ctx.a.mozart(ctx.m["merchant_id"], clear=True)


def _bulk_approval_body(ctx, client, A):
    ctx.a.mozart(ctx.m["merchant_id"], "hold")
    pend, notpending = [], []
    for i in range(3):
        cr = ctx.create(2100 + i, "bulk-approval-%d" % i)
        row = ctx.wait_status(cr["id"], "pending", timeout=60) if cr["id"] else None
        (pend if (row or {}).get("status") == "pending" else notpending).append(
            {"id": cr["id"], "row": row})
    ctx.ev["pending_payouts"] = pend
    if len(pend) != 3:
        ctx.blocked(
            "a workflow-applicable merchant for the bulk profile: only %d of 3 payouts reached "
            "`pending` (the bulk-approval batch can only approve pending payouts). The engine is "
            "wired in and healthy; feature list returned to payouts: %r."
            % (len(pend), (ctx.ev["workflow_applicability"] or {})
               .get("feature_list_returned_to_payouts")),
            {"pending": pend, "not_pending": notpending,
             "workflow_applicability": ctx.ev["workflow_applicability"]})
    ids = [p["id"] for p in pend]
    ctx.ck("three_payouts_parked_at_pending_by_the_engine", len(ids) == 3, ids)
    ctx.ck("the_engine_holds_all_three",
           all(ctx.a.wfe_for_payout(p) and ctx.a.wfe_for_payout(p)["state"] == "pending"
               for p in ids), None)
    # The PS internal approve route takes the BARE payout id -- that is the form payouts itself
    # puts in `callback_details.workflow_callbacks...url_path`, and the form the workflow engine
    # calls back on. The real payout_approval.json file carries the PUBLIC `pout_`-prefixed id and
    # batch-sim forwards it verbatim; that combination is measured first, as its own finding.
    stp, txtp = ctx.a.http(
        "POST", F.PAYOUTS + "/v1/payouts/payouts_internal/pout_%s/approve" % ids[0], {},
        {"x-creator-id": ctx.m["merchant_id"],
         "X-Razorpay-Account": "acc_" + ctx.m["merchant_id"]},
        basic="rzp_live:" + F.P._pw("auth_workflow_payouts.txt"),
        note="the approve route addressed with the PUBLIC pout_-prefixed id, exactly as a real "
             "payout_approval.json row carries it")
    ctx.ev["prefixed_id_approve"] = {"status": stp, "body": (txtp or "")[:300]}
    ctx.ck("the_pending_payout_was_not_moved_by_that_call",
           (ctx.a.payout(ids[0]) or {}).get("status") == "pending", ctx.a.payout(ids[0]))
    if isinstance(stp, int) and stp >= 500:
        ctx.note("DEFECT (reported, not fixed): POST /v1/payouts/payouts_internal/{id}/approve "
                 "answers HTTP %d server_error when {id} is the PUBLIC `pout_`-prefixed payout id, "
                 "instead of accepting it or refusing it with a 4xx. The real bulk-approval file "
                 "(payout_approval.json, column `payout_id (do not edit)`) carries exactly that "
                 "public form, so a bulk-approval batch built from a dashboard export fails every "
                 "row with API_SERVER_DOWN after 5 retries. Reproduced independently from "
                 "journey:approval-workflow/idempotency, where the same call with the bare id "
                 "answers 409." % stp)
    batch = client.create_approval_batch(ids, ctx.m["merchant_id"], action="A")
    ctx.ev["approval_batch_created"] = batch
    done = client.wait_batch(batch["id"])
    ctx.ev["approval_batch"] = done
    ctx.ck("approval_batch_completed_every_row",
           int(done.get("success_count") or 0) == len(ids) and int(done.get("failure_count") or 0) == 0,
           done)
    left = [p for p in ids
            if (ctx.wait_status(p, "initiated", timeout=90) or {}).get("status") != "initiated"]
    ctx.ck("every_approved_payout_left_pending", not left, left)
    logs = {p: sum(1 for l in ctx.a.payout_logs(p) if l["from"] == "pending") for p in ids}
    ctx.ck("each_payout_made_exactly_one_transition_out_of_pending",
           all(n == 1 for n in logs.values()), logs)
    for pid in ids:
        ctx.a.finish_bank(pid, "success")
    bad = [p for p in ids
           if (ctx.wait_status(p, "processed", timeout=150) or {}).get("status") != "processed"]
    ctx.ck("every_approved_payout_reached_processed", not bad, bad)
    calls = client.arena_calls()
    ctx.ev["batch_sim_calls"] = calls[-8:]
    ctx.ck("batch_sim_called_the_real_payouts_internal_approve_route",
           any("/payouts_internal/" in str(c.get("url") or "") and
               str(c.get("url") or "").endswith("/approve") for c in calls), calls[-5:])
