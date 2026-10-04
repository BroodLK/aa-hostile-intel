"""
Tests for django-esi synchronization pipeline, timezone inference, and Celery tasks
"""

# Standard Library
from unittest.mock import MagicMock, patch

# Django
from django.test import TestCase
from django.utils import timezone

# Third Party
from eve_sde.models import SolarSystem

# AA Hostile Intel
from hostile.models import HostileAlliance, StructureTimer
from hostile.services.esi_client import HostileESIClientProvider
from hostile.services.sov_engine import SovAnalysisEngine
from hostile.tasks import update_sovereignty_campaigns, update_sovereignty_intelligence


class TestTasksAndSovEngine(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.system_1dq = SolarSystem.objects.create(id=30004759, name="1DQ1-A", security_status=-0.5)
        cls.alliance_goons = HostileAlliance.objects.create(
            alliance_id=1354830081,
            alliance_name="Goonswarm Federation",
            ticker="CONDI",
        )

    def test_timezone_inference(self):
        # EUTZ: 14:00 - 22:00 UTC
        eutz_distribution = {"18": 15, "19": 20, "20": 10, "2": 2}
        self.assertEqual(SovAnalysisEngine.infer_timezone(eutz_distribution), "EUTZ")

        # USTZ: 00:00 - 08:00 UTC
        ustz_distribution = {"1": 15, "2": 25, "3": 10, "18": 2}
        self.assertEqual(SovAnalysisEngine.infer_timezone(ustz_distribution), "USTZ")

        # AUTZ: 08:00 - 14:00 UTC
        autz_distribution = {"9": 10, "10": 18, "11": 14}
        self.assertEqual(SovAnalysisEngine.infer_timezone(autz_distribution), "AUTZ")

        # Unknown / Empty
        self.assertEqual(SovAnalysisEngine.infer_timezone({}), "UNKNOWN")

    def test_process_sovereignty_structures_does_not_auto_create_alliances(self):
        dummy_structures = [
            {
                "alliance_id": 1354830081,
                "solar_system_id": 30004759,
                "structure_id": 1000000001,
                "vulnerable_start_time": "2026-10-01T18:00:00Z",
            },
            {
                "alliance_id": 1354830081,
                "solar_system_id": 30004759,
                "structure_id": 1000000002,
                "vulnerable_start_time": "2026-10-01T19:00:00Z",
            },
            {
                "alliance_id": 99000002,
                "solar_system_id": 30004759,
                "structure_id": 1000000003,
                "vulnerable_start_time": "2026-10-01T02:00:00Z",
            },
        ]

        res = SovAnalysisEngine.process_sovereignty_structures(dummy_structures)
        # Tracked alliance is updated
        self.assertIn(1354830081, res)
        self.assertEqual(res[1354830081]["primary_timezone"], "EUTZ")
        self.assertIn("18", res[1354830081]["distribution"])

        self.alliance_goons.refresh_from_db()
        self.assertEqual(self.alliance_goons.primary_timezone, "EUTZ")

        # Untracked alliance is NOT auto-created
        self.assertNotIn(99000002, res)
        self.assertFalse(HostileAlliance.objects.filter(alliance_id=99000002).exists())

    def test_process_sovereignty_campaigns(self):
        dummy_campaigns = [
            {
                "campaign_id": 99991,
                "structure_id": 1000000001,
                "solar_system_id": 30004759,
                "event_type": "ihub_defense",
                "start_time": "2026-10-05T18:30:00Z",
                "defender_id": 1354830081,
                "defender_score": 0.6,
            }
        ]

        timers = SovAnalysisEngine.process_sovereignty_campaigns(dummy_campaigns)
        self.assertEqual(len(timers), 1)
        timer = timers[0]
        self.assertEqual(timer.timer_type, "SOV_IHUB")
        self.assertEqual(timer.solar_system, self.system_1dq)
        self.assertTrue(timer.is_verified)
        self.assertTrue(timer.is_sov_campaign)
        self.assertEqual(timer.defender_id, 1354830081)
        self.assertEqual(timer.defender_name, "Goonswarm Federation")
        self.assertEqual(timer.defender_ticker, "CONDI")
        self.assertEqual(timer.defender_score, 0.6)
        self.assertEqual(timer.defender_percent, 60)
        self.assertEqual(timer.attacker_percent, 40)
        self.assertTrue(timer.is_tracked_hostile)
        self.assertNotIn("Campaign ID:", timer.notes)
        self.assertIn("Defender: Goonswarm Federation [CONDI]", timer.notes)

    @patch("hostile.tasks.esi_client.get_sovereignty_structures")
    def test_update_sovereignty_intelligence_task(self, mock_get_structures):
        mock_get_structures.return_value = [
            {
                "alliance_id": 1354830081,
                "solar_system_id": 30004759,
                "vulnerable_start_time": "2026-10-01T18:00:00Z",
            }
        ]
        result = update_sovereignty_intelligence()
        self.assertIn("Successfully updated sovereignty profiles", result)

    @patch("hostile.tasks.esi_client.get_sovereignty_campaigns")
    def test_update_sovereignty_campaigns_task(self, mock_get_campaigns):
        mock_get_campaigns.return_value = [
            {
                "campaign_id": 8888,
                "solar_system_id": 30004759,
                "event_type": "tcu_defense",
                "start_time": "2026-10-06T19:00:00Z",
                "defender_id": 1354830081,
            }
        ]
        result = update_sovereignty_campaigns()
        self.assertIn("Successfully synchronized 1 active sovereignty campaign timers", result)

    def test_process_sovereignty_campaigns_pydantic_object(self):
        class MockPydanticCampaign:
            def __init__(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)

            def __getattr__(self, item):
                raise AttributeError(f"'{type(self).__name__}' object has no attribute {item!r}")

        pydantic_campaign = MockPydanticCampaign(
            campaign_id=77771,
            solar_system_id=30004759,
            event_type="ihub_defense",
            start_time="2026-10-07T12:00:00Z",
            defender_id=1354830081,
            defender_score=0.45,
        )

        timers = SovAnalysisEngine.process_sovereignty_campaigns([pydantic_campaign])
        self.assertEqual(len(timers), 1)
        self.assertEqual(timers[0].timer_type, "SOV_IHUB")
        self.assertEqual(timers[0].solar_system, self.system_1dq)
        self.assertTrue(timers[0].is_sov_campaign)
        self.assertEqual(timers[0].defender_percent, 45)
        self.assertEqual(timers[0].attacker_percent, 55)
        self.assertNotIn("Campaign ID:", timers[0].notes)

    def test_process_sovereignty_structures_pydantic_object(self):
        class MockPydanticStructure:
            def __init__(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)

            def __getattr__(self, item):
                raise AttributeError(f"'{type(self).__name__}' object has no attribute {item!r}")

        pydantic_structure = MockPydanticStructure(
            alliance_id=1354830081,
            solar_system_id=30004759,
            structure_id=1000000099,
            vulnerable_start_time="2026-10-01T20:00:00Z",
        )

        res = SovAnalysisEngine.process_sovereignty_structures([pydantic_structure])
        self.assertIn(1354830081, res)
        self.assertEqual(res[1354830081]["primary_timezone"], "EUTZ")

    @patch("hostile.services.esi_client.HostileESIClientProvider.fetch_safely")
    def test_esi_client_sovereignty_modern_and_fallback(self, mock_fetch):
        client_provider = HostileESIClientProvider()

        # Mock the modern client interface
        mock_client = MagicMock()
        mock_client.Sovereignty.GetSovereigntySystems = MagicMock()
        client_provider._client = mock_client

        # Modern Equinox ESI mock response from GetSovereigntySystems
        mock_fetch.return_value = {
            "solar_systems": [
                {
                    "solar_system_id": 30004759,
                    "claim": {
                        "alliance": {
                            "alliance_id": 1354830081,
                            "corporation_id": 98599770,
                            "is_capital_system": True,
                            "sovereignty_hub": {
                                "id": 1034510825648,
                                "vulnerability_window": {
                                    "start": "2026-10-03T18:00:00Z",
                                    "end": "2026-10-03T21:00:00Z",
                                },
                            },
                            "development": {
                                "activity_defense_multiplier": 6.0,
                            },
                        }
                    },
                }
            ]
        }

        # 1. Test get_sovereignty_systems
        systems = client_provider.get_sovereignty_systems()
        self.assertEqual(len(systems), 1)
        self.assertEqual(systems[0]["solar_system_id"], 30004759)

        # 2. Test get_sovereignty_map from modern systems
        sov_map = client_provider.get_sovereignty_map()
        self.assertEqual(len(sov_map), 1)
        self.assertEqual(sov_map[0]["solar_system_id"], 30004759)
        self.assertEqual(sov_map[0]["alliance_id"], 1354830081)
        self.assertTrue(sov_map[0]["is_capital_system"])

        # 3. Test get_sovereignty_structures from modern systems
        structures = client_provider.get_sovereignty_structures()
        self.assertEqual(len(structures), 1)
        self.assertEqual(structures[0]["solar_system_id"], 30004759)
        self.assertEqual(structures[0]["alliance_id"], 1354830081)
        self.assertEqual(structures[0]["structure_id"], 1034510825648)
        self.assertEqual(structures[0]["vulnerability_occupancy_level"], 6.0)

        # 4. Test sync_sovereignty_held auto-assigns capital and counts
        held_counts = SovAnalysisEngine.sync_sovereignty_held(sov_map)
        self.assertEqual(held_counts.get(1354830081), 1)
        self.alliance_goons.refresh_from_db()
        self.assertEqual(self.alliance_goons.systems_held_count, 1)
        self.assertEqual(self.alliance_goons.sov_capital, self.system_1dq)

        # 5. Test legacy fallback when GetSovereigntySystems is absent
        mock_legacy_client = MagicMock()
        del mock_legacy_client.Sovereignty.GetSovereigntySystems
        mock_legacy_client.Sovereignty.GetSovereigntyMap = MagicMock()
        mock_legacy_client.Sovereignty.GetSovereigntyStructures = MagicMock()
        client_provider._client = mock_legacy_client

        mock_fetch.side_effect = [
            [{"system_id": 30004759, "alliance_id": 1354830081, "corporation_id": 98599770}],
            [{"solar_system_id": 30004759, "alliance_id": 1354830081, "structure_id": 1034510825648, "vulnerable_start_time": "2026-10-03T18:00:00Z"}]
        ]
        legacy_map = client_provider.get_sovereignty_map()
        self.assertEqual(len(legacy_map), 1)
        legacy_structures = client_provider.get_sovereignty_structures()
        self.assertEqual(len(legacy_structures), 1)

    @patch("hostile.services.esi_client.HostileESIClientProvider.fetch_safely")
    def test_sync_alliance_members_and_corps(self, mock_fetch):
        self.alliance_goons.member_count = 350
        self.alliance_goons.prev_member_count = 350
        self.alliance_goons.save()

        mock_fetch.side_effect = [
            [98000001, 98000002],
            {"name": "GoonWaffle", "ticker": "WAFFL", "member_count": 150},
            {"name": "KarmaFleet", "ticker": "KF", "member_count": 250},
            {"executor_corporation_id": 98000001},
        ]

        res = SovAnalysisEngine.sync_alliance_members_and_corps(self.alliance_goons)
        self.assertTrue(res["success"])
        self.assertEqual(res["member_count"], 400)
        self.assertEqual(res["corps_count"], 2)

        self.alliance_goons.refresh_from_db()
        self.assertEqual(self.alliance_goons.member_count, 400)
        self.assertEqual(self.alliance_goons.prev_member_count, 350)
        self.assertEqual(self.alliance_goons.member_corps_count, 2)
        self.assertEqual(self.alliance_goons.executor_corp_id, 98000001)
        self.assertIn("members", self.alliance_goons.zkill_deltas)
        self.assertEqual(self.alliance_goons.zkill_deltas["members"]["pct"], 14)

    def test_fetch_safely_304_cache_hit_and_miss(self):
        from esi.exceptions import HTTPNotModified
        from django.core.cache import cache
        from hostile.services.esi_client import _get_esi_cache_key

        client_provider = HostileESIClientProvider()

        mock_op = MagicMock()
        mock_op.operation.operationId = "GetSovereigntyMapTest"
        mock_op.return_value = [{"system_id": 30004759, "alliance_id": 1354830081}]
        mock_op.results = MagicMock(return_value=[{"system_id": 30004759, "alliance_id": 1354830081}])

        # 1. Normal fetch caches the payload
        res = client_provider.fetch_safely(mock_op)
        self.assertEqual(len(res), 1)

        # 2. Subsequent call raises HTTPNotModified -> returns from cache
        mock_op.side_effect = HTTPNotModified(304, {})
        res_cached = client_provider.fetch_safely(mock_op)
        self.assertEqual(len(res_cached), 1)
        self.assertEqual(res_cached[0]["system_id"], 30004759)

        # 3. Cache cleared + HTTPNotModified -> triggers force_refresh fallback
        cache_key = _get_esi_cache_key(mock_op, (), {})
        cache.delete(cache_key)
        mock_op.side_effect = HTTPNotModified(304, {})
        mock_op.results.return_value = [{"system_id": 30004759, "alliance_id": 1354830081}]
        res_forced = client_provider.fetch_safely(mock_op)
        self.assertEqual(len(res_forced), 1)
        mock_op.results.assert_called_with(force_refresh=True)

    def test_sovereignty_sync_preserves_count_on_empty_or_failed_map(self):
        self.alliance_goons.systems_held_count = 15
        self.alliance_goons.prev_systems_held_count = 15
        self.alliance_goons.save()

        # Empty map should preserve existing count
        count = SovAnalysisEngine.sync_alliance_sovereignty_held_single(self.alliance_goons, sov_map=[])
        self.assertEqual(count, 15)
        self.alliance_goons.refresh_from_db()
        self.assertEqual(self.alliance_goons.systems_held_count, 15)

        # Bulk sync with empty map should also preserve existing counts
        results = SovAnalysisEngine.sync_sovereignty_held(sov_map=[])
        self.assertEqual(results.get(self.alliance_goons.alliance_id), 15)
        self.alliance_goons.refresh_from_db()
        self.assertEqual(self.alliance_goons.systems_held_count, 15)
