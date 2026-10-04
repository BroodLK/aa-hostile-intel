"""
Tests for IntelManager verification, audit logging, and auto role tagging
"""

# Django
from django.contrib.auth.models import User
from django.test import TestCase

# Third Party
from eve_sde.models import SolarSystem

# AA Hostile Intel
from hostile.models import HostilePilotDossier, HostileStructure, IntelAuditLog, SystemObservation
from hostile.services.intel_manager import IntelManager


class TestIntelManager(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.officer = User.objects.create_user(username="officer_alice", password="password")
        cls.member = User.objects.create_user(username="member_bob", password="password")
        cls.system = SolarSystem.objects.create(id=30000142, name="Jita", security_status=0.9)

    def test_verify_structure_and_audit(self):
        struct = HostileStructure.objects.create(
            name="Forward Staging Astrahus",
            solar_system=self.system,
            created_by=self.member,
            is_verified=False,
        )
        self.assertFalse(struct.is_verified)

        IntelManager.verify_structure(struct, self.officer)
        struct.refresh_from_db()
        self.assertTrue(struct.is_verified)

        audit = IntelAuditLog.objects.filter(target_id=struct.id, action="VERIFY").first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.user, self.officer)

    def test_toggle_pin_and_verify_observation(self):
        obs = SystemObservation.objects.create(
            solar_system=self.system,
            threat_level="HIGH",
            observation_text="Gate camp on Perimeter gate",
            created_by=self.member,
            is_pinned=False,
            is_verified=False,
        )
        IntelManager.toggle_pin_observation(obs, self.officer)
        obs.refresh_from_db()
        self.assertTrue(obs.is_pinned)

        IntelManager.verify_observation(obs, self.officer)
        obs.refresh_from_db()
        self.assertTrue(obs.is_verified)

    def test_auto_tag_pilot(self):
        pilot = HostilePilotDossier.objects.create(
            character_id=99123456,
            character_name="Enemy Super Pilot",
            notes="Known Cyno alt and Avatar titan pilot with Fax support",
            created_by=self.member,
        )
        IntelManager.auto_tag_pilot(pilot)
        pilot.save()

        self.assertTrue(pilot.is_cyno_alt)
        self.assertTrue(pilot.is_titan_pilot)
        self.assertTrue(pilot.is_super_pilot)
        self.assertTrue(pilot.is_fax_pilot)
        self.assertTrue(pilot.is_capital_pilot)

        dread_pilot = HostilePilotDossier.objects.create(
            character_id=99123457,
            character_name="Enemy Dread Pilot",
            notes="Active Naglfar and Revelation dread pilot in staging",
            created_by=self.member,
        )
        IntelManager.auto_tag_pilot(dread_pilot)
        self.assertTrue(dread_pilot.is_dread_pilot)
        self.assertTrue(dread_pilot.is_capital_pilot)

        IntelManager.verify_pilot(pilot, self.officer)
        pilot.refresh_from_db()
        self.assertTrue(pilot.is_verified)
