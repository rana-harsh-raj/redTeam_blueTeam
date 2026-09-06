#!/usr/bin/env python3
"""M4 context-policy tests (T08 §3.5). No network, no docker."""
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "RED_LOOP"))
from red_loop.state import CampaignStore  # noqa: E402
from red_loop import contexts  # noqa: E402
from red_loop.tools import Dispatcher  # noqa: E402


class _FakeBroker:
    request_count = 0
    request_budget = 100
    violation_count = 0

    def attacker_identity(self):
        return {"merchant_id": "acc_attacker"}

    def concurrent(self, *a, **k):
        return {"results": []}


def _store(name="ctx"):
    d = tempfile.mkdtemp(prefix="ctx-")
    return CampaignStore(campaign_id=name, root=Path(d) / name)


class TestPolicies(unittest.TestCase):
    def test_four_policies_present(self):
        names = [p.name for p in contexts.POLICIES]
        self.assertEqual(set(names), {"broad_coverage", "deep_direct_accounting",
                                      "identity_tenant_boundary", "concurrency_event_order"})
        self.assertEqual(len(names), 4)

    def test_policies_have_distinct_mandates_and_allowlists(self):
        suffixes = [p.mandate_suffix for p in contexts.POLICIES]
        self.assertEqual(len(set(suffixes)), 4)
        prefixes = [p.correlation_prefix for p in contexts.POLICIES]
        self.assertEqual(len(set(prefixes)), 4)
        # only concurrency policy gets the concurrent tool
        with_conc = [p.name for p in contexts.POLICIES
                     if "merchant_request_concurrent" in p.tool_allowlist]
        self.assertEqual(with_conc, ["concurrency_event_order"])

    def test_tool_allowlist_enforced(self):
        # a policy WITHOUT merchant_request_concurrent cannot dispatch it: blocked
        # result, not an exception.
        store = _store()
        policy = contexts.POLICY_BY_NAME["broad_coverage"]
        disp = Dispatcher(_FakeBroker(), store, candidate_hook=lambda r: {},
                          allowed_tools=policy.tool_allowlist, owner="broad_coverage")
        result = disp.dispatch("merchant_request_concurrent",
                               {"method": "POST", "path": "/v1/payouts", "count": 4})
        self.assertTrue(result.get("blocked"))
        self.assertEqual(result.get("reason"), "tool_not_in_context_allowlist")

    def test_allowed_tool_passes_allowlist(self):
        store = _store()
        policy = contexts.POLICY_BY_NAME["concurrency_event_order"]
        disp = Dispatcher(_FakeBroker(), store, candidate_hook=lambda r: {},
                          allowed_tools=policy.tool_allowlist, owner="concurrency_event_order")
        # allowed -> not blocked (broker returns empty results)
        result = disp.dispatch("merchant_request_concurrent",
                               {"method": "POST", "path": "/v1/payouts", "count": 2})
        self.assertFalse(result.get("blocked"))

    def test_supervisor_runs_contexts_with_injected_runner(self):
        store = _store()
        calls = []

        def runner(policy, merchant, budget, base_mandate):
            calls.append(policy.name)
            return {"turns_completed": 3, "stop_reason": "agent_concluded"}

        sup = contexts.ContextSupervisor(store, runner=runner)
        summary = sup.run(base_mandate="BASE")
        self.assertEqual(len(calls), 4)
        self.assertEqual(summary["total_turns"], 12)
        events = [e for e in store._read_all("contexts") if e.get("event") == "context_end"]
        self.assertEqual(len(events), 4)

    def test_supervisor_isolates_context_exceptions(self):
        store = _store()

        def runner(policy, merchant, budget, base_mandate):
            if policy.name == "deep_direct_accounting":
                raise RuntimeError("boom")
            return {"turns_completed": 1}

        sup = contexts.ContextSupervisor(store, runner=runner)
        summary = sup.run()
        errored = [c for c in summary["contexts"] if c.get("error")]
        self.assertEqual(len(errored), 1)
        self.assertEqual(errored[0]["policy"], "deep_direct_accounting")

    def test_merchant_descriptor_accepted(self):
        store = _store()
        md = contexts.MerchantDescriptor(merchant_id="acc_b", label="direct-fresh")
        seen = {}

        def runner(policy, merchant, budget, base_mandate):
            seen[policy.name] = merchant
            return {"turns_completed": 0}

        sup = contexts.ContextSupervisor(store, runner=runner)
        sup.run(merchants={"broad_coverage": md})
        self.assertIs(seen["broad_coverage"], md)


if __name__ == "__main__":
    unittest.main()
