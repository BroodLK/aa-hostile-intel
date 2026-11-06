# Standard Library
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

# Django
from django.test import SimpleTestCase

# AA Hostile Intel
from hostile.services.moonmining_integration import (
    _location,
    drill_hourly_value,
    format_isk,
    is_drill,
    reinforcement_isk_loss,
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


class _Timers(list):
    def all(self):
        return self


def _timer(timer_type, created_at, duration):
    return SimpleNamespace(
        timer_type=timer_type,
        created_at=created_at,
        timer_datetime=created_at + duration,
    )


class DrillDenialTests(SimpleTestCase):
    def test_drill_hourly_value_uses_720_hour_month(self):
        self.assertAlmostEqual(drill_hourly_value(5_200_000_000), 7_222_222.2222, 3)
        self.assertAlmostEqual(drill_hourly_value(5_200_000_000) / 60, 120_370.3704, 3)
        self.assertIsNone(drill_hourly_value(None))

    def test_reinforcement_loss_from_latest_reinforce_timer(self):
        now = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
        structure = SimpleNamespace(
            state="HULL",
            timers=_Timers(
                [
                    _timer("HULL", now - timedelta(days=10), timedelta(hours=40)),
                    _timer("HULL", now, timedelta(hours=18, minutes=32)),
                    _timer("ANCHORING", now + timedelta(hours=1), timedelta(hours=24)),
                ]
            ),
        )
        loss = reinforcement_isk_loss(structure, 5_200_000_000)
        self.assertEqual(f"{loss:,.2f}", "133,851,851.85")

    def test_reinforcement_loss_none_without_timer_or_value(self):
        structure = SimpleNamespace(state="HULL", timers=_Timers())
        self.assertIsNone(reinforcement_isk_loss(structure, 5_200_000_000))
        structure.timers = _Timers(
            [_timer("HULL", datetime.now(timezone.utc), timedelta(hours=1))]
        )
        self.assertIsNone(reinforcement_isk_loss(structure, None))
