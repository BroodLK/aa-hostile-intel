# Standard Library
from types import SimpleNamespace

# Django
from django.test import SimpleTestCase

# AA Hostile Intel
from hostile.services.moonmining_integration import (
    _location,
    format_isk,
    is_drill,
    structure_display_name,
)


class MoonminingIntegrationTests(SimpleTestCase):
    def test_isk_values_are_compact(self):
        self.assertEqual(format_isk(4_176_332_870), "4.2b")
        self.assertEqual(format_isk(1_670_533_148 * 0.001), "1.7m")

    def test_location_parser_accepts_compact_and_spaced_formats(self):
        self.assertEqual(_location("Planet 8 Moon 1 / P8M1"), (8, 1))
        self.assertEqual(_location("P 1 M 2"), (1, 2))

    def test_drill_names_are_consistent(self):
        structure = SimpleNamespace(
            name="Unhelpful Drill",
            location_details="Planet 8 Moon 1",
            solar_system=SimpleNamespace(name="BNX-AS"),
            structure_type=SimpleNamespace(name="Metenox Moon Drill"),
        )
        self.assertTrue(is_drill(structure))
        self.assertEqual(structure_display_name(structure), "BNX-AS P8M1")

    def test_non_drills_keep_their_names(self):
        structure = SimpleNamespace(
            name="Citadel",
            location_details="P8M1",
            solar_system=SimpleNamespace(name="BNX-AS"),
            structure_type=SimpleNamespace(name="Athanor"),
        )
        self.assertFalse(is_drill(structure))
        self.assertEqual(structure_display_name(structure), "Citadel")
