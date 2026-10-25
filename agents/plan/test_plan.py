"""Offline. No network, no Claude, no downloaded listing."""
import unittest

from PIL import Image, ImageDraw

import shell
import validate


def two_room_plan():
    """An original line drawing: two rooms, one shared wall, one door. Not a listing image."""
    im = Image.new("L", (240, 160), 255)
    d = ImageDraw.Draw(im)
    t = 8
    d.rectangle([20, 20, 200, 20 + t], fill=0)
    d.rectangle([20, 20, 20 + t, 108], fill=0)
    d.rectangle([192, 20, 200, 108], fill=0)
    d.rectangle([20, 100, 56, 108], fill=0)
    d.rectangle([88, 100, 200, 108], fill=0)
    d.rectangle([108, 20, 116, 108], fill=0)
    return im


def vision():
    return {
        "hazards": [
            {"hazard": "dampness_mold", "location": "kitchen counter at the faucet", "room": "kitchen", "first_seen_s": 10},
            {"hazard": "lead_paint", "location": "bedroom sill below the sliding door", "room": "bedroom", "first_seen_s": 20},
        ],
        "rooms": [{"room": "kitchen"}, {"room": "bedroom"}],
    }


class TraceTests(unittest.TestCase):
    def test_two_rooms_share_a_wall(self):
        traced = shell.trace_plan(two_room_plan())
        self.assertIsNotNone(traced)
        self.assertEqual(len(traced["rooms"]), 2)
        self.assertTrue(shell._share_edge(traced["rooms"][0]["polygon"], traced["rooms"][1]["polygon"]))
        self.assertEqual(traced["scale"], "door")

    def test_a_rendered_picture_is_rejected(self):
        im = Image.linear_gradient("L").resize((80, 60))
        self.assertIsNone(shell.trace_plan(im))


class ShellTests(unittest.TestCase):
    def test_cited_area_splits_across_vision_rooms(self):
        rooms = shell.listing_shell(vision(), 800)
        self.assertEqual(len(rooms), 2)
        areas = [abs(shell._shoelace(r["polygon"])) for r in rooms]
        self.assertAlmostEqual(areas[0], areas[1], delta=0.1)
        self.assertAlmostEqual(sum(areas), 800 * shell.SQM_PER_SQFT, delta=0.5)
        self.assertNotAlmostEqual(rooms[0]["polygon"][1][0] - rooms[0]["polygon"][0][0], 4.0, delta=0.2)

    def test_no_plan_and_no_area_is_the_fixed_schematic(self):
        rooms = shell.schematic_rooms(vision())
        width = rooms[0]["polygon"][1][0] - rooms[0]["polygon"][0][0]
        depth = rooms[0]["polygon"][2][1] - rooms[0]["polygon"][1][1]
        self.assertAlmostEqual(width, 4.0)
        self.assertAlmostEqual(depth, 3.5)
        self.assertAlmostEqual(rooms[1]["polygon"][0][0], 4.6)

    def test_unmatched_vision_room_is_an_extra_box(self):
        plan = shell.schematic_rooms({"hazards": [], "rooms": [{"room": "kitchen"}]})
        placed = shell.place_on_plan(plan, {"hazards": [], "rooms": [{"room": "kitchen"}, {"room": "balcony"}]})
        names = [r["name"] for r in placed]
        self.assertEqual(names, ["kitchen", "balcony"])
        self.assertGreater(placed[1]["polygon"][0][0], placed[0]["polygon"][1][0])

    def test_one_bedroom_matches_primary_bedroom(self):
        plan = [{"id": "room_0", "name": "primary bedroom", "polygon": [[0, 0], [4, 0], [4, 3], [0, 3]], "seen": False, "hazards": []}]
        hit = shell.match_room("bedroom", plan)
        self.assertEqual(hit["id"], "room_0")


class FactTests(unittest.TestCase):
    def test_uncited_plan_is_dropped(self):
        results = {"D1": "Studio, 450 sq ft. No drawing on this page."}
        kept, dropped = validate.keep_facts(
            [{"kind": "plan_url", "value": "https://example.com/plan.png", "data_id": "D1", "snippet": "https://example.com/plan.png"}],
            results,
        )
        self.assertEqual(kept, [])
        self.assertEqual(dropped[0]["reason"], "snippet is not in the cited page")

    def test_localhost_plan_url_is_dropped(self):
        results = {"D1": "https://localhost/plan.png"}
        kept, dropped = validate.keep_facts(
            [{"kind": "plan_url", "value": "https://localhost/plan.png", "data_id": "D1", "snippet": "https://localhost/plan.png"}],
            results,
        )
        self.assertEqual(kept, [])
        self.assertIn("public https", dropped[0]["reason"])


if __name__ == "__main__":
    unittest.main()
