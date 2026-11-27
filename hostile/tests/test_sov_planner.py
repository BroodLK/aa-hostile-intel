"""Tests for the sovereignty planner SDE snapshot."""

# Standard Library
import json

# Third Party
from eve_sde.models import (
    Constellation,
    ItemType,
    Planet,
    PlanetResource,
    SolarSystem,
    SovereigntyUpgrade,
    Star,
    Stargate,
    StarResource,
)

# Django
from django.test import TestCase

# AA Hostile Intel
from hostile.services.sov_planner import get_sov_planner_catalog, get_sov_planner_data


class TestSovPlanner(TestCase):
    def test_catalog_topology_resources_and_upgrades(self):
        constellation = Constellation.objects.create(id=1, name="A")
        other_constellation = Constellation.objects.create(id=2, name="B")
        system = SolarSystem.objects.create(
            id=3,
            name="One",
            constellation=constellation,
            security_status=-0.5,
            x_2d=-123.5,
            y_2d=0,
        )
        other = SolarSystem.objects.create(
            id=4, name="Two", constellation=other_constellation
        )
        Stargate.objects.create(
            id=5, name="Gate", solar_system=system, destination=other
        )
        Stargate.objects.create(
            id=6, name="Duplicate", solar_system=system, destination=other
        )
        Stargate.objects.create(id=7, name="Unknown", solar_system=other)
        star = Star.objects.create(id=8, solar_system=system)
        StarResource.objects.create(star=star, power=30, workforce=0)
        planet = Planet.objects.create(id=9, name="Planet", solar_system=system)
        PlanetResource.objects.create(planet=planet, power=0, workforce=12)
        item = ItemType.objects.create(
            id=10, name="Upgrade", published=True, description="<b>Benefit</b>"
        )
        SovereigntyUpgrade.objects.create(
            item_type=item,
            power_allocation=10,
            workforce_production=50,
            mutually_exclusive_group="group",
        )

        data = get_sov_planner_catalog()
        json.dumps(data, allow_nan=False)
        self.assertEqual(
            data["constellations"], [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}]
        )
        systems = {row["id"]: row for row in data["systems"]}
        self.assertEqual(
            systems[3],
            {
                "id": 3,
                "name": "One",
                "constellation_id": 1,
                "x_2d": -123.5,
                "y_2d": 0,
                "security": -0.5,
                "power": 30,
                "workforce": 12,
                "neighbors": [4],
            },
        )
        self.assertEqual(systems[4]["neighbors"], [])
        self.assertIsNone(systems[4]["x_2d"])
        self.assertIsNone(systems[4]["y_2d"])
        self.assertIsNone(systems[4]["power"])
        upgrade = data["upgrades"][0]
        self.assertEqual(upgrade["id"], 10)
        self.assertEqual(upgrade["description"], "<b>Benefit</b>")
        self.assertEqual(
            get_sov_planner_data(1)["upgrades"][0]["description"], "<b>Benefit</b>"
        )
        self.assertEqual(upgrade["power"], 10)
        self.assertIsNone(upgrade["workforce"])
        self.assertEqual(upgrade["workforce_production"], 50)
        self.assertEqual(upgrade["mutually_exclusive_group"], "group")

        Planet.objects.create(id=11, name="Missing resource", solar_system=system)
        result = {row["id"]: row for row in get_sov_planner_catalog()["systems"]}[3]
        self.assertIsNone(result["power"])
        self.assertIsNone(result["workforce"])

    def test_catalog_empty(self):
        self.assertEqual(
            get_sov_planner_catalog(),
            {"constellations": [], "systems": [], "upgrades": []},
        )

    def test_catalog_uses_planets_for_workforce_and_star_for_power(self):
        constellation = Constellation.objects.create(id=1, name="A")
        system = SolarSystem.objects.create(
            id=2, name="System", constellation=constellation
        )
        star = Star.objects.create(id=3, solar_system=system)
        star_resource = StarResource.objects.create(star=star, power=620)
        planet = Planet.objects.create(id=4, name="Planet", solar_system=system)
        planet_resource = PlanetResource.objects.create(
            planet=planet, power=600, workforce=32060
        )

        def resources():
            row = get_sov_planner_catalog()["systems"][0]
            return row["power"], row["workforce"]

        self.assertEqual(resources(), (1220, 32060))
        star_resource.delete()
        self.assertEqual(resources(), (None, 32060))
        planet_resource.workforce = None
        planet_resource.save()
        self.assertEqual(resources(), (None, None))

    def test_catalog_keeps_long_cross_constellation_gate_chains(self):
        first = Constellation.objects.create(id=1, name="First")
        second = Constellation.objects.create(id=2, name="Second")
        systems = [
            SolarSystem.objects.create(
                id=number, name=f"System {number}", constellation=constellation
            )
            for number, constellation in [
                (10, first),
                (11, first),
                (12, second),
                (13, second),
            ]
        ]
        for gate_id, (source, destination) in enumerate(
            zip(systems, systems[1:]), start=20
        ):
            Stargate.objects.create(
                id=gate_id,
                name=f"Gate {gate_id}",
                solar_system=source,
                destination=destination,
            )

        neighbors = {
            row["id"]: row["neighbors"] for row in get_sov_planner_catalog()["systems"]
        }
        self.assertEqual(neighbors, {10: [11], 11: [12], 12: [13], 13: []})

    def test_only_published_upgrades_are_available(self):
        Constellation.objects.create(id=1, name="Constellation")
        for item_id, name, published in [(1, "Active", True), (2, "Deprecated", False)]:
            item = ItemType.objects.create(id=item_id, name=name, published=published)
            SovereigntyUpgrade.objects.create(item_type=item)

        self.assertEqual(
            [item["id"] for item in get_sov_planner_catalog()["upgrades"]], [1]
        )
        self.assertEqual(
            [item["type_id"] for item in get_sov_planner_data(1)["upgrades"]], [1]
        )

    def test_snapshot_preserves_resources_and_unknowns(self):
        constellation = Constellation.objects.create(id=1, name="Constellation")
        system = SolarSystem.objects.create(
            id=2, name="System", constellation=constellation
        )
        SolarSystem.objects.create(id=3, name="Other")
        planet = Planet.objects.create(id=4, name="Planet", solar_system=system)
        Planet.objects.create(id=5, name="Unknown", solar_system=system)
        PlanetResource.objects.create(planet=planet, power=0, workforce=12)
        star = Star.objects.create(id=6, solar_system=system)
        StarResource.objects.create(star=star, power=30)
        item = ItemType.objects.create(id=7, name="Upgrade", published=True)
        SovereigntyUpgrade.objects.create(item_type=item, power_allocation=10)

        data = get_sov_planner_data(1)
        json.dumps(data, allow_nan=False)
        self.assertEqual(data["constellation"]["id"], 1)
        self.assertEqual(len(data["systems"]), 1)
        result = data["systems"][0]
        self.assertEqual(result["planets"][0]["resource"]["power"], 0)
        self.assertEqual(result["planets"][0]["resource"]["workforce"], 12)
        self.assertIsNone(result["planets"][1]["resource"])
        self.assertEqual(result["star"]["resource"]["power"], 30)
        self.assertIsNone(result["star"]["resource"]["workforce"])
        self.assertEqual(data["upgrades"][0]["power_allocation"], 10)
        self.assertIsNone(data["upgrades"][0]["workforce_allocation"])

    def test_missing_constellation_and_empty_data(self):
        self.assertIsNone(get_sov_planner_data(999))
        constellation = Constellation.objects.create(id=1, name="Empty")
        SolarSystem.objects.create(id=2, name="No star", constellation=constellation)
        data = get_sov_planner_data(1)
        self.assertIsNone(data["systems"][0]["star"])
        self.assertEqual(data["systems"][0]["planets"], [])
        self.assertEqual(data["upgrades"], [])
