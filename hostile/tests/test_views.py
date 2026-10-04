"""
Tests for Hostile Intelligence Views, Permissions, Ingestion, and Fitting Modals
"""

# Standard Library
from datetime import timedelta
from unittest.mock import patch

# Django
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

# Alliance Auth
from allianceauth.tests.auth_utils import AuthUtils

# Third Party
from eve_sde.models import ItemCategory, ItemGroup, ItemType, SolarSystem

# AA Hostile Intel
from hostile.models import (
    DoctrineFit,
    HostileAlliance,
    HostileCorporation,
    HostileDoctrine,
    HostilePilotDossier,
    HostileStagingSystem,
    HostileStructure,
    LocalThreatScan,
    StructureFitting,
    StructureModule,
    StructureTimer,
    SystemObservation,
)


class TestHostileViews(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = ItemCategory.objects.create(id=65, name="Structure", published=True)
        cls.category_module = ItemCategory.objects.create(id=7, name="Module", published=True)
        cls.group = ItemGroup.objects.create(id=1657, name="Citadel", category=cls.category, published=True)
        cls.group_service = ItemGroup.objects.create(id=1322, name="Structure Service Module", category=cls.category_module, published=True)
        cls.keepstar = ItemType.objects.create(id=35834, name="Keepstar", group=cls.group, published=True)
        cls.neut = ItemType.objects.create(
            id=35920, name="Standup Heavy Energy Neutralizer I", group=cls.group, published=True
        )

        # Ship scanner test items
        ItemType.objects.create(id=35930, name="Standup Anticapital Missile Launcher I", group=cls.group, published=True)
        ItemType.objects.create(id=35931, name="Standup XL Energy Neutralizer I", group=cls.group, published=True)
        ItemType.objects.create(id=35932, name="Standup Focused Warp Disruptor I", group=cls.group, published=True)
        ItemType.objects.create(id=35933, name="Standup Target Painter I", group=cls.group, published=True)
        ItemType.objects.create(id=35934, name="Standup Ballistic Control System I", group=cls.group, published=True)
        ItemType.objects.create(id=35935, name="Standup Missile Guidance Enhancer I", group=cls.group, published=True)
        ItemType.objects.create(id=35936, name="Standup Cloning Center I", group=cls.group_service, published=True)
        ItemType.objects.create(id=35937, name="Standup Variable Spectrum ECM I", group=cls.group, published=True)

        cls.system_1dq = SolarSystem.objects.create(id=30004759, name="1DQ1-A", security_status=-0.5)
        cls.system_f9 = SolarSystem.objects.create(id=30001234, name="F9-FUV", security_status=-0.2)

        cls.user_no_perms = AuthUtils.create_user(username="no_perm_user")
        AuthUtils.add_main_character(cls.user_no_perms, "No Perm Char", 21100001)

        cls.user_basic = AuthUtils.create_user(username="basic_user")
        AuthUtils.add_main_character(cls.user_basic, "Basic User Char", 21100002)
        AuthUtils.add_permission_to_user_by_name("hostile.basic_access", cls.user_basic)

        cls.user_officer = AuthUtils.create_user(username="officer_user")
        AuthUtils.add_main_character(cls.user_officer, "Officer User Char", 21100003)
        AuthUtils.add_permission_to_user_by_name("hostile.basic_access", cls.user_officer)
        AuthUtils.add_permission_to_user_by_name("hostile.manage_intel", cls.user_officer)

        cls.client = Client()

    def setUp(self):
        self.user_basic.refresh_from_db()
        self.user_officer.refresh_from_db()
        self.user_no_perms.refresh_from_db()

    def test_unauthenticated_redirect(self):
        url = reverse("hostile:index")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_permission_denied_without_basic_access(self):
        self.client.force_login(self.user_no_perms)
        url = reverse("hostile:index")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_basic_user_can_access_views(self):
        self.client.force_login(self.user_basic)

        for view_name in [
            "index",
            "structures",
            "timers",
            "calculator",
            "systems",
            "pilots",
            "alliances",
            "doctrines",
        ]:
            url = reverse(f"hostile:{view_name}")
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, f"Failed accessing {view_name}")

    def test_doctrines_and_stagings_workflow(self):
        self.client.force_login(self.user_basic)
        alliance = HostileAlliance.objects.create(alliance_id=99000001, alliance_name="Test Hostiles", ticker="THOST")

        # Create staging system
        staging = HostileStagingSystem.objects.create(
            solar_system=self.system_1dq,
            alliance=alliance,
            staging_type="PRIMARY",
            is_primary=True,
            notes="Main fortress staging",
            created_by=self.user_basic,
        )

        # Create doctrine
        doctrine = HostileDoctrine.objects.create(
            name="Tempest Fleet Issue (TFI)",
            alliance=alliance,
            role_type="MAIN_FLEET",
            primary_ship_types="Tempest Fleet Issue, Guardian, Damnation",
            description="Main battleship doctrine with heavy artillery",
            created_by=self.user_basic,
        )
        doctrine.stagings.add(staging)

        # Create example fit
        tfi_fit = DoctrineFit.objects.create(
            doctrine=doctrine,
            name="Dual 800mm Main DPS",
            role="DPS",
            zkill_link="https://zkillboard.com/kill/12345678/",
            eft_format="[Tempest Fleet Issue, Main DPS]\nStandup Heavy Energy Neutralizer I\n",
            notes="Standard line DPS",
        )

        # Access doctrines page
        resp = self.client.get(reverse("hostile:doctrines"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Tempest Fleet Issue (TFI)")
        self.assertContains(resp, "1DQ1-A")
        self.assertContains(resp, "https://zkillboard.com/kill/12345678/")

        # Test doctrine fit modal
        modal_url = reverse("hostile:doctrine_fit_modal", args=[tfi_fit.id])
        modal_resp = self.client.get(modal_url)
        self.assertEqual(modal_resp.status_code, 200)
        self.assertContains(modal_resp, "Dual 800mm Main DPS")
        self.assertContains(modal_resp, "Standup Heavy Energy Neutralizer I")

        # Test officer verification of staging & doctrine
        self.client.force_login(self.user_officer)
        st_verify_url = reverse("hostile:staging_verify", args=[staging.id])
        self.client.post(st_verify_url)
        staging.refresh_from_db()
        self.assertTrue(staging.is_verified)

        doc_verify_url = reverse("hostile:doctrine_verify", args=[doctrine.id])
        self.client.post(doc_verify_url)
        doctrine.refresh_from_db()
        self.assertTrue(doctrine.is_verified)

    def test_fitting_modal_view(self):
        self.client.force_login(self.user_basic)

        struct = HostileStructure.objects.create(
            name="1DQ1-A 1-HQ - Imperial Palace",
            structure_type=self.keepstar,
            solar_system=self.system_1dq,
            created_by=self.user_basic,
        )
        fitting = StructureFitting.objects.create(
            structure=struct,
            name="Defense Fit",
            created_by=self.user_basic,
        )
        StructureModule.objects.create(
            fitting=fitting,
            module_type=self.neut,
            slot_type="HIGH",
            slot_number=1,
        )

        url = reverse("hostile:structure_fitting_modal", args=[struct.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1DQ1-A 1-HQ")
        self.assertContains(response, "Standup Heavy Energy Neutralizer I")

    def test_smart_paste_dscan(self):
        self.client.force_login(self.user_basic)
        dscan_text = "35834\t1DQ1-A Staging Citadel [CONDI]\tKeepstar\t15.0 AU"

        url = reverse("hostile:smart_paste")
        response = self.client.post(url, {"intel_text": dscan_text})
        self.assertEqual(response.status_code, 302)

        struct = HostileStructure.objects.filter(name="1DQ1-A Staging Citadel [CONDI]").first()
        self.assertIsNotNone(struct)
        self.assertEqual(struct.owner_ticker, "CONDI")

    def test_smart_paste_showinfo(self):
        self.client.force_login(self.user_basic)
        showinfo_text = (
            "[Keepstar] 1DQ1-A Super Palace [CONDI]\n"
            "Owner: Goonswarm Federation\n"
            "Solar System: 1DQ1-A\n"
            "Quantum Core: Installed\n"
            "State: Online\n"
            "Fitting:\n"
            "Standup Heavy Energy Neutralizer I\n"
        )
        url = reverse("hostile:smart_paste")
        response = self.client.post(url, {"intel_text": showinfo_text})
        self.assertEqual(response.status_code, 302)

        struct = HostileStructure.objects.filter(name="1DQ1-A Super Palace [CONDI]").first()
        self.assertIsNotNone(struct)
        self.assertEqual(struct.core_status, "FITTED")
        self.assertIsNotNone(struct.latest_fitting)

    def test_officer_moderation_actions(self):
        struct = HostileStructure.objects.create(
            name="Unverified Fort",
            solar_system=self.system_1dq,
            created_by=self.user_basic,
            is_verified=False,
        )
        obs = SystemObservation.objects.create(
            solar_system=self.system_1dq,
            threat_level="HIGH",
            observation_text="Camp warning",
            created_by=self.user_basic,
            is_verified=False,
            is_pinned=False,
        )

        # Basic user cannot verify
        self.client.force_login(self.user_basic)
        verify_url = reverse("hostile:structure_verify", args=[struct.id])
        resp = self.client.get(verify_url)
        self.assertEqual(resp.status_code, 302)
        struct.refresh_from_db()
        self.assertFalse(struct.is_verified)

        # Officer can verify and pin
        self.client.force_login(self.user_officer)
        resp = self.client.get(verify_url)
        self.assertEqual(resp.status_code, 302)
        struct.refresh_from_db()
        self.assertTrue(struct.is_verified)

        pin_url = reverse("hostile:system_observation_pin", args=[obs.id])
        self.client.get(pin_url)
        obs.refresh_from_db()
        self.assertTrue(obs.is_pinned)

        # Audit logs view accessible by officer
        audit_url = reverse("hostile:audit_logs")
        resp = self.client.get(audit_url)
        self.assertEqual(resp.status_code, 200)

    def test_local_threat_scan_views(self):
        self.client.force_login(self.user_basic)

        # GET threat scans list view
        url = reverse("hostile:threat_scans")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

        # POST new local threat scan
        post_data = {
            "local_text": "CynoAlt1\tHostile Corp\tHostile Alliance\nSuperPilot1\tHostile Corp\tHostile Alliance",
            "dscan_text": "11993\tArazu\tArazu\t10 km",
            "solar_system": self.system_1dq.id,
        }
        resp = self.client.post(url, post_data)
        self.assertEqual(resp.status_code, 302)
        scan = LocalThreatScan.objects.first()
        self.assertIsNotNone(scan)
        self.assertEqual(scan.pilot_count, 2)

        # View detail report
        detail_url = reverse("hostile:threat_scan_detail", args=[scan.id])
        resp = self.client.get(detail_url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "CynoAlt1")

    def test_smart_paste_auto_routing_local(self):
        self.client.force_login(self.user_basic)
        paste_url = reverse("hostile:smart_paste")

        # Chat log paste should automatically route to LocalThreatScan detail
        post_data = {
            "intel_text": "[ 18:00:00 ] HostileHunter > tackle on gate",
            "solar_system": self.system_1dq.id,
        }
        resp = self.client.post(paste_url, post_data)
        self.assertEqual(resp.status_code, 302)
        scan = LocalThreatScan.objects.first()
        self.assertIsNotNone(scan)
        self.assertIn(str(scan.id), resp.url)

    def test_alliances_list_zkill_sync_indicator(self):
        from django.core.cache import cache

        self.client.force_login(self.user_basic)
        alliance = HostileAlliance.objects.create(
            alliance_id=99009999,
            alliance_name="Syncing Test Alliance",
            ticker="SYNCT",
        )

        url = reverse("hostile:alliances")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "zKill Pending")

        # Simulate global sync running
        cache.set("hostile-intel-update-all-zkill-stats-lock", True, 60)
        try:
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200)
            self.assertContains(resp, "zKillboard Synchronization in Progress:")
            self.assertTrue(alliance.is_zkill_syncing)
        finally:
            cache.delete("hostile-intel-update-all-zkill-stats-lock")

    def test_api_solar_systems_search_and_infer(self):
        self.client.force_login(self.user_basic)

        # 1. Search endpoint
        search_url = reverse("hostile:api_solar_systems_search")
        resp = self.client.get(search_url, {"q": "1DQ"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(any(s["name"] == "1DQ1-A" for s in data["results"]))

        # 2. Infer endpoint
        infer_url = reverse("hostile:api_infer_system")
        dscan_sample = "35834\t1DQ1-A 1-HQ - Imperial Palace [CONDI]\tKeepstar\t14.2 AU"
        resp = self.client.post(infer_url, {"text": dscan_sample})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["solar_system_name"], "1DQ1-A")
        self.assertEqual(data["solar_system_id"], self.system_1dq.id)

    def test_smart_paste_structure_fitting_ship_scanner(self):
        """Tests ingesting ship scanner structure fitting format via smart paste"""
        self.client.force_login(self.user_basic)
        scan_text = (
            "High Power Slots\n"
            "Standup Anticapital Missile Launcher I\n"
            "Standup Anticapital Missile Launcher I\n"
            "Standup XL Energy Neutralizer I\n"
            "Standup XL Energy Neutralizer I\n"
            "Medium Power Slots\n"
            "Standup Focused Warp Disruptor I\n"
            "Standup Target Painter I\n"
            "Standup Variable Spectrum ECM I\n"
            "Standup Variable Spectrum ECM I\n"
            "Low Power Slots\n"
            "Standup Ballistic Control System I\n"
            "Standup Ballistic Control System I\n"
            "Standup Missile Guidance Enhancer I\n"
            "Service Slots\n"
            "Standup Cloning Center I\n"
        )
        paste_url = reverse("hostile:smart_paste")
        post_data = {
            "intel_text": scan_text,
            "solar_system": self.system_1dq.id,
        }
        resp = self.client.post(paste_url, post_data)
        self.assertEqual(resp.status_code, 302)

        struct = HostileStructure.objects.filter(solar_system=self.system_1dq).first()
        self.assertIsNotNone(struct)
        fitting = struct.latest_fitting
        self.assertIsNotNone(fitting)
        self.assertEqual(fitting.modules.filter(slot_type="HIGH").count(), 4)
        self.assertEqual(fitting.modules.filter(slot_type="MED").count(), 4)
        self.assertEqual(fitting.modules.filter(slot_type="LOW").count(), 3)
        self.assertEqual(fitting.modules.filter(slot_type="SERVICE").count(), 1)

    def test_structure_fitting_save_view(self):
        """Tests directly updating a structure's fitting with ship scanner scan text"""
        self.client.force_login(self.user_basic)
        struct = HostileStructure.objects.create(
            name="1DQ1-A Forward Staging",
            solar_system=self.system_1dq,
            created_by=self.user_basic,
        )
        scan_text = (
            "High Power Slots\n"
            "Standup Anticapital Missile Launcher I\n"
            "Service Slots\n"
            "Standup Cloning Center I\n"
        )
        save_url = reverse("hostile:structure_fitting_save", args=[struct.id])
        post_data = {
            "fit_name": "Scouted Live Fit",
            "fitting_text": scan_text,
        }
        resp = self.client.post(save_url, post_data)
        self.assertEqual(resp.status_code, 302)

        struct.refresh_from_db()
        self.assertEqual(struct.fittings.count(), 1)
        latest = struct.latest_fitting
        self.assertEqual(latest.name, "Scouted Live Fit")
        self.assertEqual(latest.modules.count(), 2)

    def test_smart_paste_data_analyzer_hack_workflow(self):
        """Tests end-to-end ingestion of a Data Analyzer hack via Smart Paste and calculator redirect"""
        self.client.force_login(self.user_basic)
        hack_text = "F9-FUV - Darkside X\nHour (+- 3 hrs):\t02:00"

        paste_url = reverse("hostile:smart_paste")
        resp = self.client.post(paste_url, {"intel_text": hack_text})

        # Should redirect to calculator with structure_id
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/hostile/calculator/", resp.url)

        # Structure should be created with inferred system and vulnerability hour 2
        struct = HostileStructure.objects.filter(name="F9-FUV - Darkside X").first()
        self.assertIsNotNone(struct)
        self.assertEqual(struct.solar_system, self.system_f9)
        self.assertEqual(struct.vulnerability_hour, 2)
        self.assertEqual(struct.vulnerability_window, "02:00 (+- 3 hrs)")

        # Ingesting an updated hack should update the existing structure
        updated_hack = "F9-FUV - Darkside X\nHour (+- 3 hrs):\t04:00"
        resp2 = self.client.post(paste_url, {"intel_text": updated_hack})
        self.assertEqual(resp2.status_code, 302)
        struct.refresh_from_db()
        self.assertEqual(struct.vulnerability_hour, 4)
        self.assertEqual(struct.vulnerability_window, "04:00 (+- 3 hrs)")

    def test_threat_scan_list_fast_creation(self):
        """Tests that local threat scan creation does not block on external zKill queries and creates scan immediately"""
        self.client.force_login(self.user_basic)
        local_paste = "HostilePilot1\tHostile Corp\tHostile Alliance\nHostilePilot2\tHostile Corp\tHostile Alliance"
        url = reverse("hostile:threat_scans")
        resp = self.client.post(
            url,
            {
                "local_text": local_paste,
                "solar_system": self.system_1dq.id,
            },
        )
        self.assertEqual(resp.status_code, 302)
        scan = LocalThreatScan.objects.filter(solar_system=self.system_1dq).first()
        self.assertIsNotNone(scan)
        self.assertEqual(scan.pilot_count, 2)
        self.assertIn("/hostile/threat-scans/", resp.url)

    def test_api_threat_scan_pilot_intelligence_streaming(self):
        """Tests asynchronous dynamic pilot combat intelligence enrichment endpoint"""
        from unittest.mock import patch

        self.client.force_login(self.user_basic)
        scan = LocalThreatScan.objects.create(
            solar_system=self.system_1dq,
            raw_local_text="April Springtime\nCynoDropper",
            pilot_count=2,
            hostile_count=2,
            blops_drop_chance=0,
            cap_drop_chance=0,
            threat_level="LOW",
            pilots_data=[
                {
                    "character_id": 91001,
                    "character_name": "April Springtime",
                    "corporation_id": None,
                    "corporation_name": "",
                    "corporation_ticker": "",
                    "alliance_id": None,
                    "alliance_name": "",
                    "alliance_ticker": "",
                    "is_cyno_alt": False,
                    "is_blops_pilot": False,
                    "is_capital_pilot": False,
                    "danger_ratio": 0,
                    "top_ships": [],
                    "zkill_synced": False,
                },
                {
                    "character_id": 91002,
                    "character_name": "CynoDropper",
                    "corporation_id": None,
                    "corporation_name": "",
                    "corporation_ticker": "",
                    "alliance_id": None,
                    "alliance_name": "",
                    "alliance_ticker": "",
                    "is_cyno_alt": False,
                    "is_blops_pilot": False,
                    "is_capital_pilot": False,
                    "danger_ratio": 0,
                    "top_ships": [],
                    "zkill_synced": False,
                },
            ],
            created_by=self.user_basic,
        )

        mock_resolved = {
            "april springtime": {
                "character_id": 91001,
                "character_name": "April Springtime",
                "corporation_id": 2001,
                "corporation_name": "Springtime Corp",
                "corporation_ticker": "SPRG",
                "alliance_id": 3001,
                "alliance_name": "Spring Alliance",
                "alliance_ticker": "SPRA",
            },
            "cynodropper": {
                "character_id": 91002,
                "character_name": "CynoDropper",
                "corporation_id": 2002,
                "corporation_name": "Brute Force Inc",
                "corporation_ticker": "BRUT",
                "alliance_id": 3002,
                "alliance_name": "Brutal Alliance",
                "alliance_ticker": "BRTA",
            },
        }

        def mock_combat_intel(cid):
            if cid == 91001:
                return {
                    "danger_ratio": 92,
                    "kills_count": 1420,
                    "losses_count": 112,
                    "solo_kills": 240,
                    "top_ships": ["Redeemer", "Widow"],
                    "is_blops_pilot": True,
                    "is_fc": True,
                    "fc_level": "HIGH",
                    "fc_score": 105,
                    "activity": {"18": 100, "19": 150},
                }
            return {
                "danger_ratio": 20,
                "kills_count": 10,
                "losses_count": 50,
                "top_ships": ["Arazu"],
                "is_cyno_alt": True,
                "cyno_count": 8,
                "activity": {"19": 50, "20": 50},
            }

        api_url = reverse("hostile:api_threat_scan_pilot_intel", args=[scan.id])

        with patch("hostile.services.entity_resolver.EntityResolver.resolve_character", side_effect=lambda q: mock_resolved.get(str(q).lower() if not isinstance(q, int) else ("april springtime" if q == 91001 else "cynodropper"))):
            with patch("hostile.services.zkill_client.zkill_client.get_character_combat_intelligence", side_effect=mock_combat_intel):
                # 1. First batch request for 1 pilot
                resp = self.client.get(api_url, {"batch_size": 1})
                self.assertEqual(resp.status_code, 200)
                data = resp.json()
                self.assertTrue(data["success"])
                self.assertEqual(data["synced_count"], 1)
                self.assertEqual(data["remaining_count"], 1)
                self.assertFalse(data["is_finished"])
                self.assertEqual(len(data["pilots"]), 1)
                self.assertEqual(data["pilots"][0]["character_name"], "April Springtime")
                self.assertEqual(data["pilots"][0]["corporation_name"], "Springtime Corp")
                self.assertEqual(data["pilots"][0]["alliance_name"], "Spring Alliance")
                self.assertEqual(data["pilots"][0]["danger_ratio"], 92)
                self.assertTrue(data["pilots"][0]["is_blops_pilot"])

                # 2. Second batch request for remaining pilot via POST
                resp2 = self.client.post(
                    api_url,
                    {"batch_size": 5},
                    content_type="application/json",
                )
                self.assertEqual(resp2.status_code, 200)
                data2 = resp2.json()
                self.assertTrue(data2["success"])
                self.assertEqual(data2["synced_count"], 2)
                self.assertEqual(data2["remaining_count"], 0)
                self.assertTrue(data2["is_finished"])
                self.assertGreater(data2["blops_drop_chance"], 0)
                self.assertEqual(data2["activity_profile"]["primary_timezone"], "EUTZ")
                self.assertEqual(data2["activity_profile"]["tz_breakdown"]["EUTZ"], 350)
                self.assertEqual(data2["activity_profile"]["total_events"], 350)

        # Confirm scan model in database was updated
        scan.refresh_from_db()
        self.assertGreater(scan.blops_drop_chance, 0)
        self.assertEqual(scan.activity_profile["primary_timezone"], "EUTZ")
        self.assertEqual(len(scan.pilots_data), 2)
        self.assertTrue(all(p.get("zkill_synced") for p in scan.pilots_data))

    def test_api_threat_scan_pilot_intelligence_permissions(self):
        """Tests permission gate on the streaming API endpoint"""
        scan = LocalThreatScan.objects.create(
            solar_system=self.system_1dq,
            raw_local_text="April Springtime",
            pilot_count=1,
            hostile_count=1,
            created_by=self.user_basic,
        )
        api_url = reverse("hostile:api_threat_scan_pilot_intel", args=[scan.id])

        # Unauthenticated
        resp_unauth = self.client.get(api_url)
        self.assertEqual(resp_unauth.status_code, 302)

        # No permission
        self.client.force_login(self.user_no_perms)
        resp_noperm = self.client.get(api_url)
        self.assertEqual(resp_noperm.status_code, 302)

    def test_api_sov_timers_polling_and_serialization(self):
        """Tests live sovereignty timers polling API endpoint with active/concluded timers, hashing, and origin routing"""
        self.client.force_login(self.user_basic)
        now = timezone.now()

        # 1. Create upcoming contest timer
        upcoming = StructureTimer.objects.create(
            solar_system=self.system_1dq,
            timer_type="SOV_IHUB",
            timer_datetime=now + timedelta(hours=3),
            is_verified=True,
            is_sov_campaign=True,
            is_concluded=False,
            defender_id=99000099,
            defender_name="Hostile Alliance",
            defender_ticker="H-ALLI",
            defender_score=0.6,
            notes="Sovereignty Contest: Ihub",
        )

        # 2. Create ongoing contest timer (started)
        ongoing = StructureTimer.objects.create(
            solar_system=self.system_f9,
            timer_type="SOV_TCU",
            timer_datetime=now - timedelta(minutes=20),
            is_verified=True,
            is_sov_campaign=True,
            is_concluded=False,
            defender_id=88000088,
            defender_name="Target Alliance",
            defender_ticker="TGT",
            defender_score=0.45,
            notes="Sovereignty Contest: TCU",
        )

        # 3. Create concluded timer
        concluded = StructureTimer.objects.create(
            solar_system=self.system_1dq,
            timer_type="SOV_IHUB",
            timer_datetime=now - timedelta(hours=5),
            is_verified=True,
            is_sov_campaign=True,
            is_concluded=True,
            defender_id=99000099,
            defender_name="Hostile Alliance",
            defender_ticker="H-ALLI",
            defender_score=1.0,
            notes="Sovereignty Contest: Station",
        )

        api_url = reverse("hostile:api_sov_timers")

        # Call without origin
        resp = self.client.get(api_url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertIn("hash", data)
        self.assertEqual(data["active_count"], 2)
        self.assertEqual(data["concluded_count"], 1)
        self.assertEqual(len(data["upcoming_timers"]), 2)
        self.assertEqual(len(data["expired_timers"]), 1)

        # Check serialized fields
        first_upcoming = next(t for t in data["upcoming_timers"] if t["id"] == upcoming.id)
        self.assertEqual(first_upcoming["structure_event_name"], "Infrastructure Hub (I-Hub)")
        self.assertEqual(first_upcoming["defender_percent"], 60)
        self.assertEqual(first_upcoming["attacker_percent"], 40)
        self.assertFalse(first_upcoming["has_started"])
        self.assertEqual(first_upcoming["solar_system_name"], "1DQ1-A")

        ongoing_t = next(t for t in data["upcoming_timers"] if t["id"] == ongoing.id)
        self.assertTrue(ongoing_t["has_started"])
        self.assertTrue(ongoing_t["is_ongoing"])
        self.assertEqual(ongoing_t["defender_percent"], 45)

        concluded_t = data["expired_timers"][0]
        self.assertEqual(concluded_t["id"], concluded.id)
        self.assertTrue(concluded_t["is_concluded"])
        self.assertIn("Defended", concluded_t["outcome_display"])

        # Call with origin system to test distance routing
        resp_origin = self.client.get(api_url, {"origin": self.system_1dq.id})
        self.assertEqual(resp_origin.status_code, 200)
        data_origin = resp_origin.json()
        self.assertEqual(data_origin["selected_origin_id"], self.system_1dq.id)
        first_up_dist = next(t for t in data_origin["upcoming_timers"] if t["id"] == upcoming.id)
        self.assertIsNotNone(first_up_dist.get("distance_data"))
        self.assertEqual(first_up_dist["distance_data"]["ly"], 0.0)

    def test_api_sov_timers_esi_sync_and_change_detection(self):
        """Tests that api_sov_timers syncs campaigns from ESI and produces distinct hashes on changes"""
        from unittest.mock import patch

        self.client.force_login(self.user_basic)
        api_url = reverse("hostile:api_sov_timers")
        fixed_time = timezone.now() + timedelta(hours=2)

        mock_campaigns_initial = [
            {
                "campaign_id": 901,
                "solar_system_id": self.system_1dq.id,
                "structure_type_id": 32226,
                "event_type": "ihub_defense",
                "start_time": fixed_time.isoformat(),
                "defender_id": 99000099,
                "defender_score": 0.5,
            }
        ]

        with patch("hostile.services.esi_client.esi_client.get_sovereignty_campaigns", return_value=mock_campaigns_initial):
            resp1 = self.client.get(api_url, {"sync": "1"})
            self.assertEqual(resp1.status_code, 200)
            data1 = resp1.json()
            hash1 = data1["hash"]
            self.assertEqual(data1["active_count"], 1)
            self.assertEqual(data1["upcoming_timers"][0]["campaign_id"], 901)
            self.assertEqual(data1["upcoming_timers"][0]["defender_percent"], 50)

        # Update defender score to test change detection hash change
        mock_campaigns_updated = [
            {
                "campaign_id": 901,
                "solar_system_id": self.system_1dq.id,
                "structure_type_id": 32226,
                "event_type": "ihub_defense",
                "start_time": fixed_time.isoformat(),
                "defender_id": 99000099,
                "defender_score": 0.85,
            }
        ]

        with patch("hostile.services.esi_client.esi_client.get_sovereignty_campaigns", return_value=mock_campaigns_updated):
            resp2 = self.client.get(api_url, {"sync": "1"})
            self.assertEqual(resp2.status_code, 200)
            data2 = resp2.json()
            hash2 = data2["hash"]
            self.assertNotEqual(hash1, hash2)
            self.assertEqual(data2["upcoming_timers"][0]["defender_percent"], 85)

    def test_api_sov_timers_permissions(self):
        """Tests permission checks on api_sov_timers endpoint"""
        api_url = reverse("hostile:api_sov_timers")

        # Unauthenticated
        resp_unauth = self.client.get(api_url)
        self.assertEqual(resp_unauth.status_code, 302)

        # User without permission
        self.client.force_login(self.user_no_perms)
        resp_noperm = self.client.get(api_url)
        self.assertEqual(resp_noperm.status_code, 302)

    def test_pilots_list_search_by_name_and_tags(self):
        """Tests searching pilot dossiers by name, corporation, alliance, alts, and behavioral tags"""
        self.client.force_login(self.user_basic)
        url = reverse("hostile:pilots")

        # Create diverse pilot dossiers
        main_pilot = HostilePilotDossier.objects.create(
            character_id=94001,
            character_name="Valkyrie Queen",
            corporation_name="Red Federation",
            alliance_name="Hostile Alliance",
            is_titan_pilot=True,
            is_fc=True,
            fc_level="HIGH",
            fc_score=120,
            is_verified=True,
            notes="Primary enemy fleet commander and titan pilot",
        )

        alt_pilot = HostilePilotDossier.objects.create(
            character_id=94002,
            character_name="Sneaky Scout",
            corporation_name="NPC Holding Corp",
            associated_alliance_name="Hostile Alliance",
            main_character=main_pilot,
            is_alt=True,
            is_cyno_alt=True,
            cyno_count=8,
            is_out_of_corp=True,
            alt_confidence="HIGH",
            alt_inference_reason="Cyno Association",
        )

        bait_pilot = HostilePilotDossier.objects.create(
            character_id=94003,
            character_name="Bait Tanker",
            corporation_name="Trap Syndicate",
            is_bait=True,
            bait_level="HIGH",
            bait_count=15,
        )

        ganker_pilot = HostilePilotDossier.objects.create(
            character_id=94004,
            character_name="Highsec Pirate",
            corporation_name="Gank Squad",
            is_ganker=True,
            ganker_count=30,
        )

        awox_pilot = HostilePilotDossier.objects.create(
            character_id=94005,
            character_name="Awox Master",
            corporation_name="Infiltrator Corp",
            is_awox=True,
            awox_count=12,
        )

        # 1. Search by pilot character name
        res_name = self.client.get(url, {"q": "Valkyrie"})
        self.assertEqual(res_name.status_code, 200)
        self.assertIn(main_pilot, res_name.context["pilots"])
        self.assertNotIn(alt_pilot, res_name.context["pilots"])
        self.assertNotIn(bait_pilot, res_name.context["pilots"])
        self.assertEqual(len(res_name.context["pilots"]), 1)
        self.assertContains(res_name, "Valkyrie Queen")

        # 2. Search by corporation name
        res_corp = self.client.get(url, {"q": "Trap Syndicate"})
        self.assertEqual(res_corp.status_code, 200)
        self.assertIn(bait_pilot, res_corp.context["pilots"])
        self.assertNotIn(main_pilot, res_corp.context["pilots"])
        self.assertEqual(len(res_corp.context["pilots"]), 1)
        self.assertContains(res_corp, "Bait Tanker")

        # 3. Search by tag keyword in search query 'q'
        res_cyno_q = self.client.get(url, {"q": "cyno"})
        self.assertEqual(res_cyno_q.status_code, 200)
        self.assertIn(alt_pilot, res_cyno_q.context["pilots"])
        self.assertNotIn(ganker_pilot, res_cyno_q.context["pilots"])
        self.assertEqual(len(res_cyno_q.context["pilots"]), 1)
        self.assertContains(res_cyno_q, "Sneaky Scout")

        res_titan_q = self.client.get(url, {"q": "titan"})
        self.assertEqual(res_titan_q.status_code, 200)
        self.assertIn(main_pilot, res_titan_q.context["pilots"])
        self.assertNotIn(bait_pilot, res_titan_q.context["pilots"])
        self.assertEqual(len(res_titan_q.context["pilots"]), 1)
        self.assertContains(res_titan_q, "Valkyrie Queen")

        res_bait_q = self.client.get(url, {"q": "bait"})
        self.assertEqual(res_bait_q.status_code, 200)
        self.assertIn(bait_pilot, res_bait_q.context["pilots"])
        self.assertNotIn(main_pilot, res_bait_q.context["pilots"])
        self.assertEqual(len(res_bait_q.context["pilots"]), 1)
        self.assertContains(res_bait_q, "Bait Tanker")

        res_ganker_q = self.client.get(url, {"q": "ganker"})
        self.assertEqual(res_ganker_q.status_code, 200)
        self.assertIn(ganker_pilot, res_ganker_q.context["pilots"])
        self.assertNotIn(awox_pilot, res_ganker_q.context["pilots"])
        self.assertEqual(len(res_ganker_q.context["pilots"]), 1)
        self.assertContains(res_ganker_q, "Highsec Pirate")

        # 4. Filter by explicit 'tag' parameter
        res_tag_cyno = self.client.get(url, {"tag": "cyno"})
        self.assertEqual(res_tag_cyno.status_code, 200)
        self.assertIn(alt_pilot, res_tag_cyno.context["pilots"])
        self.assertNotIn(ganker_pilot, res_tag_cyno.context["pilots"])
        self.assertEqual(len(res_tag_cyno.context["pilots"]), 1)
        self.assertContains(res_tag_cyno, "Sneaky Scout")

        res_tag_fc = self.client.get(url, {"tag": "fc"})
        self.assertEqual(res_tag_fc.status_code, 200)
        self.assertIn(main_pilot, res_tag_fc.context["pilots"])
        self.assertNotIn(ganker_pilot, res_tag_fc.context["pilots"])
        self.assertEqual(len(res_tag_fc.context["pilots"]), 1)
        self.assertContains(res_tag_fc, "Valkyrie Queen")

        res_tag_awox = self.client.get(url, {"tag": "awox"})
        self.assertEqual(res_tag_awox.status_code, 200)
        self.assertIn(awox_pilot, res_tag_awox.context["pilots"])
        self.assertNotIn(main_pilot, res_tag_awox.context["pilots"])
        self.assertEqual(len(res_tag_awox.context["pilots"]), 1)
        self.assertContains(res_tag_awox, "Awox Master")

        res_tag_ooc = self.client.get(url, {"tag": "out_of_corp"})
        self.assertEqual(res_tag_ooc.status_code, 200)
        self.assertIn(alt_pilot, res_tag_ooc.context["pilots"])
        self.assertNotIn(main_pilot, res_tag_ooc.context["pilots"])
        self.assertEqual(len(res_tag_ooc.context["pilots"]), 1)
        self.assertContains(res_tag_ooc, "Sneaky Scout")

        # 5. Combined role tab and search query
        res_combined = self.client.get(url, {"role": "alts", "q": "Sneaky"})
        self.assertEqual(res_combined.status_code, 200)
        self.assertIn(alt_pilot, res_combined.context["pilots"])
        self.assertNotIn(main_pilot, res_combined.context["pilots"])
        self.assertEqual(len(res_combined.context["pilots"]), 1)
        self.assertContains(res_combined, "Sneaky Scout")

        # 6. Combined mismatch returns empty state properly
        res_empty = self.client.get(url, {"role": "cynos", "q": "Highsec Pirate"})
        self.assertEqual(res_empty.status_code, 200)
        self.assertEqual(len(res_empty.context["pilots"]), 0)
        self.assertContains(res_empty, "No matching pilot dossiers found")

    def test_pilot_sync_zkill_view(self):
        """Tests the single-pilot zKillboard sync endpoint"""
        self.client.force_login(self.user_basic)
        pilot = HostilePilotDossier.objects.create(
            character_id=980777,
            character_name="Kixkahn Target",
            corporation_name="Southern Cross Monopoly",
        )

        with patch("hostile.services.zkill_client.zkill_client.sync_pilot_dossier_zkill") as mock_sync:
            mock_sync.return_value = True
            url = reverse("hostile:pilot_sync_zkill", args=[pilot.id])
            res = self.client.get(url, follow=True)
            self.assertEqual(res.status_code, 200)
            mock_sync.assert_called_once_with(pilot)

    def test_pilot_card_danger_meter_and_tickers_rendering(self):
        """Tests rendering of 3-box danger meter, sec status, age, tickers, and zKill badges on pilot list"""
        from datetime import date

        self.client.force_login(self.user_basic)
        pilot = HostilePilotDossier.objects.create(
            character_id=980888,
            character_name="Kixkahn Officer",
            corporation_name="Southern Cross Monopoly",
            corporation_ticker="STHCM",
            alliance_name="Flying Dangerous",
            alliance_ticker="FIGL",
            danger_ratio=67,
            gang_ratio=80,
            security_status=-0.3,
            birthday=date(2004, 3, 20),
            is_fc=True,
            fc_level="LOW",
            is_bait=True,
            bait_level="LOW",
            bait_count=4,
            is_blops_pilot=True,
            blops_count=4,
            top_ships=["Redeemer", "Panther"],
        )

        url = reverse("hostile:pilots")
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)

        # Check tickers and sec status rendering
        self.assertContains(res, "[STHCM]")
        self.assertContains(res, "&lt;FIGL&gt;")
        self.assertContains(res, "Sec: -0.30")
        self.assertContains(res, "2004-03-20")

        # Check danger meter
        self.assertContains(res, "danger-meter-container")
        self.assertContains(res, "filled-danger")

        # Check tactical zKill badge classes
        self.assertContains(res, "badge-zkill-fc")
        self.assertContains(res, "FC (Low)")
        self.assertContains(res, "badge-zkill-bait")
        self.assertContains(res, "Bait (Low 4)")
        self.assertContains(res, "badge-zkill-blops")
        self.assertContains(res, "Blops (4)")
        self.assertContains(res, "Top Ships:")
        self.assertContains(res, "Redeemer, Panther")

    def test_index_dashboard_revamp_triage_and_urgency_metrics(self):
        """Test the revamped tactical command center dashboard with urgency KPI metrics and fast triage elements"""
        self.client.force_login(self.user_basic)
        now = timezone.now()

        # Create hostile alliance
        alliance = HostileAlliance.objects.create(
            alliance_id=99000101, alliance_name="Hostile Legion", ticker="HLEG"
        )

        # 1. Critical Timer (< 6h)
        timer_6h = StructureTimer.objects.create(
            solar_system=self.system_1dq,
            timer_type="ARMOR",
            timer_datetime=now + timedelta(hours=3),
            defender_id=alliance.alliance_id,
            defender_name=alliance.alliance_name,
            defender_ticker=alliance.ticker,
        )

        # 2. Timer (< 24h)
        timer_24h = StructureTimer.objects.create(
            solar_system=self.system_1dq,
            timer_type="HULL",
            timer_datetime=now + timedelta(hours=14),
            defender_id=alliance.alliance_id,
            defender_name=alliance.alliance_name,
            defender_ticker=alliance.ticker,
        )

        # 3. Active Gate Camp (< 24h)
        camp = SystemObservation.objects.create(
            solar_system=self.system_1dq,
            threat_level="EXTREME",
            observation_text="Interdictor bubble camp on gate with HICs and smartbombing Machariels",
            is_pinned=True,
            created_by=self.user_basic,
        )

        # 4. Sovereignty Campaign (Currently Contested - started 30m ago)
        sov_timer = StructureTimer.objects.create(
            solar_system=self.system_1dq,
            timer_type="SOV_IHUB",
            timer_datetime=now - timedelta(minutes=30),
            is_sov_campaign=True,
            defender_id=alliance.alliance_id,
            defender_name=alliance.alliance_name,
            defender_ticker=alliance.ticker,
            defender_score=0.6,
        )

        # Request index
        res = self.client.get(reverse("hostile:index"))
        self.assertEqual(res.status_code, 200)

        # Verify urgency metrics
        stats = res.context["stats"]
        self.assertGreaterEqual(stats["timers_6h_count"], 1)
        self.assertGreaterEqual(stats["timers_24h_count"], 2)
        self.assertGreaterEqual(stats["active_camps_count"], 1)
        self.assertGreaterEqual(stats["active_sov_contests_count"], 1)

        # Verify template components
        self.assertContains(res, "Tactical Operations Command")
        self.assertContains(res, "LIVE INTEL")
        self.assertContains(res, "Sovereignty & Structure Campaign Timers")
        self.assertContains(res, "Active Sov Campaigns")
        self.assertContains(res, "Timers < 6 Hours")
        self.assertContains(res, "Timers < 24h")
        self.assertContains(res, "Active Gate Camps")
        self.assertContains(res, "Hotspots (7d)")
        self.assertContains(res, "Tracked Assets")
        self.assertContains(res, "Supers / Cynos")
        self.assertContains(res, "Interdictor bubble camp on gate")
        self.assertContains(res, "Real-Time Intelligence Feed")

    def test_index_dashboard_active_sov_campaigns_untracked_entities(self):
        """Verify active sov campaigns count and deck display campaigns currently being contested even when defender is not a registered HostileAlliance"""
        self.client.force_login(self.user_basic)
        now = timezone.now()

        # Delete any hostile alliances
        HostileAlliance.objects.all().delete()
        HostileCorporation.objects.all().delete()

        # Create public sovereignty contest timer for un-tracked entity that is currently contested (started 30m ago)
        StructureTimer.objects.create(
            solar_system=self.system_1dq,
            timer_type="SOV_TCU",
            timer_datetime=now - timedelta(minutes=30),
            is_sov_campaign=True,
            defender_id=88888888,
            defender_name="Third Party Sov Holder",
            defender_ticker="TPSH",
            defender_score=0.7,
        )

        # Create upcoming sovereignty campaign (not yet started - 4h in future)
        StructureTimer.objects.create(
            solar_system=self.system_1dq,
            timer_type="SOV_IHUB",
            timer_datetime=now + timedelta(hours=4),
            is_sov_campaign=True,
            defender_id=88888888,
            defender_name="Third Party Sov Holder",
            defender_ticker="TPSH",
            defender_score=0.6,
        )

        res = self.client.get(reverse("hostile:index"))
        self.assertEqual(res.status_code, 200)

        stats = res.context["stats"]
        # Only the 1 currently contested campaign is counted in active_sov_contests_count
        self.assertEqual(stats["active_sov_contests_count"], 1)
        self.assertEqual(len(res.context["active_sov_campaigns"]), 1)
        self.assertContains(res, "Active Sovereignty Battles")
        self.assertContains(res, "Third Party Sov Holder")
        self.assertContains(res, "Score: 70% / 30%")

    def test_index_dashboard_staging_proximity_and_hotspots(self):
        """Test home staging distance calculation and hostile hotspot aggregation on index dashboard"""
        self.client.force_login(self.user_basic)
        alliance = HostileAlliance.objects.create(
            alliance_id=99000102, alliance_name="Shadow Coalition", ticker="SHAD"
        )

        # Create primary staging system
        staging = HostileStagingSystem.objects.create(
            solar_system=self.system_1dq,
            alliance=alliance,
            staging_type="PRIMARY",
            is_primary=True,
            created_by=self.user_basic,
        )

        # Create active structure
        struct = HostileStructure.objects.create(
            name="Forward Staging Fortizar",
            solar_system=self.system_1dq,
            alliance=alliance,
            created_by=self.user_basic,
        )

        # Request with explicit staging origin
        res = self.client.get(reverse("hostile:index"), {"origin": self.system_1dq.id})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context["selected_origin_id"], self.system_1dq.id)
        self.assertEqual(res.context["selected_origin_name"], self.system_1dq.name)

        # Verify hotspots list contains 1DQ1-A
        hotspots = res.context["sorted_hotspots"]
        self.assertTrue(any(h["system"].id == self.system_1dq.id for h in hotspots))

        # Verify session persistence of home staging
        self.assertEqual(self.client.session.get("hostile_home_staging"), self.system_1dq.id)

    def test_pilot_detail_view(self):
        """Test detailed pilot dossier view with character info, alts, and threat sightings"""
        self.client.force_login(self.user_basic)
        main_pilot = HostilePilotDossier.objects.create(
            character_id=980991,
            character_name="Main Hunter",
            corporation_name="Hunting Corp",
            is_cyno_alt=True,
            cyno_count=5,
            is_blops_pilot=True,
            blops_count=3,
            danger_ratio=85,
            security_status=-5.5,
            notes="Primary cyno hunter for deployment fleet.",
        )
        alt_pilot = HostilePilotDossier.objects.create(
            character_id=980992,
            character_name="Alt Scout",
            corporation_name="Scouting Corp",
            is_alt=True,
            main_character=main_pilot,
            alt_confidence="HIGH",
            alt_inference_reason="Name Pattern Correlation",
        )

        # Create linked observation / sitrep
        obs = SystemObservation.objects.create(
            solar_system=self.system_1dq,
            observation_text="Spotted Main Hunter lighting covert cyno near gate",
            threat_level="EXTREME",
            created_by=self.user_basic,
        )

        # Create scan sighting
        scan = LocalThreatScan.objects.create(
            solar_system=self.system_1dq,
            raw_local_text="Main Hunter\nAlt Scout",
            pilot_count=2,
            blops_drop_chance=75,
            created_by=self.user_basic,
        )

        url = reverse("hostile:pilot_detail", args=[main_pilot.id])
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)

        # Verify pilot header & stats
        self.assertContains(res, "Main Hunter")
        self.assertContains(res, "Hunting Corp")
        self.assertContains(res, "85% Danger")
        self.assertContains(res, "Sec: -5.50")

        # Verify role tags
        self.assertContains(res, "Cyno (5)")
        self.assertContains(res, "Blops (3)")

        # Verify alt network
        self.assertContains(res, "Alt Scout")
        self.assertContains(res, "HIGH")

        # Verify sightings & observations
        self.assertContains(res, "Spotted Main Hunter")
        self.assertContains(res, "Recent Local Threat Scan Sightings")
        self.assertContains(res, "75%")

    def test_friendly_alliances_and_corporations_ignored_in_local_scanner(self):
        """Test marking alliances and corporations as friendly/ignored and verifying exclusion in local threat scanner"""
        from hostile.parsers.local import LocalThreatParser

        # Create friendly alliance and corporation
        friendly_alliance = HostileAlliance.objects.create(
            alliance_id=99000999,
            alliance_name="Blue Friendly Alliance",
            ticker="BLUEA",
            is_friendly=True,
        )
        friendly_corp = HostileCorporation.objects.create(
            corporation_id=98000999,
            corporation_name="Blue Friendly Corp",
            ticker="BLUEC",
            is_friendly=True,
        )

        # Create hostile alliance
        hostile_alliance = HostileAlliance.objects.create(
            alliance_id=99000888,
            alliance_name="Red Hostile Alliance",
            ticker="REDA",
            is_friendly=False,
        )

        # Check is_ignored_entity
        self.assertTrue(LocalThreatParser.is_ignored_entity(alliance_name="Blue Friendly Alliance"))
        self.assertTrue(LocalThreatParser.is_ignored_entity(corp_name="Blue Friendly Corp"))
        self.assertTrue(LocalThreatParser.is_ignored_entity(alliance_id=99000999))
        self.assertTrue(LocalThreatParser.is_ignored_entity(corp_id=98000999))
        self.assertTrue(LocalThreatParser.is_ignored_entity(ticker="BLUEA"))

        # Red alliance is not ignored
        self.assertFalse(LocalThreatParser.is_ignored_entity(alliance_name="Red Hostile Alliance"))
        self.assertFalse(LocalThreatParser.is_ignored_entity(ticker="REDA"))

        # Verify AllianceQuerySet methods
        self.assertIn(friendly_alliance, HostileAlliance.objects.friendlies())
        self.assertNotIn(friendly_alliance, HostileAlliance.objects.hostiles())
        self.assertIn(hostile_alliance, HostileAlliance.objects.hostiles())
        self.assertNotIn(hostile_alliance, HostileAlliance.objects.friendlies())
