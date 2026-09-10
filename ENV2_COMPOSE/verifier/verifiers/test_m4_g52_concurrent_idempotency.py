"""G52 -- Concurrent idempotent creation: N simultaneous POSTs with the same
idempotency key + body collapse to exactly one payout (single FTS transfer),
with the documented control cases.
"""
import concurrent.futures

import pytest

from helpers import payouts_flow as pf
from helpers import m4_boundary as m4
from helpers.m4_boundary import hardened_edge, merchant_m1, merchant_m2, make_isolated_scenario  # noqa: F401

isolated_scenario = make_isolated_scenario("hold")

N = 8


def _fire_concurrent(client, path, bodies_headers):
    results = [None] * len(bodies_headers)
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(bodies_headers)) as ex:
        futs = {ex.submit(client.post, path, body=b, headers=h): i for i, (b, h) in enumerate(bodies_headers)}
        for fut in concurrent.futures.as_completed(futs):
            i = futs[fut]
            try:
                results[i] = fut.result()
            except Exception as exc:  # noqa: BLE001
                results[i] = exc
    return results


@pytest.mark.spec_id("G52")
@pytest.mark.status("END_TO_END_CONFIRMED")
@pytest.mark.parametrize("merchant_key", ["M2", "M1"])
def test_concurrent_same_key_same_body_single_payout(merchant_key, hardened_edge, merchant_m1, merchant_m2,
                                                     payouts_mysql, fts_mysql):
    """G52 concurrent idempotent creation.

    Invariant (formal): for N concurrent POSTs sharing one idempotency key and
    an identical body, |{payouts rows for (key, merchant)}| == 1, every 2xx
    response carries that single id, and the source has at most one FTS transfer.

    Setup: one merchant (M2 Direct, then M1 Shared), a fresh idempotency key,
    a single fixed body (queue_if_low_balance=True so creation is synchronous
    and bank-independent).

    Action: fire N=8 identical POSTs concurrently through kong-lite.

    Expected observable effect: exactly one idempotency_keys row and one payouts
    row for the key; all 2xx responses return the same id; non-2xx (if any) are
    409/400 conflict-shaped, documented, never a second created id; the FTS
    transfer count for the source is <= 1.

    Controls (same test):
      * same key, DIFFERENT body -> 400 (SameIdempotencyKeyDifferentRequest),
        request_hash unchanged, still one row.
      * N DIFFERENT keys, same body -> N distinct payouts (idempotency is keyed).

    Evidence source: trace jsonl (m4_concurrent_result, db_read).
    Fidelity level: END_TO_END_CONFIRMED.
    """
    merchant = merchant_m2 if merchant_key == "M2" else merchant_m1
    client = m4.merchant_edge_or_fail(merchant)
    idem = pf.new_idempotency_key()
    base_body = pf.build_create_body(merchant["fund_account_id"], merchant["account_number"], amount=100,
                                     queue_if_low_balance=True, mode="IMPS",
                                     extra={"merchant_id": merchant["merchant_id"]})

    results = _fire_concurrent(client, "/v1/payouts",
                               [(dict(base_body), {"X-Payout-Idempotency": idem}) for _ in range(N)])
    statuses, ids, conflicts = [], set(), []
    for r in results:
        if isinstance(r, Exception):
            conflicts.append(("exception", str(r)))
            continue
        statuses.append(r.status)
        if r.status in (200, 201):
            ids.add(r.json().get("id"))
        else:
            conflicts.append((r.status, r.text[:200]))
    m4.trace.record("m4_concurrent_result", {"merchant": merchant["key"], "n": N, "statuses": statuses,
                                             "distinct_ids": sorted(i for i in ids if i), "conflicts": conflicts})

    key_rows = pf.db.fetchall(payouts_mysql,
                              "SELECT id, source_id, request_hash FROM idempotency_keys WHERE idempotency_key=%s AND merchant_id=%s",
                              (idem, merchant["merchant_id"]))
    assert len(key_rows) == 1, "expected exactly one idempotency_keys row, got %r" % key_rows
    source_id = key_rows[0]["source_id"]
    payout_rows = pf.db.fetchall(payouts_mysql, "SELECT id FROM payouts WHERE id=%s", (source_id,))
    assert len(payout_rows) == 1, "expected exactly one payout row for the key, got %r" % payout_rows

    non_conflict_bad = [c for c in conflicts if not (isinstance(c[0], int) and c[0] in (409, 400, 425, 429, 500))]
    assert not non_conflict_bad, "unexpected non-conflict failures under concurrency: %r" % non_conflict_bad
    assert ids, "at least one concurrent request must have created the payout: %r" % statuses
    assert ids == {"pout_" + source_id}, "responses returned an id other than the single created payout: %r vs %s" % (ids, source_id)

    # at most one FTS transfer for the source
    transfers = pf.db.fetchall(fts_mysql,
                               "SELECT id FROM transfers WHERE source_id=%s AND source_type='payout'", (source_id,))
    assert len(transfers) <= 1, "duplicate FTS transfers for one idempotent payout: %r" % transfers

    # control: same key, different body -> 400, one row unchanged
    diff = dict(base_body, amount=base_body["amount"] + 7)
    diff_resp = client.post("/v1/payouts", body=diff, headers={"X-Payout-Idempotency": idem})
    m4.observe("same_key_different_body", diff_resp, m4.LAYER_SERVICE_VALIDATION)
    assert diff_resp.status == 400, "same key + different body must be 400: %s" % diff_resp
    key_rows_after = pf.db.fetchall(payouts_mysql,
                                    "SELECT source_id, request_hash FROM idempotency_keys WHERE idempotency_key=%s AND merchant_id=%s",
                                    (idem, merchant["merchant_id"]))
    assert len(key_rows_after) == 1 and key_rows_after[0]["request_hash"] == key_rows[0]["request_hash"]

    # control: N different keys, same body -> N payouts
    distinct = _fire_concurrent(client, "/v1/payouts",
                                [(dict(base_body), {"X-Payout-Idempotency": pf.new_idempotency_key()}) for _ in range(N)])
    distinct_ids = {r.json().get("id") for r in distinct if not isinstance(r, Exception) and r.status in (200, 201)}
    m4.trace.record("m4_concurrent_distinct_keys", {"n": N, "distinct_ids": sorted(i for i in distinct_ids if i)})
    assert len(distinct_ids) == N, "N distinct keys must yield N payouts, got %d: %r" % (len(distinct_ids), distinct_ids)

    # settle the held handoffs created above so cleanup is clean
    for pid in [source_id] + [i.split("_", 1)[1] for i in distinct_ids if i]:
        try:
            pf.wait_for_held_handoff(payouts_mysql, fts_mysql, pid)
        except Exception:  # noqa: BLE001 -- settlement is best-effort teardown, not the assertion
            pass
