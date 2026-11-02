"""
Tests for pasting a copied structure reinforcement timer into Add Timer
"""

# Standard Library
from datetime import UTC, datetime, timedelta

# Third Party
from eve_sde.models import ItemCategory, ItemGroup, ItemType, SolarSystem

# Django
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

# Alliance Auth
from allianceauth.tests.auth_utils import AuthUtils

# AA Hostile Intel
from hostile.models import HostileStructure, StructureTimer
from hostile.parsers.classifier import IntelFormat, classify_intel_text
from hostile.parsers.reinforcement import ReinforcementTimerParser
from hostile.services.intel_manager import IntelManager

MERC_DEN_PASTE = """Mercenary Den
99 m
Sec. -9.9
Reinforced until 2026.11.03 19:13:30"""


def citadel_paste(name: str, when: datetime, stage: str = "") -> str:
    prefix = f"{stage} r" if stage else "R"
    return f"{name}\n12 km\nSec. -0.4\n{prefix}einforced until {when:%Y.%m.%d %H:%M:%S}"


class TestReinforcementParser(TestCase):
    def test_merc_den_sample(self):
        parsed = ReinforcementTimerParser.parse(MERC_DEN_PASTE)
        self.assertEqual(parsed["name"], "Mercenary Den")
        self.assertEqual(
            parsed["timer_datetime"], datetime(2026, 11, 3, 19, 13, 30, tzinfo=UTC)
        )
        self.assertEqual(parsed["distance"], "99 m")
        self.assertEqual(parsed["security"], -9.9)
        self.assertEqual(parsed["timer_type"], "")

    def test_citadel_with_stage_word(self):
        when = datetime(2026, 11, 4, 18, 0, tzinfo=UTC)
        parsed = ReinforcementTimerParser.parse(
            citadel_paste("1DQ1-A - Keepstar Prime", when, "Armor")
        )
        self.assertEqual(parsed["name"], "1DQ1-A - Keepstar Prime")
        self.assertEqual(parsed["timer_datetime"], when)
        self.assertEqual(parsed["timer_type"], "ARMOR")

        parsed = ReinforcementTimerParser.parse(
            citadel_paste("1DQ1-A - Keepstar Prime", when, "Hull")
        )
        self.assertEqual(parsed["timer_type"], "HULL")

    def test_anchoring(self):
        parsed = ReinforcementTimerParser.parse(
            "Some Astrahus\n5 km\nAnchoring until 2026.11.05 01:02"
        )
        self.assertEqual(parsed["timer_type"], "ANCHORING")
        self.assertEqual(
            parsed["timer_datetime"], datetime(2026, 11, 5, 1, 2, tzinfo=UTC)
        )

    def test_junk(self):
        self.assertIsNone(ReinforcementTimerParser.parse(""))
        self.assertIsNone(ReinforcementTimerParser.parse("Mercenary Den\n99 m"))
        self.assertIsNone(
            ReinforcementTimerParser.parse("X\nReinforced until 2026.13.45 99:99:99")
        )

    def test_classifier(self):
        self.assertEqual(classify_intel_text(MERC_DEN_PASTE), IntelFormat.REINFORCEMENT)


class ReinforcementFixtures(TestCase):
    @classmethod
    def setUpTestData(cls):
        category = ItemCategory.objects.create(id=65, name="Structure", published=True)
        citadel = ItemGroup.objects.create(
            id=1657, name="Citadel", category=category, published=True
        )
        den_group = ItemGroup.objects.create(
            id=4812, name="Mercenary Den", category=category, published=True
        )
        cls.keepstar = ItemType.objects.create(
            id=35834, name="Keepstar", group=citadel, published=True
        )
        cls.fortizar = ItemType.objects.create(
            id=35833, name="Fortizar", group=citadel, published=True
        )
        cls.merc_den = ItemType.objects.create(
            id=85230, name="Mercenary Den", group=den_group, published=True
        )
        cls.system = SolarSystem.objects.create(
            id=30004759, name="1DQ1-A", security_status=-0.4
        )
        cls.other_system = SolarSystem.objects.create(
            id=30002318, name="BNX-AS", security_status=-0.3
        )
        cls.keepstar_prime = HostileStructure.objects.create(
            name="1DQ1-A - Keepstar Prime",
            structure_type=cls.keepstar,
            solar_system=cls.system,
            owner_ticker="CONDI",
        )
        cls.user = AuthUtils.create_user(username="paster")
        AuthUtils.add_main_character(cls.user, "Paster", 21111111)
        AuthUtils.add_permission_to_user_by_name("hostile.basic_access", cls.user)

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.user)

    def match(self, text):
        return IntelManager.match_reinforcement_paste(
            ReinforcementTimerParser.parse(text)
        )


class TestReinforcementMatching(ReinforcementFixtures):
    def test_known_citadel_is_matched(self):
        when = timezone.now() + timedelta(hours=20)
        result = self.match(citadel_paste("1DQ1-A - Keepstar Prime", when))
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["candidates"][0]["id"], self.keepstar_prime.id)
        self.assertEqual(result["inferred_system"]["id"], self.system.id)
        self.assertEqual(result["suggested_timer_type"], "ARMOR")

    def test_ticker_and_prefix_variants_match(self):
        when = timezone.now() + timedelta(hours=20)
        result = self.match(citadel_paste("[CONDI] 1DQ1-A - Keepstar Prime", when))
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["candidates"][0]["id"], self.keepstar_prime.id)

        short = HostileStructure.objects.create(
            name="Staging Fort", structure_type=self.fortizar, solar_system=self.system
        )
        result = self.match(citadel_paste("1DQ1-A - Staging Fort", when))
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["candidates"][0]["id"], short.id)

    def test_hull_suggested_after_passed_armor_timer(self):
        StructureTimer.objects.create(
            structure=self.keepstar_prime,
            solar_system=self.system,
            timer_type="ARMOR",
            timer_datetime=timezone.now() - timedelta(hours=10),
        )
        result = self.match(
            citadel_paste("1DQ1-A - Keepstar Prime", timezone.now() + timedelta(days=2))
        )
        self.assertEqual(result["suggested_timer_type"], "HULL")

    def test_generic_merc_den_is_ambiguous(self):
        HostileStructure.objects.create(
            name="Mercenary Den (1DQ1-A)",
            structure_type=self.merc_den,
            solar_system=self.system,
        )
        HostileStructure.objects.create(
            name="Mercenary Den (BNX-AS)",
            structure_type=self.merc_den,
            solar_system=self.other_system,
        )
        result = self.match(MERC_DEN_PASTE)
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(len(result["candidates"]), 2)
        self.assertTrue(result["is_generic"])
        self.assertEqual(result["inferred_type"]["id"], self.merc_den.id)
        self.assertEqual(result["suggested_timer_type"], "HULL")

    def test_single_generic_merc_den_still_asks(self):
        HostileStructure.objects.create(
            name="Mercenary Den (1DQ1-A)",
            structure_type=self.merc_den,
            solar_system=self.system,
        )
        result = self.match(MERC_DEN_PASTE)
        self.assertEqual(result["status"], "ambiguous")

    def test_unknown_citadel_infers_system(self):
        result = self.match(
            citadel_paste("1DQ1-A - Brand New Fort", timezone.now() + timedelta(days=1))
        )
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["inferred_system"]["id"], self.system.id)
        self.assertIsNone(result["inferred_type"])
        self.assertEqual(result["candidates"], [])

    def test_unknown_merc_den_infers_type(self):
        result = self.match(MERC_DEN_PASTE)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["inferred_type"]["id"], self.merc_den.id)
        self.assertIsNone(result["inferred_system"])


class TestReinforcementApi(ReinforcementFixtures):
    def test_requires_permission(self):
        other = AuthUtils.create_user(username="nobody")
        client = Client()
        client.force_login(other)
        response = client.post(
            reverse("hostile:api_parse_reinforcement"), {"text": MERC_DEN_PASTE}
        )
        self.assertNotEqual(response.status_code, 200)

    def test_returns_match_json(self):
        when = (timezone.now() + timedelta(hours=20)).replace(microsecond=0)
        response = self.client.post(
            reverse("hostile:api_parse_reinforcement"),
            {"text": citadel_paste("1DQ1-A - Keepstar Prime", when)},
        )
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["status"], "matched")
        self.assertEqual(data["candidates"][0]["id"], self.keepstar_prime.id)
        self.assertEqual(data["timer_datetime"], when.isoformat())
        self.assertFalse(data["timer_passed"])

    def test_not_a_paste(self):
        response = self.client.post(
            reverse("hostile:api_parse_reinforcement"), {"text": "hello"}
        )
        self.assertFalse(response.json()["success"])

    def test_changes_nothing(self):
        self.client.post(
            reverse("hostile:api_parse_reinforcement"),
            {"text": citadel_paste("1DQ1-A - New Thing", timezone.now())},
        )
        self.assertEqual(HostileStructure.objects.count(), 1)
        self.assertEqual(StructureTimer.objects.count(), 0)

    def test_modal_has_paste_box(self):
        response = self.client.get(reverse("hostile:timers"))
        self.assertContains(response, 'id="reinforcementPasteInput"')
        self.assertContains(response, reverse("hostile:api_parse_reinforcement"))


class TestTimerAddFromPaste(ReinforcementFixtures):
    def post_timer(self, **data):
        payload = {"timer_type": "ARMOR", "notes": "", "state": "ONLINE"}
        payload.update(data)
        return self.client.post(reverse("hostile:timer_add"), payload)

    def test_known_structure_gets_timer_and_state(self):
        when = timezone.now() + timedelta(hours=20)
        self.post_timer(
            structure=self.keepstar_prime.id,
            timer_type="ARMOR",
            timer_datetime=when.strftime("%Y-%m-%dT%H:%M:%S"),
        )
        self.assertEqual(
            StructureTimer.objects.filter(structure=self.keepstar_prime).count(), 1
        )
        self.keepstar_prime.refresh_from_db()
        self.assertEqual(self.keepstar_prime.state, "ARMOR")

        self.post_timer(
            structure=self.keepstar_prime.id,
            timer_type="HULL",
            timer_datetime=(when + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S"),
        )
        self.keepstar_prime.refresh_from_db()
        self.assertEqual(self.keepstar_prime.state, "HULL")

    def test_second_paste_does_not_duplicate(self):
        when = timezone.now() + timedelta(hours=20)
        for offset in (0, 2):
            self.post_timer(
                structure=self.keepstar_prime.id,
                timer_type="ARMOR",
                timer_datetime=(when + timedelta(minutes=offset)).strftime(
                    "%Y-%m-%dT%H:%M:%S"
                ),
            )
        self.assertEqual(
            StructureTimer.objects.filter(structure=self.keepstar_prime).count(), 1
        )

    def test_new_merc_den_created_with_timer(self):
        when = timezone.now() + timedelta(hours=20)
        self.post_timer(
            name="Mercenary Den",
            structure_type=self.merc_den.id,
            solar_system=self.other_system.id,
            timer_type="HULL",
            timer_datetime=when.strftime("%Y-%m-%dT%H:%M:%S"),
        )
        structure = HostileStructure.objects.get(structure_type=self.merc_den)
        self.assertEqual(structure.solar_system, self.other_system)
        self.assertEqual(structure.state, "REINFORCED")
        timer = StructureTimer.objects.get(structure=structure)
        self.assertEqual(timer.timer_type, "HULL")

    def test_past_timer_does_not_change_state(self):
        self.post_timer(
            structure=self.keepstar_prime.id,
            timer_type="ARMOR",
            timer_datetime=(timezone.now() - timedelta(hours=1)).strftime(
                "%Y-%m-%dT%H:%M:%S"
            ),
        )
        self.keepstar_prime.refresh_from_db()
        self.assertEqual(self.keepstar_prime.state, "ONLINE")
