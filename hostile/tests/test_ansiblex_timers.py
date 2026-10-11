"""
Tests for Ansiblex Jump Gate and FLEX structure timers visibility and management in Hostile Intel
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
from hostile.forms import HostileStructureForm, StructureTimerForm, StructureTypeChoiceField
from hostile.models import HostileAlliance, HostileCorporation, HostileStructure, StructureTimer


class TestAnsiblexStructureTimers(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category_structure = ItemCategory.objects.create(
            id=65, name="Structure", published=True
        )
        cls.group_nav = ItemGroup.objects.create(
            id=1408,
            name="Navigation Structure",
            category=cls.category_structure,
            published=True,
        )
        cls.group_citadel = ItemGroup.objects.create(
            id=1657,
            name="Citadel",
            category=cls.category_structure,
            published=True,
        )

        cls.ansiblex_type = ItemType.objects.create(
            id=35841,
            name="Ansiblex Jump Gate",
            group=cls.group_nav,
            published=True,
        )
        cls.astrahus_type = ItemType.objects.create(
            id=35832,
            name="Astrahus",
            group=cls.group_citadel,
            published=True,
        )

        cls.system_jita = SolarSystem.objects.create(
            id=30000142, name="Jita", security_status=0.9
        )
        cls.system_1dq = SolarSystem.objects.create(
            id=30004759, name="1DQ1-A", security_status=-0.5
        )

        cls.alliance = HostileAlliance.objects.create(
            alliance_id=99000099,
            alliance_name="Hostile Alliance",
            ticker="H-ALLI",
        )

        cls.user_officer = AuthUtils.create_user(username="officer_user")
        AuthUtils.add_main_character(cls.user_officer, "Officer Pilot", 21100099)
        AuthUtils.add_permission_to_user_by_name(
            "hostile.basic_access", cls.user_officer
        )
        AuthUtils.add_permission_to_user_by_name(
            "hostile.manage_intel", cls.user_officer
        )

        cls.client = Client()

    def setUp(self):
        self.client.force_login(self.user_officer)

    def test_structure_type_choice_field_includes_ansiblex(self):
        """StructureTypeChoiceField must include Ansiblex Jump Gate in its queryset"""
        field = StructureTypeChoiceField()
        self.assertTrue(
            field.queryset.filter(name="Ansiblex Jump Gate").exists(),
            "Ansiblex Jump Gate should be included in StructureTypeChoiceField",
        )

    def test_ansiblex_structure_creation_and_timer_creation(self):
        """Creating an Ansiblex structure and linking a timer displays on structure timers board"""
        ansiblex = HostileStructure.objects.create(
            name="1DQ1-A » Jita Ansiblex",
            structure_type=self.ansiblex_type,
            solar_system=self.system_1dq,
            alliance=self.alliance,
            owner_ticker="H-ALLI",
            location_details="Near Planet V",
            created_by=self.user_officer,
        )

        timer_time = timezone.now() + timedelta(hours=24)
        timer = StructureTimer.objects.create(
            structure=ansiblex,
            solar_system=self.system_1dq,
            timer_type="HULL",
            timer_datetime=timer_time,
            created_by=self.user_officer,
        )

        self.assertIn("Ansiblex", timer.target_name)
        self.assertEqual(timer.solar_system, self.system_1dq)

        response = self.client.get(reverse("hostile:timers"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1DQ1-A » Jita Ansiblex")
        self.assertContains(response, "1DQ1-A")
        self.assertContains(response, "HULL")

    def test_ansiblex_timer_add_view_creates_structure_and_timer(self):
        """Submitting StructureTimerForm with Ansiblex type auto-registers structure and timer"""
        future_time = timezone.now() + timedelta(days=2)
        post_data = {
            "name": "K-6K16 » Jita Bridge",
            "structure_type": self.ansiblex_type.id,
            "solar_system": self.system_1dq.id,
            "alliance": self.alliance.id,
            "owner_ticker": "H-ALLI",
            "timer_type": "HULL",
            "timer_datetime": future_time.strftime("%Y-%m-%dT%H:%M"),
            "notes": "Primary reinforcement exit timer",
        }

        response = self.client.post(reverse("hostile:timer_add"), data=post_data)
        self.assertRedirects(response, reverse("hostile:timers"))

        created_struct = HostileStructure.objects.filter(
            name="K-6K16 » Jita Bridge"
        ).first()
        self.assertIsNotNone(created_struct)
        self.assertEqual(created_struct.structure_type, self.ansiblex_type)

        created_timer = StructureTimer.objects.filter(
            structure=created_struct
        ).first()
        self.assertIsNotNone(created_timer)
        self.assertEqual(created_timer.timer_type, "HULL")
        self.assertEqual(created_timer.notes, "Primary reinforcement exit timer")

    def test_target_name_property_displays_type_when_not_in_name(self):
        """target_name should append structure type name if not already in structure name"""
        struct = HostileStructure.objects.create(
            name="Primary Star Gate",
            structure_type=self.ansiblex_type,
            solar_system=self.system_1dq,
        )
        timer = StructureTimer.objects.create(
            structure=struct,
            solar_system=self.system_1dq,
            timer_type="ARMOR",
            timer_datetime=timezone.now() + timedelta(hours=5),
        )
        self.assertEqual(timer.target_name, "Primary Star Gate (Ansiblex Jump Gate)")
