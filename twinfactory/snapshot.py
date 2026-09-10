"""Adapter over the M8 store: the factory reads architecture facts ONLY from an immutable ArchitectureSnapshot and its
normalized ServiceRecipes (archkit). Nothing here reads M7 reports or the running arena."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from archkit.store import SnapshotStore  # noqa: E402
from archkit.index import GraphIndex  # noqa: E402


class ArchitectureInputs:
    def __init__(self, store=None, snapshot_id=None):
        self.store = store or SnapshotStore()
        self.snapshot_id = self.store.resolve(snapshot_id or "current")
        doc = self.store.load(self.snapshot_id)
        self.body = doc["body"]
        self.index = GraphIndex(self.snapshot_id, self.body)
        self.recipe_index = self.store.recipes(self.snapshot_id)
        if not self.recipe_index:
            raise RuntimeError("snapshot %s has no recipes (run `python3 -m archkit recipes`)" % self.snapshot_id[:12])
        self.recipe_set_id = self.recipe_index["recipe_set_id"]
        self.recipes = {}
        for entry in self.recipe_index["recipes"]:
            r = self.store.recipe(entry["id"], self.snapshot_id)
            if r is None:
                raise RuntimeError("recipe file missing for %s" % entry["id"])
            self.recipes[entry["id"]] = r
        self.by_node = {r["graph_node"]: r["id"] for r in self.recipes.values() if r.get("graph_node")}

    def recipe(self, service):
        return self.recipes[service]

    def summary(self):
        return {"architecture_snapshot_id": self.snapshot_id, "recipe_set_id": self.recipe_set_id,
                "recipe_count": len(self.recipes), "schema_version": self.body.get("schema_version"),
                "source_lock_id": self.body.get("source_lock_id")}
