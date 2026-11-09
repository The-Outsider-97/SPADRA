"""Requirement in words -> typed family draft (report 8.2). A fixed pattern interpreter, so these tests pin what it recognises, what it reports as not understood, and
that it never chooses an engineering value invisibly: the report's own worked numbers (4.4-4.6) come out of the drafts, and nothing is created without a person's approval."""
from __future__ import annotations

import unittest

from applications.SPADRA import families as FM
from applications.SPADRA import family_nl as NL
from applications.SPADRA.assistant import ASSISTANT
from applications.SPADRA.project import Project
from applications.tools.errors import BadRequest, Conflict

from .harness import ServerCase

REPORT_WINDOW = "Create a configurable three-panel window with adjustable width and height"
ANSWERS = {"width": 1500, "height": 1200, "frame_width": 60, "mullion": 40}


def open_names(draft):
    return [u["parameter"] for u in draft["unresolved"] if u["blocking"]]


class Understanding(unittest.TestCase):
    def test_the_reports_example_request(self):
        d = NL.interpret(REPORT_WINDOW)
        self.assertEqual((d["status"], d["kind"], d["template"]), ("needs_input", "window", "window"))
        self.assertEqual([(e["parameter"], e["value"], e["basis"]) for e in d["extracted"]], [("panels", 3, "explicit")])
        self.assertEqual(d["adjustable"], ["height", "width"])
        params = d["family"]["parameters"]
        self.assertNotIn("scope", {k: v for k, v in params["width"].items() if k != "scope"})            # width and height may differ per placed window ...
        self.assertEqual((params["width"]["scope"], params["height"]["scope"]), ("instance", "instance"))
        self.assertEqual(params["frame_width"]["scope"], "type")                                           # ... and nothing else was made adjustable
        self.assertEqual(d["generator"], {"kind": "box", "verified": True, "note": d["generator"]["note"]})
        self.assertIn("procedural.lights", d["generator"]["note"])
        self.assertFalse(d["interpreter"]["language_model"])

    def test_it_asks_rather_than_choosing(self):
        d = NL.interpret(REPORT_WINDOW)
        self.assertEqual(open_names(d), ["width", "height", "frame_width", "mullion"])
        self.assertFalse(d["commit_allowed"])
        by_name = {u["parameter"]: u for u in d["unresolved"]}
        self.assertTrue(by_name["frame_width"]["question"])
        self.assertEqual(by_name["frame_width"]["template_default"], 60)                                    # a starting point, shown and not silently used
        self.assertFalse(by_name["max_panel_span"]["blocking"])                                            # the report's other example of an open choice
        self.assertIsNone(by_name["max_panel_span"]["template_default"])

    def test_answers_resolve_the_choices_and_give_the_reports_window_numbers(self):
        d = NL.interpret(REPORT_WINDOW, overrides=ANSWERS)
        self.assertEqual(d["status"], "draft")
        self.assertTrue(d["commit_allowed"])
        v = d["preview"]["values"]
        self.assertEqual((v["clear_span"], v["show_muntin"]), (1380.0, True))                              # section 4.5: 1500 - 2 * 60
        self.assertAlmostEqual(v["light_width"], 1300 / 3, places=6)                                       # (1380 - 80) / 3 = 433.33 mm
        self.assertEqual({e["parameter"]: e["basis"] for e in d["extracted"]}["frame_width"], "given")

    def test_door_numbers_from_the_report(self):
        d = NL.interpret("a single-leaf door 900 x 2100 with frame 35 mm and clearance 10 mm")
        self.assertEqual(d["status"], "draft")
        v = d["preview"]["values"]
        self.assertEqual((v["clear_width"], v["clearance_budget"], v["show_threshold"]), (830.0, 820.0, True))   # section 4.4, Table 5
        self.assertTrue(any("width x height" in a for a in d["assumptions"]))                              # the order of "900 x 2100" is stated as an assumption
        self.assertTrue(any("millimetres were assumed" in a for a in d["assumptions"]))

    def test_beam_numbers_from_the_report_and_what_is_not_modelled(self):
        d = NL.interpret("a timber beam 200 mm wide, 400 mm deep and 4 m span")
        self.assertEqual(d["status"], "draft")
        v = d["preview"]["values"]
        self.assertAlmostEqual(v["area"], 0.08)                                                            # m^2
        self.assertAlmostEqual(v["second_moment"] * 1e-12, 0.0010667, places=7)                             # mm^4 -> m^4, section 4.6
        self.assertEqual(d["preview"]["units"]["area"], "m2")
        self.assertEqual({e["parameter"]: e["value"] for e in d["extracted"]}, {"width": 200.0, "height": 400.0, "depth": 4000.0})
        self.assertIn("timber", [i["phrase"] for i in d["not_modelled"]])
        self.assertTrue(any("loads, supports and material" in a for a in d["assumptions"]))

    def test_units_and_ranges(self):
        d = NL.interpret("window 120 cm wide, 1.2 m high, 3 lights, frame 60 mm, mullion 40 mm, width between 900 and 1800 mm")
        self.assertEqual({e["parameter"]: e["value"] for e in d["extracted"] if e["parameter"] in ("width", "height")}, {"width": 1200.0, "height": 1200.0})
        self.assertEqual((d["family"]["parameters"]["width"]["min"], d["family"]["parameters"]["width"]["max"]), (900.0, 1800.0))
        self.assertEqual(d["status"], "draft")

    def test_a_range_without_a_default_leaves_the_default_open(self):
        d = NL.interpret("window width between 800 and 1600 mm, 1.2 m high, 2 lights")
        self.assertIn("width", open_names(d))
        self.assertEqual(d["family"]["parameters"]["width"]["min"], 800.0)

    def test_what_it_cannot_model_is_listed_not_dropped(self):
        d = NL.interpret("a triple-glazed window with u-value 1.1, fire rated, arched top, 1200 x 1500, 2 lights, frame 60, mullion 40")
        phrases = " | ".join(i["phrase"] for i in d["not_modelled"]).lower()
        for expected in ("triple-glaz", "u-value", "fire", "arch"):
            self.assertIn(expected, phrases)
        self.assertTrue(all(i["why"] for i in d["not_modelled"]))

    def test_the_door_hinge_is_reported_as_set_on_the_placed_door(self):
        d = NL.interpret("left hand door 900 x 2100 frame 35 mm clearance 10 mm")
        self.assertIn("hinge / swing", [i["phrase"] for i in d["not_modelled"]])

    def test_an_optional_span_limit_becomes_a_checked_rule(self):
        ok = NL.interpret("window 1200 x 1500, 2 lights, frame 50, mullion 40, max panel span 700 mm")
        self.assertEqual(ok["status"], "draft")
        self.assertTrue(ok["preview"]["values"]["span_ok"])
        self.assertEqual(ok["preview"]["values"]["light_width"], 530.0)
        self.assertNotIn("max_panel_span", [u["parameter"] for u in ok["unresolved"]])
        bad = NL.interpret("window 1200 x 1500, 3 lights, frame 60, mullion 40, max panel span 300 mm")      # lights come out at 333 mm: wider than the limit
        self.assertEqual(bad["status"], "invalid")
        self.assertFalse(bad["commit_allowed"])
        self.assertIn("light_width", bad["preview"]["refused"])

    def test_a_value_outside_the_template_range_is_a_visible_choice(self):
        d = NL.interpret("window 5000 mm wide, 1200 mm high, 3 lights, frame 60, mullion 40")
        self.assertEqual(d["family"]["parameters"]["width"]["max"], 5000.0)
        self.assertIn("width", open_names(d))
        self.assertFalse(d["commit_allowed"])

    def test_same_text_same_draft(self):
        self.assertEqual(NL.interpret(REPORT_WINDOW), NL.interpret(REPORT_WINDOW))

    def test_unsupported_and_ambiguous_requests_say_so(self):
        d = NL.interpret("a spiral staircase")
        self.assertEqual((d["status"], d["commit_allowed"], d["kind"]), ("unsupported", False, None))
        self.assertEqual(sorted(d["supported"]), ["beam", "door", "window"])
        both = NL.interpret("a window next to a door")
        self.assertEqual(both["status"], "unsupported")
        self.assertIn("more than one", both["reason"])

    def test_bad_input_is_refused(self):
        for text in ("", "   ", "x" * 2001, None):
            with self.assertRaises(BadRequest):
                NL.interpret(text)
        for text, extra in (("door 900 x 2100 frame 450 mm clearance 10 mm", {}), ("window with 20 lights", {}), ("window width between 1600 and 800 mm", {}),
                            (REPORT_WINDOW, {"overrides": {"nonsense": 1}})):
            with self.subTest(text=text):
                with self.assertRaises(BadRequest):
                    NL.interpret(text, **extra)

    def test_creating_the_draft_touches_nothing(self):
        p = Project.create("N")
        revision = p.revision
        NL.interpret(REPORT_WINDOW, overrides=ANSWERS)
        self.assertEqual((p.revision, FM.families(p)), (revision, []))


class Proposal(unittest.TestCase):
    def setUp(self):
        self.p = Project.create("N")

    def test_open_choices_block_the_proposal_and_are_listed(self):
        with self.assertRaises(BadRequest) as ctx:
            NL.propose(self.p, REPORT_WINDOW)
        self.assertEqual([u["parameter"] for u in ctx.exception.details["unresolved"]], ["width", "height", "frame_width", "mullion"])
        self.assertEqual(FM.families(self.p), [])

    def test_accepting_a_default_is_explicit_and_recorded(self):
        out = NL.propose(self.p, REPORT_WINDOW, accept=["width", "height", "frame_width", "mullion"])
        text = out["proposal"]["proposal"]["explanation"]
        self.assertIn("Accepted at its template default: frame_width", text)
        self.assertEqual(FM.families(self.p), [])                                                           # a proposal changes nothing
        with self.assertRaises(BadRequest):
            NL.propose(self.p, REPORT_WINDOW, accept=["width", "height", "frame_width"])                    # one still open

    def test_a_proposal_needs_approval_then_creates_the_family_and_can_be_undone(self):
        out = NL.propose(self.p, REPORT_WINDOW, overrides=ANSWERS)
        prop = out["proposal"]["proposal"]
        self.assertEqual(prop["operation"], ["family.define"])
        self.assertEqual(FM.families(self.p), [])
        with self.assertRaises(Conflict):
            ASSISTANT.run(self.p, prop["id"])                                                                 # no consent, no run
        ASSISTANT.approve(self.p, prop["id"])
        fam = FM.describe(self.p, FM.families(self.p)[0])
        by = {r["name"]: r for r in fam["parameters"]}
        self.assertEqual((by["width"]["default"], by["clear_span"]["expression"]), (1500.0, "width - 2 * frame_width"))
        values = FM.values(self.p, self.p.model.instantiate(fam["id"], {}, name="check"))                   # SGEO's own evaluation of the committed family
        self.assertAlmostEqual(values["clear_span"], 1.380, places=9)
        self.assertEqual(by["width"]["scope"], "instance")
        self.assertEqual(by["frame_width"]["scope"], "type")
        self.p.undo()
        self.assertEqual(FM.families(self.p), [])

    def test_a_rejected_proposal_creates_nothing(self):
        out = NL.propose(self.p, REPORT_WINDOW, overrides=ANSWERS)
        ASSISTANT.reject(self.p, out["proposal"]["proposal"]["id"])
        self.assertEqual(FM.families(self.p), [])

    def test_a_proposal_made_before_another_change_is_stale(self):
        out = NL.propose(self.p, REPORT_WINDOW, overrides=ANSWERS)
        self.p.execute("family.from_template", {"template": "beam"})
        with self.assertRaises(Conflict):
            ASSISTANT.approve(self.p, out["proposal"]["proposal"]["id"])
        self.assertEqual(len(FM.families(self.p)), 1)

    def test_unsupported_and_invalid_drafts_are_not_proposed(self):
        with self.assertRaises(BadRequest):
            NL.propose(self.p, "a spiral staircase")
        with self.assertRaises(BadRequest):
            NL.propose(self.p, "window 1200 x 1500, 3 lights, frame 60, mullion 40, max panel span 300 mm")

    def test_the_family_name_stays_unique(self):
        for _ in range(2):
            out = NL.propose(self.p, REPORT_WINDOW, overrides=ANSWERS)
            ASSISTANT.approve(self.p, out["proposal"]["proposal"]["id"])
        self.assertEqual(sorted(f["name"] for f in FM.families(self.p)), ["Window", "Window 2"])


class Http(ServerCase):
    def test_draft_and_propose_routes(self):
        a = self.api
        a.post("/api/project/new", {"name": "N", "unit": "mm"})
        d = a.post("/api/families/draft", {"text": REPORT_WINDOW})
        self.assertEqual((d["status"], d["kind"]), ("needs_input", "window"))
        status, out = a.call("POST", "/api/families/draft/propose", {"text": REPORT_WINDOW})
        self.assertEqual(status, 400)
        self.assertEqual([u["parameter"] for u in out["error"]["details"]["unresolved"]][:2], ["width", "height"])
        out = a.post("/api/families/draft/propose", {"text": REPORT_WINDOW, "overrides": ANSWERS})
        self.assertEqual(out["message"]["proposal"]["status"], "pending")
        self.assertEqual(a.get("/api/families")["families"], [])
        self.assertEqual(a.call("POST", "/api/families/draft", {"text": ""})[0], 400)


if __name__ == "__main__":
    unittest.main()
