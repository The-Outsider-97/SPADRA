"""P0-05 (report Table 16): undo and redo are coherent over a *chain* of multi-object transactions. Dependent geometry (family instances, walls with a
linked plan dimension, an array), references (a tag) and the project's identity move back and forward together, step by step, and every step returns to
exactly the state it had when it was made."""
from __future__ import annotations

import hashlib
import json

from applications.SPADRA import families as FM

from .test_bim import Case


def state(p) -> dict:
    """What a person sees, without the revision counter (which only ever moves forward) or history depth."""
    def digest(obj) -> str:
        return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()
    manifest = {k: v for k, v in p.manifest.items() if k not in ("revision", "commits", "modified")}
    return {"model": digest(p.model.as_dict()), "drawings": {k: digest(d.as_dict()) for k, d in sorted(p.drawings.items())}, "manifest": digest(manifest)}


class Chains(Case):
    def setUp(self) -> None:
        super().setUp()
        self.south = self.wall(a=(0, 0), b=(6000, 0))
        self.north = self.wall(a=(0, 4000), b=(6000, 4000))
        self.plan = next(v["id"] for v in self.p.manifest["views"] if v.get("level_id") == self.l1)
        self.fid = self.table()
        self.p.undo_stack.clear()                                                  # the chain starts from this model; set-up steps are not part of it

    def chain(self) -> list[tuple[str, dict]]:
        place = lambda x: ("family.place", {"family": self.fid, "level": self.l1, "point": [x, 2000]})
        return [
            ("draw.dimension", {"drawing": self.plan, "p1": [3000, 125], "p2": [3000, 3875], "dimline_point": [3500, 2000]}),
            place(1000), place(2000), place(3000),
            ("family.type_define", {"id": self.fid, "name": "Wide", "values": {"width": 2000}}),
            ("family.update", {"id": self.fid, "parameters": {"height": {"default": 900}}}),
            ("model.add_layer", {"id": self.south, "side": "left", "thickness": 50, "material": "Plasterboard"}),
            ("model.set_property", {"id": self.north, "key": "thickness", "value": 350}),
            ("model.move", {"id": self.north, "dx": 0, "dy": 0.25}),
            ("family.type_update", {"id": self.fid, "name": "Wide", "values": {"width": 1800}}),
        ]

    def test_every_step_of_a_long_chain_undoes_to_the_state_it_was_made_from_and_redoes_to_the_state_it_made(self) -> None:
        states = [state(self.p)]
        ops = self.chain()
        for op, params in ops:
            self.p.execute(op, params)
            states.append(state(self.p))
        self.assertEqual(len(set(json.dumps(s, sort_keys=True) for s in states)), len(states), "every command in the chain must change something")
        for i in range(len(ops), 0, -1):                                           # back, one transaction at a time
            self.p.undo()
            self.assertEqual(state(self.p), states[i - 1], f"undoing step {i} ({ops[i - 1][0]}) did not restore the state before it")
        with self.assertRaisesRegex(Exception, "Nothing to undo"):
            self.p.undo()
        for i in range(1, len(ops) + 1):                                           # and forward again
            self.p.redo()
            self.assertEqual(state(self.p), states[i], f"redoing step {i} ({ops[i - 1][0]}) did not reproduce the state it made")
        with self.assertRaisesRegex(Exception, "Nothing to redo"):
            self.p.redo()

    def test_a_new_edit_in_the_middle_of_the_history_discards_the_redo_branch_cleanly(self) -> None:
        ops = self.chain()
        for op, params in ops[:6]:
            self.p.execute(op, params)
        self.p.undo(), self.p.undo(), self.p.undo()
        middle = state(self.p)
        self.p.execute("model.set_property", {"id": self.north, "key": "thickness", "value": 300})
        self.assertEqual(self.p.redo_stack, [])
        self.p.undo()
        self.assertEqual(state(self.p), middle)

    def test_dependents_stay_consistent_with_their_sources_at_every_undo_step(self) -> None:
        """After each undo the linked dimension still sits on the wall faces the plan shows, and the instances have the width their type says."""
        from applications.SPADRA import dimension_links as DL
        results = [self.p.execute(op, params)["result"] for op, params in self.chain()]
        dim = results[0]["id"]
        for remaining in range(len(results), 0, -1):
            frames = DL._frames(self.p, self.plan)
            if self.p.drawing(self.plan).get(dim) is not None and self.p.drawing(self.plan).get(dim)["geometry"].get("p1"):
                g = self.p.drawing(self.plan).get(dim)["geometry"]
                self.assertAlmostEqual(g["p1"][1], frames[self.south]["a"][1] + frames[self.south]["hi"], 6, f"p1 left its face with {remaining} steps left")
                self.assertAlmostEqual(g["p2"][1], frames[self.north]["a"][1] + frames[self.north]["lo"], 6, f"p2 left its face with {remaining} steps left")
            for oid in FM.instances(self.p, self.fid):
                width = round(FM.values(self.p, oid)["width"], 6)
                self.assertIn(width, (1.2, 2.0, 1.8), f"instance {oid} has width {width} m, which no type or default gives")
            self.p.undo()
        self.assertEqual(FM.instances(self.p, self.fid), [])

    def test_the_commit_receipts_are_not_forgotten_by_undo(self) -> None:
        self.p.manifest.setdefault("commits", {})["k1"] = {"state": "confirmed"}
        self.p.execute("model.set_property", {"id": self.north, "key": "thickness", "value": 300})
        self.p.undo()
        self.assertEqual(self.p.manifest["commits"], {"k1": {"state": "confirmed"}})
        self.p.redo()
        self.assertEqual(self.p.manifest["commits"], {"k1": {"state": "confirmed"}})
