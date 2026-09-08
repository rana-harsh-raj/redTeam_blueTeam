#!/usr/bin/env python3
"""M6 lane I4 -- business-journey driver framework.

One executable harness for the Payouts functional-architecture twin's business
journeys. Every journey is a small driver function that receives a `Ctx`,
drives the LIVE arena through the same surfaces a merchant/operator would use
(kong-lite for the public API, the FastCron endpoints the cron-driver hits, the
substitutes' /_arena control planes for bank/monolith stimuli) and records every
HTTP call, DB read and webhook delivery it made into a per-journey evidence JSON.

Result vocabulary
  PASS              every check passed
  FAIL              at least one check failed (a real twin/product defect, or a
                    driver bug -- never patched away to go green)
  EXPECTED_FAILURE  the observed ending state matches a TWIN_SPEC/expected-
                    failures.yaml entry or a cited source behaviour; carries
                    `expected_failure_ref`
  BLOCKED           a dependency the arena does not have yet; carries
                    `missing_dependency`

Fresh synthetic merchants only (ARENA-prefixed, via the audited provisioners).
Descriptors carry secrets and are written under RED_LOOP/runs/ (git-ignored).
Stdlib only, host python3.

NOT ALLOWED here (lane rules): container restart/stop/rebuild, docker-compose or
config edits. The provisioners' own monolith-stub/kong-lite reloads are part of
the sanctioned provisioning recipe and are the only container touches performed.
"""
import base64
import json
import os
import re
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "RED_LOOP"))
sys.path.insert(0, str(REPO / "RED_LOOP" / "surface"))

from red_loop import provisioner as P            # noqa: E402
from red_loop import provisioner_direct as D     # noqa: E402

ENV2 = REPO / "ENV2_COMPOSE"
RUNS = REPO / "RED_LOOP" / "runs"
IMPL = REPO / "reports" / "implementation"

# in-arena service URLs (reached through a throwaway curl container on the arena network)
PAYOUTS = "http://payouts-api:9400"
FTS = "http://fts-web:8080"
MOZART = "http://mozart-sim:8085"
MONOLITH = "http://monolith-stub:8080"
SINK = "http://merchant-webhook-sink:8080"
STORK = "http://stork-capture:8080"
LEDGER = "http://ledger-api:8080"
PRICING = "http://pricing-stub:8080"
WORKFLOW_SIM = "http://workflow-sim:8092"       # M2 thin stand-in, still running, no longer wired
WORKFLOW_ENGINE = "http://workflow-engine:8093"  # M6 default: the durable M5 maker/checker engine
BATCH_SIM = "http://batch-sim:8094"
LOCALSTACK = "http://localstack:4566"

# canonical family ids (acceptance gate vocabulary)
FAMILIES = {
    "shared-payouts": "family:shared-payouts",
    "direct-payouts": "family:direct-payouts",
    "queued-low-balance": "family:queued-low-balance",
    "scheduled-payouts": "family:scheduled-payouts",
    "failure-reversal-cancellation": "family:failure-reversal-cancellation",
    "pricing-free-payouts": "family:pricing-free-payouts",
    "webhooks": "family:webhooks",
    "approval-workflow": "family:approval-workflow",
    "bulk-payouts": "family:bulk-payouts",
    "idempotency-retries": "family:idempotency-retries",
    "accounting": "family:accounting",
    "on-hold": "family:on-hold",
    "fetch-list": "family:fetch-list",
    "source-updates": "family:source-updates",
    "async-workers": "family:async-workers",
    "beneficiary-fund-accounts": "family:beneficiary-fund-accounts",
    # M7
    "shared-ingress": "family:shared-ingress",
    "cross-domain-s2p": "family:cross-domain-s2p",
}
P0_FAMILIES = ("shared-payouts", "direct-payouts", "queued-low-balance", "scheduled-payouts",
               "failure-reversal-cancellation", "webhooks", "idempotency-retries", "accounting",
               "approval-workflow", "fetch-list", "source-updates", "async-workers",
               "beneficiary-fund-accounts", "shared-ingress", "cross-domain-s2p")
REQUIRED_P0_VARIANTS = ("success", "failure", "idempotency", "async_state")

PASS, FAIL, EXPECTED_FAILURE, BLOCKED = "PASS", "FAIL", "EXPECTED_FAILURE", "BLOCKED"


# ---------------------------------------------------------------------------
# small utilities
# ---------------------------------------------------------------------------
def ts():
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def save(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, indent=2, default=str))
    return str(path)


def git_head():
    try:
        return subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:  # noqa: BLE001
        return None


def fingerprint():
    p = ENV2 / ".runtime" / "arena-fingerprint.json"
    try:
        fp = json.loads(p.read_text())
        return {k: fp.get(k) for k in ("boot_id", "compose_project", "route_profile",
                                       "config_digest", "git_head", "generated_at")}
    except Exception:  # noqa: BLE001
        return None


def bridge(name):
    """Service Basic-Auth pair ("user:pass") from ENV2_COMPOSE/secrets/verifier-bridge/.
    These are the same credentials the arena's own verifier and cron-driver use."""
    return (ENV2 / "secrets" / "verifier-bridge" / name).read_text().strip()


def jload(text, default=None):
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return default


def bare(pid):
    return pid.split("_", 1)[1] if pid and "_" in pid else pid


def wait_until(fn, timeout=30, interval=1.0, desc=""):
    """Poll fn() until it returns something truthy; returns None on timeout."""
    end = time.time() + timeout
    val = None
    while time.time() < end:
        try:
            val = fn()
        except Exception:  # noqa: BLE001
            val = None
        if val:
            return val
        time.sleep(interval)
    return val or None


# ---------------------------------------------------------------------------
# arena access, every call recorded on the journey's evidence bundle
# ---------------------------------------------------------------------------
class Arena:
    """Every method appends a record to the owning evidence dict. Nothing here
    mutates the arena topology -- only merchant-scoped data and the substitutes'
    documented /_arena control planes."""

    def __init__(self, ev):
        self.ev = ev
        self._secrets = set()

    # -- recording -----------------------------------------------------------
    def hide(self, *values):
        """Register a credential so it can never reach an evidence bundle or a report."""
        for v in values:
            if isinstance(v, str) and len(v) >= 8:
                self._secrets.add(v)

    def _rec(self, bucket, entry):
        entry["at"] = now()
        if self._secrets:
            try:
                blob = json.dumps(entry, default=str)
                for sec in self._secrets:
                    blob = blob.replace(sec, "<redacted>")
                entry = json.loads(blob)
            except (TypeError, ValueError):     # keep the record rather than lose it
                entry = {"note": entry.get("note"), "redaction": "record dropped (unserialisable)"}
        self.ev.setdefault(bucket, []).append(entry)
        return entry

    # -- HTTP ----------------------------------------------------------------
    def http(self, method, url, body=None, headers=None, basic=None, timeout=25, note=None):
        st, txt = D.arena_http(method, url, body, headers, basic, timeout)
        self._rec("http", {"transport": "arena-network", "method": method, "url": url,
                           "request": body, "status": st, "response": (txt or "")[:1500], "note": note})
        return st, txt

    def jhttp(self, method, url, body=None, headers=None, basic=None, timeout=25, note=None):
        st, txt = self.http(method, url, body, headers, basic, timeout, note)
        return st, (jload(txt) if txt else None)

    def kong(self, method, path, auth, body=None, headers=None, note=None):
        st, txt = P._kong_call(method, path, auth, body, headers)
        safe_headers = {k: v for k, v in (headers or {}).items()}
        self._rec("http", {"transport": "kong-lite (merchant public API)", "method": method,
                           "url": P.KONG + path, "request": body, "headers": safe_headers,
                           "status": st, "response": (txt or "")[:1500], "note": note})
        return st, txt

    # -- databases -----------------------------------------------------------
    def mysql(self, service, db, pw_file, sql, note=None):
        rc, out, err = D._mysql(service, db, P._pw(pw_file), sql)
        self._rec("db", {"store": service, "db": db, "sql": sql, "rc": rc,
                         "rows": (out or "")[:2000], "err": (err or "")[:400], "note": note})
        return rc, out, err

    def payouts_sql(self, sql, note=None):
        return self.mysql("mysql-payouts", "payouts", "mysql_payouts_root_password.txt", sql, note)

    def fts_sql(self, sql, note=None):
        return self.mysql("mysql-fts", "fts", "mysql_fts_root_password.txt", sql, note)

    def psql(self, sql, note=None):
        rc, out, err = D._psql(P._pw("postgres_ledger_password.txt"), sql)
        self._rec("db", {"store": "postgres-ledger", "db": "ledger", "sql": sql, "rc": rc,
                         "rows": (out or "")[:4000], "err": (err or "")[:400], "note": note})
        return rc, out, err

    def mongo(self, js, note=None):
        """CFA's own store. Read-only from the journeys: the beneficiary documents are what the
        audited provisioner wrote, and this is how CFA itself sees them."""
        rc, out, err = D._mongo(js)
        self._rec("db", {"store": "mongo-cfa", "db": "cfa", "sql": js, "rc": rc,
                         "rows": (out or "")[:2000], "err": (err or "")[:300], "note": note})
        return rc, out, err

    def apidb(self, sql, note=None):
        """The API-monolith MySQL stub CFA dual-writes into."""
        rc, out, err = D._mysql("mysql-apidb-stub", "api_local",
                                P._pw("mysql_apidb_root_password.txt"), sql)
        self._rec("db", {"store": "mysql-apidb-stub", "db": "api_local", "sql": sql, "rc": rc,
                         "rows": (out or "")[:2000], "err": (err or "")[:300], "note": note})
        return rc, out, err

    def redis(self, *args, note=None):
        out = subprocess.run(["docker", "exec", P.cname("redis"), "redis-cli"] + [str(a) for a in args],
                             capture_output=True, text=True, timeout=30)
        self._rec("db", {"store": "redis", "cmd": [str(a) for a in args],
                         "rows": out.stdout.strip()[:1000], "note": note})
        return out.stdout.strip()

    # -- rows ----------------------------------------------------------------
    PAYOUT_COLS = ["id", "status", "merchant_id", "balance_id", "amount", "fees", "tax", "mode",
                   "utr", "fts_transfer_id", "transaction_id", "queued_reason", "scheduled_at",
                   "on_hold_at", "cancellation_user_id", "purpose", "created_at", "updated_at"]

    def payout(self, pid, note=None):
        cols = ",".join("`%s`" % c for c in self.PAYOUT_COLS)
        rc, out, _ = self.payouts_sql("SELECT %s FROM payouts WHERE id='%s'" % (cols, bare(pid)),
                                      note=note or "payout row")
        if rc != 0 or not out.strip():
            return None
        vals = out.strip("\n").split("\t")
        return dict(zip(self.PAYOUT_COLS, [None if v == "NULL" else v for v in vals]))

    def payout_logs(self, pid):
        rc, out, _ = self.payouts_sql(
            "SELECT event,`from`,`to`,triggered_by FROM payout_logs WHERE payout_id='%s' ORDER BY created_at,id"
            % bare(pid), note="payout_logs")
        return [dict(zip(("event", "from", "to", "triggered_by"), l.split("\t")))
                for l in out.strip().splitlines() if l.strip()]

    def reversal(self, pid):
        rc, out, _ = self.payouts_sql(
            "SELECT id,payout_id,amount,transaction_id,utr FROM reversals WHERE payout_id='%s'" % bare(pid),
            note="reversal row")
        if rc != 0 or not out.strip():
            return None
        return dict(zip(("id", "payout_id", "amount", "transaction_id", "utr"),
                        [None if v == "NULL" else v for v in out.strip().split("\t")]))

    def idempotency_row(self, key, mid):
        rc, out, _ = self.payouts_sql(
            "SELECT idempotency_key,merchant_id,source_id,source_type FROM idempotency_keys "
            "WHERE idempotency_key='%s' AND merchant_id='%s'" % (key, mid), note="idempotency_keys row")
        rows = [l.split("\t") for l in out.strip().splitlines() if l.strip()]
        return [dict(zip(("idempotency_key", "merchant_id", "source_id", "source_type"), r)) for r in rows]

    def bulk_idempotency_row(self, key, mid):
        """The bulk route does NOT write `idempotency_keys`; payouts' bulkPayoutsProcessor keeps
        its own `bulk_idempotency_keys` table (columns id, idempotency_key, merchant_id, batch_id,
        source_id, source_type)."""
        rc, out, _ = self.payouts_sql(
            "SELECT idempotency_key,merchant_id,batch_id,source_id,source_type FROM "
            "bulk_idempotency_keys WHERE idempotency_key='%s' AND merchant_id='%s'" % (key, mid),
            note="bulk_idempotency_keys row")
        rows = [l.split("\t") for l in (out or "").strip().splitlines() if l.strip()]
        return [dict(zip(("idempotency_key", "merchant_id", "batch_id", "source_id", "source_type"), r))
                for r in rows]

    def transfer(self, pid, note=None):
        t = D._fts_transfer(bare(pid))
        self._rec("db", {"store": "mysql-fts", "db": "fts",
                         "sql": "transfers JOIN source_accounts WHERE source_id=%s" % bare(pid),
                         "rows": t, "note": note or "fts transfer"})
        return t

    def journals(self, transactor_id, note=None):
        """Ledger journals for one transactor id, with entries and a balance check."""
        rc, out, _ = self.psql(
            "SELECT j.id,j.transactor_event,j.merchant_id,j.amount,"
            "coalesce(sum(CASE WHEN e.type='debit' THEN e.amount ELSE 0 END),0),"
            "coalesce(sum(CASE WHEN e.type='credit' THEN e.amount ELSE 0 END),0),count(e.id) "
            "FROM journal j LEFT JOIN ledger_entries e ON e.journal_id=j.id "
            "WHERE j.transactor_id='%s' GROUP BY j.id,j.transactor_event,j.merchant_id,j.amount "
            "ORDER BY j.created_at" % transactor_id, note=note or "ledger journals")
        rows = []
        for line in out.strip().splitlines():
            if not line.strip():
                continue
            f = line.split("|")
            if len(f) < 7:
                continue
            deb, cred = float(f[4] or 0), float(f[5] or 0)
            rows.append({"id": f[0], "transactor_event": f[1], "merchant_id": f[2], "amount": f[3],
                         "sum_debit": deb, "sum_credit": cred, "entries": int(f[6] or 0),
                         "balanced": abs(deb - cred) < 1e-9})
        return rows

    def journal_entries(self, journal_id):
        rc, out, _ = self.psql(
            "SELECT account_id,type,amount FROM ledger_entries WHERE journal_id='%s' ORDER BY id" % journal_id,
            note="ledger entries")
        return [dict(zip(("account_id", "type", "amount"), l.split("|")))
                for l in out.strip().splitlines() if l.strip()]

    def merchant_balance(self, mid):
        """MerchantBalance (payable + merchant_va) ledger account balance."""
        rc, out, _ = self.psql(
            "SELECT a.balance FROM accounts a JOIN account_details d ON d.account_id=a.id "
            "WHERE a.merchant_id='%s' AND d.entities @> '{\"account_type\": [\"payable\"], "
            "\"fund_account_type\": [\"merchant_va\"]}'::jsonb ORDER BY a.created_at LIMIT 1" % mid,
            note="merchant ledger balance")
        v = out.strip()
        return float(v) if v else None

    # -- webhooks ------------------------------------------------------------
    def deliveries(self, mid, pid=None):
        url = SINK + "/_arena/deliveries?merchant=" + mid + (("&payout_id=pout_" + bare(pid)) if pid else "")
        st, txt = self.http("GET", url, note="merchant webhook sink deliveries")
        rows = (jload(txt) or {}).get("deliveries", [])
        out = []
        for r in rows:
            b = r.get("body", {}) if isinstance(r.get("body"), dict) else {}
            ent = (((b.get("payload") or {}).get("payout") or {}).get("entity") or {})
            out.append({"event": b.get("event"), "merchant": r.get("merchant"),
                        "account_id": b.get("account_id"), "event_id": r.get("event_id"),
                        "signature_present": r.get("signature_present"),
                        "signature_valid": r.get("signature_valid"),
                        "payout_id": ent.get("id"), "payout_status": ent.get("status"),
                        "utr": ent.get("utr"), "fees": ent.get("fees"), "tax": ent.get("tax")})
        self._rec("webhooks", {"merchant_id": mid, "payout_id": pid, "deliveries": out})
        return out

    def stork_events(self, mid, pid=None):
        st, txt = self.http("GET", STORK + "/_arena/events?merchant=" + mid, note="stork-capture events")
        evs = (jload(txt) or {}).get("events", [])
        out = []
        for e in evs:
            ent = ((((e.get("payload") or {}).get("payload") or {}).get("payout") or {}).get("entity") or {})
            if pid and ent.get("id") != "pout_" + bare(pid):
                continue
            out.append({"event_name": e.get("event_name"), "event_id": e.get("event_id"),
                        "delivered_status": e.get("delivered_status"), "payout_id": ent.get("id"),
                        "payout_status": ent.get("status")})
        self._rec("webhooks", {"source": "stork-capture", "merchant_id": mid, "payout_id": pid, "events": out})
        return out

    # -- stimuli / control planes -------------------------------------------
    def mozart(self, key, scenario=None, polls=2, clear=False):
        body = {"key": str(key), "clear": True} if clear else {"key": str(key), "scenario": scenario, "polls": polls}
        return self.jhttp("POST", MOZART + "/_arena/scenario", body, note="mozart-sim bank scenario")

    def mozart_events(self):
        return self.jhttp("GET", MOZART + "/_arena/scenarios", note="mozart-sim call log")

    def fts_check(self, transfer_id):
        return self.http("POST", FTS + "/v1/transfer/%s/check" % transfer_id, {},
                         basic=D._fts_basic(), note="FTS bank status check (real polling action)")

    def finish_bank(self, pid, scenario="success", tries=20):
        """Real path: point the selected attempt at a bank scenario, then ask FTS
        to run its normal status-check action (verifier helpers.payouts_flow.finish_bank)."""
        t = wait_until(lambda: (lambda x: x if x and x.get("attempt_id") else None)(self.transfer(pid)),
                       timeout=tries * 2, interval=2, desc="fts attempt")
        if not t:
            return None, {"error": "no fts attempt"}
        self.mozart(t["attempt_id"], scenario)
        st, txt = self.fts_check(t["id"])
        return t, {"check_status": st, "check_body": (txt or "")[:400]}

    def cron(self, job_key, note=None):
        """Run one cron tick exactly the way the arena's cron-driver container does
        (ENV2_COMPOSE/scripts/cron-driver/driver.py --once <job>): same endpoint,
        same FastCron Basic credential, same DTO."""
        out = subprocess.run(["docker", "exec", P.cname("cron-driver"), "python3", "/app/driver.py",
                              "--once", job_key], capture_output=True, text=True, timeout=120)
        rec = self._rec("http", {"transport": "cron-driver container (--once %s)" % job_key,
                                 "method": "POST", "url": "payouts-api /v1/cron/* via driver.py",
                                 "status": "rc=%d" % out.returncode,
                                 "response": (out.stdout + out.stderr).strip()[-600:], "note": note})
        return out.returncode == 0, rec["response"]

    def cron_http(self, path, body=None, note=None):
        """Same wire call the cron-driver makes, but with an explicit DTO body
        (e.g. balance_ids scoping) the container's env does not carry."""
        return self.jhttp("POST", PAYOUTS + path, body if body is not None else {},
                          basic=bridge("ps-fastcron"),
                          note=note or "FastCron endpoint (cron-driver credential)")

    def ledger_topup(self, m, amount):
        bank_acc = "bacc_" + m.get("banking_account_id", "")
        body = {"merchant_id": m["merchant_id"], "currency": "INR",
                "transactor_id": "arenam6top_%s" % uuid.uuid4().hex[:12],
                "transactor_event": "positive_adjustment_processed",
                "transaction_date": int(time.time()),
                "identifiers": {"banking_account_id": bank_acc},
                "amount": str(int(amount)), "base_amount": str(int(amount)),
                "commission": "0", "tax": "0",
                "money_params": {"amount": str(int(amount)), "base_amount": str(int(amount))},
                "notes": {"balance_id": m["balance_id"], "arena": "m6 journey top-up"},
                "additional_params": {}}
        return self.jhttp("POST", LEDGER + "/twirp/rzp.ledger.journal.v1.JournalAPI/Create", body,
                          headers={"Ledger-Tenant": "X"}, basic=bridge("ledger"),
                          note="real ledger journal top-up (positive_adjustment_processed)")

    def balance_sync(self, balance_ids, deliver_event=False):
        return self.jhttp("POST", MONOLITH + "/_arena/balance-sync",
                          {"balance_ids": list(balance_ids), "deliver_event": bool(deliver_event)},
                          basic=D._mono_basic(), note="monolith balance sync")

    def merchant_features(self, mid, features):
        return self.jhttp("POST", MONOLITH + "/_arena/merchant_features",
                          {"merchant_id": mid, "features": features}, basic=D._mono_basic(),
                          note="monolith-stub merchant feature override")

    def monolith_log(self, needle):
        st, txt = self.http("GET", MONOLITH + "/_arena/log", basic=D._mono_basic(), note="monolith-stub log")
        doc = jload(txt) or {}
        entries = doc if isinstance(doc, list) else (doc.get("log") or doc.get("events") or [])
        return [e for e in entries if needle in json.dumps(e, default=str)]

    def reservations(self, mid, bal):
        snap = D._reservations(mid, bal)
        self._rec("db", {"store": "payouts-api /v1/inflight_reservations", "rows": snap,
                         "note": "in-flight reservation snapshot"})
        items = (snap["body"] or {}).get("items", []) if isinstance(snap.get("body"), dict) else []
        return snap, items

    # -- workflow engine (M6 default; ENV2_COMPOSE/substitutes/workflow-engine/ARENA.md) ------
    def wfe_admin_token(self):
        """Admin-plane token, read host-side like every other arena secret. Registered as a
        secret first, so it can never appear in an evidence bundle or a report."""
        tok = (ENV2 / "secrets" / "wfe_admin_token.txt").read_text().strip()
        self.hide(tok)
        return tok

    def wfe(self, method, path, body=None, token=None, admin=False, note=None):
        """One call on the workflow engine. `admin` uses the admin token, `token` an actor
        bearer; both are redacted from the record."""
        headers = {}
        if admin:
            headers["Authorization"] = "Bearer " + self.wfe_admin_token()
        elif token:
            self.hide(token)
            headers["Authorization"] = "Bearer " + token
        st, txt = D.arena_http(method, WORKFLOW_ENGINE + path, body, headers, None, 30)
        self._rec("http", {"transport": "arena-network", "method": method,
                           "url": WORKFLOW_ENGINE + path, "request": body, "status": st,
                           "response": (txt or "")[:1500],
                           "auth_plane": "admin" if admin else ("actor" if token else "none"),
                           "note": note or ("workflow-engine " + path)})
        return st, (jload(txt) if txt else None)

    def wfe_health(self):
        out = {}
        for path in ("/health", "/_arena/health"):
            st, body = self.wfe("GET", path, note="workflow-engine liveness " + path)
            out[path] = {"status": st, "body": body}
        return out

    def wfe_pending(self):
        st, body = self.wfe("GET", "/_arena/pending", note="workflow-engine pending workflows")
        return (body or {}).get("pending", [])

    def wfe_workflows(self):
        st, body = self.wfe("GET", "/_arena/workflows", note="workflow-engine evidence plane")
        return (body or {}).get("workflows", [])

    def wfe_for_payout(self, pid):
        want = "pout_" + bare(pid)
        for w in self.wfe_workflows():
            if w.get("payout_id") in (want, bare(pid)):
                return w
        return None

    def wfe_decide(self, pid, decision, queue_if_low_balance=None):
        body = {"payout_id": "pout_" + bare(pid), "decision": decision}
        if queue_if_low_balance is not None:
            body["queue_if_low_balance"] = bool(queue_if_low_balance)
        return self.wfe("POST", "/_arena/decide", body,
                        note="operator control plane: fires the REAL payouts %s callback" % decision)

    def wfe_create_actor(self, org_id, name, roles):
        """Admin plane. The bearer token is returned exactly once; it is registered as a
        secret before the call is recorded, so only its shape is ever written down."""
        tok = self.wfe_admin_token()
        body = {"org_id": org_id, "name": name, "roles": list(roles)}
        st, txt = D.arena_http("POST", WORKFLOW_ENGINE + "/admin/actors", body,
                               {"Authorization": "Bearer " + tok}, None, 30)
        doc = jload(txt) or {}
        if doc.get("token"):
            self.hide(doc["token"])
        self._rec("http", {"transport": "arena-network", "method": "POST",
                           "url": WORKFLOW_ENGINE + "/admin/actors", "request": body, "status": st,
                           "response": (txt or "")[:600], "auth_plane": "admin",
                           "note": "workflow-engine admin: provision an approver identity "
                                   "(actor token redacted)"})
        return st, doc

    def wfe_set_policy(self, org_id, required_approvals=1, separation=1,
                       eligible_approvers=None, expiry_seconds=86400):
        return self.wfe("POST", "/admin/policies",
                        {"org_id": org_id, "entity_type": "payout",
                         "required_approvals": int(required_approvals),
                         "separation": int(separation),
                         "eligible_approvers": list(eligible_approvers or []),
                         "expiry_seconds": int(expiry_seconds)},
                        admin=True, note="workflow-engine admin: approval policy")

    def wfe_actor(self, token, action, wf_id, version=None):
        body = {} if version is None else {"version": version}
        return self.wfe("POST", "/v1/workflows/%s/%s" % (wf_id, action), body, token=token,
                        note="actor plane: %s by a real approver identity" % action)

    def wfe_audit(self, token, wf_id):
        st, body = self.wfe("GET", "/v1/workflows/%s/audit" % wf_id, token=token,
                            note="actor plane: append-only audit trail")
        return (body or {}).get("events", [])

    def ps_workflow_map(self, pid):
        """The PS-side workflow_entity_map row -- proof payouts itself stored the engine's id."""
        rc, out, _ = self.payouts_sql(
            "SELECT workflow_id,config_id,entity_id,entity_type FROM workflow_entity_map "
            "WHERE entity_id='%s'" % bare(pid), note="payouts workflow_entity_map row")
        rows = [dict(zip(("workflow_id", "config_id", "entity_id", "entity_type"), l.split("\t")))
                for l in (out or "").strip().splitlines() if l.strip()]
        return rows[0] if rows else None

    # -- making a fresh merchant workflow-applicable -------------------------
    # ARENA.md section 2a documents the DCS `Workflows` object as the PS-side switch. In THIS
    # arena that plane is inert against the pristine payouts binary: payouts/pkg/dcs/client.go
    # never sets ServerURL and goutils/dcs@v1.7.3 resolves the login host from a hardcoded
    # env->hostname map whenever exactly one Mode is configured, so `[dcs] Env` is deliberately
    # set to an unrecognised value and dcs.New() fails before any network call
    # (ENV2_COMPOSE/substitutes/dcs-stub/CONTRACT.md "Known, load-bearing gap").
    # The name payouts actually resolves is therefore the appConstants one, `payout_workflows`,
    # off the monolith merchant-config feature list -- which is exactly how the M2 surface
    # acceptance activated the workflow slice for the M3 fixture
    # (seeds/monolith/merchants.json ARENAM00000003.merchant.feature carries `payout_workflows`).
    # Both planes are written here: the DCS object through the real KV surface (so a patched
    # binary or the verifier sees the documented config), and the monolith feature (the plane
    # this arena's binary reads). The payouts merchant-config cache key is dropped afterwards.
    MERCHANT_CONFIG_CACHE_KEY = "{payouts_merchant_config_key}_%s"

    def dcs_workflows(self, mid, enable=True, skip_api=False):
        """Register the DCS `rzp/x/merchant/payouts/Workflows` object for a merchant through
        the real KV surface (POST /v1/kv/put; proto3 wire bytes, zero values omitted)."""
        fields = []                                     # (field_number, bool)
        if skip_api:
            fields.append((4, True))                    # skip_approval_workflow_for_api
        if enable:
            fields.append((5, True))                    # enable_payout_workflow
        raw = b"".join(bytes([(n << 3) | 0, 1]) for n, _v in fields)
        body = {"key": {"namespace": "config", "entity": "merchant", "entity_id": mid,
                        "domain": "payouts", "object_name": "Workflows"},
                "value": base64.b64encode(raw).decode()}
        return self.jhttp("POST", "http://dcs-stub:8080/v1/kv/put", body,
                          note="DCS Workflows object (ARENA.md 2a; inert against the pristine "
                               "payouts binary -- recorded for completeness)")

    def set_merchant_features(self, mid, features):
        """POST /_arena/merchant_features AND drop payouts' cached MerchantConfig.

        payouts caches the whole merchant-config document in Redis under
        `{payouts_merchant_config_key}_<merchant_id>` and (per
        reports/findings/12 A.6) `UpdateMerchantFeatureInCache` is a no-op, so a feature
        flipped after payouts has once read that merchant stays invisible until the entry
        expires. That is the mechanism behind the recurring production
        `payout_workflows`-ignored incident class (reports/findings/11), and it is faithfully
        reproduced here -- so a driver that flips a feature MUST drop the key or it is
        testing a stale document."""
        st, body = self.merchant_features(mid, features)
        key = self.MERCHANT_CONFIG_CACHE_KEY % mid
        cached_before = self.redis("GET", key, note="payouts cached MerchantConfig before the flip")
        deleted = self.redis("DEL", key, note="invalidate payouts' cached MerchantConfig")
        rec = {"status": st, "features": (body or {}).get("features"),
               "overrides": (body or {}).get("overrides"),
               "merchant_config_cache_key": key,
               "cache_entry_existed": bool(cached_before),
               "cache_keys_deleted": deleted}
        self.ev.setdefault("merchant_feature_flips", []).append(rec)
        return st, body, rec

    def make_workflow_applicable(self, mid, on=True):
        out = {"merchant_id": mid, "enable": bool(on)}
        st, body, flip = self.set_merchant_features(mid, {"payout_workflows": bool(on)})
        out["monolith_feature_override"] = flip
        out["dcs_kv_put"] = self.dcs_workflows(mid, enable=bool(on), skip_api=False)
        st2, cfg = self.jhttp("GET", MONOLITH + "/internal/merchants/" + mid, basic=D._mono_basic(),
                              note="merchant-config route payouts reads for IsFeatureEnabled")
        out["feature_list_returned_to_payouts"] = ((cfg or {}).get("merchant") or {}).get("feature")
        return out

    # -- FTS retry scheduling (machinery on the arena redis) -----------------
    MACHINERY_DELAYED_ZSET = "delayed_tasks"

    def machinery_delayed(self, name=None, contains=None):
        """The machinery delayed-task ZSET FTS schedules retries on. REDIS_QUEUE_HOST now points
        at the arena redis, so a scheduled retry is visible here the moment FTS enqueues it."""
        out = subprocess.run(["docker", "exec", P.cname("redis"), "redis-cli", "--no-raw",
                              "ZRANGE", self.MACHINERY_DELAYED_ZSET, "0", "-1", "WITHSCORES"],
                             capture_output=True, text=True, timeout=45)
        raw = subprocess.run(["docker", "exec", P.cname("redis"), "redis-cli",
                              "ZRANGE", self.MACHINERY_DELAYED_ZSET, "0", "-1"],
                             capture_output=True, text=True, timeout=45).stdout
        items = []
        for line in raw.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            doc = jload(line)
            if not doc:
                continue
            if name and doc.get("Name") != name:
                continue
            if contains is not None and contains not in line:
                continue
            items.append({"member": line, "name": doc.get("Name"),
                          "routing_key": doc.get("RoutingKey"), "eta": doc.get("ETA"),
                          "args": doc.get("Args")})
        self._rec("db", {"store": "redis", "cmd": ["ZRANGE", self.MACHINERY_DELAYED_ZSET],
                         "rows": [{k: v for k, v in i.items() if k != "member"} for i in items],
                         "note": "machinery delayed tasks (FTS retry scheduling)"})
        return items

    # machinery re-delays a task it pops whose embedded ETA is still in the future, so bringing a
    # scheduled retry forward means rewriting BOTH the ZSET score AND the message's own ETA field.
    _ADVANCE_LUA = """
local key = KEYS[1]
local needle = ARGV[1]
local now_ns = tonumber(ARGV[2])
local eta = ARGV[3]
local n = 0
for _, m in ipairs(redis.call("ZRANGE", key, 0, -1)) do
  if string.find(m, needle, 1, true) then
    local nm = string.gsub(m, '"ETA":"[^"]*"', '"ETA":"' .. eta .. '"')
    redis.call("ZREM", key, m)
    redis.call("ZADD", key, now_ns, nm)
    n = n + 1
  end
end
return n
"""

    def machinery_advance(self, needle, note=None):
        """Bring every already-scheduled machinery task whose message contains `needle` forward to
        now. The task, its payload, its routing key and the worker that executes it are all the
        real ones FTS created -- only the wall-clock wait is removed, so a ~30-minute retry window
        does not have to be slept through. Nothing else in the ZSET is touched."""
        subprocess.run(["docker", "exec", "-i", P.cname("redis"), "sh", "-c",
                        "cat > /tmp/m6-advance.lua"], input=self._ADVANCE_LUA,
                       capture_output=True, text=True, timeout=45)
        now_ns = str(int(time.time() * 1e9))
        eta = time.strftime("%Y-%m-%dT%H:%M:%S.000000000Z", time.gmtime(time.time() - 60))
        out = subprocess.run(["docker", "exec", P.cname("redis"), "redis-cli", "--eval",
                              "/tmp/m6-advance.lua", self.MACHINERY_DELAYED_ZSET, ",",
                              str(needle), now_ns, eta],
                             capture_output=True, text=True, timeout=45)
        moved = (out.stdout or "").strip()
        self._rec("db", {"store": "redis", "cmd": ["EVAL advance-delayed-task",
                                                   self.MACHINERY_DELAYED_ZSET, str(needle)],
                         "rows": {"tasks_advanced": moved, "new_eta": eta},
                         "note": note or "advance one already-scheduled FTS retry task to now"})
        return moved

    # -- localstack / SQS ----------------------------------------------------
    def awslocal(self, *args, note=None, timeout=60):
        cmd = ["docker", "exec", P.cname("localstack"), "awslocal"] + [str(a) for a in args] \
              + ["--region", "ap-south-1"]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        doc = jload(out.stdout)
        self._rec("db", {"store": "localstack", "cmd": " ".join(str(a) for a in args),
                         "rc": out.returncode,
                         "rows": doc if doc is not None else (out.stdout or out.stderr)[:1500],
                         "note": note or "awslocal"})
        return out.returncode, doc, (out.stdout + out.stderr)

    def queue_url(self, name):
        rc, doc, _ = self.awslocal("sqs", "get-queue-url", "--queue-name", name,
                                   note="SQS queue url %s" % name)
        return (doc or {}).get("QueueUrl") if rc == 0 else None

    def queue_attrs(self, url, names="All"):
        rc, doc, _ = self.awslocal("sqs", "get-queue-attributes", "--queue-url", url,
                                   "--attribute-names", names, note="SQS attributes")
        return (doc or {}).get("Attributes") or {}

    def payouts_job_queues(self):
        """The live [job] key -> SQS queue-name map, read out of the running payouts-api's own
        rendered config (not a copy kept here)."""
        out = subprocess.run(["docker", "exec", P.cname("payouts-api"), "sh", "-c",
                              "sed -n '/^\\[job\\]/,/^\\[queue\\]/p' /app/config/arena.toml"],
                             capture_output=True, text=True, timeout=30)
        jobs = {}
        for line in out.stdout.splitlines():
            line = line.split("#", 1)[0].strip()
            if "=" not in line or line.startswith("["):
                continue
            k, _, v = line.partition("=")
            v = v.strip().strip('"')
            if k.strip() and v:
                jobs[k.strip()] = v
        self._rec("db", {"store": "payouts-api /app/config/arena.toml [job]", "rows": jobs,
                         "note": "job key -> SQS queue name, read from the running container"})
        return jobs

    # -- containers ----------------------------------------------------------
    # The ONLY containers this lane may restart, and only inside
    # journey:shared-payouts/restart with M6_ALLOW_RESTART=1.
    RESTARTABLE = ("api-ingress",   # M7: the shared ingress (state on the ingress-data volume)
                   "payouts-worker-fts-async-processing", "payouts-worker-webhook-event",
                   "fts-worker-fire-transfer-status-webhook")

    def container_state(self, service):
        out = subprocess.run(["docker", "inspect", "-f",
                              "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}"
                              "{{else}}none{{end}}|{{.State.StartedAt}}", P.cname(service)],
                             capture_output=True, text=True, timeout=30)
        parts = (out.stdout.strip() or "||").split("|")
        return {"service": service, "status": parts[0],
                "health": parts[1] if len(parts) > 1 else None,
                "started_at": parts[2] if len(parts) > 2 else None}

    def restart_workers(self, services, wait=180):
        """docker restart, restricted to RESTARTABLE. Records before/after timestamps."""
        bad = [s for s in services if s not in self.RESTARTABLE]
        if bad:
            raise RuntimeError("refusing to restart %r -- outside this lane's allow-list %r"
                               % (bad, list(self.RESTARTABLE)))
        rec = {"allow_list": list(self.RESTARTABLE), "restarts": []}
        for svc in services:
            before = self.container_state(svc)
            r = subprocess.run(["docker", "restart", P.cname(svc)],
                               capture_output=True, text=True, timeout=180)
            rec["restarts"].append({"service": svc, "container": P.cname(svc),
                                    "started_at_before": before["started_at"],
                                    "restart_rc": r.returncode,
                                    "restart_issued_at": now(),
                                    "stderr": (r.stderr or "").strip()[:200]})
        end = time.time() + wait
        while time.time() < end:
            states = [self.container_state(s) for s in services]
            if all(s["status"] == "running" and s["health"] in ("healthy", "none") for s in states):
                break
            time.sleep(3)
        for item, svc in zip(rec["restarts"], services):
            after = self.container_state(svc)
            item["started_at_after"] = after["started_at"]
            item["status_after"] = after["status"]
            item["health_after"] = after["health"]
            item["actually_restarted"] = item["started_at_before"] != after["started_at"]
        self._rec("db", {"store": "docker", "sql": "restart %s" % ", ".join(services),
                         "rows": rec, "note": "controlled worker restart (restart-recovery journey)"})
        return rec

    # the 25 payouts workers, the 2 kafka consumers and the ledger workers actually running in M6
    PAYOUTS_WORKERS = (
        "payouts-worker-webhook-event", "payouts-worker-queued-payout",
        "payouts-worker-schedule-payout", "payouts-worker-on-hold-payout",
        "payouts-worker-fts-async-processing", "payouts-worker-fts-async-hv-processing",
        "payouts-worker-payout-create-failure-handling",
        "payouts-worker-payout-update-failure-handling", "payouts-worker-transaction-create",
        "payouts-worker-generic-processing", "payouts-worker-async-dual-write",
        "payouts-worker-x-balances-balance-refresh",
        "payouts-worker-rbl-banking-account-statement", "payouts-worker-payout-source-updater",
        "payouts-worker-partner-bank-hold-payouts", "payouts-worker-bulk-payouts",
        "payouts-worker-batch-submitted-merchants", "payouts-worker-data-consistency-checker",
        "payouts-worker-data-consistency-event", "payouts-worker-fund-management-payout-check",
        "payouts-worker-fund-management-payout-initiate",
        "payouts-worker-payout-usage-event-processing",
        "payouts-worker-x-account-statement-source-event",
        "payouts-worker-x-balance-payouts-event",
        "payouts-worker-api-queue-for-async-dual-write-direct-push")
    KAFKA_CONSUMERS = ("payouts-kafka-fts-status-updates-consumer",
                       "payouts-kafka-fts-status-updates-retry-consumer")
    # PAYOUTS_WORKER_NAME -> the [job] key that names its queue, where the two differ
    WORKER_JOB_ALIAS = {"payout_source_updater": "source_updater",
                        "partner_bank_hold_payouts": "partner_bank_hold_payout",
                        "x_balance_payouts_event": "x_balances_payouts_event"}
    # the 10 workers M6 added (M6_RUNTIME_CHANGES.md section 8 step 2)
    NEW_M6_WORKERS = (
        "payouts-worker-bulk-payouts", "payouts-worker-batch-submitted-merchants",
        "payouts-worker-data-consistency-checker", "payouts-worker-data-consistency-event",
        "payouts-worker-fund-management-payout-check",
        "payouts-worker-fund-management-payout-initiate",
        "payouts-worker-payout-usage-event-processing",
        "payouts-worker-x-account-statement-source-event",
        "payouts-worker-x-balance-payouts-event",
        "payouts-worker-api-queue-for-async-dual-write-direct-push")

    WORKER_SERVICES = PAYOUTS_WORKERS + KAFKA_CONSUMERS + (
        "ledger-worker", "ledger-worker-journal-create", "ledger-worker-balance-update")

    def worker_env(self, service, name):
        out = subprocess.run(["docker", "exec", P.cname(service), "sh", "-c", "printenv " + name],
                             capture_output=True, text=True, timeout=30)
        return out.stdout.strip() or None

    def worker_log_hits(self, needle, services=None, since="15m"):
        """Which REAL payouts/ledger worker containers logged this identifier.
        Evidence that an `async_state` variant completed through a worker, not inline."""
        hits = {}
        for svc in (services or self.WORKER_SERVICES):
            try:
                out = subprocess.run(["docker", "logs", "--since", since,
                                      "%s-%s-1" % (P.compose_project(), svc)],
                                     capture_output=True, text=True, timeout=45)
                n = sum(1 for l in (out.stdout + out.stderr).splitlines() if needle in l)
                if n:
                    hits[svc] = n
            except Exception:  # noqa: BLE001
                continue
        self._rec("db", {"store": "docker logs", "sql": "grep %r in worker containers" % needle,
                         "rows": hits, "note": "async worker attribution"})
        return hits

    def cron_driver_hits(self, path_fragment, since="15m"):
        out = subprocess.run(["docker", "logs", "--since", since, P.cname("cron-driver")],
                             capture_output=True, text=True, timeout=45)
        n = sum(1 for l in (out.stdout + out.stderr).splitlines() if path_fragment in l)
        self._rec("db", {"store": "docker logs", "sql": "cron-driver ticks for %s" % path_fragment,
                         "rows": n, "note": "real external scheduler evidence"})
        return n

    def ps_healthy(self, tries=30, delay=2):
        for _ in range(tries):
            out = subprocess.run(["docker", "inspect", "-f",
                                  "{{.State.Status}} {{.State.Health.Status}}", P.cname("payouts-api")],
                                 capture_output=True, text=True).stdout.strip()
            if out.startswith("running") and "healthy" in out:
                return True
            time.sleep(delay)
        return False


# ---------------------------------------------------------------------------
# merchants: fresh, synthetic, cached per profile per run
# ---------------------------------------------------------------------------
class MerchantPool:
    """One fresh merchant per profile per run (a family declares its profile).

    `shared`  -> provisioner.provision_funded_merchant  (Shared/pool, ledger-backed)
    `direct`  -> provisioner_direct.provision_direct_merchant (current account, RBL)

    Provisioning is the audited recipe; it reloads monolith-stub and kong-lite
    (their documented seed-reload path) and touches nothing else."""

    def __init__(self, run_dir, campaign_prefix):
        self.run_dir = Path(run_dir)
        self.prefix = campaign_prefix
        self.cache = {}
        self.setup_log = {}
        self.collisions = []

    def get(self, profile):
        if profile in self.cache:
            return self.cache[profile]
        if profile.startswith("direct"):
            d = self._direct(profile)
        else:
            d = self._shared(profile)
        self.cache[profile] = d
        return d

    # ---- ledger account-id namespace guard ---------------------------------
    # The collision this loop was written for is FIXED (M6 lane I5): provisioner._ledger_acc_prefix
    # is now injective in the merchant number over the Shared range and tests/test_ledger_ids.py
    # proves it over 10,000 campaigns. The check and the re-mint loop are kept as a cheap guard,
    # because the failure mode was silent -- provision_funded_merchant inserts accounts /
    # account_details with `ON CONFLICT (id) DO NOTHING` and still records the step ok, so a
    # merchant with zero ledger accounts would otherwise carry `verified: true` and every
    # balance-dependent journey would fail for an unexplained reason. A collision recorded here now
    # means the invariant regressed, and it is reported as a finding rather than absorbed.
    def _ledger_state(self, d):
        pw = P._pw("postgres_ledger_password.txt")
        mid = d["merchant_id"]
        prefix = d["ids"]["acc_prefix"]
        rc, own, _ = D._psql(pw, "SELECT count(*) FROM accounts WHERE merchant_id='%s'" % mid)
        rc2, owners, _ = D._psql(
            pw, "SELECT DISTINCT merchant_id FROM accounts WHERE id LIKE '%sAC%%'" % prefix)
        return int((own or "0").strip() or 0), [o.strip() for o in owners.strip().splitlines() if o.strip()]

    def _shared(self, profile, attempts=4):
        collisions = []
        d = None
        for attempt in range(attempts):
            role = profile if attempt == 0 else "%s-r%d" % (profile, attempt)
            campaign = "%s-%s" % (self.prefix, profile)
            d = P.provision_funded_merchant(campaign, role=role, opening=10_000_000)
            d["profile"] = profile
            d["role"] = role
            d["campaign_id"] = campaign
            d["archetype"] = "shared"
            d["ids"] = P._ids_for(campaign, role)
            d["banking_account_id"] = d["ids"]["banking_account"]
            own, owners = self._ledger_state(d)
            d["ledger_accounts_owned"] = own
            if own >= 4:
                break
            collisions.append({
                "attempt": attempt, "role": role, "merchant_id": d["merchant_id"],
                "ledger_acc_prefix": d["ids"]["acc_prefix"], "accounts_owned": own,
                "prefix_owned_by": owners,
                "defect": "REGRESSION: the fresh merchant owns fewer than four ledger accounts. "
                          "provisioner._ledger_acc_prefix is supposed to be injective over the "
                          "Shared merchant-number range (M6 lane I5 fix, tests/test_ledger_ids.py); "
                          "provision_funded_merchant inserts accounts / account_details with "
                          "`ON CONFLICT (id) DO NOTHING` and records the step ok regardless, so a "
                          "prefix collision leaves the merchant with no ledger accounts while the "
                          "descriptor still says verified: true"})
            print("  [pool] ledger-account-id collision on %s (prefix %s owned by %s) -- re-minting"
                  % (d["merchant_id"], d["ids"]["acc_prefix"], owners), flush=True)
        # The Shared recipe does not register a merchant webhook subscription (only the
        # Direct one does), so a fresh Shared merchant would silently receive no webhooks.
        # Reuse the audited Direct helpers: seed file (survives the controlled reboot) +
        # a live stork WebhookAPI/Create (no restart needed).
        seed_existed, seed_err = D._register_stork_seed(d["merchant_id"])
        live_existed, live_err = D.register_stork_live(d["merchant_id"])
        d["stork_subscription"] = {"seed_existed": seed_existed, "seed_err": seed_err,
                                   "live_existed": live_existed, "live_err": live_err,
                                   "sink": "http://merchant-webhook-sink:8080/webhook/" + d["merchant_id"]}
        d["ledger_collisions"] = collisions
        save(self.run_dir / ("descriptor-%s.json" % d["merchant_id"]), d)
        self.setup_log[profile] = {
            "stork_subscription": d["stork_subscription"],
            "profile": profile, "archetype": "shared", "merchant_id": d["merchant_id"],
            "campaign_id": d["campaign_id"], "role": d["role"], "verified": d.get("verified"),
            "verify_status": d.get("verify_status"),
            "ledger_accounts_owned": d.get("ledger_accounts_owned"),
            "ledger_account_id_collisions": collisions,
            "failed_steps": [s["step"] for s in d.get("steps", []) if not s.get("ok")],
            "descriptor": "RED_LOOP/runs/.../descriptor-%s.json (git-ignored, carries the key secret)"
                          % d["merchant_id"]}
        save(self.run_dir / "merchants.json", self.setup_log)
        if collisions:
            self.collisions.extend(collisions)
        return d

    def _direct(self, profile):
        import m4_direct_journeys as M4          # noqa: N813  (lane reuse, not duplication)
        campaign = "%s-%s" % (self.prefix, profile)
        d, setup = M4.setup_merchant(campaign, self.run_dir, profile)
        d["profile"] = profile
        d["archetype"] = "direct"
        d["campaign_id"] = campaign
        self.setup_log[profile] = {"profile": profile, "archetype": "direct",
                                   "merchant_id": d["merchant_id"], "campaign_id": campaign,
                                   "self_check": setup.get("self_check")}
        save(self.run_dir / "merchants.json", self.setup_log)
        return d


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------
REGISTRY = []


def journey(family, variant, priority="P0", profile="shared", fidelity="real",
            title=None, source_ref=None, order=None):
    """Register one journey driver. `id` is journey:<family-slug>/<variant>."""
    def deco(fn):
        if family not in FAMILIES:
            raise ValueError("unknown family %r" % family)
        spec = {"id": "journey:%s/%s" % (family, variant), "family": FAMILIES[family],
                "family_slug": family, "variant": variant, "priority": priority,
                "profile": profile, "fidelity": fidelity,
                "title": title or (fn.__doc__ or "").strip().splitlines()[0] if fn.__doc__ else variant,
                "source_ref": source_ref, "order": order, "fn": fn}
        REGISTRY.append(spec)
        return fn
    return deco


def load_drivers():
    """Import every driver module so its @journey registrations land."""
    import importlib
    here = Path(__file__).resolve().parent
    mods = sorted(p.stem for p in here.glob("j_*.py"))
    for m in mods:
        importlib.import_module(m)
    return mods


# ---------------------------------------------------------------------------
# journey context
# ---------------------------------------------------------------------------
class Blocked(Exception):
    def __init__(self, missing_dependency, detail=None):
        super().__init__(missing_dependency)
        self.missing_dependency = missing_dependency
        self.detail = detail


class ExpectedFailure(Exception):
    def __init__(self, ref, detail=None):
        super().__init__(ref)
        self.ref = ref
        self.detail = detail


class Ctx:
    def __init__(self, spec, merchant, run_dir, shared_state, pool=None):
        self.spec = spec
        self.m = merchant
        self.pool = pool
        self.run_dir = Path(run_dir)
        self.state = shared_state          # cross-journey artefacts (payout ids, ...)
        self.ev = {"journey": spec["id"], "family": spec["family"], "variant": spec["variant"],
                   "title": spec.get("title"), "merchant_id": (merchant or {}).get("merchant_id"),
                   "merchant_archetype": (merchant or {}).get("archetype"),
                   "started_at": now(), "source_ref": spec.get("source_ref"),
                   "checks": [], "http": [], "db": [], "webhooks": [], "notes": []}
        self.a = Arena(self.ev)
        self.notes = []
        self.fidelity = spec.get("fidelity", "real")

    # -- assertions ----------------------------------------------------------
    def ck(self, name, ok, detail=None):
        self.ev["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
        return bool(ok)

    def note(self, text):
        self.notes.append(text)
        self.ev["notes"].append(text)

    def blocked(self, missing_dependency, detail=None):
        raise Blocked(missing_dependency, detail)

    def expected_failure(self, ref, detail=None):
        raise ExpectedFailure(ref, detail)

    # -- merchant API surface (kong-lite, exactly what a merchant can call) ---
    @property
    def auth(self):
        return P._auth(self.m["key_id"], self.m["secret"])

    def create(self, amount, note="", idem=None, mode="IMPS", purpose="payout",
               queue_if_low_balance=False, extra=None, notes=None):
        body = {"fund_account_id": self.m["fund_account_id"],
                "account_number": self.m["account_number"], "amount": int(amount),
                "currency": "INR", "mode": mode, "purpose": purpose,
                "queue_if_low_balance": bool(queue_if_low_balance),
                "merchant_id": self.m["merchant_id"],
                "narration": ("m6 " + (note or self.spec["variant"]))[:30],
                "notes": notes or {"m6": self.spec["id"][:40]}}
        if extra:
            body.update(extra)
        ik = idem or ("m6-" + uuid.uuid4().hex[:14])
        st, txt = self.a.kong("POST", "/v1/payouts", self.auth, body,
                              {"X-Payout-Idempotency": ik}, note="merchant create payout")
        r = jload(txt) or {}
        return {"status": st, "id": bare(r.get("id") or ""), "public_id": r.get("id"),
                "response_status": r.get("status"), "fees": r.get("fees"), "tax": r.get("tax"),
                "idempotency": ik, "body": r if st != 200 else None, "json": r,
                "raw": txt[:600], "request": body}

    def fetch(self, pid):
        st, txt = self.a.kong("GET", "/v1/payouts/pout_" + bare(pid), self.auth,
                              note="merchant fetch payout")
        return st, jload(txt) or txt

    def cancel(self, pid, remarks="m6 journey cancel"):
        st, txt = self.a.kong("POST", "/v1/payouts/cancel_payout/" + bare(pid), self.auth,
                              {"remarks": remarks}, note="merchant cancel payout")
        return st, jload(txt) or txt

    def free_payout_attrs(self):
        st, txt = self.a.kong("GET", "/v1/payouts/free_payout/" + self.m["balance_id"], self.auth,
                              note="free payout attributes")
        return st, jload(txt) or txt

    # -- waits ---------------------------------------------------------------
    def wait_status(self, pid, status, timeout=60, interval=2):
        row = wait_until(lambda: (lambda r: r if r and r.get("status") == status else None)(self.a.payout(pid)),
                         timeout=timeout, interval=interval, desc=status)
        return row or self.a.payout(pid)

    def wait_terminal(self, pid, timeout=90, interval=2,
                      terminal=("processed", "failed", "reversed", "cancelled", "rejected")):
        row = wait_until(lambda: (lambda r: r if r and r.get("status") in terminal else None)(self.a.payout(pid)),
                         timeout=timeout, interval=interval, desc="terminal")
        return row or self.a.payout(pid)

    def wait_delivery(self, pid, events, timeout=45, interval=2):
        mid = self.m["merchant_id"]
        got = wait_until(lambda: [d for d in self.a.deliveries(mid, pid)
                                  if d["event"] in events and d["payout_id"] == "pout_" + bare(pid)] or None,
                         timeout=timeout, interval=interval, desc="webhook")
        return got or []

    def wait_journal(self, transactor_id, event, timeout=70, interval=3):
        got = wait_until(lambda: [j for j in self.a.journals(transactor_id)
                                  if j["transactor_event"] == event] or None,
                         timeout=timeout, interval=interval, desc=event)
        return got or []


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------
class Runner:
    def __init__(self, run_dir, pool):
        self.run_dir = Path(run_dir)
        self.pool = pool
        self.results = []
        self.state = {}
        (self.run_dir / "evidence").mkdir(parents=True, exist_ok=True)

    def _slug(self, jid):
        return re.sub(r"[^A-Za-z0-9]+", "-", jid).strip("-")

    def run_one(self, spec):
        t0 = time.time()
        merchant = None
        ctx = None
        result, missing, ef_ref, err = None, None, None, None
        try:
            if spec.get("profile"):
                merchant = self.pool.get(spec["profile"])
                if merchant.get("verified") is False:
                    raise Blocked("fresh-merchant provisioning did not self-verify",
                                  {"merchant_id": merchant.get("merchant_id"),
                                   "verify_status": merchant.get("verify_status"),
                                   "verify_body": merchant.get("verify_body")})
        except Blocked as b:
            result, missing = BLOCKED, b.missing_dependency
        except Exception:  # noqa: BLE001
            result, err = FAIL, traceback.format_exc()

        ctx = Ctx(spec, merchant, self.run_dir, self.state, self.pool)
        if result is None:
            try:
                out = spec["fn"](ctx)
                if isinstance(out, str) and out in (PASS, FAIL, EXPECTED_FAILURE, BLOCKED):
                    result = out
                else:
                    checks = ctx.ev["checks"]
                    result = PASS if (checks and all(c["ok"] for c in checks)) else FAIL
                    if not checks:
                        result = FAIL
                        ctx.note("driver made no assertions")
            except Blocked as b:
                result, missing = BLOCKED, b.missing_dependency
                ctx.ev["blocked_detail"] = b.detail
            except ExpectedFailure as e:
                result, ef_ref = EXPECTED_FAILURE, e.ref
                ctx.ev["expected_failure_detail"] = e.detail
            except Exception:  # noqa: BLE001
                result, err = FAIL, traceback.format_exc()
                ctx.ev["exception"] = err

        ctx.ev["finished_at"] = now()
        ctx.ev["result"] = result
        if missing:
            ctx.ev["missing_dependency"] = missing
        if ef_ref:
            ctx.ev["expected_failure_ref"] = ef_ref
        ev_path = self.run_dir / "evidence" / (self._slug(spec["id"]) + ".json")
        save(ev_path, ctx.ev)

        failed = [c["name"] for c in ctx.ev["checks"] if not c["ok"]]
        rec = {"id": spec["id"], "family": spec["family"], "variant": spec["variant"],
               "priority": spec["priority"], "result": result, "fidelity": ctx.fidelity,
               "title": spec.get("title"),
               "evidence_path": str(ev_path.relative_to(REPO)),
               "merchant_id": (merchant or {}).get("merchant_id"),
               "duration_s": round(time.time() - t0, 1),
               "checks_passed": sum(1 for c in ctx.ev["checks"] if c["ok"]),
               "checks_total": len(ctx.ev["checks"]),
               "notes": "; ".join(ctx.notes)[:1200] or None,
               "failed_checks": failed or None}
        if missing:
            rec["missing_dependency"] = missing
        if ef_ref:
            rec["expected_failure_ref"] = ef_ref
        if err:
            rec["error"] = err.strip().splitlines()[-1][:300]
        self.results.append(rec)
        print("[%-52s] %-16s %2d/%-2d %5.1fs %s" % (
            spec["id"], result, rec["checks_passed"], rec["checks_total"], rec["duration_s"],
            (missing or ef_ref or "; ".join(failed[:2]))[:80] if (missing or ef_ref or failed) else ""),
            flush=True)
        save(self.run_dir / "results.json", self.results)
        return rec
