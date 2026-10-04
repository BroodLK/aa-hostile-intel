"""
Tests for full Edit and Delete workflows across all Hostile Intel data objects:
Timers, System Observations, Pilot Dossiers, Alliances, Corporations, Staging Systems,
Fleet Doctrines, Example Fits, and Local Threat Scans.
"""

# Standard Library
from datetime import timedelta

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
    IntelAuditLog,
    LocalThreatScan,
    StructureTimer,
    SystemObservation,
)


class TestCrudOperations(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category_structure = ItemCategory.objects.create(id=65, name="Structure", published=True)
        cls.group_citadel = ItemGroup.objects.create(id=1657, name="Citadel", category=cls.category_structure, published=True)
        cls.astrahus = ItemType.objects.create(id=35832, name="Astrahus", group=cls.group_citadel, published=True)

        cls.category_ship = ItemCategory.objects.create(id=6, name="Ship", published=True)
        cls.group_battleship = ItemGroup.objects.create(id=27, name="Battleship", category=cls.category_ship, published=True)
        cls.tfi = ItemType.objects.create(id=33472, name="Tempest Fleet Issue", group=cls.group_battleship, published=True)

        cls.system = SolarSystem.objects.create(id=30004759, name="1DQ1-A", security_status=-0.5)

        cls.alliance = HostileAlliance.objects.create(
            alliance_id=99000099, alliance_name="Hostile Alliance", ticker="H-ALLI"
        )
        cls.corporation = HostileCorporation.objects.create(
            corporation_id=98000099,
            corporation_name="Hostile Corp",
            ticker="H-CORP",
            alliance=cls.alliance,
        )

        cls.user_officer = AuthUtils.create_user(username="officer_user")
        AuthUtils.add_main_character(cls.user_officer, "Officer Pilot", 21100099)
        AuthUtils.add_permission_to_user_by_name("hostile.basic_access", cls.user_officer)
        AuthUtils.add_permission_to_user_by_name("hostile.manage_intel", cls.user_officer)

        cls.client = Client()

    def setUp(self):
        self.client.force_login(self.user_officer)

    def test_timer_edit_and_delete(self):
        """Test editing and deleting structure timers"""
        timer = StructureTimer.objects.create(
            timer_type="ARMOR",
            timer_datetime=timezone.now() + timezone.timedelta(days=1),
            solar_system=self.system,
            notes="Initial notes",
            created_by=self.user_officer,
        )

        # GET edit
        res = self.client.get(reverse("hostile:timer_edit", args=[timer.id]))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Edit Structure / Sovereignty Timer")

        # POST edit
        future_time = (timezone.now() + timezone.timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
        res_post = self.client.post(
            reverse("hostile:timer_edit", args=[timer.id]),
            data={
                "timer_type": "HULL",
                "timer_datetime": future_time,
                "solar_system": self.system.id,
                "notes": "Updated hull notes",
            },
            follow=True,
        )
        self.assertEqual(res_post.status_code, 200)
        timer.refresh_from_db()
        self.assertEqual(timer.timer_type, "HULL")
        self.assertEqual(timer.notes, "Updated hull notes")

        # GET confirm delete
        res_del_get = self.client.get(reverse("hostile:timer_delete", args=[timer.id]))
        self.assertEqual(res_del_get.status_code, 200)
        self.assertContains(res_del_get, "Confirm Timer Deletion")

        # POST delete
        res_del_post = self.client.post(reverse("hostile:timer_delete", args=[timer.id]), follow=True)
        self.assertEqual(res_del_post.status_code, 200)
        self.assertFalse(StructureTimer.objects.filter(id=timer.id).exists())

    def test_system_observation_edit_and_delete(self):
        """Test editing and deleting system observations"""
        obs = SystemObservation.objects.create(
            solar_system=self.system,
            threat_level="HIGH",
            observation_text="Gate camp active in 1DQ1-A",
            created_by=self.user_officer,
        )

        # GET edit
        res = self.client.get(reverse("hostile:system_observation_edit", args=[obs.id]))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Edit System Threat Observation")

        # POST edit
        res_post = self.client.post(
            reverse("hostile:system_observation_edit", args=[obs.id]),
            data={
                "solar_system": self.system.id,
                "threat_level": "EXTREME",
                "active_hours": "18:00 - 22:00 UTC",
                "observation_text": "Upgraded to extreme threat with bubble camp",
            },
            follow=True,
        )
        self.assertEqual(res_post.status_code, 200)
        obs.refresh_from_db()
        self.assertEqual(obs.threat_level, "EXTREME")
        self.assertEqual(obs.active_hours, "18:00 - 22:00 UTC")

        # GET delete confirm
        res_del_get = self.client.get(reverse("hostile:system_observation_delete", args=[obs.id]))
        self.assertEqual(res_del_get.status_code, 200)
        self.assertContains(res_del_get, "Confirm Observation Deletion")

        # POST delete
        res_del_post = self.client.post(reverse("hostile:system_observation_delete", args=[obs.id]), follow=True)
        self.assertEqual(res_del_post.status_code, 200)
        self.assertFalse(SystemObservation.objects.filter(id=obs.id).exists())

    def test_system_observation_create_with_time_seen_and_custom_tags(self):
        """Test creating system observation with time_seen and creating new tags on-the-fly"""
        now = timezone.now()
        past_time = now - timedelta(minutes=45)

        # 1. Submit observation with past time_seen and custom new tags
        res_add = self.client.post(
            reverse("hostile:system_observation_add"),
            data={
                "solar_system": self.system.id,
                "threat_level": "HIGH",
                "time_seen": past_time.strftime("%Y-%m-%dT%H:%M"),
                "observation_text": "Dictor bubble on Perimeter gate with cyno alt",
                "new_tag": "Titan Undocked, Supercarrier Standby",
            },
            follow=True,
        )
        self.assertEqual(res_add.status_code, 200)

        obs = SystemObservation.objects.filter(solar_system=self.system).first()
        self.assertIsNotNone(obs)
        self.assertEqual(obs.threat_level, "HIGH")
        self.assertEqual(obs.time_seen.year, past_time.year)
        self.assertEqual(obs.time_seen.minute, past_time.minute)
        self.assertTrue(obs.is_fresh)
        self.assertTrue(obs.is_active_recent)
        self.assertEqual(obs.freshness_status, "FRESH")

        # Verify created tags
        tag_names = list(obs.tags.values_list("name", flat=True))
        self.assertIn("Titan Undocked", tag_names)
        self.assertIn("Supercarrier Standby", tag_names)

        # 2. View systems list page
        res_list = self.client.get(reverse("hostile:systems"))
        self.assertEqual(res_list.status_code, 200)
        self.assertContains(res_list, "Solar System Intel & Tactical Sitreps")
        self.assertContains(res_list, "Time Seen:")
        self.assertContains(res_list, "Titan Undocked")
        self.assertContains(res_list, "Ping")

    def test_system_observation_with_local_and_dscan_scanner_integration(self):
        """Test creating and editing a system observation with Local chat and D-Scan paste integration"""
        local_text = (
            "[ 2026.10.03 16:30:00 ] HostileHunter > scouting gate\n"
            "[ 2026.10.03 16:30:15 ] CynoBait01 > cyno in position\n"
        )
        dscan_text = (
            "12345\tSabre\tSabre\t5 km\n"
            "12346\tMobile Warp Disruptor II\tMobile Warp Disruptor II\t10 km\n"
            "12347\tWidow\tWidow\t20 km\n"
        )

        # 1. Submit observation with Local & D-Scan
        res_add = self.client.post(
            reverse("hostile:system_observation_add"),
            data={
                "solar_system": self.system.id,
                "threat_level": "HIGH",
                "observation_text": "Heavy Gate Camp on 1DQ gate with anchor bubbles and blops standby.",
                "raw_local_text": local_text,
                "raw_dscan_text": dscan_text,
            },
            follow=True,
        )
        self.assertEqual(res_add.status_code, 200)

        obs = SystemObservation.objects.filter(solar_system=self.system).first()
        self.assertIsNotNone(obs)
        self.assertEqual(obs.threat_level, "HIGH")

        # Verify linked LocalThreatScan
        self.assertIsNotNone(obs.local_scan)
        self.assertEqual(obs.local_scan.pilot_count, 2)
        self.assertEqual(obs.local_scan.raw_local_text, local_text.strip())
        self.assertEqual(obs.local_scan.raw_dscan_text, dscan_text.strip())

        # Verify D-Scan Summary
        self.assertIsNotNone(obs.dscan_summary)
        self.assertTrue(obs.dscan_summary.get("has_bubbles"))
        self.assertEqual(obs.dscan_summary.get("total_ships"), 3)
        self.assertIn("Sabre", obs.dscan_summary.get("ship_counts", {}))
        self.assertIn("Widow", obs.dscan_summary.get("ship_counts", {}))

        # Verify auto-tagging
        tag_names = list(obs.tags.values_list("name", flat=True))
        self.assertIn("Bubbles on Gate", tag_names)

        # Verify systems feed rendering
        res_systems = self.client.get(reverse("hostile:systems"))
        self.assertEqual(res_systems.status_code, 200)
        self.assertContains(res_systems, "Local Threat Scan")
        self.assertContains(res_systems, "D-Scan Composition")
        self.assertContains(res_systems, "Sabre")
        self.assertContains(res_systems, "Widow")
        self.assertContains(res_systems, "Bubbles Active")

        # Verify dashboard index rendering
        res_index = self.client.get(reverse("hostile:index"))
        self.assertEqual(res_index.status_code, 200)
        self.assertContains(res_index, "Scan:")
        self.assertContains(res_index, "ships")

        # 2. Test editing observation updates linked scan and dscan summary
        updated_dscan = (
            "12348\tMachariel\tMachariel\t15 km\n"
            "12349\tLoki\tLoki\t12 km\n"
        )
        res_edit = self.client.post(
            reverse("hostile:system_observation_edit", args=[obs.id]),
            data={
                "solar_system": self.system.id,
                "threat_level": "EXTREME",
                "observation_text": "Updated camp: Machariel and Loki reinforcement arrived.",
                "raw_local_text": local_text,
                "raw_dscan_text": updated_dscan,
            },
            follow=True,
        )
        self.assertEqual(res_edit.status_code, 200)
        obs.refresh_from_db()
        self.assertEqual(obs.threat_level, "EXTREME")
        self.assertIn("Machariel", obs.dscan_summary.get("ship_counts", {}))
        self.assertIn("Loki", obs.dscan_summary.get("ship_counts", {}))

    def test_pilot_dossier_edit_and_delete(self):
        """Test editing and deleting pilot dossiers"""
        pilot = HostilePilotDossier.objects.create(
            character_id=21100555,
            character_name="Dread Pilot Alpha",
            corporation_name=self.corporation.corporation_name,
            is_dread_pilot=True,
            is_capital_pilot=True,
            notes="Primary dread alt",
        )

        # GET edit
        res = self.client.get(reverse("hostile:pilot_edit", args=[pilot.id]))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Edit Hostile Pilot Dossier")

        # POST edit
        res_post = self.client.post(
            reverse("hostile:pilot_edit", args=[pilot.id]),
            data={
                "character_id": 21100555,
                "character_name": "Titan Pilot Alpha",
                "corporation_name": self.corporation.corporation_name,
                "alliance_name": self.alliance.alliance_name,
                "is_titan_pilot": True,
                "is_super_pilot": True,
                "is_capital_pilot": True,
                "is_dread_pilot": True,
                "notes": "Upgraded to titan pilot",
            },
            follow=True,
        )
        self.assertEqual(res_post.status_code, 200)
        pilot.refresh_from_db()
        self.assertEqual(pilot.character_name, "Titan Pilot Alpha")
        self.assertTrue(pilot.is_titan_pilot)

        # GET delete confirm
        res_del_get = self.client.get(reverse("hostile:pilot_delete", args=[pilot.id]))
        self.assertEqual(res_del_get.status_code, 200)
        self.assertContains(res_del_get, "Confirm Pilot Dossier Deletion")

        # POST delete
        res_del_post = self.client.post(reverse("hostile:pilot_delete", args=[pilot.id]), follow=True)
        self.assertEqual(res_del_post.status_code, 200)
        self.assertFalse(HostilePilotDossier.objects.filter(id=pilot.id).exists())

    def test_alliance_and_corporation_delete(self):
        """Test deleting hostile alliances and corporations"""
        # Corporation delete
        corp = HostileCorporation.objects.create(
            corporation_id=98000777,
            corporation_name="Target Corp To Delete",
            ticker="TCTD",
        )
        res_corp_del_get = self.client.get(reverse("hostile:corporation_delete", args=[corp.id]))
        self.assertEqual(res_corp_del_get.status_code, 200)
        self.assertContains(res_corp_del_get, "Confirm Corporation Deletion")

        res_corp_del_post = self.client.post(reverse("hostile:corporation_delete", args=[corp.id]), follow=True)
        self.assertEqual(res_corp_del_post.status_code, 200)
        self.assertFalse(HostileCorporation.objects.filter(id=corp.id).exists())

        # Alliance delete with member corporations
        alliance = HostileAlliance.objects.create(
            alliance_id=99000777,
            alliance_name="Target Alliance To Delete",
            ticker="TATD",
        )
        corp1 = HostileCorporation.objects.create(
            corporation_id=98000778,
            corporation_name="Member Corp Alpha",
            ticker="MCA",
            alliance=alliance,
        )
        corp2 = HostileCorporation.objects.create(
            corporation_id=98000779,
            corporation_name="Member Corp Bravo",
            ticker="MCB",
            alliance=alliance,
        )
        unrelated_corp = HostileCorporation.objects.create(
            corporation_id=98000780,
            corporation_name="Unrelated Corp",
            ticker="UNR",
        )

        res_all_del_get = self.client.get(reverse("hostile:alliance_delete", args=[alliance.id]))
        self.assertEqual(res_all_del_get.status_code, 200)
        self.assertContains(res_all_del_get, "Confirm Alliance Deletion")
        self.assertContains(res_all_del_get, "Member Corp Alpha")
        self.assertContains(res_all_del_get, "Member Corp Bravo")
        self.assertContains(res_all_del_get, "all associated member corporations")

        res_all_del_post = self.client.post(reverse("hostile:alliance_delete", args=[alliance.id]), follow=True)
        self.assertEqual(res_all_del_post.status_code, 200)
        self.assertFalse(HostileAlliance.objects.filter(id=alliance.id).exists())
        self.assertFalse(HostileCorporation.objects.filter(id=corp1.id).exists())
        self.assertFalse(HostileCorporation.objects.filter(id=corp2.id).exists())
        self.assertTrue(HostileCorporation.objects.filter(id=unrelated_corp.id).exists())

    def test_staging_edit_and_delete(self):
        """Test editing and deleting staging systems"""
        staging = HostileStagingSystem.objects.create(
            solar_system=self.system,
            alliance=self.alliance,
            is_primary=False,
            notes="Forward staging",
        )

        # GET edit
        res = self.client.get(reverse("hostile:staging_edit", args=[staging.id]))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Edit Staging System")

        # POST edit
        res_post = self.client.post(
            reverse("hostile:staging_edit", args=[staging.id]),
            data={
                "solar_system": self.system.id,
                "alliance": self.alliance.id,
                "staging_type": "PRIMARY",
                "is_primary": True,
                "max_subcap_form": 150,
                "notes": "Upgraded to primary staging with 150 subcaps",
            },
            follow=True,
        )
        self.assertEqual(res_post.status_code, 200)
        staging.refresh_from_db()
        self.assertTrue(staging.is_primary)
        self.assertEqual(staging.max_subcap_form, 150)

        # GET delete confirm
        res_del_get = self.client.get(reverse("hostile:staging_delete", args=[staging.id]))
        self.assertEqual(res_del_get.status_code, 200)
        self.assertContains(res_del_get, "Confirm Staging Deletion")

        # POST delete
        res_del_post = self.client.post(reverse("hostile:staging_delete", args=[staging.id]), follow=True)
        self.assertEqual(res_del_post.status_code, 200)
        self.assertFalse(HostileStagingSystem.objects.filter(id=staging.id).exists())

    def test_doctrine_and_fit_edit_and_delete(self):
        """Test editing and deleting doctrines and fits"""
        doctrine = HostileDoctrine.objects.create(
            name="Armor TFI Fleet",
            alliance=self.alliance,
            role_type="MAIN_FLEET",
            primary_ship_types="Tempest Fleet Issue, Guardian",
        )
        fit = DoctrineFit.objects.create(
            doctrine=doctrine,
            name="Main Line TFI",
            ship_type=self.tfi,
            role="DPS",
            eft_format="[Tempest Fleet Issue, Main Line TFI]\n800mm Repeating Cannon II",
        )

        # Edit fit
        res_fit_edit = self.client.get(reverse("hostile:doctrine_fit_edit", args=[fit.id]))
        self.assertEqual(res_fit_edit.status_code, 200)
        self.assertContains(res_fit_edit, "Edit Example Fit")

        res_fit_post = self.client.post(
            reverse("hostile:doctrine_fit_edit", args=[fit.id]),
            data={
                "doctrine": doctrine.id,
                "name": "Heavy Artillery TFI",
                "ship_type": self.tfi.id,
                "role": "DPS",
                "eft_format": "[Tempest Fleet Issue, Heavy Artillery TFI]\n1400mm Howitzer Artillery II",
            },
            follow=True,
        )
        self.assertEqual(res_fit_post.status_code, 200)
        fit.refresh_from_db()
        self.assertEqual(fit.name, "Heavy Artillery TFI")

        # Delete fit
        res_fit_del_get = self.client.get(reverse("hostile:doctrine_fit_delete", args=[fit.id]))
        self.assertEqual(res_fit_del_get.status_code, 200)
        self.assertContains(res_fit_del_get, "Confirm Example Fit Deletion")

        res_fit_del_post = self.client.post(reverse("hostile:doctrine_fit_delete", args=[fit.id]), follow=True)
        self.assertEqual(res_fit_del_post.status_code, 200)
        self.assertFalse(DoctrineFit.objects.filter(id=fit.id).exists())

        # Edit doctrine
        res_doc_edit = self.client.get(reverse("hostile:doctrine_edit", args=[doctrine.id]))
        self.assertEqual(res_doc_edit.status_code, 200)
        self.assertContains(res_doc_edit, "Edit Fleet Doctrine")

        res_doc_post = self.client.post(
            reverse("hostile:doctrine_edit", args=[doctrine.id]),
            data={
                "name": "Shield TFI Fleet",
                "alliance": self.alliance.id,
                "role_type": "MAIN_FLEET",
                "description": "Swapped to shield fit",
            },
            follow=True,
        )
        self.assertEqual(res_doc_post.status_code, 200)
        doctrine.refresh_from_db()
        self.assertEqual(doctrine.name, "Shield TFI Fleet")

        # Delete doctrine
        res_doc_del_get = self.client.get(reverse("hostile:doctrine_delete", args=[doctrine.id]))
        self.assertEqual(res_doc_del_get.status_code, 200)
        self.assertContains(res_doc_del_get, "Confirm Doctrine Deletion")

        res_doc_del_post = self.client.post(reverse("hostile:doctrine_delete", args=[doctrine.id]), follow=True)
        self.assertEqual(res_doc_del_post.status_code, 200)
        self.assertFalse(HostileDoctrine.objects.filter(id=doctrine.id).exists())

    def test_threat_scan_delete(self):
        """Test deleting local threat scans"""
        scan = LocalThreatScan.objects.create(
            solar_system=self.system,
            raw_local_text="Pilot Alpha\nPilot Beta",
            raw_dscan_text="",
            pilot_count=2,
            hostile_count=2,
            blops_drop_chance=80,
            cap_drop_chance=50,
            threat_level="CRITICAL",
            created_by=self.user_officer,
        )

        # GET delete confirm
        res_del_get = self.client.get(reverse("hostile:threat_scan_delete", args=[scan.id]))
        self.assertEqual(res_del_get.status_code, 200)
        self.assertContains(res_del_get, "Confirm Threat Scan Deletion")

        # POST delete
        res_del_post = self.client.post(reverse("hostile:threat_scan_delete", args=[scan.id]), follow=True)
        self.assertEqual(res_del_post.status_code, 200)
        self.assertFalse(LocalThreatScan.objects.filter(id=scan.id).exists())

    def test_basic_access_author_edit_and_delete_permissions(self):
        """Test that basic_access users can add entities, edit their own, but cannot edit others or delete any"""
        user_basic_a = AuthUtils.create_user(username="basic_user_a")
        AuthUtils.add_main_character(user_basic_a, "Basic Pilot A", 21100091)
        AuthUtils.add_permission_to_user_by_name("hostile.basic_access", user_basic_a)

        user_basic_b = AuthUtils.create_user(username="basic_user_b")
        AuthUtils.add_main_character(user_basic_b, "Basic Pilot B", 21100092)
        AuthUtils.add_permission_to_user_by_name("hostile.basic_access", user_basic_b)

        # Login as User A
        self.client.force_login(user_basic_a)

        # 1. User A creates a structure
        struct_a = HostileStructure.objects.create(
            name="Alpha Astrahus",
            structure_type=self.astrahus,
            solar_system=self.system,
            alliance=self.alliance,
            created_by=user_basic_a,
        )

        # 2. User A creates a timer
        timer_a = StructureTimer.objects.create(
            timer_type="ARMOR",
            timer_datetime=timezone.now() + timedelta(days=1),
            solar_system=self.system,
            created_by=user_basic_a,
        )

        # 3. User A creates an observation
        obs_a = SystemObservation.objects.create(
            solar_system=self.system,
            threat_level="HIGH",
            observation_text="Camp spotted by A",
            created_by=user_basic_a,
        )

        # 4. User A can edit their own structure
        res = self.client.get(reverse("hostile:structure_edit", args=[struct_a.id]))
        self.assertEqual(res.status_code, 200)

        # 5. User A can edit their own timer
        res = self.client.get(reverse("hostile:timer_edit", args=[timer_a.id]))
        self.assertEqual(res.status_code, 200)

        # 6. User A can edit their own observation
        res = self.client.get(reverse("hostile:system_observation_edit", args=[obs_a.id]))
        self.assertEqual(res.status_code, 200)

        # 7. User A CANNOT delete their own structure (delete is manager only)
        res_del = self.client.post(reverse("hostile:structure_delete", args=[struct_a.id]))
        self.assertEqual(res_del.status_code, 302)  # redirected due to missing permission
        self.assertTrue(HostileStructure.objects.filter(id=struct_a.id).exists())

        # 8. User A CANNOT delete their own timer
        res_del_t = self.client.post(reverse("hostile:timer_delete", args=[timer_a.id]))
        self.assertEqual(res_del_t.status_code, 302)
        self.assertTrue(StructureTimer.objects.filter(id=timer_a.id).exists())

        # 9. User A CANNOT delete their own observation
        res_del_o = self.client.post(reverse("hostile:system_observation_delete", args=[obs_a.id]))
        self.assertEqual(res_del_o.status_code, 302)
        self.assertTrue(SystemObservation.objects.filter(id=obs_a.id).exists())

        # 10. User A CANNOT access Audit Logs
        res_audit = self.client.get(reverse("hostile:audit_logs"))
        self.assertEqual(res_audit.status_code, 302)

        # Switch to User B
        self.client.force_login(user_basic_b)

        # 11. User B CANNOT edit User A's structure
        res_b_struct = self.client.get(reverse("hostile:structure_edit", args=[struct_a.id]), follow=True)
        self.assertContains(res_b_struct, "You do not have permission to edit this structure.")

        # 12. User B CANNOT edit User A's timer
        res_b_timer = self.client.get(reverse("hostile:timer_edit", args=[timer_a.id]), follow=True)
        self.assertContains(res_b_timer, "You do not have permission to edit this timer.")

        # 13. User B CANNOT edit User A's observation
        res_b_obs = self.client.get(reverse("hostile:system_observation_edit", args=[obs_a.id]), follow=True)
        self.assertContains(res_b_obs, "You do not have permission to edit this observation.")
