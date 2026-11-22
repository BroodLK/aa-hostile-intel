"""Tests for the optional aa-metenox integration (drill value and running costs)."""

# Django
from django.test import SimpleTestCase

# AA Hostile Intel
from hostile.services.metenox_integration import (
    load_drill_costs,
    load_drill_inputs,
    load_metenox_values,
    metenox_available,
    monthly_running_cost,
)


class MonthlyRunningCostTests(SimpleTestCase):
    def test_cost_uses_cheapest_fuel_block_and_720_hours(self):
        cost = monthly_running_cost([1500.0, 1000.0, 0, 1200.0], 100.0, 5, 200)
        self.assertAlmostEqual(cost["fuel"], 5 * 1000.0 * 720)  # 3.6m
        self.assertAlmostEqual(cost["gas"], 200 * 100.0 * 720)  # 14.4m
        self.assertAlmostEqual(cost["total"], 18_000_000.0)

    def test_missing_prices_give_none(self):
        self.assertIsNone(monthly_running_cost([], 100.0))
        self.assertIsNone(monthly_running_cost([0, 0], 100.0))
        self.assertIsNone(monthly_running_cost([1000.0], None))
        self.assertIsNone(monthly_running_cost([1000.0], 0))

    def test_default_consumption_matches_metenox(self):
        cost = monthly_running_cost([1000.0], 100.0)
        self.assertAlmostEqual(cost["total"], (5 * 1000.0 + 200 * 100.0) * 720)


class WithoutMetenoxTests(SimpleTestCase):
    """aa-metenox isn't installed in the test settings."""

    def test_loaders_degrade_gracefully(self):
        self.assertFalse(metenox_available())
        self.assertIsNone(load_drill_costs())
        self.assertEqual(load_metenox_values(), {})
        self.assertEqual(load_drill_inputs([1, 2]), ({}, None))
