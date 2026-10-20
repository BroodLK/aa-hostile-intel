"""
Unit and integration tests for Hostile Structure tab filter/search, column sort,
and click-to-edit inline cell editing.
"""

# Standard Library
import json

# Django
from django.test import Client, TestCase
from django.urls import reverse

# Alliance Auth
from allianceauth.tests.auth_utils import AuthUtils

# Third Party
from eve_sde.models import ItemCategory, ItemGroup, ItemType, SolarSystem

# AA Hostile Intel
from hostile.models import (
    HostileAlliance,
    HostileCorporation,
    HostileStructure,
    IntelAuditLog,
)


class StructureInlineEditTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = ItemCategory.objects.create(
            id=65, name="Structure", published=True
        )
        cls.group = ItemGroup.objects.create(
            id=1657,
            name="Citadel",
            category=cls.category,
            published=True,
        )
        cls.type_fortizar = ItemType.objects.create(
            id=35833,
            name="Fortizar",
            group=cls.group,
            published=True,
        )
        cls.type_astra = ItemType.objects.create(
            id=35832,
            name="Astrahus",
            group=cls.group,
            published=True,
        )

        cls.system_1dq = SolarSystem.objects.create(
            id=30004759,
            name="1DQ1-A",
            security_status=-0.5,
            x=0,
            y=0,
            z=0,
        )
        cls.system_jita = SolarSystem.objects.create(
            id=30000142,
            name="Jita",
            security_status=0.9,
            x=10,
            y=10,
            z=10,
        )

        cls.alliance_goons = HostileAlliance.objects.create(
            alliance_id=1354830081,
            alliance_name="Goonswarm Federation",
            ticker="CONDI",
        )
        cls.alliance_test = HostileAlliance.objects.create(
            alliance_id=498125261,
            alliance_name="Test Alliance Please Ignore",
            ticker="TEST",
        )
        cls.corp_kadeshi = HostileCorporation.objects.create(
            corporation_id=98000001,
            corporation_name="Kadeshi Heavy Industries",
            ticker="KAD",
        )

        cls.user_officer = AuthUtils.create_user("officer_inline")
        AuthUtils.add_main_character(cls.user_officer, "Officer Inline Pilot", 21100091)
        AuthUtils.add_permission_to_user_by_name("hostile.basic_access", cls.user_officer)
        AuthUtils.add_permission_to_user_by_name("hostile.manage_intel", cls.user_officer)

        cls.user_scout = AuthUtils.create_user("scout_inline")
        AuthUtils.add_main_character(cls.user_scout, "Scout Inline Pilot", 21100092)
        AuthUtils.add_permission_to_user_by_name("hostile.basic_access", cls.user_scout)

        cls.user_other = AuthUtils.create_user("other_inline")
        AuthUtils.add_main_character(cls.user_other, "Other Inline Pilot", 21100093)
        AuthUtils.add_permission_to_user_by_name("hostile.basic_access", cls.user_other)

        cls.struct_1 = HostileStructure.objects.create(
            name="1DQ Imperial Palace",
            solar_system=cls.system_1dq,
            structure_type=cls.type_fortizar,
            alliance=cls.alliance_goons,
            owner_ticker="CONDI",
            core_status="FITTED",
            state="ONLINE",
            vulnerability_hour=19,
            vulnerability_window="16:00 - 22:00 UTC",
            created_by=cls.user_officer,
            is_verified=True,
        )

        cls.struct_scout = HostileStructure.objects.create(
            name="Scout Forward Anchor",
            solar_system=cls.system_jita,
            structure_type=cls.type_astra,
            owner_ticker="UNKWN",
            core_status="UNKNOWN",
            state="ONLINE",
            created_by=cls.user_scout,
            is_verified=False,
        )

    def setUp(self):
        self.client = Client()

    def test_structures_tab_renders_search_filter_and_sort_controls(self):
        """Test structures tab HTML contains search input, filters, sort headers, and editable cells"""
        self.client.force_login(self.user_officer)
        response = self.client.get(reverse("hostile:structures"))
        self.assertEqual(response.status_code, 200)

        content = response.content.decode("utf-8")
        self.assertIn("structureSearchInput", content)
        self.assertIn("structureStateFilter", content)
        self.assertIn("structureCoreFilter", content)
        self.assertIn("structureVerifiedFilter", content)
        self.assertIn("sortable-th", content)
        self.assertIn("editable-cell", content)
        self.assertIn("data-sort=\"name\"", content)
        self.assertIn("data-sort=\"vulnerability\"", content)
        self.assertIn("data-sort=\"core\"", content)
        self.assertIn("data-sort=\"state\"", content)
        self.assertIn("data-field=\"name\"", content)
        self.assertIn("data-field=\"owner\"", content)
        self.assertIn("data-field=\"vulnerability\"", content)
        self.assertIn("data-field=\"state\"", content)
        self.assertIn("data-field=\"core_status\"", content)

    def test_inline_update_name(self):
        """Test inline editing structure name with automatic Ansiblex normalization"""
        self.client.force_login(self.user_officer)
        url = reverse("hostile:structure_inline_update", args=[self.struct_1.id])

        payload = {"field": "name", "value": "1DQ1-A >> K-6K16 - Tactical Gate"}
        response = self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["name"], "1DQ1-A » K-6K16 - Tactical Gate")

        self.struct_1.refresh_from_db()
        self.assertEqual(self.struct_1.name, "1DQ1-A » K-6K16 - Tactical Gate")

        # Empty name should fail
        response_empty = self.client.post(
            url,
            data=json.dumps({"field": "name", "value": "   "}),
            content_type="application/json",
        )
        self.assertEqual(response_empty.status_code, 400)
        self.assertFalse(response_empty.json()["success"])

    def test_inline_update_owner_alliance_and_corporation(self):
        """Test inline editing owner ticker or name matching alliance/corporation or custom text"""
        self.client.force_login(self.user_officer)
        url = reverse("hostile:structure_inline_update", args=[self.struct_1.id])

        # Match TEST alliance
        response = self.client.post(
            url,
            data={"field": "owner", "value": "[TEST]"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["owner_ticker"], "TEST")
        self.assertEqual(data["alliance_name"], "Test Alliance Please Ignore")

        self.struct_1.refresh_from_db()
        self.assertEqual(self.struct_1.alliance, self.alliance_test)
        self.assertEqual(self.struct_1.owner_ticker, "TEST")

        # Match KAD corp
        response_corp = self.client.post(
            url,
            data={"field": "owner", "value": "Kadeshi Heavy Industries"},
        )
        self.assertEqual(response_corp.status_code, 200)
        data_corp = response_corp.json()
        self.assertTrue(data_corp["success"])
        self.assertEqual(data_corp["corporation_name"], "Kadeshi Heavy Industries")

        self.struct_1.refresh_from_db()
        self.assertEqual(self.struct_1.corporation, self.corp_kadeshi)
        self.assertIsNone(self.struct_1.alliance)

        # Custom untracked ticker
        response_custom = self.client.post(
            url,
            data={"field": "owner", "value": "[PIRAT]"},
        )
        self.assertEqual(response_custom.status_code, 200)
        self.struct_1.refresh_from_db()
        self.assertEqual(self.struct_1.owner_ticker, "PIRAT")
        self.assertIsNone(self.struct_1.alliance)
        self.assertIsNone(self.struct_1.corporation)

    def test_inline_update_vulnerability(self):
        """Test inline editing vulnerability by hour integer and window string"""
        self.client.force_login(self.user_officer)
        url = reverse("hostile:structure_inline_update", args=[self.struct_1.id])

        # By UTC hour integer (e.g. 14)
        response_hour = self.client.post(
            url,
            data=json.dumps({"field": "vulnerability", "value": "14"}),
            content_type="application/json",
        )
        self.assertEqual(response_hour.status_code, 200)
        data = response_hour.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["vulnerability_hour"], 14)
        self.assertEqual(data["vulnerability_window"], "11:00 - 17:00 UTC")

        self.struct_1.refresh_from_db()
        self.assertEqual(self.struct_1.vulnerability_hour, 14)
        self.assertEqual(self.struct_1.vulnerability_window, "11:00 - 17:00 UTC")

        # By window string
        response_window = self.client.post(
            url,
            data=json.dumps({"field": "vulnerability", "value": "20:00 - 02:00 UTC"}),
            content_type="application/json",
        )
        self.assertEqual(response_window.status_code, 200)
        self.struct_1.refresh_from_db()
        self.assertEqual(self.struct_1.vulnerability_window, "20:00 - 02:00 UTC")
        self.assertEqual(self.struct_1.vulnerability_hour, 23)

        # Clear vulnerability
        response_clear = self.client.post(
            url,
            data=json.dumps({"field": "vulnerability", "value": "clear"}),
            content_type="application/json",
        )
        self.assertEqual(response_clear.status_code, 200)
        self.struct_1.refresh_from_db()
        self.assertIsNone(self.struct_1.vulnerability_hour)
        self.assertEqual(self.struct_1.vulnerability_window, "")

    def test_inline_update_state(self):
        """Test inline editing state and verify ANCHORING enforces UNFITTED core"""
        self.client.force_login(self.user_officer)
        url = reverse("hostile:structure_inline_update", args=[self.struct_1.id])

        response_low = self.client.post(
            url,
            data={"field": "state", "value": "LOW_POWER"},
        )
        self.assertEqual(response_low.status_code, 200)
        self.struct_1.refresh_from_db()
        self.assertEqual(self.struct_1.state, "LOW_POWER")

        # Changing to ANCHORING should auto-set core_status to UNFITTED
        response_anch = self.client.post(
            url,
            data={"field": "state", "value": "ANCHORING"},
        )
        self.assertEqual(response_anch.status_code, 200)
        self.struct_1.refresh_from_db()
        self.assertEqual(self.struct_1.state, "ANCHORING")
        self.assertEqual(self.struct_1.core_status, "UNFITTED")

        # Invalid state
        response_invalid = self.client.post(
            url,
            data={"field": "state", "value": "INVALID_STATE_XYZ"},
        )
        self.assertEqual(response_invalid.status_code, 400)

    def test_inline_update_quantum_core(self):
        """Test inline editing quantum core status"""
        self.client.force_login(self.user_officer)
        url = reverse("hostile:structure_inline_update", args=[self.struct_1.id])

        response_fitted = self.client.post(
            url,
            data={"field": "core_status", "value": "FITTED"},
        )
        self.assertEqual(response_fitted.status_code, 200)
        self.struct_1.refresh_from_db()
        self.assertEqual(self.struct_1.core_status, "FITTED")

        response_unfitted = self.client.post(
            url,
            data={"field": "core_status", "value": "UNFITTED"},
        )
        self.assertEqual(response_unfitted.status_code, 200)
        self.struct_1.refresh_from_db()
        self.assertEqual(self.struct_1.core_status, "UNFITTED")

        # Invalid core status
        response_invalid = self.client.post(
            url,
            data={"field": "core_status", "value": "INVALID_CORE"},
        )
        self.assertEqual(response_invalid.status_code, 400)

    def test_inline_update_permissions(self):
        """Test permission checks for inline structure editing"""
        url_struct1 = reverse("hostile:structure_inline_update", args=[self.struct_1.id])
        url_scout = reverse("hostile:structure_inline_update", args=[self.struct_scout.id])

        # Scout (author of struct_scout) can edit struct_scout
        self.client.force_login(self.user_scout)
        resp_scout_own = self.client.post(
            url_scout,
            data={"field": "name", "value": "Scout Forward Anchor Updated"},
        )
        self.assertEqual(resp_scout_own.status_code, 200)

        # Scout CANNOT edit struct_1 (created by officer)
        resp_scout_other = self.client.post(
            url_struct1,
            data={"field": "name", "value": "Hacked Name"},
        )
        self.assertEqual(resp_scout_other.status_code, 403)
        self.assertFalse(resp_scout_other.json()["success"])

        # Officer with manage_intel can edit any structure
        self.client.force_login(self.user_officer)
        resp_officer = self.client.post(
            url_scout,
            data={"field": "name", "value": "Officer Approved Name"},
        )
        self.assertEqual(resp_officer.status_code, 200)
