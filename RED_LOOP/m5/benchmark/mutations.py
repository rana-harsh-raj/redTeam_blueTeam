"""Blind-benchmark mutation registry.

Each mutation is a surgical, single-property source patch applied to a *copy*
of the engine's decision core. The fixed reference is left pristine. Every
mutant still supports the full valid workflow (valid distinct-approver flows
succeed); the defect only manifests under the specific invariant probe.

Four mutants span four invariant families:
  cross_org   -> org-boundary          (authorization)
  separation  -> maker/checker (SoD)    (authorization)
  counting    -> approval-integrity     (N distinct approvers)
  terminal    -> state-integrity        (terminal-state resurrection)

build.py asserts every anchor actually matched, so a drift in core.py fails the
build loudly rather than silently producing a non-defective "mutant".
"""

# (anchor, replacement) pairs. Anchors are verbatim slices of wfengine/core.py.
MUTATIONS = {
    "cross_org": {
        "family": "org-boundary",
        "invariant": "an actor may only act within its own organization",
        "patches": [(
            '            # [CHECK:org] actor must belong to the workflow\'s org (org-boundary)\n'
            '            if actor["org_id"] != wf["org_id"]:\n'
            '                return err(403, "cross_org",\n'
            '                           "actor organization does not match workflow")\n',
            '            # [MUT:cross_org] org-boundary check disabled\n'
            '            if False and actor["org_id"] != wf["org_id"]:\n'
            '                return err(403, "cross_org",\n'
            '                           "actor organization does not match workflow")\n',
        )],
    },
    "separation": {
        "family": "maker-checker",
        "invariant": "the maker (creator) may not approve their own request",
        "patches": [(
            '            # [CHECK:separation] approver must not be the creator (maker/checker)\n'
            '            if wf["separation"] and actor["actor_id"] == wf["creator_id"]:\n'
            '                return err(403, "separation_violation",\n'
            '                           "maker cannot approve own request")\n',
            '            # [MUT:separation] maker/checker separation disabled on approve\n'
            '            if False and wf["separation"] and actor["actor_id"] == wf["creator_id"]:\n'
            '                return err(403, "separation_violation",\n'
            '                           "maker cannot approve own request")\n',
        )],
    },
    "counting": {
        "family": "approval-integrity",
        "invariant": "N distinct approvers are required; repeats do not count",
        "patches": [(
            '            # [CHECK:dup-approval] a repeat approval by the same actor is\n'
            '            # idempotent and MUST NOT increment the distinct-approver count.\n'
            '            existing = conn.execute(\n'
            '                "SELECT 1 FROM approvals WHERE workflow_id=? AND approver_id=?",\n'
            '                (wf_id, actor["actor_id"])).fetchone()\n'
            '            if not existing:\n'
            '                conn.execute(\n'
            '                    "INSERT INTO approvals(workflow_id,approver_id,org_id,created_at)"\n'
            '                    " VALUES(?,?,?,?)",\n'
            '                    (wf_id, actor["actor_id"], actor["org_id"], time.time()))\n'
            '\n'
            '            count = self._distinct_approvals(conn, wf_id)\n',
            '            # [MUT:counting] counts approve CALLS, not distinct approvers\n'
            '            conn.execute(\n'
            '                "INSERT OR IGNORE INTO approvals(workflow_id,approver_id,org_id,created_at)"\n'
            '                " VALUES(?,?,?,?)",\n'
            '                (wf_id, actor["actor_id"], actor["org_id"], time.time()))\n'
            '            count = wf["approvals_count"] + 1\n',
        )],
    },
    "terminal": {
        "family": "state-integrity",
        "invariant": "a terminal workflow is frozen and cannot be resurrected",
        "patches": [(
            '            # [CHECK:terminal] a terminal workflow is frozen (state-integrity)\n'
            '            if wf["terminal"] or wf["state"] in TERMINAL_STATES:\n'
            '                return err(409, "terminal_state",\n'
            '                           f"workflow is {wf[\'state\']}", state=wf["state"])\n',
            '            # [MUT:terminal] terminal-state freeze disabled on approve\n'
            '            if False and (wf["terminal"] or wf["state"] in TERMINAL_STATES):\n'
            '                return err(409, "terminal_state",\n'
            '                           f"workflow is {wf[\'state\']}", state=wf["state"])\n',
        )],
    },
}

FAMILIES = sorted({m["family"] for m in MUTATIONS.values()})
