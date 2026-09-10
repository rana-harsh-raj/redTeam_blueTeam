"""Repository-relative paths. Everything is resolved from this file so the package is cwd-independent."""
from pathlib import Path
import os

REPO = Path(os.environ.get("ARCHKIT_REPO") or Path(__file__).resolve().parents[1])
ENV2 = REPO / "ENV2_COMPOSE"
DOMAIN = REPO / "reports" / "domain"
PARTS = DOMAIN / "parts"
IMPL = REPO / "reports" / "implementation"
ARCH = REPO / "reports" / "architecture"
STORE = Path(os.environ.get("ARCHKIT_STORE") or ARCH / "snapshots")
S2P = REPO / "DOMAIN_REPLICAS" / "source_to_pay"
M7_SNAPSHOT = ARCH / "M7_CANONICAL_SNAPSHOT.json"
M7_STATS = ARCH / "M7_CANONICAL_SNAPSHOT_STATS.json"
M7_ACCEPTANCE = IMPL / "M7_ACCEPTANCE.json"
M7_HASHES = IMPL / "M7_ARTIFACT_HASHES.json"
M7_UNKNOWNS_MD = IMPL / "M7_PRODUCTION_UNKNOWNS.md"
S2P_UNKNOWNS = S2P / "spec" / "unresolved-production-unknowns.yaml"
S2P_LOCK = S2P / "source-lock.json"
S2P_PATCH = S2P / "integration" / "unified-graph-patch.json"
S2P_OVERLAY = S2P / "inventory" / "runtime-fidelity-overlay.json"
INGRESS_CONTRACT = ENV2 / "substitutes" / "api-ingress" / "contract" / "routes.json"
COMPOSE = ENV2 / "docker-compose.yml"
COMPOSE_S2P = ENV2 / "docker-compose.s2p.yml"
COMPOSE_M11 = ENV2 / "docker-compose.m11.yml"          # M11: real trust-path overlay (snapshot input)
KONG_CONFIG = ENV2 / "trustpath" / "edge" / "kong-config.json"   # M11: derived from terraform-kong (snapshot input)
M7_TAG = "twin-m7-shared-ingress-integration"


def rel(p) -> str:
    p = Path(p)
    try:
        return p.resolve().relative_to(REPO.resolve()).as_posix()
    except ValueError:
        return p.as_posix()
