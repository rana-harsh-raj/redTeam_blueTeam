"""RED_LOOP configuration: paths, model routing, and the attacker boundary.

Credentials are read ONLY from the environment (never hard-coded, never
committed). The operator sources an env file (see RED_LOOP/README.md) that sets
LITELLM_BASE_URL and LITELLM_API_KEY before running a campaign. The API key is
never written to any log, campaign record, or the red agent's context; see
llm.py:redact().
"""
import os
from pathlib import Path

# --- repo layout -----------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
RED_LOOP_DIR = REPO_ROOT / "RED_LOOP"
RUNS_DIR = RED_LOOP_DIR / "runs"
REGISTRY_DIR = RED_LOOP_DIR / "registry"
PROMPTS_DIR = RED_LOOP_DIR / "prompts"
ENV2 = REPO_ROOT / "ENV2_COMPOSE"

# Admitted source snapshots the red agent is ALLOWED to read (white/gray box).
ACCEPTED_SRC = REPO_ROOT / ".local" / "twin-repos" / "accepted"

# --- LiteLLM gateway -------------------------------------------------------
def gateway_base() -> str:
    base = os.environ.get("LITELLM_BASE_URL", "").rstrip("/")
    if not base:
        raise RuntimeError("LITELLM_BASE_URL not set (source RED_LOOP env file)")
    return base

def gateway_key() -> str:
    key = os.environ.get("LITELLM_API_KEY", "")
    if not key:
        raise RuntimeError("LITELLM_API_KEY not set (source RED_LOOP env file)")
    return key

# Model routing. Selection rationale recorded in registry/model-selection.json.
# Primary = strongest available Claude; reproducer = strongest available GPT
# (different provider/family) for independent, decorrelated replay.
# NOTE: claude-opus-5 is budget-capped on this key (HTTP 429 budget_exceeded)
# AND content-filtered on the adversarial mandate, so the strongest USABLE
# Claude, claude-opus-4-8, is the primary. See registry/model-selection.json.
PRIMARY_MODEL = os.environ.get("REDLOOP_PRIMARY_MODEL", "claude-opus-4-8")
REPRODUCER_MODEL = os.environ.get("REDLOOP_REPRODUCER_MODEL", "gpt-5.5")
# Cheap model for non-authoritative mechanical bookkeeping only (never judging).
UTILITY_MODEL = os.environ.get("REDLOOP_UTILITY_MODEL", "gpt-5.4-mini")

# --- attacker boundary -----------------------------------------------------
# The ONLY host ingress the attacker may reach. All other arena services and
# every control/evidence plane are unreachable from the broker.
KONG_LITE_URL = os.environ.get("KONG_LITE_HOST_URL", "http://127.0.0.1:18080")

# Product path prefixes the attacker merchant may call through kong-lite.
# Mirrors kong-lite DEFAULT_ROUTES minus internal/twirp service planes that an
# ordinary merchant would not address directly.
ATTACKER_ALLOWED_PREFIXES = (
    "/v1/payouts",
    "/v1/contacts",
    "/v1/fund_accounts",
    "/v1/balances",       # kong routes this to xbalances-server: a real merchant read surface
    "/v1/transactions",
    "/v1/payout_links",
    "/v1/payouts_batch",
)

# Hard-denied path fragments: arena control/oracle endpoints and health.
# /_arena/mint is an UNAUTHENTICATED passport-forge oracle for the verifier;
# exposing it to red would hand out arbitrary identity. Must never be reachable.
ATTACKER_DENIED_FRAGMENTS = (
    "/_arena",       # mint, health, and any future arena control path
    "/twirp/",       # raw service-plane RPC (ledger/cfa/health) — not merchant surface
    "/metrics",
    "/debug",
)

# Merchant secret locations on host (control plane only; NEVER exposed to red).
MERCHANT_KEYS_DIR = ENV2 / "secrets" / "merchant-keys"
GENERATED_MERCHANTS = ENV2 / "seeds" / "generated" / "merchants.json"
