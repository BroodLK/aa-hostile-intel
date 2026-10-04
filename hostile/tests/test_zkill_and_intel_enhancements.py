"""
Tests for 3-month rolling zKill capability calculations, percentage deltas,
activity heat maps, timezone auto-detection, structure manual state transitions,
staging form overrides, and periodic task setup.
"""

# Standard Library
from datetime import timedelta
from unittest.mock import MagicMock, patch

# Third Party
from django_celery_beat.models import PeriodicTask
from eve_sde.models import ItemCategory, ItemGroup, ItemType, SolarSystem

# Alliance Auth
from allianceauth.tests.auth_utils import AuthUtils

# Django
from django.contrib.auth.models import Group, Permission, User
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

# AA Hostile Intel
from hostile.forms import HostileAllianceForm, HostileStagingSystemForm
from hostile.models import (
    HostileAlliance,
    HostileCorporation,
    HostileStagingSystem,
    HostileStructure,
    StructureTimer,
)
from hostile.services.sov_engine import SovAnalysisEngine
from hostile.services.zkill_client import ZKillClient, zkill_client


class TestZKillAndIntelEnhancements(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Create test user and permissions
        cls.user = AuthUtils.create_user(username="intel_officer")
        AuthUtils.add_main_character(cls.user, "Intel Officer Char", 21100099)
        AuthUtils.add_permission_to_user_by_name("hostile.basic_access", cls.user)
        AuthUtils.add_permission_to_user_by_name("hostile.manage_intel", cls.user)

        cls.basic_user = AuthUtils.create_user(username="scout_user")
        AuthUtils.add_main_character(cls.basic_user, "Scout Char", 21100098)
        AuthUtils.add_permission_to_user_by_name("hostile.basic_access", cls.basic_user)

        # Structure Type setup (Astrahus)
        cls.category_structure = ItemCategory.objects.create(id=65, name="Structure", published=True)
        cls.group_citadel = ItemGroup.objects.create(id=1657, name="Citadel", category=cls.category_structure, published=True)
        cls.astrahus_type = ItemType.objects.create(id=35832, name="Astrahus", group=cls.group_citadel, published=True)

        # Solar system setup
        cls.solar_system = SolarSystem.objects.create(id=30000142, name="Jita", security_status=0.9)
        cls.null_system = SolarSystem.objects.create(id=30004759, name="1DQ1-A", security_status=-0.5)

        # Alliance setup
        cls.alliance = HostileAlliance.objects.create(
            alliance_id=99000001,
            alliance_name="Test Threat Alliance",
            ticker="TTA",
            primary_timezone="UNKNOWN",
            max_subcap_form=100,
            max_cap_form=20,
            max_super_form=5,
            prev_max_subcap_form=100,
            prev_max_cap_form=20,
            prev_max_super_form=5,
        )

        # Corporation setup
        cls.corporation = HostileCorporation.objects.create(
            corporation_id=98000001,
            corporation_name="Test Threat Corp",
            ticker="TTC",
            alliance=cls.alliance,
        )

        # Structure setup
        cls.structure = HostileStructure.objects.create(
            structure_id=103000000001,
            name="1DQ1-A Prime Citadel",
            structure_type=cls.astrahus_type,
            solar_system=cls.null_system,
            alliance=cls.alliance,
            corporation=cls.corporation,
            state="ONLINE",
            vulnerability_hour=19,
        )

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.user)

    def test_staging_system_unknown_structure_and_form_estimation(self):
        """Test staging systems allow unassigned/unknown structures and estimate formup numbers from entity"""
        staging = HostileStagingSystem.objects.create(
            solar_system=self.null_system,
            alliance=self.alliance,
            structure=None,  # Unknown / unassigned
            max_subcap_form=0,  # Auto-estimate
            max_cap_form=0,
            max_super_form=0,
        )
        self.assertEqual(staging.estimated_subcap_form, 100)
        self.assertEqual(staging.estimated_cap_form, 20)
        self.assertEqual(staging.estimated_super_form, 5)
        self.assertFalse(staging.is_form_overridden)

        # Test manual override
        staging.max_subcap_form = 180
        staging.save()
        self.assertEqual(staging.estimated_subcap_form, 180)
        self.assertTrue(staging.is_form_overridden)

    def test_staging_form_empty_structure_selection(self):
        """Test HostileStagingSystemForm renders and accepts empty structure as Unknown"""
        form = HostileStagingSystemForm(
            data={
                "solar_system": self.null_system.id,
                "alliance": self.alliance.id,
                "structure": "",  # Empty structure -> Unknown
                "staging_type": "PRIMARY",
                "max_subcap_form": "",
                "max_cap_form": "",
                "max_super_form": "",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        instance = form.save()
        self.assertIsNone(instance.structure)
        self.assertEqual(instance.max_subcap_form, 0)

    def test_structure_manual_state_changes(self):
        """Test changing a structure to LOW_POWER, OFFLINE, ABANDONED, DESTROYED via structure_set_state endpoint"""
        # 1. Set to Low Power
        url = reverse("hostile:structure_set_state", args=[self.structure.id])
        res = self.client.post(url, {"state": "LOW_POWER"}, follow=True)
        self.assertEqual(res.status_code, 200)
        self.structure.refresh_from_db()
        self.assertEqual(self.structure.state, "LOW_POWER")

        # 2. Set to Offline
        res = self.client.post(url, {"state": "OFFLINE"}, follow=True)
        self.assertEqual(res.status_code, 200)
        self.structure.refresh_from_db()
        self.assertEqual(self.structure.state, "OFFLINE")

        # 3. Set to Abandoned
        res = self.client.post(url, {"state": "ABANDONED"}, follow=True)
        self.assertEqual(res.status_code, 200)
        self.structure.refresh_from_db()
        self.assertEqual(self.structure.state, "ABANDONED")

        # 4. Set to Destroyed
        res = self.client.post(url, {"state": "DESTROYED"}, follow=True)
        self.assertEqual(res.status_code, 200)
        self.structure.refresh_from_db()
        self.assertEqual(self.structure.state, "DESTROYED")

    def test_zkill_3m_rolling_stats_and_deltas(self):
        """Test 3-month rolling capabilities, percentage deltas (+20% supers, etc.), and activity heat map"""
        now = timezone.now()
        cur_m = f"{now.year:04d}{now.month:02d}"

        mock_zkill_data = {
            "activepvp": {
                "characters": {"count": 120},
                "kills": {"count": 450},
                "losses": {"count": 30},
            },
            "months": {
                cur_m: {"characters": {"count": 150}},
            },
            "supers": {
                "supercarrier": {"count": 4},
                "titan": {"count": 2},
            },
            "groups": {
                "485": {"count": 15},  # Dreadnoughts
                "1538": {"count": 10},  # FAX
                "547": {"count": 5},   # Carriers
            },
            "activity": {
                "2": {"18": 15, "19": 40, "20": 25},  # Tuesday peak
                "3": {"19": 30, "20": 20},
            },
        }

        with patch.object(ZKillClient, "fetch_alliance_stats", return_value=mock_zkill_data):
            client = ZKillClient()
            success = client.update_alliance_stats(self.alliance)
            self.assertTrue(success)

            self.alliance.refresh_from_db()
            self.assertEqual(self.alliance.active_pilots_count, 120)
            self.assertEqual(self.alliance.weekly_kills_count, 450)
            self.assertEqual(self.alliance.max_subcap_form, 90)  # 150 * 0.6
            self.assertEqual(self.alliance.max_cap_form, 30)     # 15 + 10 + 5
            self.assertEqual(self.alliance.max_super_form, 6)    # 4 + 2

            # Check deltas (+20% supers detected from 5 -> 6, etc.)
            self.assertIn("super", self.alliance.zkill_deltas)
            self.assertEqual(self.alliance.zkill_deltas["super"]["pct"], 20)
            self.assertEqual(self.alliance.zkill_deltas["super"]["text"], "+20% supers detected")

            # Check auto-inferred timezone from zKill kill distribution (18-20 UTC -> EUTZ)
            self.assertEqual(self.alliance.primary_timezone, "EUTZ")

            # Check 7x24 heatmap matrix
            heatmap = self.alliance.activity_heatmap
            self.assertIn("matrix", heatmap)
            self.assertEqual(len(heatmap["matrix"]), 7)
            self.assertEqual(len(heatmap["matrix"][0]), 24)
            self.assertEqual(heatmap["peak_day"], "Tuesday")
            self.assertEqual(heatmap["peak_hour"], 19)

    def test_alliance_timezone_override(self):
        """Test that timezone override prevents automated zKill/timer TZ changes"""
        self.alliance.override_timezone = True
        self.alliance.primary_timezone = "AUTZ"
        self.alliance.save()

        mock_zkill_data = {
            "activepvp": {"characters": {"count": 50}, "kills": {"count": 100}, "losses": {"count": 5}},
            "activity": {"2": {"19": 50}},  # EUTZ kills
        }

        with patch.object(ZKillClient, "fetch_alliance_stats", return_value=mock_zkill_data):
            client = ZKillClient()
            client.update_alliance_stats(self.alliance)
            self.alliance.refresh_from_db()
            # TZ must remain AUTZ because override_timezone is True
            self.assertEqual(self.alliance.primary_timezone, "AUTZ")

    def test_management_command_hostile_setup_tasks(self):
        """Test management command creates Celery Beat tasks for weekly zKill scan and sovereignty sync"""
        call_command("hostile_setup_tasks")

        task_zkill = PeriodicTask.objects.get(task="hostile.tasks.update_all_zkill_stats")
        self.assertTrue(task_zkill.enabled)
        self.assertIsNotNone(task_zkill.crontab)
        self.assertEqual(task_zkill.crontab.day_of_week, "0")  # Sunday

        task_sov = PeriodicTask.objects.get(task="hostile.tasks.update_sovereignty_intelligence")
        self.assertTrue(task_sov.enabled)
        self.assertIsNotNone(task_sov.crontab)

        task_campaign = PeriodicTask.objects.get(task="hostile.tasks.update_sovereignty_campaigns")
        self.assertTrue(task_campaign.enabled)
        self.assertIsNotNone(task_campaign.interval)
        self.assertEqual(task_campaign.interval.every, 1)

        # Test disable flag
        call_command("hostile_setup_tasks", disable=True)
        task_zkill.refresh_from_db()
        task_sov.refresh_from_db()
        task_campaign.refresh_from_db()
        self.assertFalse(task_zkill.enabled)
        self.assertFalse(task_sov.enabled)
        self.assertFalse(task_campaign.enabled)

    def test_alliance_and_corporation_detail_view_heatmap(self):
        """Test detail views load and format 7x24 heatmap data properly in template context"""
        self.alliance.activity_heatmap = {
            "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
            "matrix": [[5 if h == 19 else 0 for h in range(24)] for _ in range(7)],
            "max_val": 5,
            "peak_day": "Monday",
            "peak_hour": 19,
            "total_kills_3m": 35,
        }
        self.alliance.save()

        res = self.client.get(reverse("hostile:alliance_detail", args=[self.alliance.id]))
        self.assertEqual(res.status_code, 200)
        self.assertIn("heatmap_data", res.context)
        self.assertEqual(len(res.context["heatmap_data"]["rows"]), 7)
        self.assertContains(res, "Combat Activity Heatmap")
        self.assertContains(res, "hostile-heatmap-cell")
        self.assertContains(res, "heatmap-legend-badge")

        # Verify peak / yellow / colored cell properties
        peak_cell = res.context["heatmap_data"]["rows"][0]["cells"][19]
        self.assertEqual(peak_cell["count"], 5)
        self.assertEqual(peak_cell["cell_class"], "cell-peak")
        self.assertEqual(peak_cell["text_color"], "#f87171")
        self.assertEqual(res.context["heatmap_data"]["peak_tz"], "EUTZ")
        self.assertContains(res, "Peak: Monday 19:00 UTC (EUTZ)")
        self.assertContains(res, "Primary TZ:")

        # Corporation detail view
        res_corp = self.client.get(reverse("hostile:corporation_detail", args=[self.corporation.id]))
        self.assertEqual(res_corp.status_code, 200)
        self.assertIn("heatmap_data", res_corp.context)

    def test_timezone_hour_classification(self):
        """Test SovAnalysisEngine.get_hour_timezone returns accurate EVE Online timezone codes"""
        self.assertEqual(SovAnalysisEngine.get_hour_timezone(20), "EUTZ")
        self.assertEqual(SovAnalysisEngine.get_hour_timezone(19), "EUTZ")
        self.assertEqual(SovAnalysisEngine.get_hour_timezone(14), "EUTZ")
        self.assertEqual(SovAnalysisEngine.get_hour_timezone(0), "USTZ")
        self.assertEqual(SovAnalysisEngine.get_hour_timezone(4), "USTZ")
        self.assertEqual(SovAnalysisEngine.get_hour_timezone(23), "USTZ")
        self.assertEqual(SovAnalysisEngine.get_hour_timezone(10), "AUTZ")

    def test_heatmap_native_zkill_arrays_and_day_parsing(self):
        """Test that native zKillboard activity arrays (24 ints per day) are parsed accurately 1-to-1"""
        # Test 1: Native zKillboard structure with 24-hour integer arrays for 0..6
        zkill_native_data = {
            "activity": {
                "days": ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
                "max": 357,
                "0": [15, 28, 175, 106, 34, 14, 12, 4, 16, 11, 38, 207, 357, 84, 30, 27, 26, 22, 43, 90, 24, 26, 30, 47],
                "1": [25, 102, 117, 50, 29, 16, 11, 15, 10, 9, 10, 6, 10, 15, 27, 34, 27, 30, 28, 23, 26, 25, 17, 16],
                "2": [30, 51, 100, 46, 41, 8, 20, 15, 14, 17, 32, 36, 20, 16, 28, 19, 14, 23, 38, 32, 11, 28, 25, 48],
                "3": [26, 75, 156, 45, 19, 13, 19, 15, 9, 12, 10, 4, 16, 8, 6, 28, 13, 16, 18, 22, 42, 21, 11, 11],
                "4": [27, 70, 106, 54, 18, 14, 11, 5, 12, 15, 35, 6, 11, 19, 28, 52, 31, 25, 39, 34, 26, 30, 19, 26],
                "5": [51, 84, 159, 63, 48, 11, 23, 8, 18, 20, 20, 35, 95, 30, 11, 34, 20, 16, 24, 26, 38, 18, 37, 27],
                "6": [48, 22, 42, 82, 66, 44, 14, 6, 5, 9, 6, 31, 136, 59, 16, 15, 21, 35, 46, 48, 66, 56, 20, 40],
            }
        }
        heatmap = ZKillClient._build_activity_heatmap(zkill_native_data)
        self.assertEqual(len(heatmap["matrix"]), 7)
        # Sunday is row index 6
        self.assertEqual(heatmap["matrix"][6][12], 357, "Sunday 12:00 UTC must be 357 kills")
        self.assertEqual(heatmap["matrix"][6][11], 207, "Sunday 11:00 UTC must be 207 kills")
        self.assertEqual(heatmap["matrix"][6][2], 175, "Sunday 02:00 UTC must be 175 kills")
        # Monday is row index 0
        self.assertEqual(heatmap["matrix"][0][0], 25, "Monday 00:00 UTC must be 25 kills")
        self.assertEqual(heatmap["matrix"][0][1], 102, "Monday 01:00 UTC must be 102 kills")
        self.assertEqual(heatmap["matrix"][0][2], 117, "Monday 02:00 UTC must be 117 kills")
        # Friday is row index 4
        self.assertEqual(heatmap["matrix"][4][2], 159, "Friday 02:00 UTC must be 159 kills")
        # Saturday is row index 5
        self.assertEqual(heatmap["matrix"][5][12], 136, "Saturday 12:00 UTC must be 136 kills")

        self.assertEqual(heatmap["max_val"], 357)
        self.assertEqual(heatmap["peak_day"], "Sunday")
        self.assertEqual(heatmap["peak_hour"], 12)

        # Test 2: 0-indexed nested dict day data where 0 is Sunday
        nested_sunday_0 = {
            "activity": {
                "0": {"19": 15},  # Sunday
                "1": {"19": 8},   # Monday
            }
        }
        heatmap_0 = ZKillClient._build_activity_heatmap(nested_sunday_0)
        self.assertEqual(heatmap_0["matrix"][6][19], 15, "0-key should map to Sunday (row 6)")
        self.assertEqual(heatmap_0["matrix"][0][19], 8, "1-key should map to Monday (row 0)")

        # Test 3: Named days
        named_days_data = {
            "activity": {
                "Sunday": {"18": 25},
                "Sun": {"19": 30},
                "Monday": {"19": 12},
            }
        }
        heatmap_named = ZKillClient._build_activity_heatmap(named_days_data)
        self.assertEqual(heatmap_named["matrix"][6][18], 25)
        self.assertEqual(heatmap_named["matrix"][6][19], 30)
        self.assertEqual(heatmap_named["matrix"][0][19], 12)
        self.assertEqual(heatmap_named["peak_day"], "Sunday")

    def test_heatmap_no_synthetic_fallback(self):
        """Test that missing activity data produces zeroes and never synthesizes fake curves"""
        # When activity data is empty / absent, matrix should remain all zeroes
        data_empty_activity = {
            "months": {
                "202610": {"shipsDestroyed": 100},
                "202609": {"shipsDestroyed": 150},
                "202608": {"shipsDestroyed": 50},
            },
            "activepvp": {
                "kills": {"count": 40},
                "characters": {"count": 10},
            },
        }
        heatmap = ZKillClient._build_activity_heatmap(data_empty_activity)
        self.assertEqual(heatmap["total_kills_3m"], 0)
        self.assertEqual(heatmap["max_val"], 0)
        self.assertEqual(heatmap["peak_day"], "Unknown")
        self.assertEqual(sum(sum(r) for r in heatmap["matrix"]), 0)

    def test_dossier_view_empty_heatmap_renders_clean_placeholder(self):
        """Test that viewing an alliance or corporation detail page with empty heatmap shows clean placeholder"""
        alliance_unpopulated = HostileAlliance.objects.create(
            alliance_id=99009999,
            alliance_name="Unpopulated Heatmap Alliance",
            ticker="UNPOP",
            weekly_kills_count=200,
            active_pilots_count=45,
            primary_timezone="UNKNOWN",
        )
        res = self.client.get(reverse("hostile:alliance_detail", args=[alliance_unpopulated.id]))
        self.assertEqual(res.status_code, 200)
        self.assertIn("heatmap_data", res.context)
        self.assertEqual(res.context["heatmap_data"], {})
        self.assertContains(res, "No 3-month rolling zKillboard combat activity data recorded yet")
