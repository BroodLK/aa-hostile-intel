"""
Tests for bidirectional synchronization between Hostile Intel Structure Timers and aa-structuretimers (3.2.0)
"""

# Standard Library
from datetime import timedelta

# Django
from django.test import Client, TestCase
from django.utils import timezone

# Alliance Auth
from allianceauth.eveonline.models import EveAllianceInfo, EveCorporationInfo
from allianceauth.tests.auth_utils import AuthUtils

# Third Party
from eve_sde.models import ItemCategory, ItemGroup, ItemType, SolarSystem
from eveuniverse.models import (
    EveCategory,
    EveConstellation,
    EveGroup,
    EveRegion,
    EveSolarSystem,
    EveType,
)
from structuretimers.models import Timer as StructureTimersTimer

# AA Hostile Intel
from hostile.models import HostileAlliance, HostileCorporation, HostileStructure, StructureTimer
from hostile.services.timer_sync import TimerSyncService, compute_timer_hash
from hostile.signals import connect_all_timer_signals


class TestStructureTimersSynchronization(TestCase):
    @classmethod
    def setUpTestData(cls):
        connect_all_timer_signals()

        # Setup universe data
        cls.eve_region = EveRegion.objects.create(
            id=10000002, name="The Forge"
        )
        cls.eve_constellation = EveConstellation.objects.create(
            id=20000020, name="Kimotoro", eve_region=cls.eve_region
        )
        cls.eve_system_jita = EveSolarSystem.objects.create(
            id=30000142,
            name="Jita",
            eve_constellation=cls.eve_constellation,
            security_status=0.9,
        )
        cls.eve_system_1dq = EveSolarSystem.objects.create(
            id=30004759,
            name="1DQ1-A",
            eve_constellation=cls.eve_constellation,
            security_status=-0.5,
        )

        cls.eve_cat = EveCategory.objects.create(
            id=65, name="Structure", published=True
        )
        cls.eve_group_citadel = EveGroup.objects.create(
            id=1657,
            name="Citadel",
            eve_category=cls.eve_cat,
            published=True,
        )
        cls.eve_group_nav = EveGroup.objects.create(
            id=1408,
            name="Navigation Structure",
            eve_category=cls.eve_cat,
            published=True,
        )

        cls.eve_type_astrahus = EveType.objects.create(
            id=35832,
            name="Astrahus",
            eve_group=cls.eve_group_citadel,
            published=True,
        )
        cls.eve_type_ansiblex = EveType.objects.create(
            id=35841,
            name="Ansiblex Jump Gate",
            eve_group=cls.eve_group_nav,
            published=True,
        )

        # Setup eve_sde data
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

        cls.eve_alliance_info = EveAllianceInfo.objects.create(
            alliance_id=99000099,
            alliance_name="Hostile Alliance",
            alliance_ticker="H-ALLI",
        )

        cls.user_officer = AuthUtils.create_user(username="officer_user_st")
        AuthUtils.add_main_character(
            cls.user_officer, "Officer Pilot ST", 21100099
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

    def test_sync_hostile_structure_timer_to_structuretimers(self):
        """Creating a Hostile StructureTimer automatically creates an aa-structuretimers Timer"""
        ansiblex = HostileStructure.objects.create(
            name="1DQ1-A » Jita Ansiblex Gate",
            structure_type=self.ansiblex_type,
            solar_system=self.system_1dq,
            alliance=self.alliance,
            owner_ticker="H-ALLI",
            location_details="Planet IV Moon 1",
            created_by=self.user_officer,
        )

        expiry = timezone.now() + timedelta(days=2)
        structure_timer = StructureTimer.objects.create(
            structure=ansiblex,
            solar_system=self.system_1dq,
            timer_type="HULL",
            timer_datetime=expiry,
            notes="Reinforced by hostile dread fleet",
            created_by=self.user_officer,
        )

        structure_timer.refresh_from_db()
        self.assertIsNotNone(structure_timer.structuretimers_timer_id)
        self.assertTrue(len(structure_timer.last_sync_hash) > 0)

        st_timer = StructureTimersTimer.objects.filter(
            id=structure_timer.structuretimers_timer_id
        ).first()
        self.assertIsNotNone(st_timer)
        self.assertEqual(st_timer.structure_name, "1DQ1-A » Jita Ansiblex Gate")
        self.assertEqual(st_timer.structure_type, self.eve_type_ansiblex)
        self.assertEqual(st_timer.eve_solar_system, self.eve_system_1dq)
        self.assertEqual(st_timer.timer_type, StructureTimersTimer.Type.HULL)
        self.assertEqual(st_timer.objective, StructureTimersTimer.Objective.HOSTILE)
        self.assertEqual(st_timer.location_details, "Planet IV Moon 1")
        self.assertEqual(st_timer.owner_name, "Hostile Alliance")
        self.assertEqual(st_timer.details_notes, "Reinforced by hostile dread fleet")

    def test_update_hostile_structure_timer_updates_structuretimers(self):
        """Editing StructureTimer updates aa-structuretimers Timer seamlessly"""
        struct = HostileStructure.objects.create(
            name="Primary Fortizar",
            structure_type=self.astrahus_type,
            solar_system=self.system_jita,
            alliance=self.alliance,
            owner_ticker="H-ALLI",
            created_by=self.user_officer,
        )

        expiry = timezone.now() + timedelta(hours=12)
        structure_timer = StructureTimer.objects.create(
            structure=struct,
            solar_system=self.system_jita,
            timer_type="ARMOR",
            timer_datetime=expiry,
            notes="Armor timer",
            created_by=self.user_officer,
        )

        structure_timer.refresh_from_db()
        st_timer_id = structure_timer.structuretimers_timer_id

        # Update fields on StructureTimer
        new_expiry = timezone.now() + timedelta(hours=36)
        structure_timer.timer_datetime = new_expiry
        structure_timer.timer_type = "HULL"
        structure_timer.notes = "Armor repaired failed, now Hull"
        structure_timer.save()

        structure_timer.refresh_from_db()
        st_timer = StructureTimersTimer.objects.get(id=st_timer_id)
        self.assertEqual(st_timer.timer_type, StructureTimersTimer.Type.HULL)
        self.assertEqual(st_timer.date, new_expiry)
        self.assertEqual(st_timer.details_notes, "Armor repaired failed, now Hull")

    def test_create_structuretimers_timer_syncs_to_hostile(self):
        """External creation in aa-structuretimers automatically creates Hostile Structure and Timer"""
        expiry = timezone.now() + timedelta(days=1)
        st_timer = StructureTimersTimer.objects.create(
            eve_solar_system=self.eve_system_1dq,
            structure_type=self.eve_type_ansiblex,
            structure_name="1DQ1-A External Gate",
            location_details="Planet VII",
            owner_name="External Hostile Entity",
            timer_type=StructureTimersTimer.Type.ARMOR,
            objective=StructureTimersTimer.Objective.HOSTILE,
            date=expiry,
            details_notes="Created via Discord bot into aa-structuretimers",
            user=self.user_officer,
        )

        linked_timer = StructureTimer.objects.filter(
            structuretimers_timer_id=st_timer.id
        ).first()
        self.assertIsNotNone(linked_timer)
        self.assertEqual(linked_timer.timer_type, "ARMOR")
        self.assertEqual(linked_timer.solar_system, self.system_1dq)
        self.assertEqual(linked_timer.notes, "Created via Discord bot into aa-structuretimers")
        self.assertIsNotNone(linked_timer.structure)
        self.assertEqual(linked_timer.structure.name, "1DQ1-A External Gate")
        self.assertEqual(linked_timer.structure.structure_type, self.ansiblex_type)
        self.assertEqual(linked_timer.structure.owner_name, "External Hostile Entity")

    def test_update_structuretimers_timer_detects_untracked_external_change(self):
        """External modifications to aa-structuretimers are recognized via hash marker and reconciled"""
        # First create in Hostile
        struct = HostileStructure.objects.create(
            name="Astrahus Beta",
            structure_type=self.astrahus_type,
            solar_system=self.system_jita,
            owner_ticker="H-ALLI",
            created_by=self.user_officer,
        )
        expiry = timezone.now() + timedelta(hours=10)
        structure_timer = StructureTimer.objects.create(
            structure=struct,
            solar_system=self.system_jita,
            timer_type="ARMOR",
            timer_datetime=expiry,
            notes="Initial timer notes",
            created_by=self.user_officer,
        )

        structure_timer.refresh_from_db()
        st_timer = StructureTimersTimer.objects.get(
            id=structure_timer.structuretimers_timer_id
        )

        # External change made directly in structuretimers
        new_date = timezone.now() + timedelta(days=3)
        st_timer.date = new_date
        st_timer.timer_type = StructureTimersTimer.Type.HULL
        st_timer.details_notes = "Updated externally by reconnaissance wing"
        st_timer.save()

        # Hostile StructureTimer should have picked up untracked external changes
        structure_timer.refresh_from_db()
        self.assertEqual(structure_timer.timer_type, "HULL")
        self.assertEqual(structure_timer.timer_datetime, new_date)
        self.assertEqual(
            structure_timer.notes, "Updated externally by reconnaissance wing"
        )

    def test_delete_hostile_structure_timer_deletes_structuretimers(self):
        """Deleting StructureTimer deletes linked aa-structuretimers Timer"""
        struct = HostileStructure.objects.create(
            name="Temporary Citadel",
            structure_type=self.astrahus_type,
            solar_system=self.system_jita,
            created_by=self.user_officer,
        )
        structure_timer = StructureTimer.objects.create(
            structure=struct,
            solar_system=self.system_jita,
            timer_type="ARMOR",
            timer_datetime=timezone.now() + timedelta(hours=5),
            created_by=self.user_officer,
        )

        structure_timer.refresh_from_db()
        st_timer_id = structure_timer.structuretimers_timer_id
        self.assertTrue(StructureTimersTimer.objects.filter(id=st_timer_id).exists())

        structure_timer.delete()
        self.assertFalse(StructureTimersTimer.objects.filter(id=st_timer_id).exists())

    def test_delete_structuretimers_deletes_hostile_timer(self):
        """Deleting aa-structuretimers Timer deletes linked Hostile StructureTimer"""
        st_timer = StructureTimersTimer.objects.create(
            eve_solar_system=self.eve_system_jita,
            structure_type=self.eve_type_astrahus,
            structure_name="Ephemeral Structure",
            timer_type=StructureTimersTimer.Type.ARMOR,
            date=timezone.now() + timedelta(hours=8),
            user=self.user_officer,
        )

        linked_timer = StructureTimer.objects.filter(
            structuretimers_timer_id=st_timer.id
        ).first()
        self.assertIsNotNone(linked_timer)
        linked_timer_id = linked_timer.id

        st_timer.delete()
        self.assertFalse(
            StructureTimer.objects.filter(id=linked_timer_id).exists()
        )

    def test_sync_all_timers_self_correcting_reconciliation(self):
        """Periodic database check self-corrects any missing links or drifted data"""
        st_timer = StructureTimersTimer.objects.create(
            eve_solar_system=self.eve_system_1dq,
            structure_type=self.eve_type_ansiblex,
            structure_name="Drifted Ansiblex",
            timer_type=StructureTimersTimer.Type.HULL,
            date=timezone.now() + timedelta(days=2),
            user=self.user_officer,
        )
        # Simulate out-of-band database drift by updating structuretimers without triggering signals
        new_date = timezone.now() + timedelta(days=5)
        StructureTimersTimer.objects.filter(id=st_timer.id).update(
            date=new_date, details_notes="Drifted out of band notes"
        )

        stats = TimerSyncService.sync_all_timers()
        self.assertTrue(stats.get("synced_from_structuretimers", 0) >= 1)

        linked_timer = StructureTimer.objects.filter(
            structuretimers_timer_id=st_timer.id
        ).first()
        self.assertIsNotNone(linked_timer)
        self.assertEqual(linked_timer.timer_datetime, new_date)
        self.assertEqual(linked_timer.notes, "Drifted out of band notes")
