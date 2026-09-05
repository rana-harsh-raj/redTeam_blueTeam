"""V3 -- FTS transfer dedupe on (source_id, source_type).

Covers: C20, Invariant 3. Calls fts-web directly (not via payouts) with the
PS service identity, per fts/.agents/skills/repo-skill/modules/integration/apis/transfer-api.md
(Group /v1/transfer, Auth: APIAuth, SettlementAuth, ValidxAuth, PayoutsService).
"""
import uuid

import pytest

from helpers import db


@pytest.mark.spec_id("V3")
@pytest.mark.status("CROSS_SERVICE_CONFIRMED")
def test_fts_transfer_dedupe_on_source_id_and_type(fts_client, fts_mysql, merchant_m1):
    source_id = "vt%s" % uuid.uuid4().hex[:12]  # fts transfers.source_id is varchar(14)
    source_type = "payout"
    # fts transferCreateRequestValidation (internal/controllers/validation.go:230): product, merchant_id,
    # a "transfer" object (amount, source_id, source_type, preferred_mode) and an "account" object whose
    # single key selects the destination type -- fund_account_id (integer, fts's own id) for payouts.
    body = {
        "product": "PAYOUT",
        "merchant_id": merchant_m1["merchant_id"],
        "transfer": {
            "amount": 100,
            "source_id": source_id,
            "source_type": source_type,
            "preferred_mode": "IMPS",
        },
        "account": {"fund_account_id": 900001},
    }

    resp1 = fts_client.post("/v1/transfer", body=body)
    assert resp1.status in (200, 201), "first /v1/transfer create failed: %s" % resp1

    resp2 = fts_client.post("/v1/transfer", body=body)
    assert resp2.status in (200, 201), "duplicate /v1/transfer create failed: %s" % resp2

    id1 = resp1.json().get("fund_transfer_id")  # fts responds {fund_account_id, fund_transfer_id, status}
    id2 = resp2.json().get("fund_transfer_id")
    assert id1 and id1 == id2, "duplicate (source_id, source_type) must return the same transfer id"

    rows = db.fetchall(
        fts_mysql,
        "SELECT id, status FROM transfers WHERE source_type=%s AND source_id=%s",
        (source_type, source_id),
    )
    assert len(rows) == 1, "expected exactly one transfers row for (source_id, source_type), got %d" % len(rows)

    attempts = db.fetchall(fts_mysql, "SELECT id FROM attempts WHERE transfer_id=%s", (id1,))
    assert len(attempts) <= 1, "duplicate transfer create must not spawn a second attempts row"
