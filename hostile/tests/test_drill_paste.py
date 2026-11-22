"""Tests for the Moon Data drill quick-add paste parser."""

# Django
from django.test import SimpleTestCase

# AA Hostile Intel
from hostile.parsers.drill_paste import drill_location, parse_drill_paste


class ParseDrillPasteTests(SimpleTestCase):
    def test_overview_name_and_distance(self):
        drills = parse_drill_paste("F9-FUV - Bucket 10-10\n29.9 AU\n")
        self.assertEqual(
            drills,
            [{"name": "F9-FUV - Bucket 10-10", "distance": "29.9 AU", "type_name": ""}],
        )

    def test_overview_several_drills_with_and_without_distance(self):
        text = "F9-FUV - Bucket 10-10\n29.9 AU\n\nF9-FUV - Bucket 3-1\nF9-FUV - Bucket 4-2\n1,204 km\n"
        drills = parse_drill_paste(text)
        self.assertEqual(
            [(d["name"], d["distance"]) for d in drills],
            [
                ("F9-FUV - Bucket 10-10", "29.9 AU"),
                ("F9-FUV - Bucket 3-1", ""),
                ("F9-FUV - Bucket 4-2", "1,204 km"),
            ],
        )

    def test_dscan_lines_keep_only_drills(self):
        text = (
            "81826\tF9-FUV - Bucket 10-10\tMetenox Moon Drill\t120 m\n"
            "35832\tF9-FUV - Home\tAstrahus\t2.1 AU\n"
            "670\tSome Pod\tCapsule\t500 km\n"
            "81826\tF9-FUV - Bucket 3-1\tMetenox Moon Drill\t-\n"
        )
        drills = parse_drill_paste(text)
        self.assertEqual(
            [(d["name"], d["type_name"], d["distance"]) for d in drills],
            [
                ("F9-FUV - Bucket 10-10", "Metenox Moon Drill", "120 m"),
                ("F9-FUV - Bucket 3-1", "Metenox Moon Drill", "-"),
            ],
        )

    def test_dscan_recognised_by_type_id(self):
        drills = parse_drill_paste("81826\tF9-FUV - Bucket 1-1\tMoonbucket\t5 km")
        self.assertEqual([d["name"] for d in drills], ["F9-FUV - Bucket 1-1"])

    def test_duplicates_are_merged(self):
        text = (
            "81826\tF9-FUV - Bucket 10-10\tMetenox Moon Drill\t120 m\n"
            "F9-FUV - Bucket 10-10\n29.9 AU\n"
        )
        drills = parse_drill_paste(text)
        self.assertEqual(len(drills), 1)
        self.assertEqual(drills[0]["distance"], "120 m")

    def test_empty_and_distance_only(self):
        self.assertEqual(parse_drill_paste(""), [])
        self.assertEqual(parse_drill_paste(None), [])
        self.assertEqual(parse_drill_paste("29.9 AU\n120 m"), [])

    def test_plain_text_without_system_prefix_is_ignored(self):
        self.assertEqual(parse_drill_paste("hello there\nanother line"), [])
        drills = parse_drill_paste("some note\nF9-FUV - Bucket 1-1\n5 km")
        self.assertEqual(
            [(d["name"], d["distance"]) for d in drills],
            [("F9-FUV - Bucket 1-1", "5 km")],
        )


class DrillLocationTests(SimpleTestCase):
    def test_trailing_planet_moon(self):
        self.assertEqual(drill_location("F9-FUV - Bucket 10-10"), (10, 10))
        self.assertEqual(drill_location("F9-FUV - Bucket 3 - 1"), (3, 1))

    def test_existing_location_formats(self):
        self.assertEqual(drill_location("BNX-AS VI - Moon 2 drill"), (6, 2))
        self.assertEqual(drill_location("BNX-AS - Goo P3M4"), (3, 4))

    def test_system_dash_is_not_a_location(self):
        self.assertIsNone(drill_location("F9-FUV - Bucket"))
        self.assertIsNone(drill_location("4-07MU - Rocks"))
        self.assertIsNone(drill_location(""))
