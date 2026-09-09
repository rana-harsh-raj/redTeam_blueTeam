"""Filesystem layout for the control plane.

The control plane's durable home is OUTSIDE the checkout (like the M9 factory
home) so a clean checkout provisions cleanly and several campaigns can coexist.

  $CAMPAIGN_CONTROL_HOME              (default ~/.campaign-control)
    campaigns/<campaign_id>/          one durable campaign
      manifest.json                   immutable, content-addressed
      *.jsonl                         append-only ledgers (authoritative)
      evidence/                       content-addressed blobs
      checkpoints/                    periodic control-plane snapshots
      workers/<worker_id>/            per-worker scratch + logs
      STOP / PAUSE                    external control files
"""
import os
from pathlib import Path

REPO = Path(os.environ.get("CONTROLPLANE_REPO") or os.environ.get("ARCHKIT_REPO")
            or Path(__file__).resolve().parents[1]).resolve()

HOME = Path(os.environ.get("CAMPAIGN_CONTROL_HOME") or (Path.home() / ".campaign-control")).resolve()
CAMPAIGNS = HOME / "campaigns"

# Reused platform packages live in the same checkout.
RED_LOOP = REPO / "RED_LOOP"
JOURNEY_RUNNER = REPO / "RED_LOOP" / "m6" / "journeys" / "run.py"


def campaign_dir(campaign_id):
    return CAMPAIGNS / campaign_id


def ensure_home():
    CAMPAIGNS.mkdir(parents=True, exist_ok=True)
    return HOME
