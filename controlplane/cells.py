"""Dynamic cells.

A cell is a durable grouping of related theses pursued on one assigned twin. The
scheduler creates cells as new thesis clusters appear, reallocates a cell's open
work to another twin when its twin fails, and retires a cell when its theses are
exhausted. Cells are folded last-writer-wins from ``cells.jsonl``.
"""
from .store import utc


class CellManager:
    def __init__(self, control):
        self.control = control

    def active(self):
        return [c for c in self.control.fold("cells") if c.get("status") == "active"]

    def get(self, cell_id):
        return self.control.get("cells", cell_id)

    def for_twin(self, twin):
        return [c for c in self.active() if c.get("twin") == twin]

    def create(self, twin, focus, theses=None, reason="new_cluster"):
        cid = self.control.next_id("cells", "CELL")
        self.control.put("cells", {
            "cell_id": cid, "twin": twin, "focus": focus,
            "theses": list(theses or []), "status": "active",
            "created_at": utc(), "created_reason": reason})
        self.control.event("cell_created", cell_id=cid, twin=twin, focus=focus, reason=reason)
        return cid

    def attach_thesis(self, cell_id, thesis_id):
        cell = self.get(cell_id)
        theses = list(cell.get("theses") or [])
        if thesis_id not in theses:
            theses.append(thesis_id)
            self.control.update("cells", cell_id, theses=theses)

    def reallocate(self, from_twin, to_twin, reason="twin_failed"):
        """Move every active cell on ``from_twin`` to ``to_twin`` and return the
        moved cell ids. The scheduler re-homes their open tasks."""
        moved = []
        for c in self.for_twin(from_twin):
            self.control.update("cells", c["cell_id"], twin=to_twin,
                                reallocated_from=from_twin, reallocated_reason=reason)
            self.control.event("cell_reallocated", cell_id=c["cell_id"],
                               frm=from_twin, to=to_twin, reason=reason)
            moved.append(c["cell_id"])
        return moved

    def retire(self, cell_id, reason="theses_exhausted"):
        self.control.update("cells", cell_id, status="retired", retired_reason=reason)
        self.control.event("cell_retired", cell_id=cell_id, reason=reason)

    def retire_exhausted(self):
        theses = {t["thesis_id"]: t for t in self.control.fold("theses")}
        retired = []
        for c in self.active():
            tids = c.get("theses") or []
            if tids and all(theses.get(t, {}).get("status") in ("exhausted", "retired") for t in tids):
                self.retire(c["cell_id"])
                retired.append(c["cell_id"])
        return retired
