"""Path conventions. The factory home (registry, image cache, instance execution directories) lives OUTSIDE the source
checkout by default (~/.twin-factory) so generated secrets, configuration, seeds and runtime files never touch the tree."""
import os
from pathlib import Path

REPO = Path(os.environ.get("TWIN_FACTORY_REPO") or os.environ.get("ARCHKIT_REPO") or Path(__file__).resolve().parents[1])
ENV2 = REPO / "ENV2_COMPOSE"
S2P = REPO / "DOMAIN_REPLICAS" / "source_to_pay"
IMPL = REPO / "reports" / "implementation"
RED_LOOP = REPO / "RED_LOOP"
JOURNEY_RUNNER = RED_LOOP / "m6" / "journeys" / "run.py"
FACTORY_HOME = Path(os.environ.get("TWIN_FACTORY_HOME") or (Path.home() / ".twin-factory"))
LOCAL_MIGRATIONS = REPO / ".local" / "twin-runtime-migrations" / "accepted"
LOCAL_REPOS = REPO / ".local" / "twin-repos" / "accepted"
COMPOSE = ENV2 / "docker-compose.yml"
COMPOSE_S2P = ENV2 / "docker-compose.s2p.yml"
COMPOSE_TWIN = ENV2 / "docker-compose.twin.yml"
COMPOSE_M11 = ENV2 / "docker-compose.m11.yml"   # M11: real trust-path overlay (edge gateway, Shield, banking-accounts, Workflow service)
S2P_IMAGE_ENV = S2P / ".build" / "runtime-image.env"


def rel(p) -> str:
    p = Path(p)
    try:
        return p.resolve().relative_to(REPO.resolve()).as_posix()
    except ValueError:
        return p.as_posix()


def instances_dir() -> Path:
    return FACTORY_HOME / "instances"


def images_dir() -> Path:
    return FACTORY_HOME / "images"


def registry_path() -> Path:
    return FACTORY_HOME / "registry.json"
