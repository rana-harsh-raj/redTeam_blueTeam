"""Budget accounting and enforcement, computed from the durable ledgers.

Consumption is never trusted from memory: actions, model calls, tokens and wall
time are recomputed from the append-only ledgers, so a restarted Director sees
the true spend. ``check`` returns the first exhausted budget (if any); the engine
stops the campaign on it. The external kill switch is the ``STOP`` file, checked
independently. Cost is reported in tokens per model plus an optional USD estimate
from an overridable price table (tokens are authoritative; USD is advisory).
"""
import os
import time

# advisory $/1M tokens (prompt, completion); override via CONTROLPLANE_PRICES json env.
_DEFAULT_PRICES = {
    "claude-opus": (15.0, 75.0), "claude-sonnet": (3.0, 15.0), "claude-haiku": (0.8, 4.0),
    "gpt-5": (5.0, 15.0), "gpt-5.4-mini": (0.4, 1.6), "gemini": (2.0, 8.0), "default": (3.0, 12.0),
}


def _price_for(model):
    ml = (model or "").lower()
    for k, v in _DEFAULT_PRICES.items():
        if k in ml:
            return v
    return _DEFAULT_PRICES["default"]


class Budget:
    def __init__(self, control, manifest, clock=time.time):
        self.control = control
        self.manifest = manifest
        self.budgets = manifest.get("budgets", {})
        self.clock = clock

    def started_epoch(self):
        st = self.control.control_state()
        return st.get("started_epoch")

    def consumption(self):
        tool_calls = self.control._read_all("tool_calls")
        model_calls = self.control._read_all("model_calls")
        actions = len(tool_calls)
        n_model = len(model_calls)
        prompt_tok = comp_tok = 0
        by_model = {}
        usd = 0.0
        for mc in model_calls:
            m = mc.get("model") or "unknown"
            pt = int(mc.get("prompt_tokens") or (mc.get("usage") or {}).get("prompt_tokens") or 0)
            ct = int(mc.get("completion_tokens") or (mc.get("usage") or {}).get("completion_tokens") or 0)
            prompt_tok += pt
            comp_tok += ct
            d = by_model.setdefault(m, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0})
            d["calls"] += 1
            d["prompt_tokens"] += pt
            d["completion_tokens"] += ct
            pp, pc = _price_for(m)
            usd += pt / 1e6 * pp + ct / 1e6 * pc
        started = self.started_epoch()
        wall = (self.clock() - started) if started else 0
        return {"actions": actions, "model_calls": n_model, "prompt_tokens": prompt_tok,
                "completion_tokens": comp_tok, "wall_seconds": round(wall, 1),
                "estimated_usd": round(usd, 4), "by_model": by_model}

    def check(self):
        """Return (ok, reason, consumption). ok=False when a budget is exhausted."""
        c = self.consumption()
        b = self.budgets
        if b.get("max_actions") is not None and c["actions"] >= b["max_actions"]:
            return False, "action_budget_exhausted", c
        if b.get("max_model_calls") is not None and c["model_calls"] >= b["max_model_calls"]:
            return False, "model_call_budget_exhausted", c
        if b.get("max_wall_seconds") is not None and c["wall_seconds"] >= b["max_wall_seconds"]:
            return False, "wall_budget_exhausted", c
        if b.get("max_usd") is not None and c["estimated_usd"] >= b["max_usd"]:
            return False, "cost_budget_exhausted", c
        return True, None, c

    def record(self, phase="tick"):
        c = self.consumption()
        self.control.put("budget", {"phase": phase, **c})
        return c
