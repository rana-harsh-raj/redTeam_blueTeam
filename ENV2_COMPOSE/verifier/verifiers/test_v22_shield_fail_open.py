"""V22: scoped Shield delay is observed on a real create and fails open."""
import pytest
from helpers import payouts_flow as pf

@pytest.mark.spec_id("V22")
@pytest.mark.status("CROSS_SERVICE_CONFIRMED")
def test_shield_timeout_fails_open(ps_public_client,payouts_mysql,fts_mysql,shield_stub_client,merchant_m1):
    pf.fault(shield_stub_client,merchant_m1,delay_ms=1500)
    try:
        pid=pf.seed_payout(ps_public_client,merchant_m1)
        hits=pf.fault_hits(shield_stub_client,merchant_m1)
        assert hits and all(h["effect"]["delay_ms"]==1500 for h in hits), "real payout did not hit injected Shield delay"
        pf.wait_for_held_handoff(payouts_mysql,fts_mysql,pid)
    finally: pf.fault(shield_stub_client,merchant_m1,clear=True)
