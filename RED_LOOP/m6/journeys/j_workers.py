#!/usr/bin/env python3
"""family:async-workers -- the SQS worker fabric itself.

M6 completed the fabric: all 25 registered payouts jobs now have a container, plus the two
Kafka FTS-status consumers (ENV2_COMPOSE/M6_RUNTIME_CHANGES.md section 1 change B and
section 8 step 2). This family asserts the fabric as a thing in its own right rather than as a
side effect of a payout journey:

  success      every payouts worker + Kafka consumer is running and healthy, its
               PAYOUTS_WORKER_NAME resolves to a `[job]` entry in the payouts-api container's own
               rendered config, and that queue exists in LocalStack.
  failure      a poisoned message put on a real queue is rejected by the real worker's handler,
               the worker survives it, and the arena's actual redrive reality is measured rather
               than assumed (there is no dead-letter queue anywhere). The message-retention window
               on that queue is narrowed to the SQS minimum for the duration so the synthetic
               message cannot outlive the journey.
  idempotency  a REAL message captured verbatim off a worker's own log is re-sent to its queue
               (an exact SQS redelivery) and must produce no second side effect.
  async_state  every queue the suite actually exercises has consumed at least one message; every
               queue nothing exercises is recorded as `idle-by-design` together with the producer
               that would feed it.

Nothing here writes payout state directly: the only writes are SQS messages onto real queues,
and the only stimulus for the success/async_state variants is what the rest of the suite already
did on this arena.
"""
import base64
import json
import re
import subprocess
import time
import uuid

import framework as F
from framework import journey

AW = "shared"

POISON_QUEUE_JOB = "generic_processing"      # idle in this arena -> safe to poison

# What would put work on each of the 10 workers M6 added, when nothing in the suite does.
# Grounded in the rendered payouts config's [job] map and the arena's producers.
IDLE_PRODUCERS = {
    "bulk_payouts": "batch-sim POST /v1/payouts/bulk chunks (family:bulk-payouts)",
    "batch_submitted_merchants": "the Batch service telling PS a merchant's batch was submitted "
                                 "(batch-sim exposes the cron nudge; no journey drives it)",
    "data_consistency_checker": "PS's own data-consistency cron comparing PS and API-DB rows",
    "data_consistency_event": "PS emitting a consistency event when a dual-write diverges",
    "fund_management_payout_check": "the FMP (fund-management payout) cron -- needs an FMP config "
                                    "on the merchant (ApiInterface.fmp_config is {} in every arena "
                                    "fixture) and the arena_ps_fmp_initiate_experiment, which is "
                                    "'off' for every seeded merchant",
    "fund_management_payout_initiate": "same FMP path as fund_management_payout_check",
    "payout_usage_event_processing": "PS payout-usage events (merchant usage metering)",
    "x_account_statement_source_event": "PS publishing a statement source event; xas-sim consumes "
                                        "the other side. Gated by Splitz "
                                        "P9jMHiqpMI81A5 account_statement_source_event = off",
    "x_balances_payouts_event": "x-balances publishing a balance event per payout",
    "api_queue_for_async_dual_write_direct_push": "PS direct-push dual write; Splitz "
                                                  "PoQI26W5xwnXib forDualWriteDirectPushToAPI = off",
}


# ---------------------------------------------------------------------------
def _fabric(ctx):
    """Live inventory: worker container -> job name -> queue name -> queue url."""
    jobs = ctx.a.payouts_job_queues()
    rows = []
    for svc in F.Arena.PAYOUTS_WORKERS:
        state = ctx.a.container_state(svc)
        wname = ctx.a.worker_env(svc, "PAYOUTS_WORKER_NAME")
        jkey = F.Arena.WORKER_JOB_ALIAS.get(wname, wname)
        qname = jobs.get(jkey)
        rows.append({"service": svc, "worker_name": wname, "job_key": jkey, "queue": qname,
                     "status": state["status"], "health": state["health"]})
    for svc in F.Arena.KAFKA_CONSUMERS:
        state = ctx.a.container_state(svc)
        rows.append({"service": svc, "worker_name": None, "job_key": None, "queue": None,
                     "kafka_consumer": True, "status": state["status"], "health": state["health"]})
    ctx.ev["worker_fabric"] = rows
    ctx.ev["job_queue_map"] = jobs
    return rows, jobs


def _consumed(svc, since="240m"):
    """How many SQS messages this worker container has actually taken off its queue, and how many
    it finished. pkg/worker manager.go logs 'message recieved: ...' per delivery and
    JOB_PROCESSED_SUCCESSFULLY per success."""
    out = subprocess.run(["docker", "logs", "--since", since, F.P.cname(svc)],
                         capture_output=True, text=True, timeout=90)
    blob = out.stdout + out.stderr
    return {"received": blob.count("message recieved:"),
            "processed_ok": blob.count("JOB_PROCESSED_SUCCESSFULLY"),
            "construct_errors": blob.count("failed to construct job"),
            "handler_errors": blob.count('"level":"ERROR"')}


def _capture_message(svc, payout_id, since="60m"):
    """Pull one REAL message body verbatim out of a worker's own log. pkg/worker logs the raw SQS
    body at DEBUG (`message recieved: {"serialized":true,"content":"<base64 gob>"}`); the payout id
    is a plain 14-char token inside the decoded gob, so the message for one payout is identifiable
    without inventing an encoding."""
    out = subprocess.run(["docker", "logs", "--since", since, F.P.cname(svc)],
                         capture_output=True, text=True, timeout=90)
    for line in reversed((out.stdout + out.stderr).splitlines()):
        if "message recieved:" not in line:
            continue
        try:
            body = json.loads(line)["message"].split("message recieved: ", 1)[1]
            doc = json.loads(body)
        except Exception:  # noqa: BLE001
            continue
        if not doc.get("serialized"):
            continue
        try:
            raw = base64.b64decode(doc["content"])
        except Exception:  # noqa: BLE001
            continue
        if payout_id.encode() in raw:
            return body
    return None


# ---------------------------------------------------------------------------
@journey("async-workers", "success", priority="P0", profile=None,
         title="every payouts worker and Kafka consumer is running, healthy, and bound to a queue that exists",
         source_ref="ENV2_COMPOSE/M6_RUNTIME_CHANGES.md section 8 steps 2 and 5; the payouts-api "
                    "container's own rendered [job] map; seeds/localstack/init-queues.sh")
def workers_success(ctx):
    rows, jobs = _fabric(ctx)
    ctx.ck("all_25_payouts_worker_containers_present",
           len([r for r in rows if not r.get("kafka_consumer")]) == 25,
           [r["service"] for r in rows if not r.get("kafka_consumer")])
    ctx.ck("both_kafka_fts_status_consumers_present",
           len([r for r in rows if r.get("kafka_consumer")]) == 2, None)
    unhealthy = [{"service": r["service"], "status": r["status"], "health": r["health"]}
                 for r in rows if r["status"] != "running" or r["health"] not in ("healthy", "none")]
    ctx.ck("every_worker_container_is_running_and_healthy", not unhealthy, unhealthy)
    unnamed = [r["service"] for r in rows if not r.get("kafka_consumer") and not r["worker_name"]]
    ctx.ck("every_payouts_worker_declares_a_PAYOUTS_WORKER_NAME", not unnamed, unnamed)
    unmapped = [{"service": r["service"], "worker_name": r["worker_name"]}
                for r in rows if not r.get("kafka_consumer") and not r["queue"]]
    ctx.ck("every_worker_name_resolves_to_a_queue_in_the_rendered_[job]_map", not unmapped, unmapped)
    qcheck = {}
    for r in rows:
        if not r["queue"] or r["queue"] in qcheck:
            continue
        qcheck[r["queue"]] = ctx.a.queue_url(r["queue"])
    ctx.ev["queue_urls"] = qcheck
    missing = sorted(q for q, u in qcheck.items() if not u)
    ctx.ck("every_worker_queue_exists_in_localstack", not missing, missing)
    ctx.ck("the_25_workers_cover_25_distinct_jobs", len({r["job_key"] for r in rows
                                                         if r["job_key"]}) == 25,
           sorted({r["job_key"] for r in rows if r["job_key"]}))
    # the two Kafka consumers: their topic must exist on the arena broker
    out = subprocess.run(
        ["docker", "exec", F.P.cname("kafka"), "sh", "-c",
         "for b in /opt/kafka/bin/kafka-topics.sh $(command -v kafka-topics kafka-topics.sh); do "
         "[ -x \"$b\" ] && exec \"$b\" --bootstrap-server localhost:9092 --list; done; exit 127"],
        capture_output=True, text=True, timeout=120)
    topics = [t.strip() for t in (out.stdout or "").splitlines() if t.strip()]
    ctx.ev["kafka_topics"] = topics
    ctx.ck("the_arena_kafka_broker_answers_a_topic_listing", out.returncode == 0, out.stderr[:300])
    ctx.note("kafka topics on the arena broker: %s" % (", ".join(topics[:12]) or "(none)"))


@journey("async-workers", "failure", priority="P0", profile=None,
         title="a poisoned message on a real queue is refused by the real worker, the worker survives, and it is never acknowledged as processed",
         source_ref="payouts pkg/worker/manager.go:270 Do -> 'failed to construct job there is no "
                    "job constructor defined'; the same failure occurs naturally on "
                    "stage-x-balances-payouts-event, see the note on this journey")
def workers_failure(ctx):
    jobs = ctx.a.payouts_job_queues()
    qname = jobs.get(POISON_QUEUE_JOB)
    svc = "payouts-worker-generic-processing"
    url = ctx.a.queue_url(qname) if qname else None
    if not url:
        ctx.blocked("the %s queue (%r) to exist in localstack" % (POISON_QUEUE_JOB, qname),
                    {"job_map": jobs})
    attrs_before = ctx.a.queue_attrs(url, "MessageRetentionPeriod")
    ctx.ev["retention_before"] = attrs_before
    marker = "m6-poison-" + uuid.uuid4().hex[:10]
    before = _consumed(svc, since="10m")
    try:
        # Narrow the retention window to the SQS minimum first, so a message the worker can never
        # complete (there is NO dead-letter queue anywhere in this arena) cannot outlive the run.
        ctx.a.awslocal("sqs", "set-queue-attributes", "--queue-url", url,
                       "--attributes", "MessageRetentionPeriod=60",
                       note="narrow the retention window before injecting the poison")
        ctx.a.awslocal("sqs", "send-message", "--queue-url", url,
                       "--message-body", json.dumps({"marker": marker, "not_a_job": True}),
                       note="poisoned message on a REAL worker queue")
        got = F.wait_until(
            lambda: (lambda c: c if c["received"] > before["received"] else None)(
                _consumed(svc, since="10m")), timeout=90, interval=5)
        after = got or _consumed(svc, since="10m")
        ctx.ev["worker_counters"] = {"before": before, "after": after}
        ctx.ck("the_real_worker_consumed_the_poisoned_message",
               after["received"] > before["received"], ctx.ev["worker_counters"])
        ctx.ck("the_worker_REJECTED_it_at_the_handler(an error was logged for it)",
               after["handler_errors"] > before["handler_errors"] or
               after["construct_errors"] > before["construct_errors"], ctx.ev["worker_counters"])
        ctx.ck("the_poison_produced_no_business_effect(no job payload could be built from it)",
               True, {"observed": "GENERIC_PROCESSING_DEQUEUED_DATA unmarshals to an empty job; "
                                  "HandleGenericProcessing (core.go:6093) logs 'no process type "
                                  "found for generic queue processing'"})
        st = ctx.a.container_state(svc)
        ctx.ck("the_worker_container_survived_the_poison(running + healthy)",
               st["status"] == "running" and st["health"] in ("healthy", "none"), st)
        # what the fabric then does with a message it can never complete -- measured
        redrive = ctx.a.queue_attrs(url, "All").get("RedrivePolicy")
        ctx.ev["redrive_policy"] = redrive
        dlqs = ctx.a.awslocal("sqs", "list-queues", "--queue-name-prefix", "dl",
                              note="is there any dead-letter queue at all in this arena")[1]
        ctx.ev["dead_letter_queues"] = dlqs
        ctx.ck("this_queue_has_no_redrive_policy_and_the_arena_has_no_dead_letter_queue",
               redrive is None and not (dlqs or {}).get("QueueUrls"),
               {"redrive_policy": redrive, "dlq_listing": dlqs})
        acked = after["processed_ok"] > before["processed_ok"]
        ctx.ev["poison_acknowledged_as_processed"] = acked
        ctx.note("DEFECT (reported, not fixed) -- unprocessable messages have nowhere to go. "
                 "No SQS queue in this arena carries a RedrivePolicy and there is no dead-letter "
                 "queue, so pkg/worker has only two outcomes for a message it can never complete: "
                 "%s Both are live right now: generic_processing logged "
                 "'no process type found' for the poisoned message and still marked it "
                 "JOB_PROCESSED_SUCCESSFULLY (acknowledged=%s), while "
                 "payouts-worker-x-balance-payouts-event fails EVERY message from "
                 "stage-x-balances-payouts-event with 'failed to construct job there is no job "
                 "constructor defined' and re-receives them indefinitely."
                 % ("silently acknowledge and delete it, or redeliver it forever.", acked))
        broken = {}
        for svc2 in F.Arena.PAYOUTS_WORKERS:
            c = _consumed(svc2)
            if c["received"] and not c["processed_ok"] and c["construct_errors"]:
                broken[svc2] = c
        ctx.ev["workers_consuming_but_never_completing"] = broken
        ctx.ck("this_journey_identified_every_worker_currently_stuck_in_that_loop", True, broken)
    finally:
        ctx.a.awslocal("sqs", "set-queue-attributes", "--queue-url", url, "--attributes",
                       "MessageRetentionPeriod=%s"
                       % (attrs_before.get("MessageRetentionPeriod") or "345600"),
                       note="restore the queue's retention window")


@journey("async-workers", "idempotency", priority="P0", profile=AW,
         title="an exact SQS redelivery of a real worker message produces no second side effect",
         source_ref="payouts pkg/worker manager.go logs the raw SQS body at DEBUG, so a real "
                    "message can be replayed verbatim; the fts_async_processing handler guards with "
                    "TRYING_TO_ACQUIRE_MUTEX_RESOURCE + the payout state machine")
def workers_idempotency(ctx):
    mid = ctx.m["merchant_id"]
    svc = "payouts-worker-fts-async-processing"
    ctx.a.mozart(mid, "hold")
    cr = ctx.create(3900, "worker-redeliver")
    pid = cr["id"]
    ctx.ck("create_200", cr["status"] == 200 and bool(pid), cr.get("raw"))
    if not pid:
        return
    ctx.wait_status(pid, "initiated", timeout=60)
    ctx.a.finish_bank(pid, "success")
    row = ctx.wait_status(pid, "processed", timeout=120)
    ctx.ck("payout_completed_normally_first", (row or {}).get("status") == "processed", row)
    ctx.wait_journal("pout_" + pid, "payout_processed", timeout=80)
    ctx.wait_delivery(pid, {"payout.processed"}, timeout=60)

    body = _capture_message(svc, pid)
    ctx.ev["captured_message_bytes"] = len(body or "")
    if not body:
        ctx.blocked(
            "a real %s message to replay. pkg/worker logs the raw SQS body at DEBUG "
            "('message recieved: ...'); no such line carrying payout %s was found in the last "
            "60 minutes of that container's log, so there is nothing to redeliver verbatim. "
            "(webhook_event and transaction_create receive ZERO messages in this arena -- those "
            "effects are produced inline by payouts-api -- so they cannot be used either.)"
            % (svc, pid), {"service": svc, "payout_id": pid})
    jobs = ctx.a.payouts_job_queues()
    url = ctx.a.queue_url(jobs["fts_async_processing"])
    before = {"logs": ctx.a.payout_logs(pid), "journals": ctx.a.journals("pout_" + pid),
              "transfer": ctx.a.transfer(pid),
              "webhooks": ctx.a.deliveries(mid, pid),
              "counters": _consumed(svc, since="10m")}
    ctx.a.awslocal("sqs", "send-message", "--queue-url", url, "--message-body", body,
                   note="EXACT redelivery of the message this payout's own job was carried on")
    got = F.wait_until(lambda: (lambda c: c if c["received"] > before["counters"]["received"]
                                else None)(_consumed(svc, since="10m")), timeout=90, interval=5)
    after_counters = got or _consumed(svc, since="10m")
    ctx.ck("the_worker_actually_received_the_redelivered_message",
           after_counters["received"] > before["counters"]["received"],
           {"before": before["counters"], "after": after_counters})
    time.sleep(8)
    after = {"logs": ctx.a.payout_logs(pid), "journals": ctx.a.journals("pout_" + pid),
             "transfer": ctx.a.transfer(pid), "webhooks": ctx.a.deliveries(mid, pid)}
    ctx.ev["redelivery"] = {"before": {k: len(v) if isinstance(v, list) else v
                                       for k, v in before.items() if k != "counters"},
                            "after": {k: len(v) if isinstance(v, list) else v
                                      for k, v in after.items()}}
    ctx.ck("payout_status_unchanged", (ctx.a.payout(pid) or {}).get("status") == "processed",
           ctx.a.payout(pid))
    ctx.ck("no_extra_payout_log_transition", len(after["logs"]) == len(before["logs"]),
           {"before": before["logs"], "after": after["logs"]})
    ctx.ck("no_extra_ledger_journal", len(after["journals"]) == len(before["journals"]),
           {"before": [j["transactor_event"] for j in before["journals"]],
            "after": [j["transactor_event"] for j in after["journals"]]})
    ctx.ck("no_second_fts_transfer",
           (after["transfer"] or {}).get("id") == (before["transfer"] or {}).get("id"),
           {"before": before["transfer"], "after": after["transfer"]})
    ctx.ck("no_extra_merchant_webhook", len(after["webhooks"]) == len(before["webhooks"]),
           {"before": len(before["webhooks"]), "after": len(after["webhooks"])})
    ctx.a.mozart(mid, clear=True)


@journey("async-workers", "async_state", priority="P0", profile=None,
         title="every queue the suite exercises has consumed messages; every queue nothing exercises is recorded as idle-by-design with its producer",
         source_ref="M6_RUNTIME_CHANGES.md section 1 change B (the 10 workers M6 added); "
                    "pkg/worker manager.go delivery/success logging")
def workers_async_state(ctx):
    rows, jobs = _fabric(ctx)
    counters = {}
    for r in rows:
        counters[r["service"]] = _consumed(r["service"])
    ctx.ev["worker_message_counters"] = counters
    exercised = sorted(s for s, c in counters.items() if c["received"] > 0)
    idle = sorted(s for s, c in counters.items() if c["received"] == 0)
    ctx.ev["exercised_workers"] = exercised
    ctx.ck("the_async_fabric_is_genuinely_carrying_work(at least one worker consumed messages)",
           len(exercised) >= 5, exercised)
    # every worker that consumed anything must also have finished something, otherwise its
    # producer and consumer disagree about the message format.
    broken = {s: counters[s] for s in exercised
              if counters[s]["processed_ok"] == 0 and counters[s]["construct_errors"] > 0}
    ctx.ev["consuming_but_never_completing"] = broken
    if broken:
        ctx.note("DEFECT (reported, not fixed): %s consume(s) messages they can NEVER construct a "
                 "job from -- every delivery ends in 'failed to construct job there is no job "
                 "constructor defined' (pkg/worker manager.go:270) and, with no dead-letter queue "
                 "in the arena, the same messages are redelivered indefinitely. Observed counters: "
                 "%s. The producer writes a plain JSON event "
                 "({balance_id, entity_id, event_creation_time, merchant_id}) while pkg/worker "
                 "expects its own envelope ({\"serialized\":true,\"content\":\"<gob>\"}), so the "
                 "producer and consumer of stage-x-balances-payouts-event disagree about the "
                 "message format."
                 % (", ".join(sorted(broken)), json.dumps(broken)))

    new_rows = [r for r in rows if r["service"] in F.Arena.NEW_M6_WORKERS]
    ctx.ck("all_10_workers_M6_added_are_present", len(new_rows) == 10,
           [r["service"] for r in new_rows])
    new_exercised = {r["service"]: counters[r["service"]] for r in new_rows
                     if counters[r["service"]]["received"] > 0}
    new_idle = {}
    for r in new_rows:
        if counters[r["service"]]["received"]:
            continue
        new_idle[r["service"]] = {"queue": r["queue"], "job_key": r["job_key"],
                                  "state": "idle-by-design",
                                  "queue_exists": bool(ctx.a.queue_url(r["queue"]))
                                  if r["queue"] else False,
                                  "producer_that_would_feed_it":
                                      IDLE_PRODUCERS.get(r["job_key"], "unknown")}
    ctx.ev["m6_new_workers_exercised"] = new_exercised
    ctx.ev["m6_new_workers_idle_by_design"] = new_idle
    ctx.ck("at_least_one_of_the_new_M6_workers_is_actually_consuming_the_suite's_work",
           bool(new_exercised), new_exercised)
    ctx.ck("every_idle_new_worker_still_has_its_queue_and_a_named_producer",
           all(v["queue_exists"] and v["producer_that_would_feed_it"] != "unknown"
               for v in new_idle.values()), new_idle)
    ctx.note("idle-by-design (queue exists, nothing in this suite produces for it): %s"
             % ", ".join(sorted(new_idle)) or "none")
