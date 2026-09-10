"""Twin adapter over the M9 Twin Factory.

The control plane never touches Docker, host files or a twin's internals
directly. It asks this adapter for a twin's *coordinates* (the env context a
worker subprocess needs to reach exactly that twin's broker), for a health
verdict, and — when a twin has failed — to recover or reproduce it through
twinfactory and hand back a fresh handle.

The adapter is injectable: the live engine uses ``FactoryTwinPool``; the
deterministic tests use a fake pool with the same surface, so no test needs
Docker or a running twin.
"""


class TwinHandle:
    """Everything a worker/verifier subprocess needs to address ONE twin."""

    def __init__(self, instance_id, runtime_instance_id, env, kong_url,
                 profile=None, seed=None, healthy=True, replaced_by=None):
        self.instance_id = instance_id
        self.runtime_instance_id = runtime_instance_id
        self.env = dict(env)                 # twin-scoped process env
        self.kong_url = kong_url
        self.profile = profile
        self.seed = seed
        self.healthy = healthy
        self.replaced_by = replaced_by

    def as_dict(self):
        return {"instance_id": self.instance_id,
                "runtime_instance_id": self.runtime_instance_id,
                "kong_url": self.kong_url, "profile": self.profile,
                "seed": self.seed, "healthy": self.healthy,
                "replaced_by": self.replaced_by}


class TwinPool:
    """Interface. Implementations must provide handle/health/recover."""

    def handle(self, instance_id):
        raise NotImplementedError

    def health(self, instance_id):
        raise NotImplementedError

    def recover(self, instance_id, store=None):
        raise NotImplementedError


class FactoryTwinPool(TwinPool):
    def __init__(self, factory=None):
        from twinfactory.factory import Factory
        self.factory = factory or Factory()

    def _handle_from_manifest(self, m, healthy=True):
        from twinfactory.journeys import journey_env
        env = journey_env(self.factory, m)
        return TwinHandle(
            instance_id=m["instance_id"],
            runtime_instance_id=m.get("runtime_instance_id"),
            env=env,
            kong_url="http://127.0.0.1:%d" % m["kong_host_port"],
            profile=m.get("profile"), seed=m.get("seed"), healthy=healthy)

    def handle(self, instance_id):
        m = self.factory.manifest(instance_id)
        return self._handle_from_manifest(m)

    def health(self, instance_id):
        try:
            h = self.factory.health(instance_id)
        except Exception as e:  # noqa: BLE001
            return {"healthy": False, "error": str(e)[:300]}
        healthy = (h.get("result") == "PASS") or (h.get("healthy") is True) or \
                  (h.get("checks_passed", 0) > 0 and h.get("checks_passed") == h.get("checks_total"))
        h = dict(h)
        h["healthy"] = bool(healthy)
        return h

    def recover(self, instance_id, store=None):
        """Try an in-place restart first; if the twin cannot be brought healthy,
        reproduce a replacement instance from its own manifest and return that
        fresh handle. Every step is recorded through ``store`` when supplied."""
        def rec(kind, **f):
            if store is not None:
                store._append("recoveries", dict(kind=kind, instance_id=instance_id, **f))
                store.event("twin_recovery", stage=kind, instance_id=instance_id, **f)

        rec("restart_attempt")
        try:
            self.factory.start(instance_id)
            h = self.health(instance_id)
            if h.get("healthy"):
                rec("restart_ok")
                return self.handle(instance_id)
        except Exception as e:  # noqa: BLE001
            rec("restart_failed", error=str(e)[:300])

        # reproduce a replacement from the immutable manifest
        new_id = instance_id + "-r"
        rec("reproduce_attempt", new_instance_id=new_id)
        m2 = self.factory.reproduce(instance_id, new_id)
        self.factory.build(new_id)
        self.factory.start(new_id)
        h2 = self.health(new_id)
        rec("reproduce_ok", new_instance_id=new_id, healthy=h2.get("healthy"))
        handle = self.handle(new_id)
        handle.replaced_by = new_id
        return handle
