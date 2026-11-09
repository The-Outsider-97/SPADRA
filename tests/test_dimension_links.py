"""Dimensions placed on wall faces follow those faces when the wall changes (a layer added, a thicker wall, a moved wall)."""
from __future__ import annotations

import math

from .test_bim import Case


class DimensionLinks(Case):
    def setUp(self) -> None:
        super().setUp()
        self.south = self.wall(a=(0, 0), b=(6000, 0))
        self.north = self.wall(a=(0, 4000), b=(6000, 4000))
        self.plan = next(v["id"] for v in self.p.manifest["views"] if v.get("level_id") == self.l1)

    def dimension(self, a, b, at=(3500, 2000)):
        return self.run_("draw.dimension", drawing=self.plan, p1=list(a), p2=list(b), dimline_point=list(at))["id"]

    def geometry(self, eid):
        return self.p.drawing(self.plan).get(eid)["geometry"]

    def length(self, eid):
        g = self.geometry(eid)
        return math.dist(g["p1"], g["p2"])

    def test_a_dimension_between_two_wall_faces_follows_a_layer_added_to_a_wall(self) -> None:
        room = self.dimension((3000, 125), (3000, 3875))                      # the faces that look into the room
        self.assertAlmostEqual(self.length(room), 3750)
        self.run_("model.add_layer", id=self.south, side="left", thickness=50, material="Plasterboard")
        self.assertAlmostEqual(self.geometry(room)["p1"][1], 150)             # the wall's face moved by 25 mm, so did the dimension's point
        self.assertAlmostEqual(self.geometry(room)["p2"][1], 3875)            # the other wall did not change
        self.assertAlmostEqual(self.length(room), 3725)

    def test_undo_removes_a_dimension_that_was_linked_while_it_was_drawn(self) -> None:
        """Linking changes the plan the command itself edits: Undo must still take the whole dimension away (a later copy of that plan must not win)."""
        before = len(self.p.drawing(self.plan).as_dict()["entities"])
        room = self.dimension((3000, 125), (3000, 3875))
        self.assertIsNotNone(self.p.drawing(self.plan).get(room).get("metadata", {}).get("spadra:anchors"))
        self.p.undo()
        self.assertEqual(len(self.p.drawing(self.plan).as_dict()["entities"]), before)
        self.p.redo()
        self.assertEqual(len(self.p.drawing(self.plan).as_dict()["entities"]), before + 1)

    def test_the_dimension_line_keeps_its_place_and_one_undo_puts_wall_and_dimension_back(self) -> None:
        room = self.dimension((3000, 125), (3000, 3875), at=(3500, 2000))
        before = self.geometry(room)
        self.run_("model.add_layer", id=self.south, side="left", thickness=50, material="Plasterboard")
        self.assertAlmostEqual(self.geometry(room)["dimline_point"][1], 2000 + 25 / 2)    # moved by the average of what its points moved
        self.p.undo()
        self.assertEqual(self.geometry(room), before)
        self.p.redo()
        self.assertAlmostEqual(self.geometry(room)["p1"][1], 150)

    def test_it_also_follows_a_wall_made_thicker_and_a_wall_that_moves(self) -> None:
        room = self.dimension((3000, 125), (3000, 3875))
        self.run_("model.set_property", id=self.south, key="thickness", value=400)
        self.assertAlmostEqual(self.geometry(room)["p1"][1], 200)
        self.run_("model.set_property", id=self.north, key="thickness", value=300)
        self.assertAlmostEqual(self.geometry(room)["p2"][1], 4000 - 150)
        self.run_("model.move", id=self.north, dx=0, dy=0.5)      # metres
        self.assertAlmostEqual(self.geometry(room)["p2"][1], 4500 - 150)
        self.assertAlmostEqual(self.length(room), 4500 - 150 - 200)

    def test_a_dimension_drawn_before_this_existed_is_linked_at_the_next_model_change(self) -> None:
        doc = self.p.drawing(self.plan)
        old = doc.add_dimension_aligned((3000, 125), (3000, 3875), (3500, 2000))           # bypasses the command that links
        self.assertIsNone(doc.get(old).get("metadata", {}).get("spadra:anchors"))
        self.run_("model.add_layer", id=self.south, side="left", thickness=50, material="Plasterboard")
        self.assertAlmostEqual(self.geometry(old)["p1"][1], 150)

    def test_points_that_are_not_on_a_wall_are_left_alone_and_the_axis_is_a_face_too(self) -> None:
        free = self.dimension((1000, 1000), (2000, 1000), at=(1500, 1300))
        centre = self.dimension((1000, 0), (1000, 4000), at=(1200, 2000))                  # centre line to centre line
        self.run_("model.add_layer", id=self.south, side="left", thickness=50, material="Plasterboard")
        self.run_("model.set_property", id=self.north, key="thickness", value=500)
        self.assertEqual((self.geometry(free)["p1"], self.geometry(free)["p2"]), ([1000, 1000], [2000, 1000]))
        self.assertEqual((self.geometry(centre)["p1"], self.geometry(centre)["p2"]), ([1000, 0], [1000, 4000]))

    def test_dragging_a_point_off_the_wall_lets_the_link_go(self) -> None:
        room = self.dimension((3000, 125), (3000, 3875))
        g = self.geometry(room)
        self.run_("draw.set_geometry", drawing=self.plan, id=room, geometry={"p1": [3000, 800], "p2": g["p2"], "dimline_point": g["dimline_point"]})
        self.run_("model.add_layer", id=self.south, side="left", thickness=50, material="Plasterboard")
        self.assertEqual(self.geometry(room)["p1"], [3000, 800])

    def test_a_deleted_wall_leaves_the_dimension_where_it_was(self) -> None:
        room = self.dimension((3000, 125), (3000, 3875))
        self.run_("model.delete", id=self.south)
        self.assertEqual(self.geometry(room)["p1"], [3000, 125])
