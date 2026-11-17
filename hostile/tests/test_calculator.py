"""
Unit tests for EVE Upwell Structure Reinforcement Exit Calculator.
"""

# Standard Library
from datetime import datetime
from datetime import timezone as dt_timezone

# Third Party
from eve_sde.models import ItemCategory, ItemGroup, ItemType, SolarSystem

# Django
from django.test import Client, TestCase
from django.urls import reverse

# Alliance Auth
from allianceauth.tests.auth_utils import AuthUtils

# AA Hostile Intel
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

        cls.category = ItemCategory.objects.create(
            id=65, name="Structure", published=True
        )
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
        self.assertEqual(
            result.nominal_exit, datetime(2026, 10, 5, 19, 0, 0, tzinfo=dt_timezone.utc)
        )
        self.assertEqual(
            result.earliest_exit,
            datetime(2026, 10, 5, 17, 30, 0, tzinfo=dt_timezone.utc),
        )
        self.assertEqual(
            result.latest_exit, datetime(2026, 10, 5, 20, 30, 0, tzinfo=dt_timezone.utc)
        )

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
        self.assertEqual(
            result.nominal_exit, datetime(2026, 10, 4, 19, 0, 0, tzinfo=dt_timezone.utc)
        )

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
        self.assertEqual(
            armor_result.nominal_exit,
            datetime(2026, 10, 2, 19, 0, 0, tzinfo=dt_timezone.utc),
        )

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
        self.assertEqual(
            hull_result.nominal_exit,
            datetime(2026, 10, 4, 19, 0, 0, tzinfo=dt_timezone.utc),
        )

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
        self.assertEqual(
            calc_res.nominal_exit,
            datetime(2026, 10, 2, 20, 0, 0, tzinfo=dt_timezone.utc),
        )

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
        self.assertContains(response, 'id="calcAnswer"')
        self.assertEqual(response.context["calc_state"]["structure_id"], struct.id)
        self.assertTrue(response.context["calc_result"].is_applicable)

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
        timer = StructureTimer.objects.filter(
            structure=struct, timer_type=TimerStage.SHIELD_REINFORCE
        ).first()
        self.assertIsNotNone(timer)
        self.assertEqual(timer.timer_datetime.hour, 18)

    def test_get_valid_stages(self):
        self.assertEqual(
            [
                s[0]
                for s in ReinforcementCalculator.get_valid_stages(
                    StructureCategory.MEDIUM
                )
            ],
            [TimerStage.SHIELD_REINFORCE],
        )
        self.assertEqual(
            [
                s[0]
                for s in ReinforcementCalculator.get_valid_stages(
                    StructureCategory.POCO
                )
            ],
            [TimerStage.SHIELD_REINFORCE],
        )
        self.assertEqual(
            [
                s[0]
                for s in ReinforcementCalculator.get_valid_stages(
                    StructureCategory.FLEX
                )
            ],
            [TimerStage.ARMOR_REINFORCE],
        )
        self.assertEqual(
            [
                s[0]
                for s in ReinforcementCalculator.get_valid_stages(
                    StructureCategory.LARGE_XL, is_low_power=False
                )
            ],
            [TimerStage.SHIELD_REINFORCE, TimerStage.ARMOR_REINFORCE],
        )
        self.assertEqual(
            [
                s[0]
                for s in ReinforcementCalculator.get_valid_stages(
                    StructureCategory.LARGE_XL, is_low_power=True
                )
            ],
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
        # AA Hostile Intel
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
        self.assertEqual(
            form_large_lp_hull.cleaned_data["power_state"], PowerState.LOW_POWER
        )

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
        self.assertEqual(
            res.nominal_exit, datetime(2026, 10, 4, 18, 0, 0, tzinfo=dt_timezone.utc)
        )
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
        self.assertEqual(
            json_data["valid_stages"][0]["value"], TimerStage.SHIELD_REINFORCE
        )

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
        response = self.client.get(
            f"{reverse('hostile:calculator')}?structure_id={drill_struct.id}"
        )
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertEqual(form.initial.get("structure_size"), StructureCategory.FLEX)
        self.assertEqual(form.initial.get("timer_stage"), TimerStage.ARMOR_REINFORCE)
        self.assertEqual(form.initial.get("vulnerability_hour"), 18)
        self.assertEqual(form.initial.get("structure"), drill_struct)

    def test_calculator_default_get_renders_an_answer(self):
        """With no prefill the page still opens on a calculated Large/XL null hull timer"""
        response = self.client.get(reverse("hostile:calculator"))
        self.assertEqual(response.status_code, 200)
        state = response.context["calc_state"]
        self.assertEqual(state["size"], StructureCategory.LARGE_XL)
        self.assertEqual(state["space"], LocationSecurity.NULLSEC_LOWSEC)
        self.assertEqual(state["stage"], TimerStage.ARMOR_REINFORCE)
        self.assertIsNone(state["structure_id"])
        result = response.context["calc_result"]
        self.assertTrue(result.is_applicable)
        self.assertEqual(result.base_delay_hours, 60.0)
        self.assertTrue(response.context["calc_payload"]["nominal_exit"])
        self.assertContains(response, 'id="calcInitialResult"')

    def test_calculator_prefill_armor_reinforced_structure_targets_hull(self):
        """A structure already in armor reinforce opens on its hull timer"""
        struct = HostileStructure.objects.create(
            name="Reffed Keepstar",
            structure_type=self.type_keepstar,
            solar_system=self.system_null,
            alliance=self.alliance,
            vulnerability_hour=20,
            state="ARMOR",
        )
        response = self.client.get(
            f"{reverse('hostile:calculator')}?structure_id={struct.id}"
        )
        self.assertEqual(
            response.context["calc_state"]["stage"], TimerStage.ARMOR_REINFORCE
        )
        self.assertEqual(response.context["calc_state"]["vuln_hour"], 20)

    def test_calculator_picker_structures(self):
        """The picker lists live registry structures with size and space already inferred"""
        HostileStructure.objects.create(
            name="WH Fortizar",
            structure_type=self.type_fortizar,
            solar_system=self.system_wh,
            alliance=self.alliance,
            state="LOW_POWER",
            vulnerability_hour=21,
        )
        HostileStructure.objects.create(
            name="Highsec Astrahus",
            structure_type=self.type_astrahus,
            solar_system=self.system_jita,
            owner_ticker="ABC",
        )
        dead = HostileStructure.objects.create(
            name="Dead Astrahus",
            structure_type=self.type_astrahus,
            solar_system=self.system_null,
            state="DESTROYED",
        )
        response = self.client.get(reverse("hostile:calculator"))
        picker = {p["name"]: p for p in response.context["picker_structures"]}
        self.assertNotIn("Dead Astrahus", picker)

        fortizar = picker["WH Fortizar"]
        self.assertEqual(fortizar["size"], StructureCategory.LARGE_XL)
        self.assertEqual(fortizar["space"], LocationSecurity.WORMHOLE)
        self.assertEqual(fortizar["power"], PowerState.LOW_POWER)
        # Low power skips the armor timer, so the hull timer is the one to calculate
        self.assertEqual(fortizar["stage"], TimerStage.ARMOR_REINFORCE)
        self.assertEqual(fortizar["owner"], "THA")
        self.assertEqual(fortizar["type_id"], self.type_fortizar.id)
        self.assertEqual(fortizar["vuln_hour"], 21)

        astrahus = picker["Highsec Astrahus"]
        self.assertEqual(astrahus["size"], StructureCategory.MEDIUM)
        self.assertEqual(astrahus["space"], LocationSecurity.HIGHSEC)
        self.assertEqual(astrahus["stage"], TimerStage.SHIELD_REINFORCE)
        self.assertEqual(astrahus["owner"], "ABC")

        # A destroyed structure still shows up when it's the one being prefilled
        response = self.client.get(
            f"{reverse('hostile:calculator')}?structure_id={dead.id}"
        )
        names = [p["name"] for p in response.context["picker_structures"]]
        self.assertIn("Dead Astrahus", names)

    def test_calculator_rule_rows_match_engine(self):
        """Every rules-panel cell is the engine's own base delay and jitter"""
        response = self.client.get(reverse("hostile:calculator"))
        rows = response.context["rule_rows"]
        self.assertEqual(len(rows), 7)
        reference = datetime(2026, 10, 1, 12, 0, tzinfo=dt_timezone.utc)
        for row in rows:
            self.assertEqual(len(row["cells"]), 4)
            for cell in row["cells"]:
                expected = ReinforcementCalculator.calculate_exit_timer(
                    reinforced_at=reference,
                    structure_size=row["size"],
                    location_type=cell["space"],
                    timer_stage=row["stage"],
                    power_state=row["power"],
                )
                self.assertEqual(cell["applicable"], expected.is_applicable)
                self.assertEqual(cell["base_hours"], expected.base_delay_hours)
                self.assertEqual(cell["jitter_hours"], expected.jitter_hours)

        by_label = {row["label"]: row for row in rows}
        hull = {c["space"]: c for c in by_label["Large/XL · hull timer"]["cells"]}
        self.assertEqual(hull[LocationSecurity.WORMHOLE]["base_hours"], 36.0)
        self.assertFalse(by_label["Medium · hull timer"]["cells"][0]["applicable"])

    def test_calculator_stage_map(self):
        """Stages that can't be calculated carry the reason shown on the disabled button"""
        response = self.client.get(reverse("hostile:calculator"))
        stage_map = response.context["stage_map"]
        armor, hull = TimerStage.SHIELD_REINFORCE, TimerStage.ARMOR_REINFORCE

        large = stage_map[StructureCategory.LARGE_XL]
        self.assertIsNone(large[PowerState.FULL_POWER][armor])
        self.assertIsNone(large[PowerState.FULL_POWER][hull])
        self.assertTrue(large[PowerState.LOW_POWER][armor])
        self.assertIsNone(large[PowerState.LOW_POWER][hull])
        self.assertTrue(large[PowerState.ABANDONED][armor])
        self.assertTrue(large[PowerState.ABANDONED][hull])

        self.assertTrue(stage_map[StructureCategory.MEDIUM][PowerState.FULL_POWER][hull])
        self.assertTrue(stage_map[StructureCategory.FLEX][PowerState.FULL_POWER][armor])
        # POCOs compute a hull timer in the engine, but only have the one timer
        self.assertTrue(stage_map[StructureCategory.POCO][PowerState.FULL_POWER][hull])
        self.assertIsNone(stage_map[StructureCategory.POCO][PowerState.FULL_POWER][armor])

    def test_calculator_add_timer_post_from_page_fields(self):
        """The page's hidden form fields (ISO RF time, system by name) save a timer"""
        struct = HostileStructure.objects.create(
            name="Target Fortizar",
            structure_type=self.type_fortizar,
            solar_system=self.system_null,
            alliance=self.alliance,
            vulnerability_hour=18,
        )
        post_data = {
            "structure": struct.id,
            "structure_size": StructureCategory.LARGE_XL,
            "location_type": LocationSecurity.NULLSEC_LOWSEC,
            "timer_stage": TimerStage.ARMOR_REINFORCE,
            "power_state": PowerState.FULL_POWER,
            "is_low_power": "",
            "reinforced_at": "2026-10-01T13:00:00.000+00:00",
            "vulnerability_hour": "18",
            "vulnerability_window": "",
            "save_to_timers": "on",
            "solar_system": "1DQ1-A",
            "notes": "From the new page",
        }
        response = self.client.post(reverse("hostile:calculator"), post_data)
        self.assertRedirects(response, reverse("hostile:timers"))
        timer = StructureTimer.objects.get(structure=struct)
        self.assertEqual(timer.timer_type, TimerStage.ARMOR_REINFORCE)
        self.assertEqual(timer.solar_system, self.system_null)
        # 60h after 1 Oct 13:00 is 4 Oct 01:00, so the next 18:00 is 4 Oct 18:00
        self.assertEqual(
            timer.timer_datetime, datetime(2026, 10, 4, 18, 0, tzinfo=dt_timezone.utc)
        )
        self.assertIn("From the new page", timer.notes)

    def test_api_timer_calculate_reports_reinforced_at(self):
        """The API echoes the RF time the page shows in its meta line"""
        response = self.client.get(
            reverse("hostile:api_timer_calculate"),
            {
                "structure_size": StructureCategory.POCO,
                "location_type": LocationSecurity.HIGHSEC,
                "timer_stage": TimerStage.SHIELD_REINFORCE,
                "power_state": PowerState.FULL_POWER,
                "reinforced_at": "2026-10-01T13:00:00.000Z",
                "vulnerability_hour": "",
            },
        )
        data = response.json()
        self.assertTrue(data["success"])
        self.assertIn("2026-10-01T13:00:00", data["reinforced_at"])
        self.assertEqual(data["base_delay_hours"], 24.0)
        self.assertEqual(data["jitter_hours"], 1.0)
