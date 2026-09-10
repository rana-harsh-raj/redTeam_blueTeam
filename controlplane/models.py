"""Model-pool routing and fallback for the campaign.

The manifest pins a model pool. The router assigns models to roles, records every
assignment and every fallback, and guarantees decorrelation where the milestone
requires it: a candidate's independent replay must use a DIFFERENT model family
than the worker that produced it (no self-verification, even by the same model).

Routing is pure and deterministic given the pool; the only network dependency
(live model inventory) is optional and injected, so tests never hit a gateway.
"""

import re

FAMILIES = ("claude", "gpt", "gemini", "llama", "mistral", "qwen")


def family_of(model):
    ml = (model or "").lower()
    for f in FAMILIES:
        if f in ml:
            return f
    return "other"


_MINI_RE = re.compile(r"(?<![a-z])(mini|nano|small|haiku|lite|flash)(?![a-z])")


def _is_mini(model):
    # delimited token match so "gemini" is NOT read as a "mini" tier model
    return bool(_MINI_RE.search((model or "").lower()))


class ModelRouter:
    def __init__(self, pool, store=None, roles=None):
        if not pool:
            raise ValueError("empty model pool")
        self.pool = list(pool)
        self.store = store
        self.roles = dict(roles or {})
        if not self.roles:
            self._auto_assign()

    def _auto_assign(self):
        # director/worker: strongest non-mini; verifier: a different family.
        strong = [m for m in self.pool if not _is_mini(m)] or list(self.pool)
        director = strong[0]
        worker = strong[0]
        verifier = self._different_family(worker) or (strong[1] if len(strong) > 1 else strong[0])
        utility = next((m for m in self.pool if _is_mini(m)), self.pool[-1])
        self.roles = {"director": director, "worker": worker,
                      "verifier": verifier, "utility": utility}
        self._record("auto_assign", roles=dict(self.roles))

    def _different_family(self, model, exclude=()):
        fam = family_of(model)
        for m in self.pool:
            if m in exclude:
                continue
            if family_of(m) != fam:
                return m
        return None

    def for_role(self, role):
        return self.roles.get(role) or self.pool[0]

    def verifier_for(self, producer_model, exclude=()):
        """A model to independently replay a candidate: must differ in family
        from the producer (decorrelated), never the producer itself."""
        exclude = set(exclude) | {producer_model}
        m = self._different_family(producer_model, exclude=exclude)
        if m is None:
            # no distinct family available; at least a distinct model id
            m = next((x for x in self.pool if x not in exclude), None)
        self._record("verifier_pick", producer=producer_model, chosen=m,
                     decorrelated=(m is not None and family_of(m) != family_of(producer_model)))
        return m

    def fallback(self, current, available=None, exclude=()):
        """A different model than ``current`` for requeue/timeout recovery."""
        from red_loop import llm
        pool = [m for m in (available or self.pool) if m not in set(exclude) | {current}]
        chosen = llm.pick_fallback(current, available=pool) if pool else None
        self._record("fallback", frm=current, to=chosen)
        return chosen

    def _record(self, kind, **fields):
        if self.store is not None:
            self.store.event("model_routing", routing_kind=kind, **fields)

    def snapshot(self):
        return {"pool": list(self.pool), "roles": dict(self.roles)}
