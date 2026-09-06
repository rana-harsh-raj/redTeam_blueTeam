"""Bounded experiment templates (M4).

The cross-merchant / identity-override template is the load-bearing one: it
forces the arms that separate a real cross-tenant effect from an own-merchant
control, and encodes the rule that a *denial* is never evidence the service is
safe. Validators here gate the hypothesis lifecycle so that a cross-merchant
hypothesis cannot be marked ``supported`` or ``falsified`` without the merchant-B
arm actually executing against observed state.

No network, no credentials.
"""
from enum import Enum


class Arm(str, Enum):
    OWN_MERCHANT_CONTROL = "own_merchant_control"
    MERCHANT_B_VARIANT = "merchant_b_variant"
    MALFORMED_OR_ABSENT_IDENTITY_VARIANT = "malformed_or_absent_identity_variant"
    EXPECTED_DENIAL_CONTROL = "expected_denial_control"
    STATE_OBSERVATION = "state_observation"
    CLEAN_RESET = "clean_reset"

    def __str__(self):
        return self.value


# The arms a cross-merchant / identity-override experiment MUST define.
REQUIRED_ARMS = [
    Arm.OWN_MERCHANT_CONTROL,
    Arm.MERCHANT_B_VARIANT,
    Arm.MALFORMED_OR_ABSENT_IDENTITY_VARIANT,
    Arm.EXPECTED_DENIAL_CONTROL,
    Arm.STATE_OBSERVATION,
    Arm.CLEAN_RESET,
]

DENIED_AT_LAYERS = {"broker", "gateway", "service", "unknown"}


class ExperimentError(ValueError):
    pass


class CrossMerchantExperiment:
    """A cross-merchant / identity-override experiment with mandatory arms and
    the ``replay_required_if_unauthorized_effect`` flag.

    Each arm accumulates observations via ``observe(arm, ...)``. The kind is
    ``cross_merchant``; a validator refuses to mark the owning hypothesis
    ``supported``/``falsified`` unless the merchant-B arm executed with a state
    observation.
    """

    kind = "cross_merchant"

    def __init__(self, hypothesis_id, target_asset, replay_required_if_unauthorized_effect=True):
        self.hypothesis_id = hypothesis_id
        self.target_asset = target_asset
        self.replay_required_if_unauthorized_effect = bool(replay_required_if_unauthorized_effect)
        # arm -> list of observation dicts
        self.arms = {a.value: [] for a in REQUIRED_ARMS}
        self.independent_replay = None   # set by record_replay()

    # -- observation --------------------------------------------------------
    def observe(self, arm, status=None, state_observed=None, denied=False,
                denied_at_layer=None, unauthorized_effect=False, evidence_ref=None,
                note=None):
        arm = _as_arm(arm)
        if denied:
            layer = (denied_at_layer or "unknown")
            if layer not in DENIED_AT_LAYERS:
                raise ExperimentError("denied_at_layer must be one of %s" % sorted(DENIED_AT_LAYERS))
            denied_at_layer = layer
        obs = {
            "arm": arm.value, "status": status, "state_observed": state_observed,
            "denied": bool(denied), "denied_at_layer": denied_at_layer,
            "unauthorized_effect": bool(unauthorized_effect),
            "evidence_ref": evidence_ref, "note": note,
        }
        self.arms[arm.value].append(obs)
        return obs

    def record_replay(self, independent, reproduced, note=None):
        self.independent_replay = {"independent": bool(independent),
                                   "reproduced": bool(reproduced), "note": note}
        return self.independent_replay

    # -- introspection ------------------------------------------------------
    def executed_arms(self):
        return {arm for arm, obs in self.arms.items() if obs}

    def missing_arms(self):
        return [a.value for a in REQUIRED_ARMS if not self.arms[a.value]]

    def merchant_b_executed_with_state(self):
        for obs in self.arms[Arm.MERCHANT_B_VARIANT.value]:
            if obs.get("state_observed") is not None:
                return True
        return False

    def unauthorized_effect_observed(self):
        for obs_list in self.arms.values():
            for obs in obs_list:
                if obs.get("unauthorized_effect"):
                    return True
        return False

    def only_own_control_succeeded(self):
        """True when the ONLY success is the own-merchant control (i.e. no
        cross-merchant / identity-variant unauthorized effect)."""
        own = any(_is_success(o) for o in self.arms[Arm.OWN_MERCHANT_CONTROL.value])
        return own and not self.unauthorized_effect_observed()

    # -- validators (gate the lifecycle) -----------------------------------
    def can_support(self):
        """(ok, reason) — may the owning hypothesis be marked SUPPORTED?"""
        missing = self.missing_arms()
        if missing:
            return False, "missing_arms:" + ",".join(missing)
        if not self.merchant_b_executed_with_state():
            return False, "merchant_b_arm_no_state_observation"
        if not self.unauthorized_effect_observed():
            return False, "no_unauthorized_effect_only_controls"
        return True, None

    def can_falsify(self):
        """(ok, reason) — may the owning hypothesis be marked FALSIFIED?

        A cross-merchant hypothesis cannot be falsified until the merchant-B arm
        actually executed with a state observation. A denial alone is NOT a
        falsification (denied path is not evidence of service safety)."""
        if not self.merchant_b_executed_with_state():
            return False, "merchant_b_arm_did_not_execute_with_state"
        return True, None

    def can_accept(self):
        """(ok, reason) — replay discipline for an unauthorized effect."""
        ok, reason = self.can_support()
        if not ok:
            return False, reason
        if self.replay_required_if_unauthorized_effect:
            if not (self.independent_replay and self.independent_replay.get("independent")
                    and self.independent_replay.get("reproduced")):
                return False, "independent_replay_required"
        return True, None

    def denial_summary(self):
        """Denials recorded per layer — reported as information, never as
        'service safe'."""
        out = []
        for obs_list in self.arms.values():
            for obs in obs_list:
                if obs.get("denied"):
                    out.append({"arm": obs["arm"], "denied_at_layer": obs["denied_at_layer"],
                                "status": obs.get("status")})
        return out

    def to_dict(self):
        return {
            "kind": self.kind, "hypothesis_id": self.hypothesis_id,
            "target_asset": self.target_asset,
            "replay_required_if_unauthorized_effect": self.replay_required_if_unauthorized_effect,
            "arms": self.arms, "executed_arms": sorted(self.executed_arms()),
            "missing_arms": self.missing_arms(),
            "unauthorized_effect": self.unauthorized_effect_observed(),
            "independent_replay": self.independent_replay,
            "denials": self.denial_summary(),
        }


def _as_arm(v):
    if isinstance(v, Arm):
        return v
    try:
        return Arm(str(v).lower())
    except ValueError:
        raise ExperimentError("unknown arm %r (must be one of %s)"
                              % (v, [a.value for a in Arm]))


def _is_success(obs):
    st = obs.get("status")
    if st is None:
        return bool(obs.get("state_observed"))
    try:
        return 200 <= int(st) < 300
    except (TypeError, ValueError):
        return str(st).lower() in ("ok", "success", "created", "processed")


def new_cross_merchant(hypothesis_id, target_asset, **kw):
    return CrossMerchantExperiment(hypothesis_id, target_asset, **kw)
