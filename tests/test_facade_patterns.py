"""Façade patterns beyond the plain grid: running bond (every other row shifted) and a rhythm of listed panel widths. The panels are read back from the stored elements,
not from the generator's own bookkeeping, so the checks are about the geometry that exists: coverage, joints, openings, stable ids and what is refused."""
from __future__ import annotations

import math
import unittest

from applications.SPADRA import elements as E
from applications.SPADRA import procedural as PG
from applications.SPADRA.project import Project
from applications.tools.errors import BadRequest

JOINT = 10


class Base(unittest.TestCase):
    def setUp(self):
        self.p = Project.create("Facade patterns")
        self.l0 = self.p.manifest["levels"][0]["id"]

    def x(self, op, **kw):
        return self.p.execute(op, kw)["result"]

    def wall(self, length=6000, height=3.0):
        return self.x("model.add_wall", level=self.l0, start=[0, 0], end=[length, 0], thickness=200, height=height)["id"]

    def rect_of(self, oid, wall):
        obj = self.p.model.get(oid)
        t, size = obj["transform"], obj["geometry"]["size"]
        f = E.wall_frame(self.p, wall)
        u, v = t[3] - f["start"][0], t[11] - f["base"]
        return (u - size[0] / 2, u + size[0] / 2, v - size[2] / 2, v + size[2] / 2)

    def rows(self, group, wall):
        """The panels of a group by row, each row as (u0, u1) sorted along the wall, from the stored elements."""
        by_row: dict[int, list[tuple[float, float]]] = {}
        for key, oid in PG.group(self.p, group["id"])["members"].items():
            row = int(key.split(":")[1])
            u0, u1, _, _ = self.rect_of(oid, wall)
            by_row.setdefault(row, []).append((u0, u1))
        return {k: sorted(v) for k, v in sorted(by_row.items())}

    def facade(self, wall, **kw):
        params = {"panel_width": 1200, "panel_height": 1500, "joint": JOINT, **kw}
        return self.x("procedural.facade", wall=wall, **params)


class Grid(Base):
    def test_the_default_is_the_grid_it_always_was(self):
        w = self.wall()
        g = self.facade(w)
        self.assertEqual((g["report"]["pattern"], g["report"]["columns"], g["report"]["rows"], g["report"]["panels"]), ("grid", 5, 2, 10))
        rows = self.rows(g, w)
        self.assertEqual({len(r) for r in rows.values()}, {5})
        self.assertEqual(rows[0], rows[1])                                    # every row has the same columns


class RunningBond(Base):
    def test_odd_rows_are_shifted_by_half_a_panel_and_end_in_partial_panels(self):
        w = self.wall()
        g = self.facade(w, pattern="running_bond")
        rows = self.rows(g, w)
        self.assertEqual((len(rows[0]), len(rows[1])), (5, 6))
        first, last = rows[1][0], rows[1][-1]
        self.assertAlmostEqual(first[1] - first[0], 0.6 - JOINT / 1000, places=6)         # half a panel, less the joint (half a joint on each side of its cell)
        self.assertAlmostEqual(last[1] - last[0], 0.6 - JOINT / 1000, places=6)
        self.assertEqual(g["report"]["panels"], 11)

    def test_vertical_joints_do_not_line_up_between_adjacent_rows(self):
        w = self.wall()
        rows = self.rows(self.facade(w, pattern="running_bond"), w)

        def joints(row):
            return {round((a[1] + b[0]) / 2, 6) for a, b in zip(row, row[1:])}

        self.assertEqual(joints(rows[0]) & joints(rows[1]), set())

    def test_every_row_covers_the_wall_with_only_the_joints_left(self):
        w = self.wall()
        for offset in (0.5, 0.25, 0.8):
            with self.subTest(offset=offset):
                self.setUp()
                w = self.wall()
                rows = self.rows(self.facade(w, pattern="running_bond", bond_offset=offset), w)
                for row in rows.values():
                    covered = sum(b - a for a, b in row)
                    gaps = sum(b[0] - a[1] for a, b in zip(row, row[1:]))
                    self.assertAlmostEqual(covered + gaps + JOINT / 2000 * 2, 6.0, places=6)        # panels + joints between them + the half joints at the two ends
                    self.assertAlmostEqual(gaps, JOINT / 1000 * (len(row) - 1), places=6)

    def test_panels_are_cut_around_openings_and_nothing_overlaps(self):
        w = self.wall()
        win = self.x("model.add_window", wall=w, offset=3.0, width=1200, height=1200, sill=900)["id"]
        g = self.facade(w, pattern="running_bond")
        x0, x1, z0, z1 = E.opening_span(E.meta_of(self.p, win))
        rects = [self.rect_of(oid, w) for oid in g["members"].values()]
        for u0, u1, v0, v1 in rects:
            self.assertTrue(u1 <= x0 + 1e-6 or u0 >= x1 - 1e-6 or v1 <= z0 + 1e-6 or v0 >= z1 - 1e-6)
        for i, a in enumerate(rects):
            for b in rects[i + 1:]:
                self.assertTrue(a[1] <= b[0] + 1e-9 or b[1] <= a[0] + 1e-9 or a[3] <= b[2] + 1e-9 or b[3] <= a[2] + 1e-9)
        self.assertGreater(g["report"]["panels_cut_by_openings"], 0)

    def test_switching_pattern_keeps_the_ids_of_unchanged_panels_and_undo_restores(self):
        w = self.wall()
        g = self.facade(w)
        before = dict(PG.group(self.p, g["id"])["members"])
        self.x("procedural.update", id=g["id"], pattern="running_bond")
        after = PG.group(self.p, g["id"])["members"]
        self.assertEqual({k: after[k] for k in before if k.split(":")[1] == "0"}, {k: v for k, v in before.items() if k.split(":")[1] == "0"})       # row 0 did not move
        self.assertNotEqual(len(after), len(before))
        self.p.undo()
        self.assertEqual(PG.group(self.p, g["id"])["members"], before)

    def test_it_follows_its_wall_when_an_opening_is_added(self):
        w = self.wall()
        g = self.facade(w, pattern="running_bond")
        before = PG.group(self.p, g["id"])["report"]
        self.assertEqual(before["panels_cut_by_openings"], 0)
        self.x("model.add_window", wall=w, offset=3.0, width=1200, height=1200, sill=900)          # the wall changed: the group regenerates inside the same command
        after = PG.group(self.p, g["id"])["report"]
        self.assertEqual(after["pattern"], "running_bond")
        self.assertGreater(after["panels_cut_by_openings"], 0)


class Rhythm(Base):
    def test_the_listed_widths_repeat_along_the_wall(self):
        w = self.wall()
        g = self.facade(w, pattern="rhythm", rhythm=[900, 1200, 900])
        rows = self.rows(g, w)
        widths = [round(b - a + JOINT / 1000, 6) for a, b in rows[0]]
        self.assertEqual(widths, [0.9, 1.2, 0.9, 0.9, 1.2, 0.9])                                  # 6 x the 3-panel rhythm exactly fills 6 m
        self.assertEqual(rows[0], rows[1])

    def test_the_last_panel_is_trimmed_to_the_wall(self):
        w = self.wall(length=6500)
        rows = self.rows(self.facade(w, pattern="rhythm", rhythm=[900, 1200, 900]), w)
        widths = [round(b - a + JOINT / 1000, 6) for a, b in rows[0]]
        self.assertEqual(widths[-1], 0.5)
        self.assertAlmostEqual(sum(widths), 6.5, places=6)

    def test_a_sliver_at_the_end_is_folded_into_the_last_panel(self):
        w = self.wall(length=6030)
        rows = self.rows(self.facade(w, pattern="rhythm", rhythm=[900, 1200, 900]), w)
        widths = [round(b - a + JOINT / 1000, 6) for a, b in rows[0]]
        self.assertEqual(len(widths), 6)
        self.assertAlmostEqual(widths[-1], 0.93, places=6)                                         # 900 + the 30 mm that would have been a sliver
        self.assertTrue(all(wd >= PG.MIN_PIECE for wd in widths))

    def test_the_rhythm_can_be_changed_and_cleared_by_changing_pattern(self):
        w = self.wall()
        g = self.facade(w, pattern="rhythm", rhythm=[900, 1200])
        self.x("procedural.update", id=g["id"], rhythm=[1500])
        self.assertEqual({round(b - a + JOINT / 1000, 6) for a, b in self.rows(g, w)[0]}, {1.5})
        self.x("procedural.update", id=g["id"], pattern="grid")
        self.assertNotIn("rhythm", PG.group(self.p, g["id"])["params"])
        self.assertEqual(len(self.rows(g, w)[0]), 5)


class Refusals(Base):
    def refused(self, **kw):
        w = self.wall()
        rev, members = self.p.revision, len(self.p.model.query_ids(limit=2000))
        with self.assertRaises(BadRequest) as cm:
            self.facade(w, **kw)
        self.assertEqual((self.p.revision, len(self.p.model.query_ids(limit=2000))), (rev, members))        # nothing was created
        return str(cm.exception)

    def test_unknown_pattern(self):
        self.assertIn("pattern must be one of", self.refused(pattern="herringbone"))

    def test_rhythm_pattern_needs_a_rhythm(self):
        self.assertIn("needs a rhythm", self.refused(pattern="rhythm"))

    def test_a_rhythm_without_the_pattern(self):
        self.assertIn("only applies to the rhythm pattern", self.refused(rhythm=[900]))

    def test_a_bond_offset_without_running_bond(self):
        self.assertIn("running_bond", self.refused(bond_offset=0.3))

    def test_bad_rhythm_values(self):
        for r in ([], [10], [900] * 17, "900", [900, "x"], [900, float("nan")]):
            with self.subTest(r=r):
                self.setUp()
                self.refused(pattern="rhythm", rhythm=r)

    def test_bad_bond_offset(self):
        for v in (0, 1, -0.2, 2):
            with self.subTest(v=v):
                self.setUp()
                self.refused(pattern="running_bond", bond_offset=v)

    def test_a_joint_wider_than_a_rhythm_panel(self):
        self.assertIn("joint", self.refused(pattern="rhythm", rhythm=[900, 60], joint=100))

    def test_too_many_columns(self):
        w = self.wall(length=300000)                                                               # 300 m of 50 mm panels is 6000 columns: refused before anything is made
        rev = self.p.revision
        with self.assertRaises(BadRequest):
            self.x("procedural.facade", wall=w, panel_width=1200, panel_height=1500, joint=0, pattern="rhythm", rhythm=[50])
        self.assertEqual(self.p.revision, rev)


if __name__ == "__main__":
    unittest.main()
