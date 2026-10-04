"""
Unit tests for EVE Upwell Structure Reinforcement Exit Calculator.
"""

from datetime import datetime, timezone as dt_timezone

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from allianceauth.tests.auth_utils import AuthUtils

from eve_sde.models import ItemCategory, ItemGroup, ItemType, SolarSystem
from hostile.models import (
    HostileAlliance,
    HostileStructure,
    StructureTimer,
)
from hostile.services.reinforcement_calculator import (
    LocationSecurity,
    PowerState,
    ReinforcementCalculator,
    StructureCategory,
    TimerStage,
)


class TestReinforcementCalculator(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = AuthUtils.create_user(username="calculator_tester")
        AuthUtils.add_main_character(cls.user, "Calculator Char", 21100099)
        AuthUtils.add_permission_to_user_by_name("hostile.basic_access", cls.user)
        AuthUtils.add_permission_to_user_by_name("hostile.manage_intel", cls.user)

        cls.category = ItemCategory.objects.create(id=65, name="Structure", published=True)
        cls.group = ItemGroup.objects.create(
            id=1657, name="Citadel", category=cls.category, published=True
        )

        cls.system_jita = SolarSystem.objects.create(
            id=30000142, name="Jita", security_status=0.95
        )
        cls.system_amarr = SolarSystem.objects.create(
            id=30002187, name="Amamake", security_status=0.40
        )
        cls.system_null = SolarSystem.objects.create(
            id=30004759, name="1DQ1-A", security_status=-0.50
        )
        cls.system_wh = SolarSystem.objects.create(
            id=31000001, name="J123456", security_status=-0.99
        )

        cls.type_astrahus = ItemType.objects.create(
            id=35832, name="Astrahus", group=cls.group, published=True
        )
        cls.type_fortizar = ItemType.objects.create(
            id=35833, name="Fortizar", group=cls.group, published=True
        )
        cls.type_keepstar = ItemType.objects.create(
            id=35834, name="Keepstar", group=cls.group, published=True
        )
        cls.type_ansiblex = ItemType.objects.create(
            id=47512, name="Ansiblex Jump Gate", group=cls.group, published=True
        )
        cls.type_poco = ItemType.objects.create(
            id=2233, name="Customs Office", group=cls.group, published=True
        )
        cls.group_drill = ItemGroup.objects.create(
            id=4807, name="Moon Drill", category=cls.category
        )
        cls.type_drill = ItemType.objects.create(
            id=81143, name="Metenox Moon Drill", group=cls.group_drill, published=True
        )

        cls.alliance = HostileAlliance.objects.create(
            alliance_id=99000001, alliance_name="Test Hostile Alliance", ticker="THA"
        )
        cls.client = Client()

    def setUp(self):
        self.user.refresh_from_db()
        self.client.force_login(self.user)

    def test_infer_structure_size(self):
        self.assertEqual(
            ReinforcementCalculator.infer_structure_size(self.type_astrahus),
            StructureCategory.MEDIUM,
        )
        self.assertEqual(
            ReinforcementCalculator.infer_structure_size(self.type_fortizar),
            StructureCategory.LARGE_XL,
        )
        self.assertEqual(
            ReinforcementCalculator.infer_structure_size(self.type_keepstar),
            StructureCategory.LARGE_XL,
        )
        self.assertEqual(
            ReinforcementCalculator.infer_structure_size(self.type_ansiblex),
            StructureCategory.FLEX,
        )
        self.assertEqual(
            ReinforcementCalculator.infer_structure_size(self.type_drill),
            StructureCategory.FLEX,
        )
        self.assertEqual(
            ReinforcementCalculator.infer_structure_size("Metenox Moon Drill"),
            StructureCategory.FLEX,
        )
        self.assertEqual(
            ReinforcementCalculator.infer_structure_size("Moon Drill"),
            StructureCategory.FLEX,
        )
        self.assertEqual(
            ReinforcementCalculator.infer_structure_size("1DQ1-A - Drill Moon 3"),
            StructureCategory.FLEX,
        )
        self.assertEqual(
            ReinforcementCalculator.infer_structure_size(self.type_poco),
            StructureCategory.POCO,
        )

    def test_infer_location_type(self):
        self.assertEqual(
            ReinforcementCalculator.infer_location_type(self.system_jita),
            LocationSecurity.HIGHSEC,
        )
        self.assertEqual(
            ReinforcementCalculator.infer_location_type(self.system_amarr),
            LocationSecurity.NULLSEC_LOWSEC,
        )
        self.assertEqual(
            ReinforcementCalculator.infer_location_type(self.system_null),
            LocationSecurity.NULLSEC_LOWSEC,
        )
        self.assertEqual(
            ReinforcementCalculator.infer_location_type(self.system_wh),
            LocationSecurity.WORMHOLE,
        )

    def test_medium_structure_nullsec_shield_reinforcement(self):
        # Reinforced on Thursday 2026-10-01 13:00 UTC, defender vulnerability hour 19 (19:00 UTC)
        reinforced_at = datetime(2026, 10, 1, 13, 0, 0, tzinfo=dt_timezone.utc)
        result = ReinforcementCalculator.calculate_exit_timer(
            reinforced_at=reinforced_at,
            structure_size=StructureCategory.MEDIUM,
            location_type=LocationSecurity.NULLSEC_LOWSEC,
            timer_stage=TimerStage.SHIELD_REINFORCE,
            vulnerability_hour=19,
        )
        self.assertTrue(result.is_applicable)
        self.assertEqual(result.base_delay_hours, 84.0)  # 3.5 days
        self.assertEqual(result.jitter_hours, 1.5)

        # min_target_time = 2026-10-01 13:00 + 84h = 2026-10-05 01:00 UTC
        # Next 19:00 UTC is on 2026-10-05 19:00 UTC
        self.assertEqual(result.nominal_exit, datetime(2026, 10, 5, 19, 0, 0, tzinfo=dt_timezone.utc))
        self.assertEqual(result.earliest_exit, datetime(2026, 10, 5, 17, 30, 0, tzinfo=dt_timezone.utc))
        self.assertEqual(result.latest_exit, datetime(2026, 10, 5, 20, 30, 0, tzinfo=dt_timezone.utc))

    def test_medium_structure_wormhole_shield_reinforcement(self):
        # Wormhole Medium: 2.5 days (60h)
        reinforced_at = datetime(2026, 10, 1, 13, 0, 0, tzinfo=dt_timezone.utc)
        result = ReinforcementCalculator.calculate_exit_timer(
            reinforced_at=reinforced_at,
            structure_size=StructureCategory.MEDIUM,
            location_type=LocationSecurity.WORMHOLE,
            timer_stage=TimerStage.SHIELD_REINFORCE,
            vulnerability_hour=19,
        )
        self.assertTrue(result.is_applicable)
        self.assertEqual(result.base_delay_hours, 60.0)  # 2.5 days
        # min_target_time = 2026-10-01 13:00 + 60h = 2026-10-04 01:00 UTC -> next 19:00 is 2026-10-04 19:00 UTC
        self.assertEqual(result.nominal_exit, datetime(2026, 10, 4, 19, 0, 0, tzinfo=dt_timezone.utc))

    def test_medium_structure_no_hull_timer(self):
        # Medium structures have NO hull timer
        reinforced_at = datetime(2026, 10, 1, 13, 0, 0, tzinfo=dt_timezone.utc)
        result = ReinforcementCalculator.calculate_exit_timer(
            reinforced_at=reinforced_at,
            structure_size=StructureCategory.MEDIUM,
            location_type=LocationSecurity.NULLSEC_LOWSEC,
            timer_stage=TimerStage.ARMOR_REINFORCE,
        )
        self.assertFalse(result.is_applicable)
        self.assertIn("do not have a hull reinforcement", result.status_message)

    def test_large_structure_armor_and_hull_reinforcement(self):
        reinforced_at = datetime(2026, 10, 1, 13, 0, 0, tzinfo=dt_timezone.utc)

        # Shield Reinforce -> Armor timer (24h base delay across all space ±3h jitter)
        armor_result = ReinforcementCalculator.calculate_exit_timer(
            reinforced_at=reinforced_at,
            structure_size=StructureCategory.LARGE_XL,
            location_type=LocationSecurity.NULLSEC_LOWSEC,
            timer_stage=TimerStage.SHIELD_REINFORCE,
            vulnerability_hour=19,
        )
        self.assertTrue(armor_result.is_applicable)
        self.assertEqual(armor_result.base_delay_hours, 24.0)
        self.assertEqual(armor_result.jitter_hours, 3.0)
        self.assertEqual(armor_result.nominal_exit, datetime(2026, 10, 2, 19, 0, 0, tzinfo=dt_timezone.utc))

        # Armor Reinforce -> Hull timer (2.5 days in Nullsec ±3h jitter)
        hull_result = ReinforcementCalculator.calculate_exit_timer(
            reinforced_at=reinforced_at,
            structure_size=StructureCategory.LARGE_XL,
            location_type=LocationSecurity.NULLSEC_LOWSEC,
            timer_stage=TimerStage.ARMOR_REINFORCE,
            vulnerability_hour=19,
        )
        self.assertTrue(hull_result.is_applicable)
        self.assertEqual(hull_result.base_delay_hours, 60.0)  # 2.5 days
        self.assertEqual(hull_result.nominal_exit, datetime(2026, 10, 4, 19, 0, 0, tzinfo=dt_timezone.utc))

    def test_flex_structure_mechanics(self):
        reinforced_at = datetime(2026, 10, 1, 13, 0, 0, tzinfo=dt_timezone.utc)

        # FLEX has no armor timer
        shield_res = ReinforcementCalculator.calculate_exit_timer(
            reinforced_at=reinforced_at,
            structure_size=StructureCategory.FLEX,
            timer_stage=TimerStage.SHIELD_REINFORCE,
        )
        self.assertFalse(shield_res.is_applicable)

        # FLEX hull timer (0.5h base delay ±0.5h jitter)
        hull_res = ReinforcementCalculator.calculate_exit_timer(
            reinforced_at=reinforced_at,
            structure_size=StructureCategory.FLEX,
            timer_stage=TimerStage.ARMOR_REINFORCE,
            vulnerability_hour=19,
        )
        self.assertTrue(hull_res.is_applicable)
        self.assertEqual(hull_res.base_delay_hours, 0.5)
        self.assertEqual(hull_res.jitter_hours, 0.5)

    def test_abandoned_mode(self):
        result = ReinforcementCalculator.calculate_exit_timer(
            structure_size=StructureCategory.LARGE_XL,
            power_state=PowerState.ABANDONED,
        )
        self.assertFalse(result.is_applicable)
        self.assertIn("Abandoned", result.status_message)

    def test_calculate_from_structure_model(self):
        struct = HostileStructure.objects.create(
            name="1DQ Keepstar Main",
            structure_type=self.type_keepstar,
            solar_system=self.system_null,
            alliance=self.alliance,
            vulnerability_hour=20,
            vulnerability_window="20:00 - 24:00 UTC",
            created_by=self.user,
        )
        calc_res = ReinforcementCalculator.calculate_from_structure(
            struct,
            reinforced_at=datetime(2026, 10, 1, 12, 0, 0, tzinfo=dt_timezone.utc),
            timer_stage=TimerStage.SHIELD_REINFORCE,
        )
        self.assertTrue(calc_res.is_applicable)
        self.assertEqual(calc_res.structure_size, StructureCategory.LARGE_XL)
        self.assertEqual(calc_res.nominal_exit, datetime(2026, 10, 2, 20, 0, 0, tzinfo=dt_timezone.utc))

    def test_calculator_view_get_and_post(self):
        struct = HostileStructure.objects.create(
            name="Hostile Fortizar",
            structure_type=self.type_fortizar,
            solar_system=self.system_null,
            alliance=self.alliance,
            vulnerability_hour=18,
            created_by=self.user,
        )

        # GET request with structure_id
        url = reverse("hostile:calculator") + f"?structure_id={struct.id}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Hostile Fortizar")
        self.assertContains(response, "Calculation Output")

        # POST request to calculate and save to timer board
        post_data = {
            "structure": struct.id,
            "solar_system": self.system_null.id,
            "structure_size": StructureCategory.LARGE_XL,
            "location_type": LocationSecurity.NULLSEC_LOWSEC,
            "is_low_power": False,
            "timer_stage": TimerStage.SHIELD_REINFORCE,
            "power_state": PowerState.FULL_POWER,
            "reinforced_at": "2026-10-01T13:00",
            "vulnerability_hour": 18,
            "save_to_timers": "on",
            "notes": "Calculated test timer",
        }
        post_response = self.client.post(reverse("hostile:calculator"), post_data)
        self.assertRedirects(post_response, reverse("hostile:timers"))

        # Verify timer was created in database
        timer = StructureTimer.objects.filter(structure=struct, timer_type=TimerStage.SHIELD_REINFORCE).first()
        self.assertIsNotNone(timer)
        self.assertEqual(timer.timer_datetime.hour, 18)

    def test_get_valid_stages(self):
        self.assertEqual(
            [s[0] for s in ReinforcementCalculator.get_valid_stages(StructureCategory.MEDIUM)],
            [TimerStage.SHIELD_REINFORCE],
        )
        self.assertEqual(
            [s[0] for s in ReinforcementCalculator.get_valid_stages(StructureCategory.POCO)],
            [TimerStage.SHIELD_REINFORCE],
        )
        self.assertEqual(
            [s[0] for s in ReinforcementCalculator.get_valid_stages(StructureCategory.FLEX)],
            [TimerStage.ARMOR_REINFORCE],
        )
        self.assertEqual(
            [s[0] for s in ReinforcementCalculator.get_valid_stages(StructureCategory.LARGE_XL, is_low_power=False)],
            [TimerStage.SHIELD_REINFORCE, TimerStage.ARMOR_REINFORCE],
        )
        self.assertEqual(
            [s[0] for s in ReinforcementCalculator.get_valid_stages(StructureCategory.LARGE_XL, is_low_power=True)],
            [TimerStage.ARMOR_REINFORCE],
        )

    def test_highsec_war_hq_mechanics(self):
        reinforced_at = datetime(2026, 10, 1, 13, 0, 0, tzinfo=dt_timezone.utc)

        # Medium structure in Highsec War HQ: 24 hours base delay ±1.5h jitter
        res_med = ReinforcementCalculator.calculate_exit_timer(
            reinforced_at=reinforced_at,
            structure_size=StructureCategory.MEDIUM,
            location_type=LocationSecurity.WAR_HQ,
            timer_stage=TimerStage.SHIELD_REINFORCE,
            vulnerability_hour=19,
        )
        self.assertTrue(res_med.is_applicable)
        self.assertEqual(res_med.base_delay_hours, 24.0)
        self.assertEqual(res_med.jitter_hours, 1.5)

        # Large/XL structure in Highsec War HQ (Hull timer): 24 hours base delay ±3.0h jitter
        res_large = ReinforcementCalculator.calculate_exit_timer(
            reinforced_at=reinforced_at,
            structure_size=StructureCategory.LARGE_XL,
            location_type=LocationSecurity.WAR_HQ,
            timer_stage=TimerStage.ARMOR_REINFORCE,
            vulnerability_hour=19,
        )
        self.assertTrue(res_large.is_applicable)
        self.assertEqual(res_large.base_delay_hours, 24.0)
        self.assertEqual(res_large.jitter_hours, 3.0)

    def test_low_power_mechanics(self):
        reinforced_at = datetime(2026, 10, 1, 13, 0, 0, tzinfo=dt_timezone.utc)

        # Large/XL Low Power skips armor timer
        res_lp_armor = ReinforcementCalculator.calculate_exit_timer(
            reinforced_at=reinforced_at,
            structure_size=StructureCategory.LARGE_XL,
            timer_stage=TimerStage.SHIELD_REINFORCE,
            is_low_power=True,
        )
        self.assertFalse(res_lp_armor.is_applicable)
        self.assertIn("Low Power", res_lp_armor.status_message)

        # Large/XL Low Power Hull timer is applicable
        res_lp_hull = ReinforcementCalculator.calculate_exit_timer(
            reinforced_at=reinforced_at,
            structure_size=StructureCategory.LARGE_XL,
            timer_stage=TimerStage.ARMOR_REINFORCE,
            is_low_power=True,
            vulnerability_hour=19,
        )
        self.assertTrue(res_lp_hull.is_applicable)
        self.assertEqual(res_lp_hull.power_state, PowerState.LOW_POWER)

    def test_form_validation(self):
        from hostile.forms import ReinforcementCalculatorForm

        # Verify chosen_day is not present in form fields
        form_blank = ReinforcementCalculatorForm()
        self.assertNotIn("chosen_day", form_blank.fields)

        # Medium structure with Hull stage should be rejected
        form_med_hull = ReinforcementCalculatorForm(
            data={
                "structure_size": StructureCategory.MEDIUM,
                "location_type": LocationSecurity.NULLSEC_LOWSEC,
                "timer_stage": TimerStage.ARMOR_REINFORCE,
            }
        )
        self.assertFalse(form_med_hull.is_valid())
        self.assertIn("timer_stage", form_med_hull.errors)

        # FLEX structure with Armor stage should be rejected
        form_flex_armor = ReinforcementCalculatorForm(
            data={
                "structure_size": StructureCategory.FLEX,
                "location_type": LocationSecurity.NULLSEC_LOWSEC,
                "timer_stage": TimerStage.SHIELD_REINFORCE,
            }
        )
        self.assertFalse(form_flex_armor.is_valid())
        self.assertIn("timer_stage", form_flex_armor.errors)

        # Large/XL structure in Low Power with Armor stage should be rejected
        form_large_lp_armor = ReinforcementCalculatorForm(
            data={
                "structure_size": StructureCategory.LARGE_XL,
                "location_type": LocationSecurity.NULLSEC_LOWSEC,
                "is_low_power": True,
                "timer_stage": TimerStage.SHIELD_REINFORCE,
            }
        )
        self.assertFalse(form_large_lp_armor.is_valid())
        self.assertIn("timer_stage", form_large_lp_armor.errors)

        # Large/XL structure in Low Power with Hull stage is valid
        form_large_lp_hull = ReinforcementCalculatorForm(
            data={
                "structure_size": StructureCategory.LARGE_XL,
                "location_type": LocationSecurity.NULLSEC_LOWSEC,
                "is_low_power": True,
                "timer_stage": TimerStage.ARMOR_REINFORCE,
            }
        )
        self.assertTrue(form_large_lp_hull.is_valid())
        self.assertEqual(form_large_lp_hull.cleaned_data["power_state"], PowerState.LOW_POWER)

    def test_calculator_exit_timer_automatic_vulnerability_hour(self):
        """Verify exit calculation advances directly to next vulnerability hour without chosen day requirement"""
        reinforced_at = datetime(2026, 10, 1, 12, 0, 0, tzinfo=dt_timezone.utc)
        # Large structure in Nullsec, Hull timer -> 60h delay (2.5 days) -> min target Oct 3, 2026 at 24:00 (i.e. Oct 4, 00:00 UTC)
        # Vuln hour 18:00 UTC -> next 18:00 on or after Oct 4 00:00 UTC is Oct 4, 2026 18:00 UTC
        res = ReinforcementCalculator.calculate_exit_timer(
            reinforced_at=reinforced_at,
            structure_size=StructureCategory.LARGE_XL,
            location_type=LocationSecurity.NULLSEC_LOWSEC,
            timer_stage=TimerStage.ARMOR_REINFORCE,
            vulnerability_hour=18,
        )
        self.assertTrue(res.is_applicable)
        self.assertEqual(res.nominal_exit, datetime(2026, 10, 4, 18, 0, 0, tzinfo=dt_timezone.utc))
        self.assertFalse(hasattr(res, "chosen_day"))

    def test_api_timer_calculate(self):
        api_data = {
            "structure_size": StructureCategory.MEDIUM,
            "location_type": LocationSecurity.NULLSEC_LOWSEC,
            "timer_stage": TimerStage.SHIELD_REINFORCE,
            "vulnerability_hour": 19,
            "reinforced_at": "2026-10-01T13:00:00Z",
        }
        response = self.client.post(reverse("hostile:api_timer_calculate"), api_data)
        self.assertEqual(response.status_code, 200)
        json_data = response.json()
        self.assertTrue(json_data["success"])
        self.assertTrue(json_data["is_applicable"])
        self.assertEqual(json_data["base_delay_hours"], 84.0)
        self.assertIn("2026-10-05T19:00:00", json_data["nominal_exit"])
        self.assertEqual(len(json_data["valid_stages"]), 1)
        self.assertEqual(json_data["valid_stages"][0]["value"], TimerStage.SHIELD_REINFORCE)

    def test_calculator_preload_drill_structure(self):
        """When opening calculator with a drill structure, it must preload structure_size=FLEX and timer_stage=HULL"""
        drill_struct = HostileStructure.objects.create(
            name="1DQ1-A - Moon 1 - Metenox Drill",
            structure_type=self.type_drill,
            solar_system=self.system_null,
            alliance=self.alliance,
            owner_ticker="THA",
            vulnerability_hour=18,
            state="ONLINE",
        )
        response = self.client.get(f"{reverse('hostile:calculator')}?structure_id={drill_struct.id}")
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertEqual(form.initial.get("structure_size"), StructureCategory.FLEX)
        self.assertEqual(form.initial.get("timer_stage"), TimerStage.ARMOR_REINFORCE)
        self.assertEqual(form.initial.get("vulnerability_hour"), 18)
        self.assertEqual(form.initial.get("structure"), drill_struct)
