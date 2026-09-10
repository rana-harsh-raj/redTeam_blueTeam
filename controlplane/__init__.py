"""M10 — Durable Autonomous Campaign Control Plane.

A durable control plane that runs autonomous red-team campaigns across
M9 Twin-Factory instances. The human defines scope, budgets, safety/tool policy
and a broad mandate; the plane's Campaign Director queries the M8 archkit world
model, generates its own theses, schedules bounded work into dynamic cells and
workers, and drives every action through the M9-per-twin typed broker. All
authoritative state lives in structured, append-only durable records; the
model's conversation history is only a disposable cache.

Nothing here holds a credential in a record or a log; the gateway key is read
from the environment by the reused RED_LOOP llm client and redacted.
"""

import os as _os
import sys as _sys
from pathlib import Path as _Path

# make the reused RED_LOOP package importable wherever controlplane is imported
# (the platform substrate lives in RED_LOOP/red_loop within the same checkout).
_REPO = _Path(_os.environ.get("CONTROLPLANE_REPO") or _Path(__file__).resolve().parents[1])
for _p in (str(_REPO), str(_REPO / "RED_LOOP")):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

__version__ = "1.0.0"
SCHEMA_VERSION = "m10.1"
