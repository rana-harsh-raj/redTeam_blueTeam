#!/usr/bin/env python3
"""channel_control.py -- CONTROL-PLANE-ONLY driver for Payouts channel health.

WHAT THIS IS
------------
A host-run, Python-STDLIB-only operator tool that flips a partner-bank channel
DOWN/UP for the running arena, and (optionally) provisions a second (icici,
direct) channel as an additive M2 runtime fixture.

It is CONTROL PLANE ONLY: it is never exposed to the attacker / merchant surface.
It works by:
  * reading the FTS Basic-auth secret from the HOST secret file
    (ENV2_COMPOSE/secrets/auth_fts_payouts.txt), and
  * `docker exec`-ing into a running arena container that CAN reach payouts-api
    (kong-lite -- it runs python3 and sits on the internal `rzp-arena` net,
    reaching http://payouts-api:9400), to POST the internal route
    `POST /v1/notify/health/update` (BasicAuth cred.FTS).

payouts-api is NOT host-reachable (only kong-lite:18080 is published; the arena
nets are internal), which is exactly why the POST is proxied through a
`docker exec` rather than issued from the host.

VERIFIED CONTRACT (file:line)
-----------------------------
Route (BasicAuth cred.FTS):
  .local/twin-repos/accepted/payouts/internal/routing/router/internal_routes.go
    :11-31  group "/v1/notify", middleware BasicAuth(cred.FTS),
            endpoint POST "/health/update" -> HealthNotification.HealthStatusUpdate
DTO (request body shape):
  .local/twin-repos/accepted/payouts/internal/app/dtos/v2/channel_health_notification_request.go
    :8-30   HealthStatusUpdateRequest{ type, payload }
            HealthStatusUpdateRequestPayload{ channel, mode, account_type,
              status(binding:"required"), source, exclude_merchants, ... }
Dispatch + semantics:
  .local/twin-repos/accepted/payouts/internal/app/channelhealth/service.go:21-40
     -> FetchSourceEvent(ctx, input.Payload.Source)      # dispatch key is payload.SOURCE
  .../channelhealth/notification_factory.go:15-24
     -> case "partner_bank_health" -> PartnerBankDowntimeNotification
  .../channelhealth/partner_bank_health_manager.go
     :17-18  Redis key "partner_bank_health"
     :24-36  HSet(PartnerBankHealthRedisKey, generateConfigKey(account_type,channel,mode), payload)
     :119-123 IsChannelDown := EqualFold(result.Status, "downtime")
     :126-129 generateConfigKey := lower("<account_type>_<channel>_<mode>")
  .../appConstants/constants.go:401-402  StatusUptime="uptime", StatusDowntime="downtime"

=> DOWN body:
   {"type":"partner_bank_health",
    "payload":{"source":"partner_bank_health","channel":"rbl","mode":"IMPS",
               "account_type":"direct","status":"downtime","exclude_merchants":[]}}
   UP body is identical with "status":"uptime".
   NOTE: the factory dispatches on payload.source (NOT the top-level "type");
   both are set to "partner_bank_health" so either read path resolves correctly.

`status` subcommand: there is NO read-back route for partner_bank_health in the
payouts internal router (only /v1/notify/health/update [POST] and
/v1/notify/downtime [POST] exist -- internal_routes.go; the /health GET in
status_routes.go is the service liveness probe, unrelated). So `status` is
best-effort and reports the config it WOULD write; the store is write-only over HTTP.

SAFETY
------
* --dry-run prints the exact `docker exec` argv + JSON body WITHOUT executing.
  (The FTS password is masked as *** in printed commands; it is still loaded
  from the host secret file and sent for real, non-dry, invocations.)
* down/up are idempotent (HSet overwrites the same config key); safe to re-run.
* provision-icici is an OPTIONAL, clearly-marked, UNVERIFIED M2 additive runtime
  fixture (docker exec mysql INSERT ... ON DUPLICATE KEY) -- NOT a seed-file
  edit, to preserve the frozen fingerprint. See its own WARNING.

STDLIB only. No third-party imports.
"""
import argparse
import json
import os
import subprocess
import sys

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------
_THIS = os.path.dirname(os.path.abspath(__file__))
# RED_LOOP/surface/ -> repo root is two levels up.
_REPO_ROOT = os.path.abspath(os.path.join(_THIS, "..", ".."))
_SECRETS = os.path.join(_REPO_ROOT, "ENV2_COMPOSE", "secrets")

FTS_SECRET_FILE = os.path.join(_SECRETS, "auth_fts_payouts.txt")
MYSQL_PAYOUTS_PW_FILE = os.path.join(_SECRETS, "mysql_payouts_root_password.txt")
MYSQL_FTS_PW_FILE = os.path.join(_SECRETS, "mysql_fts_root_password.txt")

FTS_USERNAME = "fts"  # arena.toml [auth.fts] username; password = secret file
PAYOUTS_API_URL = "http://payouts-api:9400/v1/notify/health/update"

# Preferred exec container: kong-lite (has python3, reaches payouts-api on the
# internal rzp-arena net). Discovered dynamically; this is only the image hint.
KONG_IMAGE_HINT = "kong-lite"
MYSQL_PAYOUTS_CONTAINER_HINT = "mysql-payouts"
MYSQL_FTS_CONTAINER_HINT = "mysql-fts"

SOURCE = "partner_bank_health"

# Remote (inside-container) POST script -- reads everything from env so no
# shell-quoting of the JSON body is needed.
_REMOTE_POST = (
    "import os,sys,base64,urllib.request,urllib.error\n"
    "url=os.environ['PA_URL']; body=os.environ['PA_BODY'].encode()\n"
    "tok=base64.b64encode((os.environ['PA_USER']+':'+os.environ['PA_PASS']).encode()).decode()\n"
    "req=urllib.request.Request(url,data=body,method='POST',"
    "headers={'Content-Type':'application/json','Authorization':'Basic '+tok})\n"
    "try:\n"
    "    r=urllib.request.urlopen(req,timeout=10)\n"
    "    print('HTTP',r.status); sys.stdout.write(r.read().decode('utf-8','replace'))\n"
    "except urllib.error.HTTPError as e:\n"
    "    print('HTTP',e.code); sys.stdout.write(e.read().decode('utf-8','replace'))\n"
    "except Exception as e:\n"
    "    print('ERR',repr(e)); sys.exit(2)\n"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _read_secret(path):
    with open(path) as f:
        return f.read().strip()


def _load_fts_auth():
    """Return (username, password). Secret file may be 'user:pass' or a bare
    password (arena writes a bare generated password; username comes from
    arena.toml [auth.fts] = 'fts')."""
    raw = _read_secret(FTS_SECRET_FILE)
    if ":" in raw:
        user, _, pw = raw.partition(":")
        return user or FTS_USERNAME, pw
    return FTS_USERNAME, raw


def _docker_ps():
    out = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}\t{{.Image}}"],
        capture_output=True, text=True,
    )
    rows = []
    for line in out.stdout.splitlines():
        if "\t" in line:
            name, image = line.split("\t", 1)
            rows.append((name.strip(), image.strip()))
    return rows


def _discover_container(hint, explicit=None):
    if explicit:
        return explicit
    rows = _docker_ps()
    # Match on name first, then image.
    for name, image in rows:
        if hint in name:
            return name
    for name, image in rows:
        if hint in image:
            return name
    return None


def _build_body(channel, mode, account_type, status):
    payload = {
        "source": SOURCE,
        "channel": channel,
        "mode": mode,
        "account_type": account_type,
        "status": status,
        "exclude_merchants": [],
    }
    return {"type": SOURCE, "payload": payload}


def _config_key(account_type, channel, mode):
    # Mirrors generateConfigKey in partner_bank_health_manager.go:126-129.
    return ("%s_%s_%s" % (account_type, channel, mode)).lower()


def _exec_argv(container, url, user, pw, body_json, mask_pw=False):
    return [
        "docker", "exec",
        "-e", "PA_URL=" + url,
        "-e", "PA_USER=" + user,
        "-e", "PA_PASS=" + ("***" if mask_pw else pw),
        "-e", "PA_BODY=" + body_json,
        container, "python3", "-c", _REMOTE_POST,
    ]


def _print_argv(argv):
    # Render an approximately copy-pasteable command (single-quote each arg).
    def q(a):
        if a == _REMOTE_POST:
            return "'<inline python POST script>'"
        if any(c in a for c in " \t\"'{}$"):
            return "'" + a.replace("'", "'\\''") + "'"
        return a
    print(" ".join(q(a) for a in argv))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def cmd_health(args, status):
    user, pw = _load_fts_auth()
    body = _build_body(args.channel, args.mode, args.account_type, status)
    body_json = json.dumps(body, separators=(",", ":"), sort_keys=True)
    ckey = _config_key(args.account_type, args.channel, args.mode)

    container = _discover_container(KONG_IMAGE_HINT, args.container)
    if container is None and not args.dry_run:
        print("ERROR: no running kong-lite container found (image/name hint '%s'). "
              "Pass --container <name>." % KONG_IMAGE_HINT, file=sys.stderr)
        return 3

    print("# channel-health %s" % status.upper())
    print("#   config key (redis HSET field): %s" % ckey)
    print("#   exec container: %s" % (container or "<none discovered>"))
    print("#   JSON body:")
    print(body_json)

    argv_masked = _exec_argv(container or "<CONTAINER>", PAYOUTS_API_URL,
                             user, pw, body_json, mask_pw=True)
    print("# docker exec command (password masked):")
    _print_argv(argv_masked)

    if args.dry_run:
        print("# --dry-run: not executed.")
        return 0

    argv = _exec_argv(container, PAYOUTS_API_URL, user, pw, body_json, mask_pw=False)
    res = subprocess.run(argv, capture_output=True, text=True)
    if res.stdout:
        sys.stdout.write(res.stdout if res.stdout.endswith("\n") else res.stdout + "\n")
    if res.stderr:
        sys.stderr.write(res.stderr)
    return res.returncode


def cmd_status(args):
    user, _pw = _load_fts_auth()
    ckey = _config_key(args.account_type, args.channel, args.mode)
    print("# status is best-effort: the payouts internal router exposes NO")
    print("# read-back route for partner_bank_health (only POST /v1/notify/health/update")
    print("# and POST /v1/notify/downtime exist). The health store is write-only over HTTP.")
    print("# It lives in Redis under hash '%s', field '%s'." % (SOURCE, ckey))
    print("# To inspect directly (control-plane), read that Redis hash field on the")
    print("# payouts redis, e.g.:")
    print("#   docker exec <payouts-redis> redis-cli HGET %s %s" % (SOURCE, ckey))
    print("# auth user that would be used for writes: %s" % user)
    return 0


_ICICI_WARNING = (
    "WARNING: provision-icici is an UNVERIFIED, best-effort M2 additive runtime\n"
    "fixture. The payouts.banking_accounts shape is taken from\n"
    "ENV2_COMPOSE/seeds/s4/payouts.sql, but the FTS source_accounts/routing_rules\n"
    "shapes come from ENV2_COMPOSE/seeds/mysql/fts_seed.sql which ITSELF is\n"
    "flagged as an unverified skeleton (its own header TODO). balance_id and\n"
    "fts_fund_account_id below are placeholders reusing M2's values and may not\n"
    "wire up a fully functional second channel. Review before relying on it.\n"
)


def cmd_provision_icici(args):
    sys.stderr.write(_ICICI_WARNING + "\n")
    merchant = args.merchant

    payouts_sql = (
        "INSERT INTO banking_accounts "
        "(id, merchant_id, balance_id, channel, status, account_number, account_type, "
        "fts_fund_account_id, payout_service_enabled, counter_migrated, created_at, updated_at) "
        "VALUES ('ARENABAICICI01', '%s', 'ARENABAL000002', 'icici', 'activated', "
        "'2323230000000009', 'direct', '900009', 1, 1, UNIX_TIMESTAMP(), UNIX_TIMESTAMP()) "
        "ON DUPLICATE KEY UPDATE channel=VALUES(channel);" % merchant
    )
    fts_sql = (
        "INSERT INTO source_accounts (id, merchant_id, account_number, channel, gateway, "
        "gateway_version, status, created_at) VALUES "
        "('sa_%s_icici', '%s', '999000000000009', 'icici', 'icici', 'v1', 'active', UNIX_TIMESTAMP()) "
        "ON DUPLICATE KEY UPDATE status=VALUES(status); "
        "INSERT INTO routing_rules (id, merchant_id, mode, gateway, gateway_version, priority) VALUES "
        "('rr_%s_icici_imps', '%s', 'IMPS', 'icici', 'v1', 1) "
        "ON DUPLICATE KEY UPDATE priority=VALUES(priority);" % (merchant, merchant, merchant, merchant)
    )

    payouts_c = _discover_container(MYSQL_PAYOUTS_CONTAINER_HINT, args.payouts_container)
    fts_c = _discover_container(MYSQL_FTS_CONTAINER_HINT, args.fts_container)

    py_pw = _read_secret(MYSQL_PAYOUTS_PW_FILE) if os.path.exists(MYSQL_PAYOUTS_PW_FILE) else "<PW>"
    fts_pw = _read_secret(MYSQL_FTS_PW_FILE) if os.path.exists(MYSQL_FTS_PW_FILE) else "<PW>"

    def mysql_argv(container, pw, db, sql, mask=False):
        return ["docker", "exec", "-i", container or "<CONTAINER>",
                "mysql", "-uroot", "-p" + ("***" if mask else pw), db, "-e", sql]

    print("# provision-icici (merchant=%s)" % merchant)
    print("# --- payouts.banking_accounts (db 'payouts', container %s) ---" % (payouts_c or "<none>"))
    print(payouts_sql)
    _print_argv(mysql_argv(payouts_c, py_pw, "payouts", payouts_sql, mask=True))
    print("# --- fts.source_accounts + routing_rules (db 'fts', container %s) ---" % (fts_c or "<none>"))
    print(fts_sql)
    _print_argv(mysql_argv(fts_c, fts_pw, "fts", fts_sql, mask=True))

    if args.dry_run:
        print("# --dry-run: not executed.")
        return 0

    if payouts_c is None or fts_c is None:
        print("ERROR: could not discover both mysql containers; pass "
              "--payouts-container / --fts-container.", file=sys.stderr)
        return 3

    rc = 0
    for container, pw, db, sql in (
        (payouts_c, py_pw, "payouts", payouts_sql),
        (fts_c, fts_pw, "fts", fts_sql),
    ):
        res = subprocess.run(mysql_argv(container, pw, db, sql), capture_output=True, text=True)
        sys.stdout.write(res.stdout)
        sys.stderr.write(res.stderr)
        rc = rc or res.returncode
    return rc


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _add_channel_args(p):
    p.add_argument("--channel", default="rbl")
    p.add_argument("--mode", default="IMPS")
    p.add_argument("--account-type", dest="account_type", default="direct")
    p.add_argument("--container", default=None,
                   help="exec container (default: auto-discover kong-lite)")
    p.add_argument("--dry-run", action="store_true",
                   help="print the docker exec command + JSON body, do not execute")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Control-plane Payouts channel-health driver.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_down = sub.add_parser("down", help="mark channel/mode/account-type DOWN (status=downtime)")
    _add_channel_args(p_down)
    p_up = sub.add_parser("up", help="mark channel/mode/account-type UP (status=uptime)")
    _add_channel_args(p_up)
    p_status = sub.add_parser("status", help="best-effort: report the config key (write-only store)")
    _add_channel_args(p_status)

    p_prov = sub.add_parser("provision-icici",
                            help="[UNVERIFIED M2 fixture] add a second (icici,direct) channel")
    p_prov.add_argument("--merchant", required=True, help="arena merchant id, e.g. ARENAM00000002")
    p_prov.add_argument("--payouts-container", default=None)
    p_prov.add_argument("--fts-container", default=None)
    p_prov.add_argument("--dry-run", action="store_true")

    args = ap.parse_args(argv)

    if args.cmd == "down":
        return cmd_health(args, "downtime")
    if args.cmd == "up":
        return cmd_health(args, "uptime")
    if args.cmd == "status":
        return cmd_status(args)
    if args.cmd == "provision-icici":
        return cmd_provision_icici(args)
    ap.error("unknown command")


if __name__ == "__main__":
    sys.exit(main())
