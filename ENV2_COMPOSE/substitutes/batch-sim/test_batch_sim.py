#!/usr/bin/env python3
"""Tests for batch-sim against a FAKE payouts-service implementing the REAL
PS DTOs.

    python3 -m unittest discover -s ENV2_COMPOSE/substitutes/batch-sim -v
    # or:  python3 ENV2_COMPOSE/substitutes/batch-sim/test_batch_sim.py

The fake PS is stdlib-only and lives in this file. It reproduces, from the
pinned payouts repo:

  POST /v1/payouts/bulk
    request  : JSON array of dtos.BulkPayoutCreateRequest
               (internal/app/dtos/bulkPayoutCreateRequest.go:7-48)
    auth     : Basic (cred.API) + X-Passport-JWT-V1 required
               (payout_internal_routes_with_passport.go:15-20,
                middleware/passport.go:37-78)
    x-batch-id mandatory -> 400 without it
               (helpers.go:61-74 via bulkPayoutsController.go:73-81)
    >15 rows -> 400 (bulkPayoutsProcessor/constants.go:6, service.go:48-62)
    response : {"entity":"collection","count":N,"items":[...]}
               successes FIRST then errors (service.go:137-146)
               success item  = payoutApiResponse.go:12-37 (carries idempotency_key)
               error item    = bulkPayoutErrorResponse.go:9-19
    duplicate idempotency_key -> the EXISTING payout is returned as a success,
               no new payout (core.go:352-390)

  POST /v1/payouts/payouts_internal/{id}/{approve,reject}
    auth     : Basic (cred.Workflow)  (payout_internal_routes.go:16,:71,:77)
    200 on a pending payout; 409 once it has already transitioned
               (payoutController.go:1058-1066)

batch-sim itself is started as a real subprocess on a free port with a
temporary BATCH_SIM_DATA_DIR, so the durability and restart paths are exercised
for real.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "server.py")

PS_BASIC_USER = "api"
PS_BASIC_PASS = "test-api-secret"
WF_BASIC_USER = "rzp_live"
WF_BASIC_PASS = "test-workflow-secret"


_PASSPORT_KEY_PATH = [None]


def passport_key():
    """A real RSA key so batch-sim mints a real RS256 X-Passport-JWT-V1 and the
    fake PS can assert the header is present (middleware/passport.go:37-78).
    Generated once per run with openssl; if openssl is unavailable the fake PS
    drops that assertion and the header test skips."""
    if _PASSPORT_KEY_PATH[0] is not None:
        return _PASSPORT_KEY_PATH[0] or None
    path = os.path.join(tempfile.gettempdir(), "batch-sim-test-passport.pem")
    if not os.path.exists(path):
        try:
            subprocess.run(["openssl", "genrsa", "-out", path, "2048"],
                           check=True, capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            _PASSPORT_KEY_PATH[0] = ""
            return None
    _PASSPORT_KEY_PATH[0] = path
    return path


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def http(method, url, body=None, headers=None, timeout=10):
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode()
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read() or b"{}"
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, {"raw": raw.decode("utf-8", "replace")}


# --------------------------------------------------------------------------
# FAKE payouts-service
# --------------------------------------------------------------------------
class FakePS(object):
    def __init__(self):
        self.lock = threading.RLock()
        self.payouts = {}            # payout_id -> entity
        self.by_ikey = {}            # (merchant, ikey) -> payout_id
        self.create_calls = []       # every accepted /v1/payouts/bulk call
        self.approve_calls = []      # every approve/reject call
        self.fail_refs = set()       # payout.reference_id values that must fail per-row
        self.require_passport = True
        self.next_status = []        # queue of whole-call status overrides
        self.seq = 0
        self.last_passport = None
        self.bulk_requests = 0   # every inbound /v1/payouts/bulk, accepted or injected
        self.server = None
        self.port = None

    # -- helpers -------------------------------------------------------
    def _new_payout(self, item, merchant, batch_id):
        self.seq += 1
        pid = "pout_ARENA%09d" % self.seq
        amount = item.get("payout", {}).get("amount") or "0"
        try:
            amount = int(str(amount).strip() or 0)
        except ValueError:
            amount = 0
        entity = {
            "id": pid, "entity": "payout",
            "fund_account_id": item.get("fund", {}).get("id", ""),
            "amount": amount, "currency": item.get("payout", {}).get("currency", "INR"),
            "notes": item.get("notes") or {}, "fees": 0, "tax": 0,
            "status": "pending", "reason": "", "description": "",
            "purpose": item.get("payout", {}).get("purpose", ""),
            "utr": None, "mode": item.get("payout", {}).get("mode", ""),
            "reference_id": item.get("payout", {}).get("reference_id") or None,
            "narration": item.get("payout", {}).get("narration") or None,
            "idempotency_key": item.get("idempotency_key"),
            "batch_id": batch_id, "failure_reason": None,
            "created_at": int(time.time()),
        }
        self.payouts[pid] = entity
        self.by_ikey[(merchant, item.get("idempotency_key"))] = pid
        return entity

    def url(self):
        return "http://127.0.0.1:%d" % self.port

    def start(self):
        ps = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _json(self, status, payload):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _basic_ok(self, user, password):
                import base64
                header = self.headers.get("Authorization", "")
                if not header.startswith("Basic "):
                    return False
                try:
                    decoded = base64.b64decode(header[6:]).decode()
                except Exception:  # noqa: BLE001
                    return False
                return decoded == "%s:%s" % (user, password)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0) or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw or b"{}")
                except ValueError:
                    return self._json(400, {"error": {"code": "BAD_REQUEST_ERROR",
                                                      "description": "invalid json"}})
                if self.path == "/v1/payouts/bulk":
                    return ps.handle_bulk(self, body)
                if self.path.startswith("/v1/payouts/payouts_internal/"):
                    return ps.handle_approve(self, body)
                self._json(404, {"error": {"code": "BAD_REQUEST_ERROR",
                                           "description": "no route %s" % self.path}})

        self.HandlerClass = Handler
        self.port = free_port()
        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()

    # -- route handlers ------------------------------------------------
    def handle_bulk(self, h, body):
        with self.lock:
            self.bulk_requests += 1
            if self.next_status:
                status = self.next_status.pop(0)
                if status is not None:
                    return h._json(status, {"error": {"code": "SERVER_ERROR",
                                                      "description": "injected %d" % status}})
            # cred.API (payout_internal_routes_with_passport.go:16)
            if not h._basic_ok(PS_BASIC_USER, PS_BASIC_PASS):
                return h._json(401, {"error": {"code": "BAD_REQUEST_ERROR",
                                               "description": "unauthorized"}})
            # passport mandatory (middleware/passport.go:37-78)
            jwt = h.headers.get("X-Passport-JWT-V1")
            if self.require_passport and not jwt:
                return h._json(400, {"error": {"code": "BAD_REQUEST_ERROR",
                                               "description": "jwt token not present"}})
            self.last_passport = jwt
            # x-batch-id mandatory (helpers.go:61-74)
            batch_id = h.headers.get("x-batch-id")
            if not batch_id:
                return h._json(400, {"error": {"code": "BAD_REQUEST_ERROR",
                                               "description": "batch id is invalid"}})
            merchant = h.headers.get("X-Entity-Id") or ""
            if not isinstance(body, list):
                return h._json(400, {"error": {"code": "BAD_REQUEST_ERROR",
                                               "description": "expected an array"}})
            if len(body) > 15:  # bulkPayoutsProcessor/constants.go:6
                return h._json(400, {"error": {
                    "code": "BAD_REQUEST_ERROR",
                    "description": "bulk payouts count %d exceeds 15" % len(body)}})

            self.create_calls.append({
                "batch_id": batch_id, "merchant": merchant,
                "creator_id": h.headers.get("x-creator-id"),
                "creator_type": h.headers.get("x-creator-type"),
                "idempotency_keys": [i.get("idempotency_key") for i in body],
                "items": body,
            })

            successes, errors = [], []
            for item in body:
                ikey = item.get("idempotency_key")
                existing = self.by_ikey.get((merchant, ikey))
                if existing:  # core.go:352-390 -> return the EXISTING payout
                    successes.append(self.payouts[existing])
                    continue
                if (item.get("payout") or {}).get("reference_id") in self.fail_refs:
                    errors.append({"idempotency_key": ikey, "batch_id": batch_id,
                                   "http_status_code": 400,
                                   "error": {"code": "BAD_REQUEST_ERROR",
                                             "description": "synthetic per-row failure"}})
                    continue
                successes.append(self._new_payout(item, merchant, batch_id))
            items = successes + errors  # service.go:137-146
            return h._json(200, {"entity": "collection", "count": len(items), "items": items})

    def handle_approve(self, h, body):
        with self.lock:
            if self.next_status:
                status = self.next_status.pop(0)
                if status is not None:
                    return h._json(status, {"error": {"code": "SERVER_ERROR",
                                                      "description": "injected %d" % status}})
            if not h._basic_ok(WF_BASIC_USER, WF_BASIC_PASS):  # cred.Workflow
                return h._json(401, {"error": {"code": "BAD_REQUEST_ERROR",
                                               "description": "unauthorized"}})
            parts = h.path.strip("/").split("/")
            payout_id, action = parts[-2], parts[-1]
            self.approve_calls.append({
                "payout_id": payout_id, "action": action, "body": body,
                "x_creator_id": h.headers.get("x-creator-id"),
                "x_razorpay_account": h.headers.get("X-Razorpay-Account"),
                "x_batch_id": h.headers.get("x-batch-id"),
            })
            payout = self.payouts.get(payout_id)
            if payout is None:
                return h._json(400, {"error": {"code": "BAD_REQUEST_ERROR",
                                               "description": "payout not found"}})
            if payout["status"] != "pending":  # payoutController.go:1058-1066
                return h._json(409, {"error": {"code": "BAD_REQUEST_ERROR",
                                               "description": "conflict, already exists"}})
            payout["status"] = "queued" if action == "approve" else "rejected"
            return h._json(200, payout)


# --------------------------------------------------------------------------
# batch-sim process harness
# --------------------------------------------------------------------------
class SimProcess(object):
    def __init__(self, data_dir, ps_url):
        self.data_dir = data_dir
        self.ps_url = ps_url
        self.port = None
        self.proc = None

    def start(self, port=None, extra_env=None):
        self.port = port or free_port()
        env = dict(os.environ)
        env.update({
            "STUB_NAME": "batch-sim",
            "STUB_PORT": str(self.port),
            "BATCH_SIM_DATA_DIR": self.data_dir,
            "PS_API_URL": self.ps_url,
            "PS_API_AUTH_USER": PS_BASIC_USER,
            "PS_API_AUTH_PASS": PS_BASIC_PASS,
            "PS_API_AUTH_PASS_FILE": "",
            "WORKFLOW_CALLBACK_USER": WF_BASIC_USER,
            "WORKFLOW_CALLBACK_PASS": WF_BASIC_PASS,
            "WORKFLOW_CALLBACK_PASS_FILE": "",
            "PASSPORT_PRIVATE_KEY_FILE": passport_key() or "",
            "BATCH_SIM_MAX_ATTEMPTS": "2",
            "BATCH_SIM_BACKOFF_MS": "10",
            "BATCH_SIM_HTTP_TIMEOUT": "5",
        })
        env.update(extra_env or {})
        self.proc = subprocess.Popen(
            [sys.executable, SERVER], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.time() + 20
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("batch-sim exited: %s"
                                   % self.proc.stderr.read().decode("utf-8", "replace"))
            try:
                status, _ = http("GET", self.url() + "/health", timeout=2)
                if status == 200:
                    return self
            except Exception:  # noqa: BLE001
                time.sleep(0.05)
        raise RuntimeError("batch-sim did not become healthy")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def url(self):
        return "http://127.0.0.1:%d" % self.port


def make_rows(n, start=1):
    return [{
        "RazorpayX Account Number": "2224440041626905",
        "Payout Amount": str(1000 * (start + i)),
        "Payout Currency": "INR",
        "Payout Mode": "IMPS",
        "Payout Purpose": "payout",
        "Payout Narration": "row-%d" % (start + i),
        "Payout Reference Id": "ref-%d" % (start + i),
        "Fund Account Id": "fa_ARENA%05d" % (start + i),
        "Contact Name": "Contact %d" % (start + i),
        "notes[row]": str(start + i),
    } for i in range(n)]


class BatchSimTestBase(unittest.TestCase):
    def setUp(self):
        self.ps = FakePS().start()
        self.ps.require_passport = passport_key() is not None
        self.data_dir = tempfile.mkdtemp(prefix="batch-sim-test-")
        self.sim = SimProcess(self.data_dir, self.ps.url()).start()

    def tearDown(self):
        self.sim.stop()
        self.ps.stop()
        shutil.rmtree(self.data_dir, ignore_errors=True)

    # helpers
    def create(self, batch_type, rows, merchant="ARENAM00000009", settings=None,
               creator="usr_ARENA0001", expect=200):
        status, body = http("POST", self.sim.url() + "/v1/batches",
                            {"batch_type_id": batch_type, "name": "t",
                             "version": "2.0", "settings": settings or {},
                             "rows": rows},
                            {"X-Entity-Id": merchant, "X-Creator-Id": creator,
                             "X-Creator-Type": "user", "mode": "live"})
        self.assertEqual(expect, status, body)
        return body

    def wait(self, batch_id, timeout=25):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            _, last = http("GET", self.sim.url() + "/v1/batches/" + batch_id)
            if last.get("status") in ("COMPLETED", "FAILED", "CANCELLED"):
                return last
            time.sleep(0.05)
        raise AssertionError("batch %s never finished: %r" % (batch_id, last))

    def entries(self, batch_id):
        _, body = http("GET", self.sim.url() + "/v1/batches/%s/entries" % batch_id)
        return body["entries"]


class TestPayoutCreate(BatchSimTestBase):

    def test_n_rows_become_n_creates_with_correct_idempotency_keys(self):
        rows = make_rows(7)
        batch = self.create("payout", rows)
        self.assertIn(batch["status"],
                      ("CREATED", "STAGING", "PROCESSING", "OUTPUT", "COMPLETED"))
        self.assertEqual(7, batch["total_count"])
        self.assertEqual(14, len(batch["id"]))  # CustomIdGenerator.java:49

        final = self.wait(batch["id"])
        self.assertEqual("COMPLETED", final["status"])
        self.assertEqual("processed", final["outcome"])
        self.assertEqual(7, final["success_count"])
        self.assertEqual(0, final["failure_count"])

        # bulkSize 5 (payout.json) -> 7 rows = 2 calls of 5 and 2
        self.assertEqual(2, len(self.ps.create_calls))
        self.assertEqual([5, 2], [len(c["items"]) for c in self.ps.create_calls])
        self.assertEqual(7, len(self.ps.payouts))

        # idempotency key = "batch_" + entry id (BulkApiCallDataProcessorImpl:106,:163)
        entries = self.entries(batch["id"])
        self.assertEqual(7, len(entries))
        for entry in entries:
            self.assertEqual("batch_" + entry["id"], entry["idempotency_key"])
            self.assertEqual(14, len(entry["id"]))
            self.assertEqual("PROCESSED", entry["status"])
            self.assertTrue(json.loads(entry["response_data"])["payout_id"])
        sent = [k for c in self.ps.create_calls for k in c["idempotency_keys"]]
        self.assertEqual(sorted(e["idempotency_key"] for e in entries), sorted(sent))

        # headers PS actually requires / reads
        for call in self.ps.create_calls:
            self.assertEqual(batch["id"], call["batch_id"])      # x-batch-id
            self.assertEqual("ARENAM00000009", call["merchant"])  # X-Entity-Id
            self.assertEqual("usr_ARENA0001", call["creator_id"])  # x-creator-id
            self.assertEqual("user", call["creator_type"])         # x-creator-type

    def test_request_payload_matches_ps_dto(self):
        batch = self.create("payout", make_rows(1), settings={"scheduled_at": 1893456000})
        self.wait(batch["id"])
        item = self.ps.create_calls[0]["items"][0]
        self.assertEqual("2224440041626905", item["razorpayx_account_number"])
        self.assertEqual({"row": "1"}, item["notes"])
        self.assertTrue(item["idempotency_key"].startswith("batch_"))
        self.assertEqual("1000", item["payout"]["amount"])          # strings, per the DTO
        self.assertEqual("INR", item["payout"]["currency"])
        self.assertEqual("IMPS", item["payout"]["mode"])
        self.assertEqual("payout", item["payout"]["purpose"])
        self.assertEqual("row-1", item["payout"]["narration"])
        self.assertEqual("ref-1", item["payout"]["reference_id"])
        # PayoutApiProcessorImpl.java:31,:38 -- from batch settings
        self.assertEqual("1893456000", item["payout"]["scheduled_at"])
        self.assertEqual("fa_ARENA00001", item["fund"]["id"])
        self.assertEqual("Contact 1", item["contact"]["name"])
        for key in ("razorpayx_account_number", "notes", "idempotency_key",
                    "payout", "fund", "contact"):
            self.assertIn(key, item)
        for key in ("amount", "amount_in_rupees", "currency", "mode", "purpose",
                    "narration", "scheduled_at", "reference_id"):
            self.assertIn(key, item["payout"])
        for key in ("id", "account_type", "account_name", "account_IFSC",
                    "account_number", "account_vpa", "account_phone_number",
                    "account_email"):
            self.assertIn(key, item["fund"])
        for key in ("type", "name", "email", "mobile", "reference_id"):
            self.assertIn(key, item["contact"])

    def test_snake_case_row_aliases_are_accepted(self):
        batch = self.create("payout", [{
            "razorpayx_account_number": "2224440041626905",
            "payout_amount": "2500", "payout_currency": "INR", "payout_mode": "NEFT",
            "payout_purpose": "refund", "fund_account_id": "fa_X", "contact_name": "C",
        }])
        self.wait(batch["id"])
        item = self.ps.create_calls[0]["items"][0]
        self.assertEqual("2500", item["payout"]["amount"])
        self.assertEqual("NEFT", item["payout"]["mode"])
        self.assertEqual("fa_X", item["fund"]["id"])

    def test_partial_failure_gives_partially_processed_with_per_entry_errors(self):
        rows = make_rows(4)
        # PS fails rows 2 and 4 (matched on the row's Payout Reference Id, so
        # the failure is decided before batch-sim ever runs).
        with self.ps.lock:
            self.ps.fail_refs = {"ref-2", "ref-4"}
        batch = self.create("payout", rows)
        final = self.wait(batch["id"])

        self.assertEqual("COMPLETED", final["status"])
        self.assertEqual("partially_processed", final["outcome"])
        self.assertEqual(2, final["success_count"])
        self.assertEqual(2, final["failure_count"])
        self.assertEqual(4, final["processed_count"])

        entries = self.entries(batch["id"])
        statuses = [e["status"] for e in entries]
        self.assertEqual(["PROCESSED", "FAILED", "PROCESSED", "FAILED"], statuses)
        failed = json.loads(entries[1]["response_data"])
        self.assertEqual(400, failed["http_status_code"])
        self.assertEqual("BAD_REQUEST_ERROR", failed["item"]["error"]["code"])
        self.assertEqual("synthetic per-row failure",
                         failed["item"]["error"]["description"])
        # error items are matched back to their row by idempotency_key
        self.assertEqual(entries[1]["idempotency_key"],
                         failed["item"]["idempotency_key"])
        ok = json.loads(entries[0]["response_data"])
        self.assertEqual(entries[0]["idempotency_key"], ok["item"]["idempotency_key"])
        # only 2 payouts exist even though PS returned successes-then-errors
        self.assertEqual(2, len(self.ps.payouts))

    def test_resubmitting_the_same_batch_creates_no_new_payouts(self):
        """Idempotency keys are stable, so PS's duplicate-ikey path
        (core.go:352-390) returns the EXISTING payout instead of creating one."""
        batch = self.create("payout", make_rows(6))
        self.wait(batch["id"])
        self.assertEqual(6, len(self.ps.payouts))
        first_keys = sorted(k for c in self.ps.create_calls for k in c["idempotency_keys"])
        payout_ids_before = sorted(
            json.loads(e["response_data"])["payout_id"] for e in self.entries(batch["id"]))

        # /process skips already-PROCESSED entries -> no PS call at all.
        calls_before = len(self.ps.create_calls)
        http("POST", self.sim.url() + "/v1/batches/%s/process" % batch["id"], {})
        time.sleep(0.5)
        self.assertEqual(calls_before, len(self.ps.create_calls))
        self.assertEqual(6, len(self.ps.payouts))

        # /_arena/batches/{id}/reprocess forcibly resends every row with the
        # SAME keys -> PS calls happen, but still no new payouts.
        http("POST", self.sim.url() + "/_arena/batches/%s/reprocess" % batch["id"], {})
        deadline = time.time() + 20
        while time.time() < deadline and len(self.ps.create_calls) < calls_before + 2:
            time.sleep(0.05)
        self.assertEqual(calls_before + 2, len(self.ps.create_calls))
        final = self.wait(batch["id"])
        self.assertEqual(6, len(self.ps.payouts), "duplicate submit created new payouts")
        resent = sorted(k for c in self.ps.create_calls[calls_before:]
                        for k in c["idempotency_keys"])
        self.assertEqual(first_keys, resent, "idempotency keys were not stable")
        self.assertEqual(6, final["success_count"])
        self.assertEqual(payout_ids_before, sorted(
            json.loads(e["response_data"])["payout_id"] for e in self.entries(batch["id"])))

    def test_a_brand_new_batch_of_the_same_rows_does_create_new_payouts(self):
        """Real behaviour for batch_type `payout`: no cross-batch dedupe (only
        the v2 payouts_*_bene_* types have the SHA-1 cache)."""
        rows = make_rows(2)
        self.wait(self.create("payout", rows)["id"])
        self.assertEqual(2, len(self.ps.payouts))
        self.wait(self.create("payout", rows)["id"])
        self.assertEqual(4, len(self.ps.payouts))

    def test_chunking_never_exceeds_the_ps_limit(self):
        batch = self.create("payout", make_rows(13))
        final = self.wait(batch["id"])
        self.assertEqual(13, final["success_count"])
        sizes = [len(c["items"]) for c in self.ps.create_calls]
        self.assertEqual([5, 5, 3], sizes)
        self.assertTrue(all(s <= 15 for s in sizes))


class TestRetryAndFailure(BatchSimTestBase):

    def test_5xx_is_retried_then_becomes_per_row_errors_for_payout(self):
        """payout.json failOnServerError=false -> the batch still COMPLETEs with
        the rows marked failed (BulkApiCallDataProcessorImpl.java:415-427)."""
        with self.ps.lock:
            self.ps.next_status = [503, 503]  # BATCH_SIM_MAX_ATTEMPTS=2
        batch = self.create("payout", make_rows(2))
        final = self.wait(batch["id"])
        self.assertEqual("COMPLETED", final["status"])
        self.assertEqual("failed", final["outcome"])  # every row failed
        self.assertEqual(2, final["failure_count"])
        entry = json.loads(self.entries(batch["id"])[0]["response_data"])
        self.assertEqual(503, entry["http_status_code"])
        self.assertEqual(2, entry["attempts"])  # retried, then gave up

    def test_5xx_fails_the_whole_batch_for_payout_approval(self):
        """payout_approval.json has no failOnServerError -> Java default true ->
        JobFailureException -> FAILED (BulkApiCallDataProcessorImpl.java:430-431)."""
        seeded = self._seed_pending_payouts(2)
        with self.ps.lock:
            self.ps.next_status = [500, 500]
        batch = self.create("payout_approval",
                            [{"payout_id (do not edit)": pid,
                              "Approve (A) / Reject (R) payout": "A"} for pid in seeded])
        final = self.wait(batch["id"])
        self.assertEqual("FAILED", final["status"])
        self.assertEqual("failed", final["outcome"])
        self.assertEqual("API_SERVER_DOWN", final["error"])

    def test_arena_fail_next_injects_a_bounded_fault(self):
        http("POST", self.sim.url() + "/_arena/fail-next",
             {"count": 2, "status": 502, "scope": "create", "reason": "unit test"})
        batch = self.create("payout", make_rows(1))
        final = self.wait(batch["id"])
        self.assertEqual(1, final["failure_count"])
        self.assertEqual(0, len(self.ps.create_calls), "fault must not reach PS")
        _, calls = http("GET", self.sim.url() + "/_arena/calls")
        self.assertTrue(any(c.get("injected") == 502 for c in calls["calls"]))
        # bounded: the fault is spent, the next batch succeeds
        batch2 = self.create("payout", make_rows(1, start=50))
        self.assertEqual(1, self.wait(batch2["id"])["success_count"])

    def test_connection_failure_becomes_a_408_row_error(self):
        http("POST", self.sim.url() + "/_arena/fail-next",
             {"count": 5, "drop": True, "scope": "create"})
        batch = self.create("payout", make_rows(1))
        final = self.wait(batch["id"])
        self.assertEqual(1, final["failure_count"])
        resp = json.loads(self.entries(batch["id"])[0]["response_data"])
        self.assertEqual(408, resp["http_status_code"])
        self.assertEqual("GATEWAY_TIMEOUT", resp["item"]["error"]["code"])

    # helper
    def _seed_pending_payouts(self, n, merchant="ARENAM00000009"):
        batch = self.create("payout", make_rows(n), merchant=merchant)
        self.wait(batch["id"])
        return [json.loads(e["response_data"])["payout_id"] for e in self.entries(batch["id"])]


class TestApprovalBatch(BatchSimTestBase):

    def _seed(self, n, merchant="ARENAM00000009"):
        batch = self.create("payout", make_rows(n), merchant=merchant)
        self.wait(batch["id"])
        return [json.loads(e["response_data"])["payout_id"] for e in self.entries(batch["id"])]

    def test_approval_batch_calls_the_real_internal_route_per_row(self):
        payout_ids = self._seed(3)
        rows = [{"payout_id (do not edit)": pid,
                 "Approve (A) / Reject (R) payout": "A",
                 "account_number (do not edit)": "2224440041626905",
                 "amount(Rupees) (do not edit)": "10",
                 "status (do not edit)": "pending"} for pid in payout_ids]
        batch = self.create("payout_approval", rows, settings={"user_comment": "ok"})
        final = self.wait(batch["id"])

        self.assertEqual("COMPLETED", final["status"])
        self.assertEqual("processed", final["outcome"])
        self.assertEqual(3, final["success_count"])
        self.assertEqual(3, len(self.ps.approve_calls))
        for call, pid in zip(self.ps.approve_calls, payout_ids):
            self.assertEqual(pid, call["payout_id"])
            self.assertEqual("approve", call["action"])
            self.assertEqual({"queue_if_low_balance": False}, call["body"])
            self.assertEqual("usr_ARENA0001", call["x_creator_id"])
            self.assertEqual("ARENAM00000009", call["x_razorpay_account"])
            self.assertEqual(batch["id"], call["x_batch_id"])
        for pid in payout_ids:
            self.assertEqual("queued", self.ps.payouts[pid]["status"])

    def test_reject_action_uses_the_reject_route(self):
        pid = self._seed(1)[0]
        batch = self.create("payout_approval",
                            [{"payout_id (do not edit)": pid,
                              "Approve (A) / Reject (R) payout": "R"}])
        self.assertEqual(1, self.wait(batch["id"])["success_count"])
        self.assertEqual("reject", self.ps.approve_calls[-1]["action"])
        self.assertEqual({}, self.ps.approve_calls[-1]["body"])
        self.assertEqual("rejected", self.ps.payouts[pid]["status"])

    def test_409_already_transitioned_counts_as_success(self):
        """payoutController.go:1058-1066 / workflow-sim's 200|201|409 rule."""
        pid = self._seed(1)[0]
        rows = [{"payout_id (do not edit)": pid,
                 "Approve (A) / Reject (R) payout": "A"}]
        self.wait(self.create("payout_approval", rows)["id"])
        second = self.wait(self.create("payout_approval", rows)["id"])
        self.assertEqual(1, second["success_count"])
        self.assertEqual(0, second["failure_count"])
        resp = json.loads(self.entries(second["id"])[0]["response_data"])
        self.assertEqual(409, resp["http_status_code"])

    def test_queue_if_low_balance_from_settings(self):
        pid = self._seed(1)[0]
        batch = self.create("payout_approval",
                            [{"payout_id (do not edit)": pid,
                              "Approve (A) / Reject (R) payout": "A"}],
                            settings={"queue_if_low_balance": True})
        self.wait(batch["id"])
        self.assertEqual({"queue_if_low_balance": True}, self.ps.approve_calls[-1]["body"])

    def test_invalid_action_fails_the_row_locally(self):
        pid = self._seed(1)[0]
        batch = self.create("payout_approval",
                            [{"payout_id (do not edit)": pid,
                              "Approve (A) / Reject (R) payout": "X"}])
        final = self.wait(batch["id"])
        self.assertEqual(1, final["failure_count"])
        self.assertEqual(0, len(self.ps.approve_calls))
        resp = json.loads(self.entries(batch["id"])[0]["response_data"])
        self.assertIn("invalid payout_update_action", resp["item"]["error"]["description"])


class TestV2CacheIdempotency(BatchSimTestBase):
    """CacheIdempotencyEnabledBulkApiCallDataProcessorImpl.java:104-172."""

    TYPE = "payouts_bank_transfer_bene_id_process"

    def _row(self, fa, amount="100", ref="r1"):
        return {"Beneficiary's Fund Account ID (Mandatory) Unique id linked to a "
                "Razorpay Fund account.": fa,
                "Payout Amount (Mandatory) Amount should be in rupees": amount,
                "Payout Reference ID (Optional) Eg: Bill no or Invoice No or Pay ID": ref,
                "Payout Mode (Mandatory) Select IMPS/NEFT/RTGS": "IMPS",
                "RazorpayX Account Number": "2224440041626905",
                "Fund Account Id": fa,
                "Payout Amount": amount,
                "Payout Currency": "INR", "Payout Mode": "IMPS",
                "Payout Purpose": "payout", "Contact Name": "C"}

    def test_duplicate_row_in_the_same_file_is_rejected_without_an_api_call(self):
        rows = [self._row("fa_1"), self._row("fa_1"), self._row("fa_2")]
        final = self.wait(self.create(self.TYPE, rows)["id"])
        self.assertEqual(2, final["success_count"])
        self.assertEqual(1, final["failure_count"])
        self.assertEqual(2, len(self.ps.create_calls[0]["items"]))
        resp = json.loads(self.entries(final["id"])[1]["response_data"])
        self.assertEqual(400, resp["http_status_code"])
        self.assertIn("Duplicate payout found in the same file",
                      resp["item"]["error"]["description"])

    def test_duplicate_row_across_batches_is_rejected_with_the_existing_payout_id(self):
        first = self.wait(self.create(self.TYPE, [self._row("fa_9")])["id"])
        payout_id = json.loads(self.entries(first["id"])[0]["response_data"])["payout_id"]
        calls_before = len(self.ps.create_calls)

        second = self.wait(self.create(self.TYPE, [self._row("fa_9")])["id"])
        self.assertEqual(0, second["success_count"])
        self.assertEqual(1, second["failure_count"])
        self.assertEqual(calls_before, len(self.ps.create_calls), "no API call expected")
        resp = json.loads(self.entries(second["id"])[0]["response_data"])
        self.assertIn("Duplicate payout found in last 24h with the id: %s" % payout_id,
                      resp["item"]["error"]["description"])

    def test_the_cache_is_scoped_per_merchant(self):
        self.wait(self.create(self.TYPE, [self._row("fa_7")], merchant="ARENAM00000009")["id"])
        other = self.wait(self.create(self.TYPE, [self._row("fa_7")],
                                      merchant="ARENAM00000010")["id"])
        self.assertEqual(1, other["success_count"])


class TestApiSurface(BatchSimTestBase):

    def test_x_entity_id_is_required(self):
        status, body = http("POST", self.sim.url() + "/v1/batches",
                            {"batch_type_id": "payout", "rows": make_rows(1)})
        self.assertEqual(400, status)
        self.assertEqual("X_ENTITY_ID_NOT_PRESENT", body["error"]["code"])

    def test_unknown_batch_type_is_rejected(self):
        self.create("contact_bulk", make_rows(1), expect=400)

    def test_multipart_create_with_a_csv_file_part(self):
        boundary = "----batchsimtest"
        csv_text = ("RazorpayX Account Number,Payout Amount,Payout Currency,Payout Mode,"
                    "Payout Purpose,Fund Account Id,Contact Name\r\n"
                    "2224440041626905,4200,INR,IMPS,payout,fa_csv1,CSV One\r\n"
                    "2224440041626905,4300,INR,NEFT,payout,fa_csv2,CSV Two\r\n")
        parts = []
        for name, value in (("batch_type_id", "payout"), ("name", "csv-run"),
                            ("version", "2.0"), ("settings", "{}")):
            parts.append('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
                         % (boundary, name, value))
        parts.append('--%s\r\nContent-Disposition: form-data; name="file"; '
                     'filename="payouts.csv"\r\nContent-Type: text/csv\r\n\r\n%s\r\n'
                     % (boundary, csv_text))
        parts.append("--%s--\r\n" % boundary)
        body = "".join(parts).encode()
        req = urllib.request.Request(
            self.sim.url() + "/v1/batches", data=body, method="POST",
            headers={"Content-Type": "multipart/form-data; boundary=%s" % boundary,
                     "X-Entity-Id": "ARENAM00000009", "X-Creator-Id": "usr_ARENA0001",
                     "X-Creator-Type": "user"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            batch = json.loads(resp.read())
        self.assertEqual(2, batch["total_count"])
        final = self.wait(batch["id"])
        self.assertEqual(2, final["success_count"])
        item = self.ps.create_calls[0]["items"][0]
        self.assertEqual("4200", item["payout"]["amount"])
        self.assertEqual("fa_csv1", item["fund"]["id"])

    def test_passport_header_is_minted_for_the_bulk_call(self):
        if passport_key() is None:
            self.skipTest("openssl unavailable; cannot generate a passport signing key")
        self.wait(self.create("payout", make_rows(1))["id"])
        jwt = self.ps.last_passport
        self.assertIsNotNone(jwt, "X-Passport-JWT-V1 was not sent")
        header_b64, payload_b64, sig_b64 = jwt.split(".")
        import base64 as _b64

        def dec(seg):
            return json.loads(_b64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)))
        header, payload = dec(header_b64), dec(payload_b64)
        self.assertEqual("RS256", header["alg"])
        self.assertEqual("arena-passport-1", header["kid"])
        self.assertEqual({"id": "ARENAM00000009", "type": "merchant"}, payload["consumer"])
        self.assertTrue(payload["authenticated"] and payload["identified"])
        self.assertEqual("live", payload["mode"])
        self.assertGreater(payload["exp"], payload["iat"])
        self.assertTrue(sig_b64)

    def test_real_batch_path_alias(self):
        batch = self.create("payout", make_rows(1))
        status, body = http("GET", self.sim.url() + "/batch/" + batch["id"])
        self.assertEqual(200, status)
        self.assertEqual(batch["id"], body["id"])

    def test_arena_plane(self):
        batch = self.create("payout", make_rows(2))
        self.wait(batch["id"])
        status, health = http("GET", self.sim.url() + "/_arena/health")
        self.assertEqual(200, status)
        self.assertTrue(health["worker_alive"])
        self.assertIn("payout_approval", health["batch_types"])
        _, listing = http("GET", self.sim.url() + "/_arena/batches?entity_id=ARENAM00000009")
        self.assertEqual(1, listing["count"])
        _, detail = http("GET", self.sim.url() + "/_arena/batches/" + batch["id"])
        self.assertEqual(2, len(detail["entries"]))
        _, calls = http("GET", self.sim.url() + "/_arena/calls")
        self.assertEqual(batch["id"], self.ps.create_calls[0]["batch_id"])
        self.assertTrue(calls["calls"])

    def test_status_and_outcome_vocabulary(self):
        batch = self.create("payout", make_rows(1))
        self.assertIn(batch["status"], ("CREATED", "STAGING", "PROCESSING", "OUTPUT",
                                        "COMPLETED"))
        final = self.wait(batch["id"])
        self.assertEqual("COMPLETED", final["status"])
        self.assertEqual("processed", final["outcome"])
        for key in ("total_count", "processed_count", "success_count", "failure_count",
                    "attempts", "amount", "processed_amount", "batch_type_id",
                    "entity_id", "creator_id", "creator_type", "settings"):
            self.assertIn(key, final)


class TestRestartPersistence(unittest.TestCase):
    """The store outlives the process, and an interrupted batch resumes with the
    SAME idempotency keys -> no duplicate payouts."""

    def setUp(self):
        self.ps = FakePS().start()
        self.ps.require_passport = passport_key() is not None
        self.data_dir = tempfile.mkdtemp(prefix="batch-sim-restart-")

    def tearDown(self):
        try:
            self.sim.stop()
        except AttributeError:
            pass
        self.ps.stop()
        shutil.rmtree(self.data_dir, ignore_errors=True)

    def test_restart_resumes_without_duplicating(self):
        # Hold the PS down so the batch cannot finish, then kill batch-sim.
        with self.ps.lock:
            self.ps.next_status = [503] * 20
        self.sim = SimProcess(self.data_dir, self.ps.url()).start(
            extra_env={"BATCH_SIM_MAX_ATTEMPTS": "20", "BATCH_SIM_BACKOFF_MS": "300"})
        status, batch = http("POST", self.sim.url() + "/v1/batches",
                             {"batch_type_id": "payout", "rows": make_rows(4)},
                             {"X-Entity-Id": "ARENAM00000009", "X-Creator-Id": "u1",
                              "X-Creator-Type": "user"})
        self.assertEqual(200, status)
        batch_id = batch["id"]
        _, entries = http("GET", self.sim.url() + "/v1/batches/%s/entries" % batch_id)
        keys_before = sorted(e["idempotency_key"] for e in entries["entries"])
        self.assertEqual(4, len(keys_before))

        deadline = time.time() + 10
        while time.time() < deadline and not self.ps.bulk_requests:
            time.sleep(0.05)
        self.assertTrue(self.ps.bulk_requests, "PS was never called before the kill")
        self.sim.stop()

        # Restart against the SAME data dir, PS now healthy.
        with self.ps.lock:
            self.ps.next_status = []
        self.sim = SimProcess(self.data_dir, self.ps.url()).start()

        deadline = time.time() + 25
        final = None
        while time.time() < deadline:
            _, final = http("GET", self.sim.url() + "/v1/batches/" + batch_id)
            if final.get("status") in ("COMPLETED", "FAILED"):
                break
            time.sleep(0.1)
        self.assertEqual("COMPLETED", final["status"], final)
        self.assertEqual(4, final["success_count"])

        _, entries = http("GET", self.sim.url() + "/v1/batches/%s/entries" % batch_id)
        keys_after = sorted(e["idempotency_key"] for e in entries["entries"])
        self.assertEqual(keys_before, keys_after, "keys changed across the restart")
        self.assertEqual(4, len(self.ps.payouts),
                         "restart created duplicate payouts: %d" % len(self.ps.payouts))


if __name__ == "__main__":
    unittest.main(verbosity=2)
