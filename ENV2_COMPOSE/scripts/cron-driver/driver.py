#!/usr/bin/env python3
"""cron-driver: external scheduler hitting payouts-api's /v1/cron/* endpoints.

Substitutes the real FastCron-driven external scheduler (BOM §1 "Cron
driver" row: "External scheduler hitting PS /v1/cron/* with FastCron
creds; FTS crons in-process (set INSTANCE_TYPE=canary on one FTS worker)").

Cadence below is EXPLICITLY UNVERIFIED (BOM §1: "PS cadence unknown; assume
queued 5 min, scheduled 5 min, on_hold 5 min, reservation reconcile 5 min
(code constant), dual-write failure 15 min -- flag as unverified"). Override
any interval via env without rebuilding.

Stdlib only (urllib), loops forever, logs each tick to stdout. Auth: Basic,
FastCron credential pair from secrets/ (env CRON_BASIC_AUTH_USER/PASS).
"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

PAYOUTS_BASE_URL = os.environ.get("PAYOUTS_BASE_URL", "http://payouts-api:9400")
AUTH_USER = os.environ.get("CRON_BASIC_AUTH_USER", "fast_cron")
AUTH_PASS = os.environ.get("CRON_BASIC_AUTH_PASS", "")
if not AUTH_PASS:
    # per-arena FastCron credential (payouts [auth.fastcron]) mounted by compose as a secret file
    try:
        with open(os.environ.get("CRON_BASIC_AUTH_PASS_FILE", "/run/secrets/auth_fastcron_payouts")) as _f:
            AUTH_PASS = _f.read().strip()
    except OSError:
        pass

# path -> interval_seconds (env override: CRON_INTERVAL_<UPPERCASED_PATH_KEY>)
JOBS = {
    "queued_partner_bank": ("/v1/cron/process_queued_payouts", 300),          # type=partner_bank_downtime only (queued/queued_payouts_factory.go)
    "queued_low_balance": ("/v1/cron/process_queued_low_balance_payouts", 300),
    "scheduled": ("/v1/cron/process_scheduled_payouts", 300),
    "on_hold": ("/v1/cron/process_on_hold_payouts", 300),
    "reservation_reconcile": ("/v1/cron/reservation_reconcile", 300),
    "dual_write_failure": ("/v1/cron/process_dual_write_failures", 900),
}


def _log(msg):
    sys.stdout.write("[cron-driver] %s\n" % msg)
    sys.stdout.flush()


def _interval_for(key, default):
    env_key = "CRON_INTERVAL_" + key.upper()
    try:
        return int(os.environ.get(env_key, default))
    except ValueError:
        return default


# payouts-api binds a JSON body on every cron route (an empty body is a 400 "EOF");
# process_queued_payouts additionally requires balance_ids (dtos: `json:"balance_ids" binding:"required"`).
BALANCE_IDS = [b for b in os.environ.get("CRON_BALANCE_IDS", "ARENABAL000001,ARENABAL000002,ARENABAL000003").split(",") if b]


def _body_for(path):
    # payouts-api rejects unknown fields ("json: unknown field"); /process_queued_payouts takes {"type": ...}
    # (dtos/v2 ProcessQueuedPayoutsRequest) and only "partner_bank_downtime" is registered; the others take {}.
    if path.endswith("/process_queued_payouts"):
        return {"type": "partner_bank_downtime"}
    return {}


def _hit(path):
    url = PAYOUTS_BASE_URL.rstrip("/") + path
    req = urllib.request.Request(url, data=json.dumps(_body_for(path)).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    if AUTH_USER:
        token = base64.b64encode(("%s:%s" % (AUTH_USER, AUTH_PASS)).encode()).decode()
        req.add_header("Authorization", "Basic %s" % token)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            _log("%s -> %s" % (path, resp.status))
    except urllib.error.HTTPError as exc:
        _log("%s -> HTTP %s" % (path, exc.code))
    except Exception as exc:  # noqa: BLE001 - keep looping regardless of transient failures
        _log("%s -> error %r" % (path, exc))


def main():
    next_run = {key: 0.0 for key in JOBS}
    _log("starting; jobs=%s" % list(JOBS.keys()))
    while True:
        now = time.time()
        for key, (path, default_interval) in JOBS.items():
            interval = _interval_for(key, default_interval)
            if now >= next_run[key]:
                _hit(path)
                next_run[key] = now + interval
        time.sleep(5)


if __name__ == "__main__":
    main()
