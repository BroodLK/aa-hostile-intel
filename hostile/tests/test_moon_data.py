"""Tests for the Moon Data tab: moon/structure matching, payload building and views."""

# Standard Library
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

# Django
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

# Alliance Auth
from allianceauth.tests.auth_utils import AuthUtils

# AA Hostile Intel
from hostile.services.moonmining_integration import (
    DRILL_SHARE,
    build_moon_data_payload,
    is_moon_structure,
    is_refinery,
    match_structure_moons,
    match_structures_to_moons,
    moon_match_data,
    product_monthly_value,
)

VOLUME_PER_MONTH = 30_000_000


class _Manager(list):
    def all(self):
        return self


def _system(name="BNX-AS", system_id=30000001):
    region = SimpleNamespace(name="Fountain")
    constellation = SimpleNamespace(name="Y-ZXZ5", eve_region=region)
    return SimpleNamespace(
        id=system_id, name=name, security_status=-0.42, eve_constellation=constellation
    )


def _ore(ore_id, name, group_id, price, volume=10.0):
    return SimpleNamespace(
        id=ore_id,
        name=name,
        eve_group_id=group_id,
        volume=volume,
        extras=SimpleNamespace(current_price=price),
    )


def _moon(moon_id, planet="VI", number=2, system=None, value=1_000_000_000.0, **extra):
    system = system or _system()
    planet_obj = SimpleNamespace(
        name=f"{system.name} {planet}", eve_solar_system=system
    )
    eve_moon = SimpleNamespace(
        name=f"{system.name} {planet} - Moon {number}", eve_planet=planet_obj
    )
    fields = {
        "eve_moon_id": moon_id,
        "eve_moon": eve_moon,
        "value": value,
        "rarity_class": extra.pop("rarity_class", 32),
        "products": _Manager(extra.pop("products", [])),
        "label": None,
        "products_updated_at": None,
    }
    fields.update(extra)
    return SimpleNamespace(**fields)


def _structure(structure_id, type_name="Metenox Moon Drill", group_id=4807, **extra):
    fields = {
        "id": structure_id,
        "name": f"Structure {structure_id}",
        "structure_type": SimpleNamespace(name=type_name, group_id=group_id),
        "solar_system": SimpleNamespace(name="BNX-AS"),
        "location_details": "",
        "moonmining_moon_id": None,
        "state": "ONLINE",
        "alliance_id": None,
        "alliance": None,
        "corporation_id": None,
        "corporation": None,
        "owner_ticker": "",
        "owner_name": "Unknown",
        "vulnerability_hour": None,
    }
    fields.update(extra)
    return SimpleNamespace(**fields)


class MoonStructureMatchingTests(SimpleTestCase):
    def test_refineries_and_drills_are_moon_structures(self):
        athanor = _structure(1, "Athanor", 1406)
        tatara_by_name = _structure(2, "Tatara", None)
        drill = _structure(3)
        astrahus = _structure(4, "Astrahus", 1657)
        self.assertTrue(is_refinery(athanor))
        self.assertTrue(is_refinery(tatara_by_name))
        self.assertFalse(is_refinery(drill))
        self.assertTrue(is_moon_structure(drill))
        self.assertFalse(is_moon_structure(astrahus))

    def test_manual_moon_id_wins(self):
        moon_a = _moon(40001, "VI", 2)
        moon_b = _moon(40002, "VI", 3)
        drill = _structure(1, location_details="P6M3", moonmining_moon_id=40001)
        self.assertIs(match_structure_moons([moon_a, moon_b], [drill])[1], moon_a)

    def test_stale_moon_id_in_other_system_falls_back_to_location(self):
        other = _moon(40009, system=_system("1DQ1-A", 30000002))
        local = _moon(40001, "VI", 2)
        drill = _structure(1, location_details="P6M2", moonmining_moon_id=40009)
        self.assertIs(match_structure_moons([other, local], [drill])[1], local)

    def test_refinery_matched_by_location_in_name(self):
        moon = _moon(40001, "VI", 2)
        athanor = _structure(5, "Athanor", 1406, name="BNX-AS VI - Moon 2 - Rocks")
        self.assertEqual(
            match_structures_to_moons([moon], [athanor]), {40001: [athanor]}
        )

    def test_non_moon_structures_are_ignored(self):
        moon = _moon(40001, "VI", 2)
        astrahus = _structure(6, "Astrahus", 1657, location_details="P6M2")
        self.assertEqual(match_structure_moons([moon], [astrahus]), {})

    def test_drill_value_only_for_drills(self):
        moon = _moon(40001, value=2_000_000_000.0)
        drill = moon_match_data(_structure(1, location_details="P6M2"), moon)
        refinery = moon_match_data(_structure(2, "Athanor", 1406), moon)
        self.assertAlmostEqual(drill["drill_value"], 2_000_000_000.0 * DRILL_SHARE)
        self.assertEqual(drill["display_value"], "2.0b")
        self.assertIsNone(refinery["drill_value"])
        self.assertIsNone(refinery["drill_hourly_value"])
        self.assertEqual(refinery["display_drill_value"], "—")
        self.assertEqual(refinery["value"], 2_000_000_000.0)


class MoonDataPayloadTests(SimpleTestCase):
    def test_product_value_mirrors_moonmining_formula(self):
        self.assertAlmostEqual(
            product_monthly_value(100.0, 0.25, 10.0, VOLUME_PER_MONTH),
            100.0 * 0.25 * VOLUME_PER_MONTH / 10.0,
        )
        self.assertEqual(product_monthly_value(100.0, 0.25, 0, VOLUME_PER_MONTH), 0.0)
        self.assertEqual(product_monthly_value(None, 0.25, 10, VOLUME_PER_MONTH), 0.0)

    def test_products_sorted_by_value_and_ores_deduplicated(self):
        xenotime = _ore(45510, "Xenotime", 1923, 2000.0)
        bitumens = _ore(45492, "Bitumens", 1884, 100.0)
        moons = [
            _moon(
                40001,
                products=[
                    SimpleNamespace(ore_type=bitumens, amount=0.6),
                    SimpleNamespace(ore_type=xenotime, amount=0.4),
                ],
            ),
            _moon(
                40002,
                number=3,
                products=[SimpleNamespace(ore_type=xenotime, amount=1.0)],
            ),
        ]
        payload = build_moon_data_payload(moons, [], VOLUME_PER_MONTH)
        self.assertTrue(payload["available"])
        self.assertEqual(set(payload["ores"]), {45510, 45492})
        self.assertEqual(payload["ores"][45510]["rarity"], 64)
        self.assertEqual(payload["ores"][45492]["rarity"], 4)
        first = payload["rows"][0]
        self.assertEqual([p[0] for p in first["products"]], [45510, 45492])
        self.assertAlmostEqual(
            first["products"][0][2], 2000.0 * 0.4 * VOLUME_PER_MONTH / 10.0, places=1
        )
        self.assertEqual(first["location"], "P6M2")
        self.assertEqual(first["region"], "Fountain")
        self.assertEqual(first["constellation"], "Y-ZXZ5")

    def test_owner_priority_structure_then_refinery_then_unclaimed(self):
        alliance = SimpleNamespace(ticker="EVIL", is_friendly=False)
        drill = _structure(
            1,
            location_details="P6M2",
            alliance_id=99,
            alliance=alliance,
            owner_name="Evil Alliance",
        )
        refinery = SimpleNamespace(
            name="Our Athanor",
            eve_type=SimpleNamespace(name="Athanor"),
            owner=SimpleNamespace(
                corporation=SimpleNamespace(
                    corporation_name="Good Corp",
                    corporation_id=123,
                    corporation_ticker="GOOD",
                    alliance=None,
                )
            ),
        )
        moons = [
            _moon(40001, "VI", 2),
            _moon(40002, "VI", 3, refinery=refinery),
            _moon(40003, "VI", 4),
        ]
        rows = build_moon_data_payload(moons, [drill], VOLUME_PER_MONTH)["rows"]
        by_id = {r["id"]: r for r in rows}

        hostile = by_id[40001]
        self.assertEqual(hostile["status"], "hostile")
        self.assertEqual(hostile["owner_key"], "a99")
        self.assertEqual(hostile["owner_ticker"], "EVIL")
        self.assertEqual(
            hostile["owner_url"], reverse("hostile:alliance_detail", args=[99])
        )
        self.assertEqual(hostile["structures"][0]["id"], 1)
        self.assertTrue(hostile["structures"][0]["is_drill"])

        friendly = by_id[40002]
        self.assertEqual(friendly["status"], "friendly")
        self.assertEqual(friendly["owner_name"], "Good Corp")
        self.assertEqual(friendly["refinery"]["ticker"], "GOOD")
        self.assertEqual(friendly["structures"], [])

        unclaimed = by_id[40003]
        self.assertEqual(unclaimed["status"], "unclaimed")
        self.assertEqual(unclaimed["owner_key"], "unclaimed")

    def test_friendly_registry_owner(self):
        corp = SimpleNamespace(ticker="OURS", is_friendly=True)
        athanor = _structure(
            2,
            "Athanor",
            1406,
            location_details="P6M2",
            corporation_id=7,
            corporation=corp,
            owner_name="Our Corp",
        )
        row = build_moon_data_payload([_moon(40001)], [athanor], VOLUME_PER_MONTH)[
            "rows"
        ][0]
        self.assertEqual(row["status"], "friendly")
        self.assertEqual(row["owner_key"], "c7")

    def test_destroyed_structures_are_excluded(self):
        drill = _structure(1, location_details="P6M2", state="DESTROYED")
        row = build_moon_data_payload([_moon(40001)], [drill], VOLUME_PER_MONTH)[
            "rows"
        ][0]
        self.assertEqual(row["structures"], [])
        self.assertEqual(row["status"], "unclaimed")

    def test_drill_value_and_updated_at(self):
        when = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
        row = build_moon_data_payload(
            [_moon(40001, value=1_000_000_000.0, products_updated_at=when)],
            [],
            VOLUME_PER_MONTH,
        )["rows"][0]
        self.assertAlmostEqual(row["drill_value"], 400_000_000.0)
        self.assertEqual(row["updated_at"], when.isoformat())
        self.assertEqual(row["rarity"], 32)


class MoonDataViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user_no_perms = AuthUtils.create_user("moon_noperm")
        AuthUtils.add_main_character(cls.user_no_perms, "Moon Noperm", "93000001")
        cls.user_basic = AuthUtils.create_user("moon_basic")
        AuthUtils.add_main_character(cls.user_basic, "Moon Basic", "93000002")
        AuthUtils.add_permission_to_user_by_name("hostile.basic_access", cls.user_basic)

    def setUp(self):
        self.user_basic.refresh_from_db()
        self.user_no_perms.refresh_from_db()

    def _payload(self):
        return {
            "available": True,
            "ores": {
                45510: {
                    "name": "Xenotime",
                    "rarity": 64,
                    "price": 2000.0,
                    "volume": 10.0,
                    "icon": "https://images.evetech.net/types/45510/icon?size=32",
                }
            },
            "rows": [
                {
                    "id": 40001,
                    "name": "BNX-AS VI - Moon 2",
                    "location": "P6M2",
                    "system": "BNX-AS",
                    "system_id": 30000001,
                    "security": -0.42,
                    "constellation": "Y-ZXZ5",
                    "region": "Fountain",
                    "rarity": 64,
                    "value": 5_000_000_000.0,
                    "drill_value": 2_000_000_000.0,
                    "label": None,
                    "products": [[45510, 1.0, 5_000_000_000.0]],
                    "updated_at": "2026-11-01T12:00:00+00:00",
                    "structures": [],
                    "refinery": None,
                    "owner_key": "unclaimed",
                    "owner_name": "Unclaimed",
                    "owner_ticker": "",
                    "owner_url": "",
                    "status": "unclaimed",
                }
            ],
        }

    def test_requires_basic_access(self):
        self.client.force_login(self.user_no_perms)
        for name in ("moon_data", "moon_data_json"):
            self.assertEqual(
                self.client.get(reverse(f"hostile:{name}")).status_code, 302
            )
        self.assertEqual(
            self.client.get(
                reverse("hostile:moon_data_detail", args=[40001])
            ).status_code,
            302,
        )

    def test_page_without_moonmining_shows_notice(self):
        self.client.force_login(self.user_basic)
        response = self.client.get(reverse("hostile:moon_data"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "moonNotInstalled")
        self.assertNotContains(response, "moonGroups")

    def test_json_without_moonmining(self):
        self.client.force_login(self.user_basic)
        response = self.client.get(reverse("hostile:moon_data_json"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"available": False, "ores": {}, "rows": []})

    @patch(
        "hostile.services.moonmining_integration.moonmining_available",
        return_value=True,
    )
    def test_page_with_moonmining_renders_table_shell(self, _available):
        self.client.force_login(self.user_basic)
        response = self.client.get(reverse("hostile:moon_data"))
        self.assertEqual(response.status_code, 200)
        for marker in (
            "moonGroups",
            "moonSearchInput",
            "moonRarityChips",
            "moonStatusChips",
            "moonOreFilter",
            "moonGroupMode",
            'data-kpi="r64"',
            reverse("hostile:moon_data_json"),
        ):
            self.assertContains(response, marker)

    def test_json_returns_payload(self):
        self.client.force_login(self.user_basic)
        with patch(
            "hostile.services.moonmining_integration.load_moon_data_payload",
            return_value=self._payload(),
        ):
            response = self.client.get(reverse("hostile:moon_data_json"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["rows"][0]["id"], 40001)
        self.assertEqual(data["ores"]["45510"]["name"], "Xenotime")

    def test_json_reports_errors(self):
        self.client.force_login(self.user_basic)
        with patch(
            "hostile.services.moonmining_integration.load_moon_data_payload",
            side_effect=RuntimeError("boom"),
        ):
            response = self.client.get(reverse("hostile:moon_data_json"))
        self.assertEqual(response.status_code, 500)
        self.assertIn("error", response.json())

    def test_detail_modal(self):
        self.client.force_login(self.user_basic)
        with patch(
            "hostile.services.moonmining_integration.load_moon_data_payload",
            return_value=self._payload(),
        ):
            response = self.client.get(
                reverse("hostile:moon_data_detail", args=[40001])
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "BNX-AS VI - Moon 2")
        self.assertContains(response, "Xenotime")
        self.assertContains(response, "5.0b")
        self.assertContains(response, "Add structure on this moon")

    def test_detail_unknown_moon_is_404(self):
        self.client.force_login(self.user_basic)
        self.assertEqual(
            self.client.get(reverse("hostile:moon_data_detail", args=[1])).status_code,
            404,
        )
        empty = {"available": True, "ores": {}, "rows": []}
        with patch(
            "hostile.services.moonmining_integration.load_moon_data_payload",
            return_value=empty,
        ):
            response = self.client.get(reverse("hostile:moon_data_detail", args=[1]))
        self.assertEqual(response.status_code, 404)

    def test_structures_add_prefill_from_moon_data(self):
        self.client.force_login(self.user_basic)
        response = self.client.get(
            reverse("hostile:structures"), {"add": "1", "location_details": "P6M2"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["open_add_modal"])
        self.assertEqual(
            response.context["structure_form"].initial["location_details"], "P6M2"
        )
