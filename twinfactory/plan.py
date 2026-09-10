"""Instance planning: deterministic, collision-free names/addresses/ports and backend sizing for an instance.

Names are derived from the instance id (no canonical arena name is ever assumed); host ports and subnets are checked
against the durable registry so two instances on one host never share a published port or a subnet."""
import hashlib

from .util import sanitize_id

DEFAULT_EPOCH = 1735689600          # seeds/generator/generate.py DEFAULT_EPOCH (2025-01-01T00:00:00Z)
EPOCH_SPAN = 365 * 86400
import math


def sizing_for(service_count):
    """Backend boundary sizing derived from the profile's service count (measured: the 98-service arena uses ~7 GiB of
    container memory at rest); memory 1.5 GiB + 90 MiB per service, capped at 10 GiB; cpus 2..4; disk 30/40 GiB."""
    n = max(1, int(service_count))
    return {"cpus": max(2, min(4, 1 + n // 25)), "memory_gib": max(3, min(10, math.ceil(1.5 + 0.09 * n))), "disk_gib": 40 if n >= 60 else 30}


def seed_epoch(seed):
    """Synthetic-data seed -> fixture epoch (deterministic; the seed generator is deterministic given the epoch)."""
    h = int(hashlib.sha256(str(seed).encode()).hexdigest()[:12], 16)
    return DEFAULT_EPOCH + (h % EPOCH_SPAN)


def derive_names(instance_id):
    s = sanitize_id(instance_id)
    h = int(hashlib.sha256(s.encode()).hexdigest(), 16)
    base = 32 + (h % 99) * 2
    return {
        "instance_id": s,
        "arena_suffix": "-" + s,
        "compose_project": ("twin_" + s).replace("-", "_"),
        "arena_network": "rzp-arena-" + s,
        "ingress_network": "rzp-ingress-" + s,
        "s2p_network": "rzp-s2p-internal-" + s,
        "arena_subnet": "172.28.%d.0/24" % base,
        "ingress_subnet": "172.28.%d.0/24" % (base + 1),
        "kong_host_port": 18100 + (h % 800),
        "backend_profile": "twin-" + s,
    }


def plan_instance(instance_id, profile_name, registry_records=(), sizing=None, exclude_ids=(), service_count=98):
    names = derive_names(instance_id)
    used_ports = {r.get("kong_host_port") for r in registry_records if r.get("instance_id") not in exclude_ids and r.get("instance_id") != names["instance_id"]}
    used_subnets = {r.get("arena_subnet") for r in registry_records if r.get("instance_id") not in exclude_ids and r.get("instance_id") != names["instance_id"]}
    used_subnets |= {"172.28.16.0/24", "172.28.17.0/24"}   # never collide with the historical env2 defaults
    port = names["kong_host_port"]
    while port in used_ports:
        port = 18100 + ((port - 18100 + 1) % 800)
    names["kong_host_port"] = port
    third = int(names["arena_subnet"].split(".")[2])
    while names["arena_subnet"] in used_subnets:
        third = 32 + ((third - 32 + 2) % 198)
        names["arena_subnet"] = "172.28.%d.0/24" % third
        names["ingress_subnet"] = "172.28.%d.0/24" % (third + 1)
    size = sizing_for(service_count)
    if sizing:
        size.update({k: v for k, v in sizing.items() if v is not None})
    names["sizing"] = size
    return names
