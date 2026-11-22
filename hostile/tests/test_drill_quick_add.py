"""Tests for Moon Data's Quick Add Drills (parse + save endpoints)."""

# Standard Library
import json
from unittest.mock import patch

# Third Party
from eve_sde.models import (
    Constellation,
    ItemCategory,
    ItemGroup,
    ItemType,
    Moon,
    Region,
    SolarSystem,
)

# Django
from django.test import TestCase
from django.urls import reverse

# Alliance Auth
from allianceauth.tests.auth_utils import AuthUtils

# AA Hostile Intel
from hostile.models import HostileAlliance, HostileStructure, IntelAuditLog
from hostile.services.moonmining_integration import (
    _sde_moon_adapter,
    _sde_moon_from_values,
    build_moon_data_payload,
)

PASTE_OVERVIEW = "F9-FUV - Bucket 10-10\n29.9 AU"
PASTE_DSCAN = (
    "81826\tF9-FUV - Bucket 10-10\tMetenox Moon Drill\t120 m\n"
    "81826\tF9-FUV - Bucket 3-1\tMetenox Moon Drill\t2.0 AU"
)


class DrillQuickAddTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        category = ItemCategory.objects.create(id=65, name="Structure")
        group = ItemGroup.objects.create(id=4807, name="Moon Drill", category=category)
        cls.metenox = ItemType.objects.create(
            id=81826, name="Metenox Moon Drill", group=group
        )
        region = Region.objects.create(id=10000058, name="Fountain")
        constellation = Constellation.objects.create(
            id=20000001, name="Y-ZXZ5", region=region
        )
        cls.f9 = SolarSystem.objects.create(
            id=30000001,
            name="F9-FUV",
            constellation=constellation,
            security_status=-0.3,
        )
        cls.other = SolarSystem.objects.create(
            id=30000002,
            name="BNX-AS",
            constellation=constellation,
            security_status=-0.4,
        )
        cls.m10_10 = Moon.objects.create(
            id=40000110,
            name="F9-FUV X - Moon 10",
            solar_system=cls.f9,
            celestial_index=10,
            orbit_index=10,
        )
        cls.m3_1 = Moon.objects.create(
            id=40000031,
            name="F9-FUV III - Moon 1",
            solar_system=cls.f9,
            celestial_index=3,
            orbit_index=1,
        )
        cls.other_moon = Moon.objects.create(
            id=40000201,
            name="BNX-AS I - Moon 1",
            solar_system=cls.other,
            celestial_index=1,
            orbit_index=1,
        )
        cls.alliance = HostileAlliance.objects.create(
            alliance_id=99000001, alliance_name="Evil Alliance", ticker="EVIL"
        )
        cls.user = AuthUtils.create_user("drill_basic")
        AuthUtils.add_main_character(cls.user, "Drill Basic", "93200001")
        AuthUtils.add_permission_to_user_by_name("hostile.basic_access", cls.user)
        cls.user_no_perms = AuthUtils.create_user("drill_noperm")
        AuthUtils.add_main_character(cls.user_no_perms, "Drill Noperm", "93200002")

    def setUp(self):
        self.user.refresh_from_db()
        self.client.force_login(self.user)

    def _parse(self, text):
        return self.client.post(
            reverse("hostile:api_drill_paste_parse"), {"text": text}
        )

    def _save(self, drills):
        return self.client.post(
            reverse("hostile:api_drill_paste_save"),
            data=json.dumps({"drills": drills}),
            content_type="application/json",
        )

    # --- parse ---------------------------------------------------------------

    def test_parse_infers_system_and_preselects_moon(self):
        data = self._parse(PASTE_OVERVIEW).json()
        self.assertTrue(data["success"])
        self.assertEqual(data["metenox_type"]["id"], 81826)
        drill = data["drills"][0]
        self.assertEqual(drill["system"], {"id": 30000001, "name": "F9-FUV"})
        self.assertEqual(drill["location"], "P10M10")
        self.assertEqual(drill["moon_id"], 40000110)
        self.assertEqual(drill["distance"], "29.9 AU")
        self.assertEqual([m["location"] for m in drill["moons"]], ["P3M1", "P10M10"])
        self.assertIsNone(drill["existing"])

    def test_parse_dscan_several_drills(self):
        data = self._parse(PASTE_DSCAN).json()
        self.assertEqual(
            [(d["name"], d["moon_id"]) for d in data["drills"]],
            [("F9-FUV - Bucket 10-10", 40000110), ("F9-FUV - Bucket 3-1", 40000031)],
        )

    def test_parse_flags_existing_drill(self):
        existing = HostileStructure.objects.create(
            name="F9-FUV - Bucket 10-10",
            structure_type=self.metenox,
            solar_system=self.f9,
            owner_ticker="EVIL",
        )
        drill = self._parse(PASTE_OVERVIEW).json()["drills"][0]
        self.assertEqual(drill["existing"]["id"], existing.id)
        self.assertEqual(drill["existing"]["owner_ticker"], "EVIL")

    def test_parse_without_drills_is_an_error(self):
        response = self._parse("hello there")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])
        response = self._parse("35832\tF9-FUV - Home\tAstrahus\t2.1 AU")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])

    # --- save ----------------------------------------------------------------

    def test_save_creates_drill_on_moon_with_owner_and_audit(self):
        response = self._save(
            [
                {
                    "name": "F9-FUV - Bucket 10-10",
                    "solar_system": "30000001",
                    "moon_id": "40000110",
                    "untracked_alliance": "EVIL",
                    "state": "ONLINE",
                    "notes": "seen on probes",
                }
            ]
        )
        data = response.json()
        self.assertTrue(data["success"])
        result = data["results"][0]
        self.assertTrue(result["ok"])
        self.assertTrue(result["created"])
        struct = HostileStructure.objects.get(pk=result["structure_id"])
        self.assertEqual(struct.structure_type_id, 81826)
        self.assertEqual(struct.solar_system_id, 30000001)
        self.assertEqual(struct.location_details, "P10M10")
        self.assertEqual(struct.moonmining_moon_id, 40000110)
        self.assertEqual(struct.alliance, self.alliance)
        self.assertEqual(struct.owner_ticker, "EVIL")
        self.assertEqual(struct.notes, "seen on probes")
        self.assertEqual(struct.created_by, self.user)
        self.assertTrue(
            IntelAuditLog.objects.filter(
                target_id=str(struct.id), action="CREATE"
            ).exists()
        )

    def test_save_again_updates_instead_of_duplicating(self):
        row = {
            "name": "F9-FUV - Bucket 10-10",
            "solar_system": "30000001",
            "moon_id": "40000110",
        }
        first = self._save([row]).json()["results"][0]
        second = self._save([{**row, "owner_ticker": "EVIL"}]).json()["results"][0]
        self.assertTrue(second["ok"])
        self.assertFalse(second["created"])
        self.assertEqual(first["structure_id"], second["structure_id"])
        self.assertEqual(HostileStructure.objects.count(), 1)

    def test_bad_row_does_not_block_others(self):
        data = self._save(
            [
                {
                    "name": "F9-FUV - Bucket 3-1",
                    "solar_system": "30000001",
                    "moon_id": "40000201",  # moon in BNX-AS
                },
                {
                    "name": "F9-FUV - Bucket 10-10",
                    "solar_system": "30000001",
                    "moon_id": "40000110",
                },
            ]
        ).json()
        self.assertFalse(data["results"][0]["ok"])
        self.assertIn("solar system", data["results"][0]["errors"][0])
        self.assertTrue(data["results"][1]["ok"])
        self.assertEqual(HostileStructure.objects.count(), 1)

    def test_save_without_moon_uses_name_location(self):
        result = self._save(
            [{"name": "F9-FUV - Bucket 3-1", "solar_system": "30000001"}]
        ).json()["results"][0]
        struct = HostileStructure.objects.get(pk=result["structure_id"])
        self.assertEqual(struct.location_details, "P3M1")
        # The name's planet-moon is a real moon here, so the drill is linked to it.
        self.assertEqual(struct.moonmining_moon_id, 40000031)

    def test_name_location_that_is_not_a_moon(self):
        drill = self._parse("F9-FUV - Bucket 12-9").json()["drills"][0]
        self.assertEqual(drill["location"], "P12M9")
        self.assertTrue(drill["location_missing"])
        self.assertIsNone(drill["moon_id"])
        result = self._save(
            [{"name": "F9-FUV - Bucket 12-9", "solar_system": "30000001"}]
        ).json()["results"][0]
        struct = HostileStructure.objects.get(pk=result["structure_id"])
        self.assertEqual(struct.location_details, "")
        self.assertIsNone(struct.moonmining_moon_id)
        self.assertFalse(
            self._parse(PASTE_OVERVIEW).json()["drills"][0]["location_missing"]
        )

    def test_blocked_relink_does_not_backfill_location(self):
        other_user = AuthUtils.create_user("drill_other2")
        existing = HostileStructure.objects.create(
            name="F9-FUV - Bucket 10-10",
            structure_type=self.metenox,
            solar_system=self.f9,
            moonmining_moon_id=40000031,
            location_details="",
            created_by=other_user,
        )
        result = self._save(
            [
                {
                    "name": "F9-FUV - Bucket 10-10",
                    "solar_system": "30000001",
                    "moon_id": "40000110",
                }
            ]
        ).json()["results"][0]
        self.assertTrue(result["ok"])
        self.assertEqual(
            result["warnings"], ["Already linked to another moon; not changed."]
        )
        existing.refresh_from_db()
        self.assertEqual(existing.moonmining_moon_id, 40000031)
        self.assertEqual(existing.location_details, "")

    def test_failed_row_is_rolled_back(self):
        with patch(
            "hostile.services.intel_manager.IntelManager.record_audit",
            side_effect=RuntimeError("boom"),
        ):
            data = self._save(
                [
                    {
                        "name": "F9-FUV - Bucket 10-10",
                        "solar_system": "30000001",
                        "moon_id": "40000110",
                    }
                ]
            ).json()
        self.assertFalse(data["results"][0]["ok"])
        self.assertEqual(HostileStructure.objects.count(), 0)

    def test_too_many_rows(self):
        rows = [
            {"name": f"F9-FUV - Bucket {i}", "solar_system": "30000001"}
            for i in range(51)
        ]
        response = self._save(rows)
        self.assertEqual(response.status_code, 400)
        self.assertIn("max 50", response.json()["error"])
        self.assertEqual(HostileStructure.objects.count(), 0)

    def test_cannot_relink_someone_elses_drill(self):
        other_user = AuthUtils.create_user("drill_other")
        HostileStructure.objects.create(
            name="F9-FUV - Bucket 10-10",
            structure_type=self.metenox,
            solar_system=self.f9,
            moonmining_moon_id=40000031,
            location_details="P3M1",
            created_by=other_user,
        )
        result = self._save(
            [
                {
                    "name": "F9-FUV - Bucket 10-10",
                    "solar_system": "30000001",
                    "moon_id": "40000110",
                }
            ]
        ).json()["results"][0]
        self.assertTrue(result["ok"])
        self.assertEqual(result["moon_id"], 40000031)
        self.assertTrue(result["warnings"])

    def test_save_requires_rows_and_json(self):
        self.assertEqual(self._save([]).status_code, 400)
        response = self.client.post(
            reverse("hostile:api_drill_paste_save"),
            data="nope",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_missing_metenox_type(self):
        ItemType.objects.filter(pk=81826).delete()
        result = self._save(
            [{"name": "F9-FUV - Bucket 10-10", "solar_system": "30000001"}]
        ).json()["results"][0]
        self.assertFalse(result["ok"])
        self.assertIn("Metenox", result["errors"][0])

    def test_moons_endpoint(self):
        data = self.client.get(
            reverse("hostile:api_drill_paste_moons"), {"system_id": 30000001}
        ).json()
        self.assertEqual([m["id"] for m in data["moons"]], [40000031, 40000110])

    def test_permission_required(self):
        self.client.force_login(self.user_no_perms)
        self.assertEqual(self._parse(PASTE_OVERVIEW).status_code, 302)
        self.assertEqual(self._save([{"name": "x"}]).status_code, 302)

    # --- Moon Data placement ---------------------------------------------------

    def test_quick_added_drill_shows_on_unscanned_moon(self):
        result = self._save(
            [
                {
                    "name": "F9-FUV - Bucket 10-10",
                    "solar_system": "30000001",
                    "moon_id": "40000110",
                    "untracked_alliance": "EVIL",
                }
            ]
        ).json()["results"][0]
        struct = HostileStructure.objects.select_related(
            "structure_type", "solar_system", "alliance", "corporation"
        ).get(pk=result["structure_id"])
        sde_row = Moon.objects.filter(pk=40000110).values(
            "id",
            "name",
            "solar_system__id",
            "solar_system__name",
            "solar_system__security_status",
            "solar_system__constellation__id",
            "solar_system__constellation__name",
            "solar_system__constellation__region__id",
            "solar_system__constellation__region__name",
        )[0]
        moon = _sde_moon_adapter(_sde_moon_from_values(sde_row))
        row = build_moon_data_payload([moon], [struct], 30_000_000)["rows"][0]
        self.assertEqual(row["structures"][0]["id"], struct.id)
        self.assertEqual(row["status"], "hostile")
        self.assertEqual(row["owner_ticker"], "EVIL")
