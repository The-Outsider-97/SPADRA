"""Copy and mirror properties: modifiers, connections and the reference plane.

A bend goes to another object and flips with the faces; what cannot go (a modifier that points at other elements) is said, not half-copied. A wall takes
the same roof, level or floor as its source; a pitched roof takes the source's end connections, eave for eave, and swaps eave when mirrored. A plane
along the element's length means its sides, one square to it its ends; an oblique plane is refused and nothing changes."""
from __future__ import annotations

import importlib.util
import unittest
from unittest import mock

from applications.SPADRA import elements as E
from applications.SPADRA.project import NS, Project
from applications.SPADRA.tests import profiler_env  # noqa: F401
from applications.tools.errors import BadRequest

SQUARE = [[0, 0], [4000, 0], [4000, 4000], [0, 4000]]
LONG = [[0, 0], [6000, 0], [6000, 3000], [0, 3000]]


class Case(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project.create("Transfer 2")
        self.l1, self.l2, self.l3 = (l["id"] for l in self.p.manifest["levels"])

    def do(self, op, **params):
        return self.p.execute(op, params)["result"]

    def meta(self, oid):
        return self.p.model.get(oid)["metadata"]

    def block(self, x=0):
        return self.do("model.add_extrusion", level=self.l1, points=[[x, 0], [x + 3000, 0], [x + 3000, 500], [x, 500]], depth=0.4)["id"]

    def bend(self, oid, angle=0.8):
        """A bend record as the modifier command would write it (the mesh kernel is not needed to *record* one)."""
        self.p.model.add_modifier(oid, kind="sgeo.bend", parameters={"axis": "x", "start": 0.0, "end": 3.0, "angle": angle, "segments": 8},
                                  evaluation_policy={"mode": "display_only"})

    def kernel_ok(self):
        """What the mesh kernel answers when it can evaluate every modifier: the transfer itself is under test, not trimesh."""
        fake = lambda model: [{"modifier_id": m["id"], "status": "ok", "result": {"geometry": {"kind": "mesh"}}} for m in model.modifiers()]
        return mock.patch.object(type(self.p.model), "evaluate_modifiers", fake)

    def mods(self, oid, kind="sgeo.bend"):
        return [m for m in self.p.model.modifiers() if m["target"]["item_id"] == oid and m["kind"] == kind]

    def paste(self, source, targets, groups, **kw):
        return self.do("model.paste_properties", source=source, destinations=targets, groups=list(groups), **kw)


class Modifiers(Case):
    def test_a_bend_goes_to_another_object_and_leaves_the_original_alone(self):
        a, b = self.block(), self.block(6000)
        self.bend(a, 0.8)
        with self.kernel_ok():
            out = self.paste(a, [b], ["modifiers"])
        self.assertIn("modifier:bend", out["applied"][0]["changed"])
        self.assertEqual([m["parameters"]["angle"] for m in self.mods(b)], [0.8])
        self.assertEqual([m["parameters"]["angle"] for m in self.mods(a)], [0.8])
        self.assertNotEqual(self.mods(a)[0]["id"], self.mods(b)[0]["id"])                           # independent: editing one never moves the other

    def test_pasting_again_replaces_the_bend_rather_than_stacking_it(self):
        a, b = self.block(), self.block(6000)
        self.bend(a, 0.8)
        self.bend(b, 0.2)
        with self.kernel_ok():
            self.paste(a, [b], ["modifiers"])
        self.assertEqual([m["parameters"]["angle"] for m in self.mods(b)], [0.8])

    def test_the_bend_turns_the_other_way_when_the_faces_are_mirrored_and_not_when_the_ends_are(self):
        a, b, c = self.block(), self.block(6000), self.block(12000)
        self.bend(a, 0.8)
        with self.kernel_ok():
            self.paste(a, [b], ["modifiers"], mode="mirror", axis="side")
            out = self.paste(a, [c], ["modifiers"], mode="mirror", axis="length")
        self.assertEqual([m["parameters"]["angle"] for m in self.mods(b)], [-0.8])
        self.assertEqual([m["parameters"]["angle"] for m in self.mods(c)], [0.8])
        self.assertTrue(any("bend span" in x for x in out["applied"][0]["no_equivalent"]))          # said, not silently pretended

    def test_modifiers_that_point_at_other_elements_are_named_and_left_out(self):
        a, b, cutter = self.block(), self.block(6000), self.block(12000)
        self.p.model.add_modifier(a, kind="sgeo.boolean", operands=[cutter], parameters={"operation": "subtract"})
        self.bend(a)
        with self.kernel_ok():
            out = self.paste(a, [b], ["modifiers"])
        self.assertEqual(len(self.mods(b)), 1)
        self.assertTrue(any("boolean" in x for x in out["applied"][0]["no_equivalent"]))

    def test_a_target_the_modifier_does_not_fit_is_reported_failed_and_put_back_exactly(self):
        a, b = self.block(), self.block(6000)
        self.bend(a)
        failing = lambda model: [{"modifier_id": m["id"], "status": "error", "result": None} for m in model.modifiers()]
        digest = self.p.model.content_digest()
        with mock.patch.object(type(self.p.model), "evaluate_modifiers", failing):
            out = self.paste(a, [b], ["modifiers"])
        self.assertEqual([f["id"] for f in out["failed"]], [b])
        self.assertEqual(self.mods(b), [])
        self.assertEqual(self.p.model.content_digest(), digest)

    def test_no_element_is_created_or_moved(self):
        a, b = self.block(), self.block(6000)
        self.bend(a)
        count, points = len(E.element_ids(self.p)), self.meta(b).get(f"{NS}:points")
        with self.kernel_ok():
            self.paste(a, [b], ["modifiers"])
        self.assertEqual(len(E.element_ids(self.p)), count)
        self.assertEqual(self.meta(b).get(f"{NS}:points"), points)

    @unittest.skipUnless(importlib.util.find_spec("trimesh") and importlib.util.find_spec("numpy"), "a bend needs numpy and trimesh, which are not installed here")
    def test_with_the_real_mesh_kernel_the_pasted_bend_evaluates(self):
        a, b = self.block(), self.block(6000)
        self.do("model.modifier", id=a, operation="bend", parameters={"axis": "x", "start": 0.0, "end": 3.0, "angle": 0.8, "segments": 8})
        self.assertEqual(self.paste(a, [b], ["modifiers"])["failed"], [])
        self.assertTrue(all(i["status"] == "ok" for i in self.p.model.evaluate_modifiers() if i["modifier_id"] in {m["id"] for m in self.mods(b)}))


class Connections(Case):
    def wall(self, y=0):
        return self.do("model.add_wall", start=[500, y + 500], end=[3500, y + 500], level=self.l1, thickness=200, height=3.2)["id"]

    def roof(self, kind="gable", pts=LONG, level=None, **kw):
        return self.do("model.add_roof", level=level or self.l1, points=pts, roof=kind, **kw)["id"]

    def test_a_wall_takes_the_same_roof_and_offset_as_its_source(self):
        roof = self.roof(level=self.l3, pitch=30, thickness=100)
        a, b = self.wall(0), self.wall(1000)
        self.do("model.attach_wall", ids=[a], to="roof", roof=roof)
        self.assertIsNone(self.meta(b).get(f"{NS}:top_to"))
        out = self.paste(a, [b], ["connections"])
        self.assertIn("top attachment", out["applied"][0]["changed"])
        top = self.meta(b)[f"{NS}:top_to"]
        self.assertEqual((top["kind"], top["roof"]), ("roof", roof))
        self.assertEqual(top["offset"], self.meta(a)[f"{NS}:top_to"]["offset"])

    def test_a_wall_takes_the_same_level_attachment(self):
        a, b = self.wall(0), self.wall(1000)
        self.do("model.attach_wall", ids=[a], to="level", level=self.l2)
        self.paste(a, [b], ["connections"])
        top = self.meta(b)[f"{NS}:top_to"]
        self.assertEqual((top["kind"], top["level"]), ("level", self.l2))

    def test_roof_end_connections_arrive_eave_for_eave_and_swap_eave_when_mirrored(self):
        a, b, c = self.roof(), self.roof(pts=[[0, 5000], [6000, 5000], [6000, 8000], [0, 8000]]), self.roof(pts=[[0, 9000], [6000, 9000], [6000, 12000], [0, 12000]])
        eaves = E.roof_eave_edges(self.meta(a))
        self.assertEqual(len(eaves), 2)
        self.do("model.set_roof_end", id=a, edge=eaves[0], type="B2")
        self.paste(a, [b], ["connections"])
        self.assertEqual(sorted(int(k) for k in self.meta(b)[f"{NS}:roof_ends"]), [eaves[0]])
        self.assertEqual(self.meta(b)[f"{NS}:roof_ends"][str(eaves[0])]["type"], "B2")
        self.paste(a, [c], ["connections"], mode="mirror", axis="side")
        self.assertEqual(sorted(int(k) for k in self.meta(c)[f"{NS}:roof_ends"]), [eaves[1]])      # the other eave now carries it

    def test_a_flat_roof_is_told_it_has_no_end_connections_to_take(self):
        a, flat = self.roof(), self.roof("flat", pts=[[0, 5000], [4000, 5000], [4000, 9000], [0, 9000]])
        self.do("model.set_roof_end", id=a, edge=E.roof_eave_edges(self.meta(a))[0], type="B2")
        out = self.paste(a, [flat], ["connections"])
        self.assertEqual(out["applied"][0]["changed"], [])
        self.assertTrue(any("end connections" in x for x in out["applied"][0]["no_equivalent"]))


class Plane(Case):
    def wall(self, y=0):
        return self.do("model.add_wall", start=[0, y], end=[6000, y], level=self.l1, thickness=250, height=3.2)["id"]

    def test_a_plane_along_the_wall_means_its_sides_and_one_across_it_means_its_ends(self):
        w = self.wall()
        self.assertEqual(PT_axis(self.p, w, [0, 100], [100, 100]), "side")
        self.assertEqual(PT_axis(self.p, w, [3000, 0], [3000, 5]), "length")

    def test_an_oblique_plane_is_refused_and_nothing_changes(self):
        a, b = self.wall(0), self.wall(5000)
        self.do("model.add_layer", id=a, material="Mineral wool", thickness=100)
        revision = self.p.revision
        with self.assertRaises(BadRequest) as why:
            self.paste(a, [b], ["construction"], mode="mirror", plane=[[0, 0], [1000, 1000]])
        self.assertIn("oblique", str(why.exception.hint or "") + str(why.exception))
        self.assertEqual(self.p.revision, revision)

    def test_a_plane_reverses_the_layers_like_the_side_axis(self):
        a, b, c = self.wall(0), self.wall(5000), self.wall(9000)
        self.do("model.add_layer", id=a, material="Mineral wool", thickness=100)
        self.paste(a, [b], ["construction"], mode="mirror", axis="side")
        out = self.paste(a, [c], ["construction"], mode="mirror", plane=[[0, 2000], [6000, 2000]])
        self.assertEqual(out["axis"], "side")
        self.assertTrue(out["by_plane"])
        from applications.SPADRA import layers as LY
        self.assertEqual([r["material"] for r in LY.stack(self.p, b)], [r["material"] for r in LY.stack(self.p, c)])

    def test_an_element_with_no_direction_cannot_be_mirrored_across_a_plane(self):
        floor = self.do("model.add_floor", level=self.l1, points=SQUARE)["id"]
        other = self.do("model.add_floor", level=self.l1, points=[[5000, 0], [9000, 0], [9000, 4000], [5000, 4000]])["id"]
        with self.assertRaises(BadRequest):
            self.paste(floor, [other], ["dimensions"], mode="mirror", plane=[[0, 0], [0, 1000]])

    def test_the_transfer_info_says_whether_a_plane_can_be_used(self):
        from applications.SPADRA import property_transfer as PT
        w = self.wall()
        self.assertTrue(PT.describe(self.p, w)["has_direction"])
        self.assertTrue({"modifiers", "connections"} <= {g["id"] for g in PT.describe(self.p, w)["groups"]})


def PT_axis(project, oid, a, b):
    from applications.SPADRA import property_transfer as PT
    return PT.axis_for_plane(project, oid, a, b)


if __name__ == "__main__":
    unittest.main()
