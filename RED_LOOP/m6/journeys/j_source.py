#!/usr/bin/env python3
"""family:source-updates -- telling the payout's SOURCE (payout_links / vendor_payments /
xpayroll / ...) how the payout is going.

A payout carries zero or more sources: `POST /v1/payouts` accepts `source_details`
[{source_id, source_type, priority}] and payouts persists them in `payout_sources`. Every time
the payout's internal status advances, payouts notifies the source. Two transports exist in the
source:

  HTTP  -> `POST /payouts_service/source_update` on the API monolith, with
           {payout_id, source_details, previous_status, expected_current_status}. This is the one
           the arena runs; monolith-stub records every call on its `/_arena/log` evidence plane
           and the payouts worker/api logs it as SOURCE_UPDATE_SUCCESS.
  SNS   -> `[source_update_topics]` maps each source_type to an SNS topic
           (`payout-updates-test-dev` for all nine in the arena). It is gated twice:
           `[features] source_updater_sns_enabled` in the rendered payouts config and the Splitz
           experiment `arena_ps_source_updater_sns_experiment`
           (seeds/splitz/experiments.json, `source_updater_sns_experiment`).

The SNS leg is deliberately NOT switched on: `source_updater_sns_enabled` is false in payouts'
own `config/default.toml:718` and `config/prod.toml` carries no override, and the Splitz
experiment defaults off -- so in the pinned production config the legacy HTTP call is the
transport that actually runs. The `async_state` variant therefore asserts that transport, and
keeps a real SQS queue subscribed to the real `payout-updates-test-dev` topic as the faithful
negative: it must stay empty.
"""
import json
import re
import time
import uuid

import framework as F
from framework import journey

SRC = "source"
SRC_TYPE = "payout_links"
SNS_TOPIC = "payout-updates-test-dev"
PROBE_QUEUE = "m6-journeys-source-updates"


def _source_details(tag="pl"):
    return [{"source_id": "%s_%s" % (tag, uuid.uuid4().hex[:12]),
             "source_type": SRC_TYPE, "priority": 1}]


def _create_with_source(ctx, amount, note, sources=None, mode="IMPS"):
    sources = sources if sources is not None else _source_details()
    cr = ctx.create(amount, note, mode=mode, extra={"source_details": sources})
    ctx.ck("create_200_%s" % note, cr["status"] == 200 and bool(cr["id"]), cr.get("raw"))
    cr["source_details"] = sources
    return cr


def _payout_sources(ctx, pid):
    rc, out, _ = ctx.a.payouts_sql(
        "SELECT source_id,source_type,priority FROM payout_sources WHERE payout_id='%s'" % pid,
        note="payout_sources rows payouts persisted from source_details")
    return [dict(zip(("source_id", "source_type", "priority"), l.split("\t")))
            for l in (out or "").strip().splitlines() if l.strip()]


def _updates(ctx, pid):
    """Every source_update / status_details_source_update the monolith recorded for this payout."""
    hits = ctx.a.monolith_log(pid)
    out = []
    for h in hits:
        if h.get("kind") not in ("source_update", "status_details_source_update"):
            continue
        p = h.get("payload") or {}
        if p.get("payout_id") != pid:
            continue
        out.append({"kind": h["kind"], "at": h.get("at"),
                    "source_details": p.get("source_details"),
                    "previous_status": p.get("previous_status"),
                    "expected_current_status": p.get("expected_current_status")})
    out.sort(key=lambda x: x.get("at") or 0)
    return out


def _sns_probe_setup(ctx):
    """Real SNS -> SQS fan-out so a publish, if any, is observable. Returns (topic_arn, queue_url)."""
    rc, topics, _ = ctx.a.awslocal("sns", "list-topics", note="SNS topics in localstack")
    arns = [t["TopicArn"] for t in (topics or {}).get("Topics", [])]
    arn = next((a for a in arns if a.endswith(":" + SNS_TOPIC)), None)
    ctx.ev["sns_topics"] = arns
    if not arn:
        ctx.blocked(
            "the SNS topic %r to exist in the localstack bootstrap. payouts' "
            "[source_update_topics] maps every source_type to %r but localstack only has %s."
            % (SNS_TOPIC, SNS_TOPIC, arns), {"topics": arns})
    ctx.a.awslocal("sqs", "create-queue", "--queue-name", PROBE_QUEUE,
                   note="observation queue for the payout-updates topic")
    url = ctx.a.queue_url(PROBE_QUEUE)
    qarn = ctx.a.queue_attrs(url, "QueueArn").get("QueueArn")
    ctx.a.awslocal("sns", "subscribe", "--topic-arn", arn, "--protocol", "sqs",
                   "--notification-endpoint", qarn, "--attributes", "RawMessageDelivery=true",
                   note="subscribe the observation queue to the payout-updates topic")
    # drain anything left from an earlier run so a read is unambiguous
    ctx.a.awslocal("sqs", "purge-queue", "--queue-url", url, note="drain the observation queue")
    time.sleep(2)
    return arn, url


def _sns_read(ctx, url, tries=6):
    msgs = []
    for _ in range(tries):
        rc, doc, _raw = ctx.a.awslocal("sqs", "receive-message", "--queue-url", url,
                                       "--max-number-of-messages", "10", "--wait-time-seconds", "5",
                                       note="read the payout-updates observation queue")
        got = (doc or {}).get("Messages") or []
        msgs.extend(got)
        for m in got:
            ctx.a.awslocal("sqs", "delete-message", "--queue-url", url,
                           "--receipt-handle", m["ReceiptHandle"], note="ack observed message")
        if got:
            break
    return msgs


def _worker_source_updates(ctx, pid, services=None):
    """Which REAL worker containers emitted SOURCE_UPDATE_SUCCESS *for this payout*.

    The log line itself carries only the worker request/task ids, so it is correlated with the
    payout through the JOB_HANDLER_INIT line of the same request_id, which does carry payout_id.
    That is what makes 'the HTTP source-update transport runs inside a worker container' an
    assertion rather than an assumption."""
    import subprocess
    out = {}
    for svc in (services or F.Arena.PAYOUTS_WORKERS):
        try:
            blob = subprocess.run(["docker", "logs", "--since", "30m", F.P.cname(svc)],
                                  capture_output=True, text=True, timeout=60)
        except Exception:  # noqa: BLE001
            continue
        lines = (blob.stdout + blob.stderr).splitlines()
        reqs = set()
        for line in lines:
            if pid in line:
                m = re.search(r'"request_id":"([^"]+)"', line)
                if m:
                    reqs.add(m.group(1))
        if not reqs:
            continue
        n = sum(1 for line in lines
                if "SOURCE_UPDATE_SUCCESS" in line
                and any('"request_id":"%s"' % r in line for r in reqs))
        if n:
            out[svc] = {"source_update_success_lines": n, "request_ids": sorted(reqs)[:4]}
    ctx.ev.setdefault("worker_source_update_attribution", {})[pid] = out
    return out


def _sns_gates(ctx):
    """Read the two gates out of the live arena rather than asserting them from the tree."""
    import subprocess
    out = subprocess.run(["docker", "exec", F.P.cname("payouts-api"), "sh", "-c",
                          "grep -n 'source_updater_sns_enabled' /app/config/arena.toml"],
                         capture_output=True, text=True, timeout=30)
    cfg = (out.stdout + out.stderr).strip()[:200]
    st, body = ctx.a.jhttp(
        "POST", "http://splitz-stub:8080/twirp/rzp.splitz.evaluate.v1.EvaluateAPI/Evaluate",
        {"experiment_id": "arena_ps_source_updater_sns_experiment",
         "id": ctx.m["merchant_id"] if ctx.m else "ARENAM00000001",
         "id_type": "merchant_id", "request_data": {}},
        note="Splitz gate for the source-updater SNS transport")
    gates = {"config_line": cfg, "splitz_status": st, "splitz_body": body}
    ctx.ev["sns_gates"] = gates
    return gates


# ---------------------------------------------------------------------------
@journey("source-updates", "success", priority="P0", profile=SRC,
         title="a payout created with source_details notifies its source at every internal status advance, right through to the terminal state",
         source_ref="payouts payout_sources + the source-updater; monolith Route.php "
                    "payouts_service/source_update; ENV2_COMPOSE/substitutes/monolith-stub/server.py "
                    "_source_update records every call on /_arena/log")
def source_success(ctx):
    mid = ctx.m["merchant_id"]
    ctx.a.mozart(mid, "hold")
    cr = _create_with_source(ctx, 4300, "src-success")
    pid = cr["id"]
    if not pid:
        return
    rows = _payout_sources(ctx, pid)
    ctx.ev["payout_sources"] = rows
    ctx.ck("payouts_persisted_the_source_from_source_details",
           len(rows) == 1 and rows[0]["source_id"] == cr["source_details"][0]["source_id"]
           and rows[0]["source_type"] == SRC_TYPE, {"expected": cr["source_details"], "rows": rows})
    ctx.wait_status(pid, "initiated", timeout=60)
    early = _updates(ctx, pid)
    ctx.ev["updates_before_terminal"] = early
    ctx.ck("the_source_was_notified_while_the_payout_was_still_in_flight", len(early) >= 2, early)
    ctx.ck("every_notification_carries_this_payout's_own_source_details",
           all(u["source_details"] == cr["source_details"] for u in early), early)
    ctx.ck("every_notification_names_the_transition_it_reports",
           all(u["previous_status"] and u["expected_current_status"] for u in early), early)
    chain = [(u["previous_status"], u["expected_current_status"]) for u in early]
    ctx.ck("the_reported_transitions_chain_together(previous == the last expected_current)",
           all(chain[i][1] == chain[i + 1][0] for i in range(len(chain) - 1)), chain)

    ctx.a.finish_bank(pid, "success")
    row = ctx.wait_status(pid, "processed", timeout=120)
    ctx.ck("payout_processed", (row or {}).get("status") == "processed", row)
    final = F.wait_until(lambda: (lambda u: u if len(u) > len(early) else None)(_updates(ctx, pid)),
                         timeout=60, interval=4) or _updates(ctx, pid)
    ctx.ev["updates_after_terminal"] = final
    ctx.ck("the_source_was_notified_of_the_terminal_outcome", len(final) > len(early), final[-3:])
    ctx.ck("a_terminal_notification_names_the_processed_state",
           any("process" in str(u["expected_current_status"]).lower()
               or "process" in str(u["previous_status"]).lower() for u in final[len(early):]),
           final[len(early):])
    ctx.state["src_pid"] = pid
    ctx.state["src_details"] = cr["source_details"]
    ctx.a.mozart(mid, clear=True)


@journey("source-updates", "failure", priority="P0", profile=SRC,
         title="a payout with no source notifies nobody; a sourced payout that fails at the bank still has its failure reported to the source",
         source_ref="the source-updater only fires for payouts with payout_sources rows; "
                    "asyncFailureHandlingHelper.go drives the failure/reversal transitions")
def source_failure(ctx):
    mid = ctx.m["merchant_id"]
    ctx.a.mozart(mid, "hold")
    # negative control: no source_details -> no payout_sources row and no source notification
    plain = ctx.create(4100, "src-nosource")
    ctx.ck("create_200_no_source", plain["status"] == 200 and bool(plain["id"]), plain.get("raw"))
    if not plain["id"]:
        return
    ctx.wait_status(plain["id"], "initiated", timeout=60)
    ctx.ck("a_payout_created_without_source_details_has_no_payout_sources_row",
           _payout_sources(ctx, plain["id"]) == [], _payout_sources(ctx, plain["id"]))
    sourceless = _updates(ctx, plain["id"])
    ctx.ev["sourceless_updates"] = sourceless
    ctx.ck("no_real_source_is_ever_addressed_for_a_payout_that_has_none",
           all(not (sd or {}).get("source_id") and not (sd or {}).get("source_type")
               for u in sourceless for sd in (u["source_details"] or [{}])), sourceless)
    ctx.a.finish_bank(plain["id"], "success")
    ctx.wait_status(plain["id"], "processed", timeout=120)
    after_plain = _updates(ctx, plain["id"])
    ctx.ck("still_no_real_source_addressed_after_it_completed",
           all(not (sd or {}).get("source_id") and not (sd or {}).get("source_type")
               for u in after_plain for sd in (u["source_details"] or [{}])), after_plain)
    if after_plain:
        ctx.note("OBSERVATION (reported, not asserted as a defect): payouts calls the monolith's "
                 "`/payouts_service/source_update` on EVERY internal status advance even when the "
                 "payout has no sources at all -- %d calls for a source-less payout, each carrying "
                 "`source_details: [{}]`. The notification addresses nobody, so it is one wasted "
                 "monolith round-trip per transition per payout." % len(after_plain))

    # the failure path of a sourced payout
    cr = _create_with_source(ctx, 4200, "src-failure")
    pid = cr["id"]
    if not pid:
        return
    ctx.wait_status(pid, "initiated", timeout=60)
    before = _updates(ctx, pid)
    ctx.a.finish_bank(pid, "failure")
    row = ctx.wait_terminal(pid, timeout=140, terminal=("reversed", "failed"))
    ctx.ck("the_bank_failure_became_a_terminal_payout_state",
           (row or {}).get("status") in ("reversed", "failed"), row)
    after = F.wait_until(lambda: (lambda u: u if len(u) > len(before) else None)(_updates(ctx, pid)),
                         timeout=70, interval=4) or _updates(ctx, pid)
    ctx.ev["updates_on_failure"] = after
    ctx.ck("the_source_was_notified_after_the_failure_too", len(after) > len(before), after[-3:])
    ctx.ck("the_failure_notifications_still_carry_the_right_source",
           all(u["source_details"] == cr["source_details"] for u in after), after[-3:])
    ctx.a.mozart(mid, clear=True)


@journey("source-updates", "idempotency", priority="P0", profile=SRC,
         title="each lifecycle transition is reported to the source exactly once: a replayed terminal FTS webhook adds no further notification",
         source_ref="payouts fts_transfer_status_webhook.go state guard (the same guard "
                    "journey:shared-payouts/duplicate pins); monolith-stub /_arena/log is the "
                    "per-call record")
def source_idempotency(ctx):
    mid = ctx.m["merchant_id"]
    ctx.a.mozart(mid, "hold")
    cr = _create_with_source(ctx, 4400, "src-idem")
    pid = cr["id"]
    if not pid:
        return
    ctx.wait_status(pid, "initiated", timeout=60)
    ctx.a.finish_bank(pid, "success")
    row = ctx.wait_status(pid, "processed", timeout=120)
    ctx.ck("payout_processed", (row or {}).get("status") == "processed", row)
    time.sleep(6)
    before = _updates(ctx, pid)
    ctx.ev["updates_before_replay"] = before
    ctx.ck("the_source_was_notified_at_least_once", bool(before), before)
    pairs = [(u["kind"], u["previous_status"], u["expected_current_status"]) for u in before]
    dupes = [p for p in set(pairs) if pairs.count(p) > 1]
    ctx.ck("no_lifecycle_transition_was_reported_to_the_source_twice", not dupes,
           {"duplicated": dupes, "chain": pairs})

    t = ctx.a.transfer(pid)
    body = {"fund_transfer_id": int(t["id"]), "source_id": pid, "source_type": "payout",
            "status": "processed", "utr": (row or {}).get("utr"),
            "source_account_id": int(t["source_account_id"]),
            "bank_account_type": t.get("sa_bank_account_type")}
    st, txt = ctx.a.http("POST", F.PAYOUTS + "/v1/payouts/transfer_status_webhook", body,
                         basic=F.bridge("ps-service"),
                         note="replay the terminal FTS status webhook (real internal route)")
    ctx.ck("the_replay_reached_the_real_route_and_was_guarded",
           isinstance(st, int) and st in (200, 400, 409), {"status": st, "body": (txt or "")[:250]})
    time.sleep(8)
    after = _updates(ctx, pid)
    ctx.ev["updates_after_replay"] = after
    ctx.ck("the_replay_produced_no_additional_source_notification", len(after) == len(before),
           {"before": len(before), "after": len(after), "new": after[len(before):]})
    ctx.ck("payout_status_unchanged_by_the_replay",
           (ctx.a.payout(pid) or {}).get("status") == "processed", None)
    ctx.a.mozart(mid, clear=True)


@journey("source-updates", "async_state", priority="P0", profile=SRC,
         title="the source is notified of the worker-driven terminal outcome over the LIVE transport (legacy HTTP source_update); the SNS leg is source-faithfully disabled and publishes nothing",
         source_ref="payouts config/default.toml:718 source_updater_sns_enabled = false with NO "
                    "override in config/prod.toml (verified) and Splitz source_updater_sns_experiment "
                    "defaulting off -> the SNS transport is OFF in the pinned production config, so "
                    "the legacy HTTP call to the monolith (Route.php payouts_service/source_update) "
                    "is the transport production actually runs")
def source_async_state(ctx):
    """The transport asserted here is the one that is live in the pinned production config.

    The SNS leg is NOT switched on for this journey: `source_updater_sns_enabled` is false in
    payouts' own default.toml (:718) and prod.toml carries no override, and the Splitz experiment
    `source_updater_sns_experiment` defaults off -- so a twin that published to SNS would be
    *less* faithful, not more. The SQS-subscription probe is kept as the faithful negative: a real
    queue is subscribed to the real `payout-updates-test-dev` topic and must stay empty.
    """
    mid = ctx.m["merchant_id"]
    arn, url = _sns_probe_setup(ctx)
    ctx.ck("the_payout-updates_SNS_topic_exists_in_localstack", bool(arn), arn)
    ctx.ck("an_observation_queue_is_subscribed_to_it_and_drained", bool(url), url)

    ctx.a.mozart(mid, "hold")
    cr = _create_with_source(ctx, 4600, "src-async")
    pid = cr["id"]
    if not pid:
        return
    ctx.wait_status(pid, "initiated", timeout=60)
    before_terminal = _updates(ctx, pid)
    ctx.ev["updates_before_terminal"] = before_terminal
    ctx.ck("the_source_was_already_being_notified_in_flight", bool(before_terminal),
           before_terminal[-2:])

    ctx.a.finish_bank(pid, "success")
    row = ctx.wait_status(pid, "processed", timeout=120)
    ctx.ck("the_payout_reached_a_terminal_state_through_the_workers",
           (row or {}).get("status") == "processed", row)
    hits = ctx.a.worker_log_hits(pid)
    ctx.ev["worker_log_hits"] = hits
    ctx.ck("the_terminal_transition_is_attributable_to_a_real_worker_container",
           any(k.startswith("payouts-worker-") for k in hits), hits)

    # -- the transport that IS live: legacy HTTP source_update to the monolith --------------
    final = F.wait_until(
        lambda: (lambda u: u if len(u) > len(before_terminal) else None)(_updates(ctx, pid)),
        timeout=70, interval=4) or _updates(ctx, pid)
    ctx.ev["http_transport_updates"] = final
    new_after_terminal = final[len(before_terminal):]
    ctx.ck("the_source_was_notified_of_the_TERMINAL_outcome_over_the_legacy_HTTP_transport",
           bool(new_after_terminal), new_after_terminal)
    ctx.ck("the_terminal_notification_names_the_processed_transition",
           any("process" in str(u["expected_current_status"]).lower()
               or "process" in str(u["previous_status"]).lower() for u in new_after_terminal),
           new_after_terminal)
    ctx.ck("every_notification_carries_this_payout's_own_source",
           all(u["source_details"] == cr["source_details"] for u in final), final[-2:])
    attribution = _worker_source_updates(ctx, pid)
    ctx.ck("the_HTTP_source-update_transport_runs_inside_a_real_worker_container(request-id correlated)",
           bool(attribution), attribution)

    # -- the SNS leg: faithful negative -----------------------------------------------------
    msgs = _sns_read(ctx, url, tries=3)
    gates = _sns_gates(ctx)
    updater = ctx.a.worker_log_hits(pid, services=("payouts-worker-payout-source-updater",))
    mine = [m for m in msgs if pid in (m.get("Body") or "")]
    ctx.ev["sns_leg"] = {
        "status": "source_faithful_disabled",
        "topic": arn,
        "observation_queue": url,
        "messages_seen_for_this_payout": len(mine),
        "messages_seen_on_the_topic_at_all": len(msgs),
        "gate_1_payouts_config": "payouts config/default.toml:718 source_updater_sns_enabled = "
                                 "false, with NO override in config/prod.toml (verified); the "
                                 "arena's rendered copy carries the same value -> %s"
                                 % gates["config_line"],
        "gate_2_splitz": "experiment source_updater_sns_experiment "
                         "(arena_ps_source_updater_sns_experiment) defaults off; live evaluation "
                         "-> %s" % json.dumps(gates["splitz_body"])[:200],
        "payout_source_updater_worker_hits": updater,
        "why_not_enabled": "turning the SNS leg on would make the twin LESS faithful than the "
                           "pinned production config, so it is deliberately left off and the "
                           "absence of a publish is asserted as the expected outcome."}
    ctx.ck("SNS_leg_is_source-faithfully_disabled:_nothing_was_published_for_this_payout",
           not mine, {"messages_for_this_payout": len(mine), "messages_on_topic": len(msgs)})
    ctx.ck("and_the_payout_source_updater_worker_consumed_nothing(its queue is fed by the SNS path)",
           not updater, updater)
    ctx.note("SNS transport recorded as `source_faithful_disabled`: payouts "
             "config/default.toml:718 `source_updater_sns_enabled = false` with no prod.toml "
             "override, and Splitz `source_updater_sns_experiment` defaults off. Topic "
             "%s exists in localstack and SQS queue %s is subscribed to it as the faithful "
             "negative -- it stayed empty. The transport asserted instead is the legacy HTTP "
             "`payouts_service/source_update` call to the monolith, which fired %d times for this "
             "payout including after the terminal outcome."
             % (SNS_TOPIC, PROBE_QUEUE, len(final)))
    ctx.a.mozart(mid, clear=True)
