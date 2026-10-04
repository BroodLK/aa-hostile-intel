"""
Unit tests for zKillboard stats client and synchronization tasks
"""

# Standard Library
from datetime import timedelta
from unittest.mock import MagicMock, patch

# Django
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

# Alliance Auth
from allianceauth.tests.auth_utils import AuthUtils

# AA Hostile Intel
from eve_sde.models import SolarSystem
from hostile.models import (
    HostileAlliance,
    HostileCorporation,
    HostilePilotDossier,
    StructureTimer,
)
from hostile.services.zkill_client import ZKillClient, zkill_client
from hostile.tasks import sync_alliance_zkill_stats, update_all_zkill_stats


class ZKillClientTestCase(TestCase):
    """Tests for ZKillClient statistics parsing and model updates"""

    def setUp(self):
        self.alliance = HostileAlliance.objects.create(
            alliance_id=99005338,
            alliance_name="Pandemic Horde",
            ticker="REKTD",
        )
        self.corp = HostileCorporation.objects.create(
            corporation_id=98040755,
            corporation_name="Sniggerdly",
            ticker="SNIGG",
            alliance=self.alliance,
        )
        self.mock_alliance_data = {
            "activepvp": {
                "characters": {"count": 450},
                "corporations": {"count": 25},
                "ships": {"count": 120},
                "kills": {"count": 1820},
                "losses": {"count": 310},
            },
            "supers": {
                "supercarrier": {"count": 35},
                "titan": {"count": 18},
            },
            "hasSupers": True,
        }

    @patch("requests.Session.get")
    def test_fetch_alliance_stats_success(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = self.mock_alliance_data
        mock_get.return_value = mock_response

        data = zkill_client.fetch_alliance_stats(99005338)
        self.assertIsNotNone(data)
        self.assertEqual(data["activepvp"]["characters"]["count"], 450)
        # Verify User-Agent and gzip header
        mock_get.assert_called_once()
        args, kwargs = mock_get.call_args
        headers = kwargs.get("headers", {})
        self.assertIn("Alliance Auth Hostile Intel Plugin", headers.get("User-Agent", ""))
        self.assertEqual(headers.get("Accept-Encoding"), "gzip")

    @patch.object(ZKillClient, "fetch_alliance_stats")
    def test_update_alliance_stats(self, mock_fetch):
        mock_fetch.return_value = self.mock_alliance_data

        success = zkill_client.update_alliance_stats(self.alliance)
        self.assertTrue(success)

        self.alliance.refresh_from_db()
        self.assertEqual(self.alliance.active_pilots_count, 450)
        self.assertEqual(self.alliance.weekly_kills_count, 1820)
        self.assertEqual(self.alliance.weekly_losses_count, 310)
        self.assertEqual(self.alliance.max_super_form, 53)
        self.assertGreater(self.alliance.max_subcap_form, 0)
        self.assertIsNotNone(self.alliance.zkill_stats_updated_at)

    @patch.object(ZKillClient, "fetch_alliance_stats")
    def test_update_alliance_stats_infer_timezone_from_activity(self, mock_fetch):
        # Activity with heavy USTZ distribution (hours 01 to 06 UTC)
        mock_fetch.return_value = {
            "activepvp": {
                "characters": {"count": 200},
                "kills": {"count": 500},
                "losses": {"count": 100},
            },
            "activity": {
                "0": 10,
                "1": 45,
                "2": 80,
                "3": 95,
                "4": 70,
                "5": 30,
                "14": 5,
                "18": 10,
            },
        }

        success = zkill_client.update_alliance_stats(self.alliance)
        self.assertTrue(success)

        self.alliance.refresh_from_db()
        self.assertEqual(self.alliance.primary_timezone, "USTZ")

    @patch.object(ZKillClient, "fetch_alliance_stats")
    def test_update_alliance_stats_historical_max_form_lookback(self, mock_fetch):
        # Current active characters is 50, but historical months had 500 active characters
        mock_fetch.return_value = {
            "activepvp": {
                "characters": {"count": 50},
                "kills": {"count": 120},
                "losses": {"count": 30},
            },
            "months": {
                "202601": {"characters": {"count": 500}, "kills": {"count": 2500}},
                "202512": {"characters": {"count": 420}, "kills": {"count": 2100}},
            },
            "groups": {
                "485": {"count": 40},  # Dreadnoughts
                "1538": {"count": 20},  # Force Auxiliaries
                "547": {"count": 15},   # Carriers
            },
            "supers": {
                "supercarrier": {"count": 25},
                "titan": {"count": 10},
            },
        }

        # Reset form counts
        self.alliance.max_subcap_form = 0
        self.alliance.max_cap_form = 0
        self.alliance.max_super_form = 0
        self.alliance.save()

        success = zkill_client.update_alliance_stats(self.alliance)
        self.assertTrue(success)

        self.alliance.refresh_from_db()
        # Max subcap form should be based on peak historical 500 * 0.6 = 300
        self.assertEqual(self.alliance.max_subcap_form, 300)
        # Capitals from groups: 40 + 20 + 15 = 75
        self.assertEqual(self.alliance.max_cap_form, 75)
        # Supers: 25 + 10 = 35
        self.assertEqual(self.alliance.max_super_form, 35)

    @patch.object(ZKillClient, "fetch_corporation_stats")
    def test_update_corporation_stats(self, mock_fetch):
        mock_fetch.return_value = {
            "activepvp": {
                "characters": {"count": 80},
                "kills": {"count": 320},
                "losses": {"count": 45},
            },
            "supers": {
                "supercarrier": {"count": 5},
                "titan": {"count": 2},
            },
        }

        success = zkill_client.update_corporation_stats(self.corp)
        self.assertTrue(success)

        self.corp.refresh_from_db()
        self.assertEqual(self.corp.active_pilots_count, 80)
        self.assertEqual(self.corp.weekly_kills_count, 320)
        self.assertEqual(self.corp.weekly_losses_count, 45)
        self.assertEqual(self.corp.max_super_form, 7)
        self.assertIsNotNone(self.corp.zkill_stats_updated_at)

    @patch.object(ZKillClient, "fetch_alliance_stats")
    def test_update_alliance_stats_with_weekly_labels(self, mock_fetch):
        """Test extraction of 7d kills and losses when zKill returns data using weeklyLabels"""
        mock_fetch.return_value = {
            "activepvp": {
                "characters": {"count": 320},
                "kills": {"count": 950},
                # Note: activepvp in zKillboard API does not provide losses count
            },
            "weeklyLabels": {
                "pvp": {
                    "shipsDestroyed": 950,
                    "shipsLost": 184,
                    "pointsDestroyed": 5000,
                    "pointsLost": 1200,
                    "iskDestroyed": 15000000000,
                    "iskLost": 4000000000,
                }
            },
        }

        success = zkill_client.update_alliance_stats(self.alliance)
        self.assertTrue(success)

        self.alliance.refresh_from_db()
        self.assertEqual(self.alliance.active_pilots_count, 320)
        self.assertEqual(self.alliance.weekly_kills_count, 950)
        self.assertEqual(self.alliance.weekly_losses_count, 184)

    @patch.object(ZKillClient, "fetch_corporation_stats")
    def test_update_corporation_stats_with_weekly_labels(self, mock_fetch):
        """Test extraction of 7d kills and losses for corporation with weeklyLabels"""
        mock_fetch.return_value = {
            "activepvp": {
                "characters": {"count": 42},
                "kills": {"count": 120},
            },
            "weeklyLabels": {
                "cat:6": {
                    "shipsDestroyed": 120,
                    "shipsLost": 37,
                }
            },
        }

        success = zkill_client.update_corporation_stats(self.corp)
        self.assertTrue(success)

        self.corp.refresh_from_db()
        self.assertEqual(self.corp.active_pilots_count, 42)
        self.assertEqual(self.corp.weekly_kills_count, 120)
        self.assertEqual(self.corp.weekly_losses_count, 37)

    @patch.object(ZKillClient, "update_alliance_stats")
    @patch.object(ZKillClient, "update_corporation_stats")
    def test_celery_tasks(self, mock_corp_update, mock_alliance_update):
        mock_alliance_update.return_value = True
        mock_corp_update.return_value = True

        result = update_all_zkill_stats()
        self.assertIn("zKillboard stats updated", result)
        self.assertTrue(mock_alliance_update.called)
        self.assertTrue(mock_corp_update.called)

        sync_res = sync_alliance_zkill_stats(99005338)
        self.assertTrue(sync_res)

    @patch("django.core.cache.cache.add")
    def test_celery_task_locked(self, mock_cache_add):
        mock_cache_add.return_value = False
        result = update_all_zkill_stats()
        self.assertEqual(result, "Task already running")


class ZKillViewsTestCase(TestCase):
    """Tests for Alliance edit, add, and zKill sync views"""

    def setUp(self):
        self.client = Client()
        self.officer = AuthUtils.create_user(username="intel_officer")
        AuthUtils.add_main_character(self.officer, "Intel Officer Char", 21100099)
        AuthUtils.add_permission_to_user_by_name("hostile.basic_access", self.officer)
        AuthUtils.add_permission_to_user_by_name("hostile.manage_intel", self.officer)

        self.alliance = HostileAlliance.objects.create(
            alliance_id=99005338,
            alliance_name="Pandemic Horde",
            ticker="REKTD",
        )

    @patch.object(ZKillClient, "update_alliance_stats")
    def test_alliance_sync_zkill_view(self, mock_update):
        mock_update.return_value = True
        self.client.force_login(self.officer)

        url = reverse("hostile:alliance_sync_zkill", args=[self.alliance.id])
        response = self.client.get(url, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(mock_update.called)

    def test_alliance_edit_view(self):
        self.client.force_login(self.officer)
        url = reverse("hostile:alliance_edit", args=[self.alliance.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        post_data = {
            "alliance_id": self.alliance.alliance_id,
            "alliance_name": "Pandemic Horde Inc",
            "ticker": "PH",
            "primary_timezone": "EUTZ",
            "max_subcap_form": 250,
            "max_cap_form": 60,
            "max_super_form": 25,
        }
        post_response = self.client.post(url, post_data, follow=True)
        self.assertEqual(post_response.status_code, 200)

        self.alliance.refresh_from_db()
        self.assertEqual(self.alliance.alliance_name, "Pandemic Horde Inc")
        self.assertEqual(self.alliance.max_subcap_form, 250)
        self.assertEqual(self.alliance.max_cap_form, 60)
        self.assertEqual(self.alliance.max_super_form, 25)

    def test_ingest_pilots_from_zkill_supers(self):
        """Test automatic ingestion of Titan and Supercarrier pilots from zKill supers payload"""
        payload = {
            "supers": {
                "titan": {
                    "count": 2,
                    "data": [
                        {
                            "characterID": 950001,
                            "characterName": "Titan Commander Alpha",
                            "corporationName": "Sniggerdly",
                            "shipName": "Avatar",
                        }
                    ],
                },
                "supercarrier": {
                    "count": 1,
                    "data": [
                        {
                            "characterID": 950002,
                            "characterName": "Super Carrier Pilot Beta",
                            "corporationName": "Sniggerdly",
                            "shipName": "Nyx",
                        }
                    ],
                },
            }
        }
        count = zkill_client.ingest_pilots_from_zkill_data(payload, alliance=self.alliance)
        self.assertEqual(count, 2)

        titan_dossier = HostilePilotDossier.objects.get(character_id=950001)
        self.assertTrue(titan_dossier.is_titan_pilot)
        self.assertTrue(titan_dossier.is_super_pilot)
        self.assertTrue(titan_dossier.is_capital_pilot)
        self.assertEqual(titan_dossier.alliance_name, self.alliance.alliance_name)
        self.assertEqual(titan_dossier.corporation_name, "Sniggerdly")
        self.assertIn("Titan", titan_dossier.notes)

        super_dossier = HostilePilotDossier.objects.get(character_id=950002)
        self.assertFalse(super_dossier.is_titan_pilot)
        self.assertTrue(super_dossier.is_super_pilot)
        self.assertTrue(super_dossier.is_capital_pilot)
        self.assertEqual(super_dossier.alliance_name, self.alliance.alliance_name)

    def test_ingest_pilots_from_zkill_toplists_and_groups(self):
        """Test automatic ingestion of Dreadnought, FAX, and Black Ops pilots from topLists and groups"""
        payload = {
            "topLists": [
                {
                    "type": "dreadnought",
                    "values": [
                        {
                            "characterID": 950003,
                            "characterName": "Dread Striker Gamma",
                            "corporationName": "Sniggerdly",
                            "shipName": "Naglfar",
                        }
                    ],
                },
                {
                    "type": "forceauxiliary",
                    "values": [
                        {
                            "characterID": 950004,
                            "characterName": "Logi FAX Delta",
                            "corporationName": "Sniggerdly",
                            "shipName": "Apostle",
                        }
                    ],
                },
            ],
            "groups": {
                "898": {  # Black Ops
                    "data": [
                        {
                            "characterID": 950005,
                            "characterName": "Hunter Epsilon",
                            "corporationName": "Sniggerdly",
                            "shipName": "Redeemer",
                        }
                    ]
                }
            },
        }
        count = zkill_client.ingest_pilots_from_zkill_data(payload, alliance=self.alliance)
        self.assertEqual(count, 3)

        dread_dossier = HostilePilotDossier.objects.get(character_id=950003)
        self.assertTrue(dread_dossier.is_dread_pilot)
        self.assertTrue(dread_dossier.is_capital_pilot)
        self.assertFalse(dread_dossier.is_fax_pilot)
        self.assertIn("Dreadnought", dread_dossier.notes)

        fax_dossier = HostilePilotDossier.objects.get(character_id=950004)
        self.assertTrue(fax_dossier.is_fax_pilot)
        self.assertTrue(fax_dossier.is_capital_pilot)
        self.assertFalse(fax_dossier.is_dread_pilot)
        self.assertIn("FAX", fax_dossier.notes)

        blops_dossier = HostilePilotDossier.objects.get(character_id=950005)
        self.assertTrue(blops_dossier.is_blops_pilot)
        self.assertFalse(blops_dossier.is_capital_pilot)

    def test_ingest_pilots_merge_existing_dossier(self):
        """Test that re-running ingestion merges new capabilities without overwriting existing tags or manual notes"""
        existing = HostilePilotDossier.objects.create(
            character_id=950006,
            character_name="Multi Role Pilot",
            corporation_name="Sniggerdly",
            is_cyno_alt=True,
            notes="Manual scout notes.",
        )
        payload = {
            "topLists": [
                {
                    "type": "dreads",
                    "values": [
                        {
                            "characterID": 950006,
                            "characterName": "Multi Role Pilot",
                            "shipName": "Revelation",
                        }
                    ],
                }
            ]
        }
        zkill_client.ingest_pilots_from_zkill_data(payload, alliance=self.alliance)
        existing.refresh_from_db()

        self.assertTrue(existing.is_cyno_alt)
        self.assertTrue(existing.is_dread_pilot)
        self.assertTrue(existing.is_capital_pilot)
        self.assertIn("Manual scout notes.", existing.notes)
        self.assertIn("Revelation", existing.notes)
        self.assertEqual(existing.alliance_name, self.alliance.alliance_name)

    def test_ingest_pilots_rejects_ships_systems_gates_and_corps(self):
        """
        Test that automatic pilot ingestion rejects non-pilot data (ships, solar systems,
        stargates, corporations, alliances) and instead links ships to actual pilots.
        """
        payload = {
            "topLists": [
                {
                    "type": "character",
                    "values": [
                        {
                            "id": 950008,
                            "name": "Legitimate Pilot",
                            "kills": 45,
                        }
                    ],
                },
                {
                    "type": "shipType",
                    "values": [
                        {
                            "id": 19720,  # Revelation
                            "name": "Revelation",
                            "kills": 30,
                        },
                        {
                            "id": 645,  # Dominix
                            "name": "Dominix",
                            "kills": 15,
                        },
                    ],
                },
                {
                    "type": "solarSystem",
                    "values": [
                        {
                            "id": 30000142,  # Jita
                            "name": "Jita",
                            "kills": 60,
                        }
                    ],
                },
                {
                    "type": "location",
                    "values": [
                        {
                            "id": 40012345,
                            "name": "Stargate (Jita)",
                            "kills": 20,
                        }
                    ],
                },
                {
                    "type": "corporation",
                    "values": [
                        {
                            "id": 98040755,
                            "name": "Sniggerdly",
                            "kills": 100,
                        }
                    ],
                },
            ],
            "groups": {
                "485": {  # Dreadnought group
                    "topLists": [
                        {
                            "type": "character",
                            "values": [
                                {
                                    "id": 950009,
                                    "name": "Dread Commander Zeta",
                                    "kills": 10,
                                }
                            ],
                        },
                        {
                            "type": "shipType",
                            "values": [
                                {
                                    "id": 19720,
                                    "name": "Revelation",
                                    "kills": 8,
                                }
                            ],
                        },
                        {
                            "type": "solarSystem",
                            "values": [
                                {
                                    "id": 30000142,
                                    "name": "Jita",
                                    "kills": 5,
                                }
                            ],
                        },
                    ]
                }
            },
        }

        count = zkill_client.ingest_pilots_from_zkill_data(payload, alliance=self.alliance)
        # Only 2 legitimate characters should be created (950008, 950009)
        self.assertEqual(count, 2)

        # Confirm non-pilot entities were NOT created as dossiers
        self.assertFalse(HostilePilotDossier.objects.filter(character_id=19720).exists())
        self.assertFalse(HostilePilotDossier.objects.filter(character_id=645).exists())
        self.assertFalse(HostilePilotDossier.objects.filter(character_id=30000142).exists())
        self.assertFalse(HostilePilotDossier.objects.filter(character_id=40012345).exists())
        self.assertFalse(HostilePilotDossier.objects.filter(character_id=98040755).exists())
        self.assertFalse(HostilePilotDossier.objects.filter(character_name="Revelation").exists())
        self.assertFalse(HostilePilotDossier.objects.filter(character_name="Jita").exists())
        self.assertFalse(HostilePilotDossier.objects.filter(character_name="Stargate (Jita)").exists())

        # Confirm legitimate pilots exist and dread pilot linked to group ship data
        p_dread = HostilePilotDossier.objects.get(character_id=950009)
        self.assertTrue(p_dread.is_dread_pilot)
        self.assertTrue(p_dread.is_capital_pilot)
        self.assertIn("Revelation", p_dread.notes)

    def test_cleanup_invalid_pilot_dossiers(self):
        """Test that cleanup_invalid_pilot_dossiers purges previously created bad dossiers"""
        # Create bad dossiers
        HostilePilotDossier.objects.create(
            character_id=19720,
            character_name="Revelation",
            notes="Bad ship dossier",
        )
        HostilePilotDossier.objects.create(
            character_id=30000142,
            character_name="Jita",
            notes="Bad system dossier",
        )
        HostilePilotDossier.objects.create(
            character_id=40099999,
            character_name="Stargate (Amamake)",
            notes="Bad gate dossier",
        )
        valid_pilot = HostilePilotDossier.objects.create(
            character_id=950010,
            character_name="Valid Pilot Eta",
            notes="Legitimate dossier",
        )

        deleted = zkill_client.cleanup_invalid_pilot_dossiers()
        self.assertGreaterEqual(deleted, 3)
        self.assertFalse(HostilePilotDossier.objects.filter(character_id=19720).exists())
        self.assertFalse(HostilePilotDossier.objects.filter(character_id=30000142).exists())
        self.assertFalse(HostilePilotDossier.objects.filter(character_id=40099999).exists())
        self.assertTrue(HostilePilotDossier.objects.filter(character_id=valid_pilot.character_id).exists())

    @patch.object(ZKillClient, "fetch_alliance_stats")
    def test_update_alliance_stats_ingests_pilots(self, mock_fetch):
        """Test that update_alliance_stats automatically ingests pilots on initial add and periodic sync"""
        mock_fetch.return_value = {
            "activepvp": {"characters": {"count": 200}, "kills": {"count": 800}, "losses": {"count": 100}},
            "supers": {
                "titan": {
                    "count": 1,
                    "data": [
                        {
                            "characterID": 950007,
                            "characterName": "Periodic Titan Commander",
                            "corporationName": "Sniggerdly",
                            "shipName": "Erebus",
                        }
                    ],
                }
            },
        }
        success = zkill_client.update_alliance_stats(self.alliance)
        self.assertTrue(success)

        pilot = HostilePilotDossier.objects.filter(character_id=950007).first()
        self.assertIsNotNone(pilot)
        self.assertTrue(pilot.is_titan_pilot)
        self.assertTrue(pilot.is_capital_pilot)

    @patch("hostile.services.zkill_client.esi_client.post_characters_affiliation")
    @patch("hostile.services.zkill_client.esi_client.post_universe_names")
    def test_ingest_pilots_with_affiliation_resolution(self, mock_names, mock_affiliations):
        """Test that character affiliations are bulk resolved to corporations and tactical roles/stats are recorded"""
        mock_affiliations.return_value = [
            {"character_id": 960001, "corporation_id": 98000001, "alliance_id": self.alliance.alliance_id},
            {"character_id": 960002, "corporation_id": 98000002, "alliance_id": 99000003},  # Out of corp / alt
        ]
        mock_names.return_value = [
            {"id": 98000001, "name": "Primary Combat Corp", "category": "corporation"},
            {"id": 98000002, "name": "Undercover Holding Corp", "category": "corporation"},
            {"id": 99000003, "name": "Alt Holding Alliance", "category": "alliance"},
        ]

        payload = {
            "topLists": [
                {
                    "type": "character",
                    "values": [
                        {
                            "id": 960001,
                            "name": "Top Pilot Alpha",
                            "kills": 84,
                            "losses": 5,
                        },
                        {
                            "id": 960002,
                            "name": "Alt Scout Beta",
                            "kills": 30,
                        },
                    ],
                }
            ]
        }

        count = zkill_client.ingest_pilots_from_zkill_data(payload, alliance=self.alliance)
        self.assertEqual(count, 2)

        p1 = HostilePilotDossier.objects.get(character_id=960001)
        self.assertEqual(p1.character_name, "Top Pilot Alpha")
        self.assertEqual(p1.corporation_name, "Primary Combat Corp")
        self.assertTrue(p1.is_fc)
        self.assertFalse(p1.is_out_of_corp)
        self.assertIn("Rank #1 on combat board", p1.notes)
        self.assertIn("84 kills", p1.notes)

        p2 = HostilePilotDossier.objects.get(character_id=960002)
        self.assertEqual(p2.character_name, "Alt Scout Beta")
        self.assertEqual(p2.corporation_name, "Undercover Holding Corp")
        self.assertTrue(p2.is_out_of_corp)
        self.assertEqual(p2.associated_alliance_name, self.alliance.alliance_name)
        self.assertIn("Operating in holding/alt corp", p2.notes)

    def test_parse_pilot_labels_all_behavioral_tags(self):
        """Test parsing all behavioral labels: AWOX, FC, BAIT, CYNO, GANKER, BLOPS, LOGI, CAPITAL, SUPER, TITAN, ROOKIE"""
        raw_labels = [
            "AWOX (12)",
            "ALLIANCE AWOX (18)",
            "FACTION AWOX (25)",
            "FC (Medium 65)",
            "BAIT (High 14)",
            "CYNO (3)",
            "GANKER (15)",
            "BLOPS (5)",
            "LOGI (16)",
            "CAPITAL (8)",
            "SUPER (3)",
            "TITAN (2)",
            "ROOKIE",
        ]
        parsed = ZKillClient.parse_pilot_labels(raw_labels)
        self.assertTrue(parsed["is_awox"])
        self.assertEqual(parsed["awox_count"], 12)
        self.assertTrue(parsed["is_alliance_awox"])
        self.assertEqual(parsed["alliance_awox_count"], 18)
        self.assertTrue(parsed["is_faction_awox"])
        self.assertEqual(parsed["faction_awox_count"], 25)
        self.assertTrue(parsed["is_fc"])
        self.assertEqual(parsed["fc_level"], "MEDIUM")
        self.assertEqual(parsed["fc_score"], 65)
        self.assertTrue(parsed["is_bait"])
        self.assertEqual(parsed["bait_level"], "HIGH")
        self.assertEqual(parsed["bait_count"], 14)
        self.assertTrue(parsed["is_cyno_alt"])
        self.assertEqual(parsed["cyno_count"], 3)
        self.assertTrue(parsed["is_ganker"])
        self.assertEqual(parsed["ganker_count"], 15)
        self.assertTrue(parsed["is_blops_pilot"])
        self.assertEqual(parsed["blops_count"], 5)
        self.assertTrue(parsed["is_logi_pilot"])
        self.assertEqual(parsed["logi_count"], 16)
        self.assertTrue(parsed["is_capital_pilot"])
        self.assertEqual(parsed["capital_count"], 8)
        self.assertTrue(parsed["is_super_pilot"])
        self.assertEqual(parsed["super_count"], 3)
        self.assertTrue(parsed["is_titan_pilot"])
        self.assertEqual(parsed["titan_count"], 2)
        self.assertTrue(parsed["is_rookie"])

    def test_pilot_dossier_display_properties(self):
        """Test formatting and display helper properties on HostilePilotDossier"""
        dossier = HostilePilotDossier.objects.create(
            character_id=970001,
            character_name="Tactical Lead Zeta",
            corporation_name="Sniggerdly",
            is_fc=True,
            fc_level="HIGH",
            fc_score=105,
            is_awox=True,
            awox_count=12,
            is_alliance_awox=True,
            alliance_awox_count=18,
            is_faction_awox=True,
            faction_awox_count=22,
            is_bait=True,
            bait_level="MEDIUM",
            bait_count=8,
            is_ganker=True,
            ganker_count=14,
            is_logi_pilot=True,
            logi_count=15,
            is_cyno_alt=True,
            cyno_count=4,
            is_blops_pilot=True,
            blops_count=6,
            is_capital_pilot=True,
            capital_count=10,
            is_super_pilot=True,
            super_count=4,
            is_titan_pilot=True,
            titan_count=2,
            is_rookie=True,
        )
        self.assertEqual(dossier.fc_display, "FC (High 105)")
        self.assertEqual(dossier.awox_display, "AWOX (12)")
        self.assertEqual(dossier.alliance_awox_display, "Alliance AWOX (18)")
        self.assertEqual(dossier.faction_awox_display, "Faction AWOX (22)")
        self.assertEqual(dossier.bait_display, "Bait (Medium 8)")
        self.assertEqual(dossier.ganker_display, "Ganker (14)")
        self.assertEqual(dossier.logi_display, "Logi (15)")
        self.assertEqual(dossier.cyno_display, "Cyno (4)")
        self.assertEqual(dossier.blops_display, "Blops (6)")
        self.assertEqual(dossier.capital_display, "Capital (10)")
        self.assertEqual(dossier.super_display, "Super (4)")
        self.assertEqual(dossier.titan_display, "Titan (2)")

    @patch.object(ZKillClient, "fetch_character_stats")
    def test_sync_pilot_dossier_zkill(self, mock_char_stats):
        """Test fetching character stats and updating pilot dossier with zKillboard tags"""
        mock_char_stats.return_value = {
            "labels": [
                "FC (High 110)",
                "AWOX (15)",
                "BAIT (Low 5)",
                "GANKER (22)",
                "LOGI (18)",
            ],
            "shipsDestroyed": 100,
            "shipsLost": 10,
        }
        pilot = HostilePilotDossier.objects.create(
            character_id=980011,
            character_name="FC Pilot Bravo",
            corporation_name="Sniggerdly",
        )
        updated = zkill_client.sync_pilot_dossier_zkill(pilot)
        self.assertTrue(updated)
        pilot.refresh_from_db()
        self.assertTrue(pilot.is_fc)
        self.assertEqual(pilot.fc_level, "HIGH")
        self.assertEqual(pilot.fc_score, 110)
        self.assertTrue(pilot.is_awox)
        self.assertEqual(pilot.awox_count, 15)
        self.assertTrue(pilot.is_bait)
        self.assertEqual(pilot.bait_level, "LOW")
        self.assertEqual(pilot.bait_count, 5)
        self.assertTrue(pilot.is_ganker)
        self.assertEqual(pilot.ganker_count, 22)
        self.assertTrue(pilot.is_logi_pilot)
        self.assertEqual(pilot.logi_count, 18)

    @patch.object(ZKillClient, "fetch_character_stats")
    def test_sync_pilot_dossier_zkill_kixkahn_profile(self, mock_char_stats):
        """Test syncing a pilot with FC (LOW), BAIT LOW (4), and BLOPS (4) tags, danger meter, sec status and tickers"""
        from datetime import date

        mock_char_stats.return_value = {
            "dangerRatio": 67,
            "gangRatio": 80,
            "shipsDestroyed": 250,
            "shipsLost": 120,
            "labels": [
                "FC (LOW)",
                "BAIT LOW (4)",
                "BLOPS (4)",
            ],
            "info": {
                "sec_status": -0.30,
                "birthday": "2004-03-20 00:00:00",
            },
            "topLists": [
                {
                    "type": "ships",
                    "values": [
                        {"name": "Redeemer"},
                        {"name": "Panther"},
                        {"name": "Sin"},
                    ],
                }
            ],
        }
        pilot = HostilePilotDossier.objects.create(
            character_id=980099,
            character_name="Kixkahn",
            corporation_name="Southern Cross Monopoly",
            corporation_ticker="STHCM",
            alliance_name="Flying Dangerous",
            alliance_ticker="FIGL",
        )
        updated = zkill_client.sync_pilot_dossier_zkill(pilot)
        self.assertTrue(updated)
        pilot.refresh_from_db()

        self.assertTrue(pilot.is_fc)
        self.assertEqual(pilot.fc_level, "LOW")
        self.assertTrue(pilot.is_bait)
        self.assertEqual(pilot.bait_level, "LOW")
        self.assertEqual(pilot.bait_count, 4)
        self.assertTrue(pilot.is_blops_pilot)
        self.assertEqual(pilot.blops_count, 4)

        self.assertEqual(pilot.danger_ratio, 67)
        self.assertEqual(pilot.gang_ratio, 80)
        self.assertEqual(pilot.danger_boxes, [True, True, False])
        self.assertEqual(pilot.danger_level_class, "danger")
        self.assertEqual(pilot.formatted_sec_status, "-0.30")
        self.assertEqual(pilot.birthday, date(2004, 3, 20))
        self.assertGreaterEqual(pilot.age_years, 20)
        self.assertIn("Redeemer", pilot.top_ships)
        self.assertEqual(pilot.likely_ship, "Redeemer")

        self.assertEqual(pilot.fc_display, "FC (Low)")
        self.assertEqual(pilot.bait_display, "Bait (Low 4)")
        self.assertEqual(pilot.blops_display, "Blops (4)")

    def test_pilot_list_behavioral_filters(self):
        """Test filtering pilots by tactical role and behavioral tags"""
        user = AuthUtils.create_user("pilot_tester")
        AuthUtils.add_main_character(user, "Pilot Tester Char", 21100088)
        AuthUtils.add_permission_to_user_by_name("hostile.basic_access", user)
        client = Client()
        client.force_login(user)

        HostilePilotDossier.objects.create(character_id=9901, character_name="Pilot Awox", is_awox=True)
        HostilePilotDossier.objects.create(character_id=9902, character_name="Pilot Bait", is_bait=True)
        HostilePilotDossier.objects.create(character_id=9903, character_name="Pilot Ganker", is_ganker=True)
        HostilePilotDossier.objects.create(character_id=9904, character_name="Pilot Logi", is_logi_pilot=True)
        HostilePilotDossier.objects.create(character_id=9905, character_name="Pilot Rookie", is_rookie=True)

        # Test AWOX filter
        res_awox = client.get(reverse("hostile:pilots") + "?role=awox")
        self.assertEqual(res_awox.status_code, 200)
        self.assertContains(res_awox, "Pilot Awox")
        self.assertNotContains(res_awox, '<h5 class="card-title mb-0 fw-bold text-light">Pilot Ganker</h5>')

        # Test Bait filter
        res_bait = client.get(reverse("hostile:pilots") + "?role=bait")
        self.assertEqual(res_bait.status_code, 200)
        self.assertContains(res_bait, "Pilot Bait")
        self.assertNotContains(res_bait, '<h5 class="card-title mb-0 fw-bold text-light">Pilot Awox</h5>')

        # Test Ganker filter
        res_ganker = client.get(reverse("hostile:pilots") + "?role=gankers")
        self.assertEqual(res_ganker.status_code, 200)
        self.assertContains(res_ganker, "Pilot Ganker")
        self.assertNotContains(res_ganker, '<h5 class="card-title mb-0 fw-bold text-light">Pilot Bait</h5>')

        # Test Logi filter
        res_logi = client.get(reverse("hostile:pilots") + "?role=logi")
        self.assertEqual(res_logi.status_code, 200)
        self.assertContains(res_logi, "Pilot Logi")
        self.assertNotContains(res_logi, '<h5 class="card-title mb-0 fw-bold text-light">Pilot Rookie</h5>')

        # Test Rookie filter
        res_rookie = client.get(reverse("hostile:pilots") + "?role=rookies")
        self.assertEqual(res_rookie.status_code, 200)
        self.assertContains(res_rookie, "Pilot Rookie")
        self.assertNotContains(res_rookie, '<h5 class="card-title mb-0 fw-bold text-light">Pilot Logi</h5>')

    def test_alt_detection_engine(self):
        """Test alt detection patterns, scoring, and automated correlation"""
        from hostile.services.intel_manager import AltDetectionEngine, IntelManager

        # Main character (FC and Titan pilot)
        main_p = HostilePilotDossier.objects.create(
            character_id=991001,
            character_name="Valkyrie Leader",
            corporation_name="Sniggerdly",
            alliance_name="Northern Coalition.",
            is_fc=True,
            fc_level="HIGH",
            fc_score=120,
            is_titan_pilot=True,
        )

        # Alt 1: Suffix pattern ("Valkyrie Leader Cyno")
        alt_cyno = HostilePilotDossier.objects.create(
            character_id=991002,
            character_name="Valkyrie Leader Cyno",
            corporation_name="Sniggerdly",
            alliance_name="Northern Coalition.",
            is_cyno_alt=True,
        )

        # Alt 2: Suffix pattern ("Valkyrie Leader II")
        alt_two = HostilePilotDossier.objects.create(
            character_id=991003,
            character_name="Valkyrie Leader II",
            corporation_name="Sniggerdly",
            alliance_name="Northern Coalition.",
            is_dread_pilot=True,
        )

        # Alt 3: Out-of-corp scout ("Valkyrie Leader Scout")
        alt_scout = HostilePilotDossier.objects.create(
            character_id=991004,
            character_name="Valkyrie Leader Scout",
            corporation_name="Center for Advanced Studies",
            is_out_of_corp=True,
            associated_alliance_name="Northern Coalition.",
        )

        # Unrelated character
        unrelated = HostilePilotDossier.objects.create(
            character_id=991005,
            character_name="Random Lone Wolf",
            corporation_name="Sniggerdly",
        )

        linked = IntelManager.run_alt_detection()
        self.assertEqual(linked, 3)

        alt_cyno.refresh_from_db()
        alt_two.refresh_from_db()
        alt_scout.refresh_from_db()
        main_p.refresh_from_db()
        unrelated.refresh_from_db()

        self.assertTrue(alt_cyno.is_alt)
        self.assertEqual(alt_cyno.main_character_id, main_p.id)
        self.assertEqual(alt_cyno.alt_confidence, "HIGH")

        self.assertTrue(alt_two.is_alt)
        self.assertEqual(alt_two.main_character_id, main_p.id)

        self.assertTrue(alt_scout.is_alt)
        self.assertEqual(alt_scout.main_character_id, main_p.id)

        self.assertFalse(main_p.is_alt)
        self.assertTrue(main_p.is_main)
        self.assertEqual(main_p.known_alts.count(), 3)
        self.assertIn("Main (3 Alts)", main_p.alt_display)

        self.assertFalse(unrelated.is_alt)
        self.assertFalse(unrelated.is_main)

    def test_alt_detection_transitive_and_scoring(self):
        """Test transitive alt correlation chains (A -> B and B -> C) resolve to common main"""
        from hostile.services.intel_manager import AltDetectionEngine, IntelManager

        # Main combat pilot
        p_main = HostilePilotDossier.objects.create(
            character_id=992001,
            character_name="Apex Hunter",
            is_fc=True,
        )
        # Intermediary named alt
        p_dread = HostilePilotDossier.objects.create(
            character_id=992002,
            character_name="Apex Hunter Dread",
            is_dread_pilot=True,
        )
        # Secondary alt named from dread
        p_cyno = HostilePilotDossier.objects.create(
            character_id=992003,
            character_name="Apex Hunter Dread Cyno",
            is_cyno_alt=True,
        )

        IntelManager.run_alt_detection()

        p_main.refresh_from_db()
        p_dread.refresh_from_db()
        p_cyno.refresh_from_db()

        self.assertEqual(p_dread.main_character_id, p_main.id)
        self.assertEqual(p_cyno.main_character_id, p_main.id)
        self.assertTrue(p_dread.is_alt)
        self.assertTrue(p_cyno.is_alt)
        self.assertFalse(p_main.is_alt)

    def test_pilot_detect_alts_view(self):
        """Test GET /pilots/detect-alts/ triggering background scan"""
        user = AuthUtils.create_user("alt_tester_user")
        AuthUtils.add_main_character(user, "Alt Tester Char Unique", 21100999)
        AuthUtils.add_permission_to_user_by_name("hostile.basic_access", user)
        client = Client()
        client.force_login(user)

        HostilePilotDossier.objects.create(
            character_id=993001, character_name="Titan Boss", is_titan_pilot=True
        )
        HostilePilotDossier.objects.create(
            character_id=993002, character_name="Titan Boss Cyno", is_cyno_alt=True
        )

        res = client.get(reverse("hostile:pilot_detect_alts"))
        self.assertEqual(res.status_code, 302)

        p_alt = HostilePilotDossier.objects.get(character_id=993002)
        self.assertTrue(p_alt.is_alt)

        # Check pilots filter for alts and mains
        res_alts = client.get(reverse("hostile:pilots") + "?role=alts")
        self.assertContains(res_alts, "Titan Boss Cyno")
        self.assertNotContains(res_alts, '<h5 class="card-title mb-0 fw-bold text-light">Titan Boss</h5>')

        res_mains = client.get(reverse("hostile:pilots") + "?role=mains")
        self.assertContains(res_mains, "Titan Boss")

    @patch("hostile.services.map_routing.esi_client.get_route")
    def test_map_routing_service_and_distance_summary(self, mock_get_route):
        """Test MapRoutingService 3D light year distance calculation and stargate jump routing"""
        from django.core.cache import cache
        from hostile.services.map_routing import MapRoutingService

        cache.clear()
        mock_get_route.return_value = [30000142, 30000144, 30002187]

        origin_sys, _ = SolarSystem.objects.get_or_create(
            id=30000142,
            defaults={"name": "Jita", "x": 1e16, "y": 2e16, "z": 3e16},
        )
        origin_sys.x = 10000000000000000.0
        origin_sys.y = 20000000000000000.0
        origin_sys.z = 30000000000000000.0
        origin_sys.save()

        dest_sys, _ = SolarSystem.objects.get_or_create(
            id=30002187,
            defaults={"name": "Amamake", "x": 1e16, "y": 2e16, "z": 3e16},
        )
        dest_sys.x = 10000000000000000.0 + 9.4607304725808e16  # 10 Light Years delta along X axis
        dest_sys.y = 20000000000000000.0
        dest_sys.z = 30000000000000000.0
        dest_sys.save()

        dist = MapRoutingService.calculate_ly_distance(origin_sys, dest_sys)
        self.assertEqual(dist, 10.0)

        # Stargate jumps calculation
        jumps = MapRoutingService.calculate_stargate_jumps(origin_sys, dest_sys, flag="shortest")
        self.assertEqual(jumps, 2)

        # Same system distance
        same_dist = MapRoutingService.calculate_ly_distance(origin_sys, origin_sys)
        self.assertEqual(same_dist, 0.0)
        same_jumps = MapRoutingService.calculate_stargate_jumps(origin_sys, origin_sys)
        self.assertEqual(same_jumps, 0)

        summary = MapRoutingService.get_system_distance_summary(origin_sys, dest_sys, flag="shortest")
        self.assertEqual(summary["ly"], 10.0)
        self.assertEqual(summary["jumps"], 2)
        self.assertEqual(summary["display"], "10.00 LY · 2 Jumps")

        # Batch summary
        batch = MapRoutingService.get_batch_routes_summary(origin_sys.id, [dest_sys.id], flag="shortest")
        self.assertIn(dest_sys.id, batch)
        self.assertEqual(batch[dest_sys.id]["jumps"], 2)

        # Null handling
        null_dist = MapRoutingService.calculate_ly_distance(None, dest_sys)
        self.assertIsNone(null_dist)
        null_jumps = MapRoutingService.calculate_stargate_jumps(None, dest_sys)
        self.assertIsNone(null_jumps)

    @patch("hostile.services.map_routing.esi_client.get_route")
    def test_sov_timers_distance_calculation_with_origin(self, mock_get_route):
        """Test Sov Timers tab attached distance calculation when origin is selected"""
        from django.core.cache import cache

        cache.clear()
        mock_get_route.return_value = [30004759, 30004760]

        user = AuthUtils.create_user("sov_distance_tester_user")
        AuthUtils.add_main_character(user, "Distance Tester Unique", 21100998)
        AuthUtils.add_permission_to_user_by_name("hostile.basic_access", user)
        client = Client()
        client.force_login(user)

        origin_sys, _ = SolarSystem.objects.get_or_create(
            id=30004759,
            defaults={"name": "1DQ1-A", "x": 1e16, "y": 1e16, "z": 1e16},
        )
        origin_sys.x = 1e16
        origin_sys.y = 1e16
        origin_sys.z = 1e16
        origin_sys.save()

        target_sys, _ = SolarSystem.objects.get_or_create(
            id=30004760,
            defaults={"name": "T-J6C4", "x": 1e16, "y": 1e16, "z": 1e16},
        )
        target_sys.x = 1e16 + 4.73036523629e16  # ~5.0 LY
        target_sys.y = 1e16
        target_sys.z = 1e16
        target_sys.save()

        now = timezone.now()
        StructureTimer.objects.create(
            solar_system=target_sys,
            timer_type="SOV_IHUB",
            timer_datetime=now + timedelta(hours=2),
            is_verified=True,
            is_sov_campaign=True,
            defender_id=99000099,
            defender_name="Hostile Alliance",
            defender_score=0.4,
        )

        res = client.get(reverse("hostile:sov_timers") + f"?origin={origin_sys.id}")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "1DQ1-A")
        self.assertContains(res, "5.00 LY")
        self.assertContains(res, "1 Jumps")

    @patch("hostile.services.map_routing.esi_client.get_route")
    def test_sov_timers_sorting_by_ly_region_and_jumps(self, mock_get_route):
        """Test Sov Timers sorting by LY, Region, and Jumps from origin"""
        from django.core.cache import cache
        from eve_sde.models import Constellation, Region

        cache.clear()

        user = AuthUtils.create_user("sov_sort_tester_user")
        AuthUtils.add_main_character(user, "Sort Tester User", 21100996)
        AuthUtils.add_permission_to_user_by_name("hostile.basic_access", user)
        client = Client()
        client.force_login(user)

        reg_alpha, _ = Region.objects.get_or_create(id=10000001, defaults={"name": "Alpha Region"})
        reg_zulu, _ = Region.objects.get_or_create(id=10000002, defaults={"name": "Zulu Region"})

        con_alpha, _ = Constellation.objects.get_or_create(id=20000001, defaults={"name": "Alpha Constellation", "region": reg_alpha})
        con_zulu, _ = Constellation.objects.get_or_create(id=20000002, defaults={"name": "Zulu Constellation", "region": reg_zulu})

        origin_sys, _ = SolarSystem.objects.get_or_create(
            id=30005000,
            defaults={"name": "Origin-0", "x": 0.0, "y": 0.0, "z": 0.0, "constellation": con_alpha},
        )
        origin_sys.x = 0.0
        origin_sys.y = 0.0
        origin_sys.z = 0.0
        origin_sys.save()

        # Far system in Alpha Region (20 LY, 5 Jumps)
        far_sys, _ = SolarSystem.objects.get_or_create(
            id=30005001,
            defaults={"name": "Far-Sys", "x": 1.892e17, "y": 0.0, "z": 0.0, "constellation": con_alpha},
        )
        far_sys.x = 1.892e17
        far_sys.y = 0.0
        far_sys.z = 0.0
        far_sys.save()

        # Near system in Zulu Region (2 LY, 15 Jumps via winding gates)
        near_sys, _ = SolarSystem.objects.get_or_create(
            id=30005002,
            defaults={"name": "Near-Sys", "x": 1.892e16, "y": 0.0, "z": 0.0, "constellation": con_zulu},
        )
        near_sys.x = 1.892e16
        near_sys.y = 0.0
        near_sys.z = 0.0
        near_sys.save()

        now = timezone.now()
        timer_far = StructureTimer.objects.create(
            solar_system=far_sys,
            timer_type="SOV_IHUB",
            timer_datetime=now + timedelta(hours=3),
            is_verified=True,
            is_sov_campaign=True,
            defender_id=99000001,
            defender_name="Alpha Defender",
            defender_score=0.7,
        )
        timer_near = StructureTimer.objects.create(
            solar_system=near_sys,
            timer_type="SOV_TCU",
            timer_datetime=now + timedelta(hours=1),
            is_verified=True,
            is_sov_campaign=True,
            defender_id=99000002,
            defender_name="Zulu Defender",
            defender_score=0.3,
        )

        def mock_route_fn(*args, **kwargs):
            dest = kwargs.get("destination_system_id") or (args[1] if len(args) > 1 else None)
            orig = kwargs.get("origin_system_id") or (args[0] if len(args) > 0 else None)
            if dest and int(dest) == 30005001:
                return [30005000, 1, 2, 3, 4, 30005001]  # 5 jumps
            elif dest and int(dest) == 30005002:
                return [30005000] + list(range(100, 114)) + [30005002]  # 15 jumps
            return [int(orig), int(dest)] if orig and dest else []

        mock_get_route.side_effect = mock_route_fn

        # 1. Sort by Region (asc vs desc)
        res_reg_asc = client.get(reverse("hostile:sov_timers") + "?sort=region&order=asc")
        self.assertEqual(res_reg_asc.status_code, 200)
        self.assertContains(res_reg_asc, "Alpha Region")
        self.assertContains(res_reg_asc, "Zulu Region")
        upcoming_reg_asc = res_reg_asc.context["upcoming_timers"]
        self.assertEqual(upcoming_reg_asc[0].id, timer_far.id)  # Alpha before Zulu
        self.assertEqual(upcoming_reg_asc[1].id, timer_near.id)

        res_reg_desc = client.get(reverse("hostile:sov_timers") + "?sort=region&order=desc")
        self.assertEqual(res_reg_desc.status_code, 200)
        upcoming_reg_desc = res_reg_desc.context["upcoming_timers"]
        self.assertEqual(upcoming_reg_desc[0].id, timer_near.id)  # Zulu before Alpha
        self.assertEqual(upcoming_reg_desc[1].id, timer_far.id)

        # 2. Sort by LY with origin (asc vs desc)
        cache.clear()
        res_ly_asc = client.get(reverse("hostile:sov_timers") + f"?origin={origin_sys.id}&sort=ly&order=asc")
        self.assertEqual(res_ly_asc.status_code, 200)
        upcoming_ly_asc = res_ly_asc.context["upcoming_timers"]
        self.assertEqual(upcoming_ly_asc[0].id, timer_near.id)  # ~2.0 LY is closer than ~20.0 LY
        self.assertEqual(upcoming_ly_asc[1].id, timer_far.id)

        res_ly_desc = client.get(reverse("hostile:sov_timers") + f"?origin={origin_sys.id}&sort=ly&order=desc")
        self.assertEqual(res_ly_desc.status_code, 200)
        upcoming_ly_desc = res_ly_desc.context["upcoming_timers"]
        self.assertEqual(upcoming_ly_desc[0].id, timer_far.id)  # ~20.0 LY is further than ~2.0 LY
        self.assertEqual(upcoming_ly_desc[1].id, timer_near.id)

        # 3. Sort by Jumps from Origin (asc vs desc)
        cache.clear()
        res_jumps_asc = client.get(reverse("hostile:sov_timers") + f"?origin={origin_sys.id}&sort=jumps&order=asc")
        self.assertEqual(res_jumps_asc.status_code, 200)
        upcoming_jumps_asc = res_jumps_asc.context["upcoming_timers"]
        self.assertEqual(upcoming_jumps_asc[0].id, timer_far.id)  # 5 jumps < 15 jumps
        self.assertEqual(upcoming_jumps_asc[1].id, timer_near.id)

        res_jumps_desc = client.get(reverse("hostile:sov_timers") + f"?origin={origin_sys.id}&sort=jumps&order=desc")
        self.assertEqual(res_jumps_desc.status_code, 200)
        upcoming_jumps_desc = res_jumps_desc.context["upcoming_timers"]
        self.assertEqual(upcoming_jumps_desc[0].id, timer_near.id)  # 15 jumps > 5 jumps
        self.assertEqual(upcoming_jumps_desc[1].id, timer_far.id)

        # 4. Check UI elements: sort dropdowns and sortable headers
        self.assertContains(res_ly_asc, 'id="activeTableSort"')
        self.assertContains(res_ly_asc, 'data-sort-key="ly"')
        self.assertContains(res_ly_asc, 'data-sort-key="region"')
        self.assertContains(res_ly_asc, 'data-sort-key="jumps"')

    @patch("hostile.services.map_routing.esi_client.get_route")
    def test_api_routes_batch_endpoint(self, mock_get_route):
        """Test /api/routes/ batch calculation endpoint"""
        from django.core.cache import cache

        cache.clear()
        mock_get_route.return_value = [30000142, 30000144, 30002187]

        SolarSystem.objects.get_or_create(
            id=30000142,
            defaults={"name": "Jita", "x": 1e16, "y": 2e16, "z": 3e16},
        )
        SolarSystem.objects.get_or_create(
            id=30002187,
            defaults={"name": "Amamake", "x": 1e16, "y": 2e16, "z": 3e16},
        )

        user = AuthUtils.create_user("api_routes_tester_user")
        AuthUtils.add_main_character(user, "API Routes Tester", 21100997)
        AuthUtils.add_permission_to_user_by_name("hostile.basic_access", user)
        client = Client()
        client.force_login(user)

        res = client.get(reverse("hostile:api_routes_batch") + "?origin=30000142&destinations=30002187&flag=shortest")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["origin_id"], 30000142)
        self.assertIn("routes", data)
        self.assertIn("30002187", data["routes"])
        self.assertEqual(data["routes"]["30002187"]["jumps"], 2)

    def test_esi_client_get_route_direct(self):
        """Test HostileESIClientProvider get_route with PostRoute and dict unwrapping"""
        from hostile.services.esi_client import esi_client

        with patch.object(esi_client, "fetch_safely") as mock_fetch:
            # Test unwrapping [{'route': [30000142, 30002187]}]
            mock_fetch.return_value = [{"route": [30000142, 30002187]}]
            route = esi_client.get_route(30000142, 30002187, flag="secure")
            self.assertEqual(route, [30000142, 30002187])

            # Test flat list format
            mock_fetch.return_value = [30000142, 30002187]
            route_flat = esi_client.get_route(30000142, 30002187, flag="shortest")
            self.assertEqual(route_flat, [30000142, 30002187])

            # Test same system
            route_same = esi_client.get_route(30000142, 30000142)
            self.assertEqual(route_same, [30000142])
