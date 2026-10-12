"""
Tests for bidirectional synchronization between Hostile Intel Structure Timers and Alliance Auth Timerboard
"""

# Standard Library
from datetime import timedelta
from unittest.mock import patch

# Django
from django.apps import apps
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

# Alliance Auth
from allianceauth.eveonline.models import EveCharacter, EveCorporationInfo
from allianceauth.tests.auth_utils import AuthUtils
from allianceauth.timerboard.models import Timer

# Third Party
from eve_sde.models import ItemCategory, ItemGroup, ItemType, SolarSystem

# AA Hostile Intel
from hostile.models import HostileAlliance, HostileCorporation, HostileStructure, StructureTimer
from hostile.services.timer_sync import TimerboardSyncService
from hostile.signals import connect_timerboard_signals
from hostile.tasks import sync_timerboard_timers


class TestTimerboardSynchronization(TestCase):
    @classmethod
    def setUpTestData(cls):
        connect_timerboard_signals()
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
        cls.keepstar_type = ItemType.objects.create(
            id=35834,
            name="Keepstar",
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

        cls.corp = EveCorporationInfo.objects.create(
            corporation_id=20000001,
            corporation_name="Hostile Corp",
            corporation_ticker="HCORP",
            member_count=50,
        )

        cls.user_officer = AuthUtils.create_user(username="officer_user")
        cls.char = AuthUtils.add_main_character(
            cls.user_officer, "Officer Pilot", 21100099, corp_id=20000001
        )
        AuthUtils.add_permission_to_user_by_name(
            "hostile.basic_access", cls.user_officer
        )
        AuthUtils.add_permission_to_user_by_name(
            "hostile.manage_intel", cls.user_officer
        )

        cls.client = Client()

    def setUp(self):
        self.client.force_login(self.user_officer)

    def test_sync_hostile_structure_timer_to_timerboard(self):
        """Creating a Hostile StructureTimer automatically creates a corresponding Timer in Timerboard"""
        ansiblex = HostileStructure.objects.create(
            name="1DQ1-A » Jita Ansiblex",
            structure_type=self.ansiblex_type,
            solar_system=self.system_1dq,
            alliance=self.alliance,
            owner_ticker="H-ALLI",
            location_details="Planet IV - Moon 1",
            created_by=self.user_officer,
        )

        expiry = timezone.now() + timedelta(days=1)
        st = StructureTimer.objects.create(
            structure=ansiblex,
            solar_system=self.system_1dq,
            timer_type="HULL",
            timer_datetime=expiry,
            notes="Defend grid and bubble anchor",
            created_by=self.user_officer,
        )

        self.assertIsNotNone(st.timerboard_timer_id)
        tb_timer = Timer.objects.filter(id=st.timerboard_timer_id).first()
        self.assertIsNotNone(tb_timer)
        self.assertEqual(tb_timer.system, "1DQ1-A")
        self.assertEqual(tb_timer.structure, Timer.Structure.ANSIBLEX)
        self.assertEqual(tb_timer.timer_type, Timer.TimerType.HULL)
        self.assertEqual(tb_timer.objective, Timer.Objective.HOSTILE)
        self.assertEqual(tb_timer.planet_moon, "Planet IV - Moon 1")
        self.assertEqual(tb_timer.details, "Defend grid and bubble anchor")
        self.assertEqual(tb_timer.eve_time, expiry)

    def test_update_hostile_structure_timer_updates_timerboard(self):
        """Updating a Hostile StructureTimer updates the associated Timerboard Timer"""
        struct = HostileStructure.objects.create(
            name="1DQ1-A Keepstar Prime",
            structure_type=self.keepstar_type,
            solar_system=self.system_1dq,
            alliance=self.alliance,
            created_by=self.user_officer,
        )

        expiry = timezone.now() + timedelta(days=2)
        st = StructureTimer.objects.create(
            structure=struct,
            solar_system=self.system_1dq,
            timer_type="ARMOR",
            timer_datetime=expiry,
            notes="Initial Armor Timer",
            created_by=self.user_officer,
        )

        tb_timer = Timer.objects.get(id=st.timerboard_timer_id)
        self.assertEqual(tb_timer.timer_type, Timer.TimerType.ARMOR)

        # Update Hostile timer
        new_expiry = expiry + timedelta(hours=3)
        st.timer_type = "HULL"
        st.timer_datetime = new_expiry
        st.notes = "Updated to Hull final timer"
        st.save()

        tb_timer.refresh_from_db()
        self.assertEqual(tb_timer.timer_type, Timer.TimerType.HULL)
        self.assertEqual(tb_timer.eve_time, new_expiry)
        self.assertIn("Updated to Hull final timer", tb_timer.details)

    def test_delete_hostile_structure_timer_deletes_timerboard_timer(self):
        """Deleting a Hostile StructureTimer deletes the associated Timerboard Timer"""
        struct = HostileStructure.objects.create(
            name="Jita Perch",
            structure_type=self.ansiblex_type,
            solar_system=self.system_jita,
            created_by=self.user_officer,
        )
        st = StructureTimer.objects.create(
            structure=struct,
            solar_system=self.system_jita,
            timer_type="HULL",
            timer_datetime=timezone.now() + timedelta(hours=10),
            created_by=self.user_officer,
        )
        tb_id = st.timerboard_timer_id
        self.assertTrue(Timer.objects.filter(id=tb_id).exists())

        st.delete()
        self.assertFalse(Timer.objects.filter(id=tb_id).exists())

    def test_sync_timerboard_timer_to_hostile_intel(self):
        """Creating a Timer in Timerboard creates a corresponding StructureTimer in Hostile Intel"""
        expiry = timezone.now() + timedelta(days=3)
        tb_timer = Timer.objects.create(
            details="Hostile Ansiblex Gate [CONDI]",
            system="1DQ1-A",
            planet_moon="Planet III",
            structure=Timer.Structure.ANSIBLEX,
            timer_type=Timer.TimerType.HULL,
            objective=Timer.Objective.HOSTILE,
            eve_time=expiry,
            user=self.user_officer,
            eve_corp=self.corp,
        )

        st = StructureTimer.objects.filter(timerboard_timer_id=tb_timer.id).first()
        self.assertIsNotNone(st)
        self.assertEqual(st.solar_system, self.system_1dq)
        self.assertEqual(st.timer_type, "HULL")
        self.assertEqual(st.timer_datetime, expiry)
        self.assertEqual(st.notes, "Hostile Ansiblex Gate [CONDI]")
        self.assertIsNotNone(st.structure)
        self.assertEqual(st.structure.structure_type, self.ansiblex_type)

    def test_update_and_delete_timerboard_timer_syncs_to_hostile(self):
        """Updating and deleting a Timerboard timer updates and deletes the Hostile StructureTimer"""
        expiry = timezone.now() + timedelta(days=1)
        tb_timer = Timer.objects.create(
            details="Hostile Keepstar",
            system="1DQ1-A",
            structure=Timer.Structure.KEEPSTAR,
            timer_type=Timer.TimerType.ARMOR,
            objective=Timer.Objective.HOSTILE,
            eve_time=expiry,
            user=self.user_officer,
            eve_corp=self.corp,
        )

        st = StructureTimer.objects.get(timerboard_timer_id=tb_timer.id)
        self.assertEqual(st.timer_type, "ARMOR")

        # Update in Timerboard
        new_expiry = expiry + timedelta(days=1)
        tb_timer.timer_type = Timer.TimerType.HULL
        tb_timer.eve_time = new_expiry
        tb_timer.details = "Keepstar Final Timer"
        tb_timer.save()

        st.refresh_from_db()
        self.assertEqual(st.timer_type, "HULL")
        self.assertEqual(st.timer_datetime, new_expiry)
        self.assertEqual(st.notes, "Keepstar Final Timer")

        # Delete from Timerboard
        tb_timer.delete()
        self.assertFalse(StructureTimer.objects.filter(id=st.id).exists())

    def test_sync_all_timers_and_celery_task(self):
        """TimerboardSyncService.sync_all_timers and sync_timerboard_timers Celery task execute cleanly"""
        # Create un-synced hostile timer
        struct = HostileStructure.objects.create(
            name="Sync Test Astrahus",
            solar_system=self.system_jita,
            created_by=self.user_officer,
        )
        st = StructureTimer.objects.create(
            structure=struct,
            solar_system=self.system_jita,
            timer_type="ARMOR",
            timer_datetime=timezone.now() + timedelta(hours=8),
        )
        StructureTimer.objects.filter(id=st.id).update(timerboard_timer_id=None)

        task_msg = sync_timerboard_timers()
        self.assertIn("Timer sync complete", task_msg)

        st.refresh_from_db()
        self.assertIsNotNone(st.timerboard_timer_id)

    def test_manual_timerboard_sync_view(self):
        """Officers can manually trigger Timerboard sync via hostile:timerboard_sync view"""
        response = self.client.get(reverse("hostile:timerboard_sync"))
        self.assertRedirects(response, reverse("hostile:timers"))

    def test_sync_timerboard_hostile_timer_does_not_assign_eve_corp_as_owner(self):
        """Hostile timer in Timerboard created by user does not set user's eve_corp as the owner"""
        expiry = timezone.now() + timedelta(days=2)
        tb_timer = Timer.objects.create(
            details="Hostile Astrahus Anchor",
            system="1DQ1-A",
            structure=Timer.Structure.ASTRAHUS,
            timer_type=Timer.TimerType.ARMOR,
            objective=Timer.Objective.HOSTILE,
            eve_time=expiry,
            user=self.user_officer,
            eve_corp=self.corp,
        )

        st = StructureTimer.objects.filter(timerboard_timer_id=tb_timer.id).first()
        self.assertIsNotNone(st)
        self.assertIsNone(st.structure.corporation)
        self.assertIsNone(st.structure.alliance)
        self.assertNotEqual(st.structure.owner_name, self.corp.corporation_name)

    def test_sync_timerboard_friendly_timer_sets_friendly_info(self):
        """Friendly timer in Timerboard correctly resolves friendly corporation owner"""
        expiry = timezone.now() + timedelta(days=1)
        tb_timer = Timer.objects.create(
            details="Friendly Astrahus Citadel",
            system="Jita",
            structure=Timer.Structure.ASTRAHUS,
            timer_type=Timer.TimerType.ARMOR,
            objective=Timer.Objective.FRIENDLY,
            eve_time=expiry,
            user=self.user_officer,
            eve_corp=self.corp,
        )

        st = StructureTimer.objects.filter(timerboard_timer_id=tb_timer.id).first()
        self.assertIsNotNone(st)
        self.assertEqual(st.structure.corporation.corporation_id, self.corp.corporation_id)
        self.assertTrue(st.structure.corporation.is_friendly)
