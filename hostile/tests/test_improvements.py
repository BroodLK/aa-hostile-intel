"""
Unit tests for data safety management commands, redis caching, circuit breakers,
deduplication audit logging, and suspected duplicates review queue.
"""

# Standard Library
from datetime import timedelta
from io import StringIO
from unittest.mock import MagicMock, patch

# Third Party
from eve_sde.models import ItemType, SolarSystem

# Django
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

# Alliance Auth
from allianceauth.tests.auth_utils import AuthUtils

# AA Hostile Intel
from hostile.models import (
    HostileAlliance,
    HostileCorporation,
    HostileStructure,
    IntelAuditLog,
    LocalThreatScan,
)
from hostile.services.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenException,
    esi_circuit_breaker,
    zkill_circuit_breaker,
)
from hostile.services.entity_resolver import EntityResolver
from hostile.services.intel_manager import IntelManager
from hostile.services.zkill_client import zkill_client
from hostile.tasks import prune_old_threat_scans


class TestImprovements(TestCase):
    @classmethod
    def setUpTestData(cls):
        cache.clear()
        cls.system_a = SolarSystem.objects.create(
            id=30000142, name="Jita", security_status=0.9
        )
        cls.system_b = SolarSystem.objects.create(
            id=30004759, name="1DQ1-A", security_status=-0.5
        )
        cls.struct_type = ItemType.objects.create(id=35832, name="Astrahus")

        cls.user = AuthUtils.create_user(username="test_officer")
        AuthUtils.add_main_character(cls.user, "Officer Character", 21100099)
        AuthUtils.add_permission_to_user_by_name("hostile.basic_access", cls.user)
        AuthUtils.add_permission_to_user_by_name("hostile.manage_intel", cls.user)

    def setUp(self):
        cache.clear()
        esi_circuit_breaker.reset()
        zkill_circuit_breaker.reset()

    def tearDown(self):
        cache.clear()
        esi_circuit_breaker.reset()
        zkill_circuit_breaker.reset()

    def test_circuit_breaker_transitions(self):
        cb = CircuitBreaker("test_cb", failure_threshold=2, recovery_timeout=0.2)
        cb.reset()

        self.assertEqual(cb.get_state(), CircuitBreaker.STATE_CLOSED)
        self.assertTrue(cb.can_execute())

        # Record 1 failure
        cb.record_failure(Exception("Timeout 1"))
        self.assertEqual(cb.get_state(), CircuitBreaker.STATE_CLOSED)

        # Record 2nd failure -> Tripped to OPEN
        cb.record_failure(Exception("Timeout 2"))
        self.assertEqual(cb.get_state(), CircuitBreaker.STATE_OPEN)
        self.assertTrue(cb.is_open())
        self.assertFalse(cb.can_execute())

        # Calling guarded function raises CircuitBreakerOpenException
        dummy_func = MagicMock()
        with self.assertRaises(CircuitBreakerOpenException):
            cb.call(dummy_func)
        dummy_func.assert_not_called()

        # Wait for recovery timeout
        import time

        time.sleep(0.25)
        self.assertEqual(cb.get_state(), CircuitBreaker.STATE_HALF_OPEN)
        self.assertTrue(cb.can_execute())

        # Successful probe call closes circuit
        def probe_func():
            return "ok"

        res = cb.call(probe_func)
        self.assertEqual(res, "ok")
        self.assertEqual(cb.get_state(), CircuitBreaker.STATE_CLOSED)

    def test_zkill_circuit_breaker_failfast(self):
        zkill_circuit_breaker.record_failure(Exception("503 Service Unavailable"))
        zkill_circuit_breaker.record_failure(Exception("503 Service Unavailable"))
        zkill_circuit_breaker.record_failure(Exception("503 Service Unavailable"))
        zkill_circuit_breaker.record_failure(Exception("503 Service Unavailable"))
        zkill_circuit_breaker.record_failure(Exception("503 Service Unavailable"))

        self.assertTrue(zkill_circuit_breaker.is_open())
        # With circuit breaker open, _get returns None fast without calling network
        res = zkill_client._get("https://zkillboard.com/api/stats/characterID/12345/")
        self.assertIsNone(res)

    def test_deduplicate_structures_dry_run_and_system_filter(self):
        # Clear any preexisting structures
        HostileStructure.objects.all().delete()

        # Create structures in system_a (Jita)
        s1 = HostileStructure.objects.create(
            name="Perimeter Gate",
            solar_system=self.system_a,
            structure_type=self.struct_type,
            is_verified=True,
        )
        s2 = HostileStructure.objects.create(
            name="Perimeter Gate (Jita)",
            solar_system=self.system_a,
            structure_type=self.struct_type,
            is_verified=False,
        )

        # Dry run management command
        out = StringIO()
        call_command(
            "hostile_deduplicate_structures",
            dry_run=True,
            system="Jita",
            verbose=True,
            stdout=out,
        )
        output = out.getvalue()
        self.assertIn("[DRY RUN]", output)
        self.assertIn("Would merge 1 duplicate structure(s)", output)
        # Database objects should still both exist
        self.assertTrue(HostileStructure.objects.filter(id=s1.id).exists())
        self.assertTrue(HostileStructure.objects.filter(id=s2.id).exists())

        # Real run
        out_real = StringIO()
        call_command(
            "hostile_deduplicate_structures",
            system="Jita",
            verbose=True,
            stdout=out_real,
        )
        real_output = out_real.getvalue()
        self.assertIn("Successfully merged 1 duplicate structure(s)", real_output)

        # S1 retained, S2 deleted
        self.assertTrue(HostileStructure.objects.filter(id=s1.id).exists())
        self.assertFalse(HostileStructure.objects.filter(id=s2.id).exists())

        # Audit trail recorded
        audit = IntelAuditLog.objects.filter(
            action="MERGE", target_id=str(s1.id)
        ).first()
        self.assertIsNotNone(audit)
        self.assertIn("Merged 1 duplicate structure(s)", audit.details)

    def test_prune_threat_scans_command_and_task(self):
        # Clear existing scans
        LocalThreatScan.objects.all().delete()
        now = timezone.now()
        # Old scan (40 days old)
        old_scan = LocalThreatScan.objects.create(
            raw_local_text="Pilot1\nPilot2",
            solar_system=self.system_a,
            pilot_count=2,
            threat_level="HIGH",
        )
        LocalThreatScan.objects.filter(id=old_scan.id).update(
            created_at=now - timedelta(days=40)
        )

        # Recent scan (5 days old)
        recent_scan = LocalThreatScan.objects.create(
            raw_local_text="Pilot3\nPilot4",
            solar_system=self.system_b,
            pilot_count=2,
            threat_level="LOW",
        )
        LocalThreatScan.objects.filter(id=recent_scan.id).update(
            created_at=now - timedelta(days=5)
        )

        # Dry run prune
        out = StringIO()
        call_command("hostile_prune_threat_scans", days=30, dry_run=True, stdout=out)
        self.assertIn(
            "Found 1 threat scan(s) eligible for pruning", out.getvalue()
        )
        self.assertTrue(LocalThreatScan.objects.filter(id=old_scan.id).exists())

        # Execute Celery task
        msg = prune_old_threat_scans(days=30)
        self.assertIn("Pruned 1 threat scan(s)", msg)

        self.assertFalse(LocalThreatScan.objects.filter(id=old_scan.id).exists())
        self.assertTrue(
            LocalThreatScan.objects.filter(id=recent_scan.id).exists()
        )

    def test_entity_resolver_and_zkill_caching(self):
        HostileAlliance.objects.create(
            alliance_id=99005338,
            alliance_name="Pandemic Horde",
            ticker="RECK",
        )

        # First resolution
        res1 = EntityResolver.resolve_alliance("Pandemic Horde")
        self.assertIsNotNone(res1)
        self.assertEqual(res1["alliance_id"], 99005338)

        # Verify key exists in cache
        cached = cache.get("hostile_resolved_alliance_pandemic_horde")
        self.assertIsNotNone(cached)
        self.assertEqual(cached["alliance_name"], "Pandemic Horde")

        # Second resolution should hit cache
        with patch.object(
            HostileAlliance.objects, "filter", side_effect=Exception("DB should not be called")
        ):
            res2 = EntityResolver.resolve_alliance("Pandemic Horde")
            self.assertEqual(res2["alliance_id"], 99005338)

    def test_find_suspected_duplicates_and_view(self):
        # Create 2 unverified / differing named structures of same type in same system
        p1 = HostileStructure.objects.create(
            name="1DQ1-A Keepstar Staging Main",
            solar_system=self.system_b,
            structure_type=self.struct_type,
            is_verified=True,
        )
        p2 = HostileStructure.objects.create(
            name="1DQ1-A Keepstar Citadel",
            solar_system=self.system_b,
            structure_type=self.struct_type,
            is_verified=False,
        )

        suspected = IntelManager.find_suspected_duplicates(system=self.system_b)
        self.assertTrue(len(suspected) >= 1)
        group = suspected[0]
        self.assertEqual(group["primary"].id, p1.id)
        self.assertIn(p2.id, [d.id for d in group["duplicates"]])

        # Test view JSON API
        self.client.force_login(self.user)
        url = reverse("hostile:structure_suspected_duplicates")
        response = self.client.get(url, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("suspected_groups", data)
        self.assertTrue(data["count"] >= 1)
