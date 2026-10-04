"""
Unit tests for EntityResolver, Name/ID character & alliance lookups, out-of-corp alts, and SolarSystemChoiceField.
"""

from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.urls import reverse
from allianceauth.tests.auth_utils import AuthUtils

from allianceauth.eveonline.models import EveAllianceInfo, EveCharacter, EveCorporationInfo
from eve_sde.models import SolarSystem

from hostile.forms import HostileAllianceForm, HostilePilotDossierForm, SolarSystemChoiceField
from hostile.models import HostileAlliance, HostilePilotDossier
from hostile.services.entity_resolver import EntityResolver


class EntityResolverTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = AuthUtils.create_user(username="intel_agent")
        AuthUtils.add_main_character(cls.user, "Agent Main", 21100099)
        AuthUtils.add_permission_to_user_by_name("hostile.basic_access", cls.user)
        AuthUtils.add_permission_to_user_by_name("hostile.manage_intel", cls.user)

        cls.system = SolarSystem.objects.create(
            id=30000142,
            name="Jita",
            security_status=0.9,
        )

        # Create internal Alliance Auth models
        cls.eve_char = EveCharacter.objects.create(
            character_id=90001,
            character_name="Alice Hostile",
            corporation_id=20001,
            corporation_name="Bad Guys Corp",
            corporation_ticker="BGC",
            alliance_id=30001,
            alliance_name="Hostile Coalition",
            alliance_ticker="BAD",
        )

        cls.eve_alliance = EveAllianceInfo.objects.create(
            alliance_id=30002,
            alliance_name="Northern Legion",
            alliance_ticker="NORTH",
            executor_corp_id=20002,
        )

        cls.hostile_alliance = HostileAlliance.objects.create(
            alliance_id=30003,
            alliance_name="Southern Swarm",
            ticker="SOUTH",
        )

        cls.dossier = HostilePilotDossier.objects.create(
            character_id=90002,
            character_name="Bob Undercover",
            corporation_name="Neutral Corp",
            alliance_name="",
            associated_alliance_name="Southern Swarm",
            is_out_of_corp=True,
            is_cyno_alt=True,
        )

    def test_resolve_character_from_evecharacter_by_name(self):
        res = EntityResolver.resolve_character("Alice Hostile")
        self.assertIsNotNone(res)
        self.assertEqual(res["character_id"], 90001)
        self.assertEqual(res["corporation_name"], "Bad Guys Corp")
        self.assertEqual(res["alliance_name"], "Hostile Coalition")
        self.assertEqual(res["source"], "internal_evecharacter")

    def test_resolve_character_from_evecharacter_by_id(self):
        res = EntityResolver.resolve_character(90001)
        self.assertIsNotNone(res)
        self.assertEqual(res["character_name"], "Alice Hostile")
        self.assertEqual(res["alliance_name"], "Hostile Coalition")

    def test_resolve_character_from_dossier_by_name(self):
        res = EntityResolver.resolve_character("Bob Undercover")
        self.assertIsNotNone(res)
        self.assertEqual(res["character_id"], 90002)
        self.assertEqual(res["associated_alliance_name"], "Southern Swarm")
        self.assertTrue(res["is_out_of_corp"])

    @patch("hostile.services.entity_resolver.esi_client.post_universe_ids")
    @patch("hostile.services.entity_resolver.esi_client.post_characters_affiliation")
    @patch("hostile.services.entity_resolver.esi_client.post_universe_names")
    def test_resolve_character_esi_fallback(self, mock_names, mock_affil, mock_ids):
        mock_ids.return_value = {"characters": [{"id": 99999, "name": "External Pilot"}]}
        mock_affil.return_value = [{"character_id": 99999, "corporation_id": 20099, "alliance_id": 30099}]
        mock_names.return_value = [
            {"id": 99999, "name": "External Pilot"},
            {"id": 20099, "name": "External Corp"},
            {"id": 30099, "name": "External Alliance"},
        ]

        res = EntityResolver.resolve_character("External Pilot")
        self.assertIsNotNone(res)
        self.assertEqual(res["character_id"], 99999)
        self.assertEqual(res["character_name"], "External Pilot")
        self.assertEqual(res["corporation_name"], "External Corp")
        self.assertEqual(res["alliance_name"], "External Alliance")
        self.assertEqual(res["source"], "esi")

    def test_resolve_alliance_from_hostile_alliance(self):
        res = EntityResolver.resolve_alliance("Southern Swarm")
        self.assertIsNotNone(res)
        self.assertEqual(res["alliance_id"], 30003)
        self.assertEqual(res["ticker"], "SOUTH")

    def test_resolve_alliance_from_eve_alliance(self):
        res = EntityResolver.resolve_alliance(30002)
        self.assertIsNotNone(res)
        self.assertEqual(res["alliance_name"], "Northern Legion")
        self.assertEqual(res["ticker"], "NORTH")

    def test_search_characters(self):
        results = EntityResolver.search_characters("Alice")
        self.assertTrue(len(results) >= 1)
        self.assertEqual(results[0]["character_name"], "Alice Hostile")

    def test_search_alliances(self):
        results = EntityResolver.search_alliances("Southern")
        self.assertTrue(len(results) >= 1)
        self.assertEqual(results[0]["alliance_name"], "Southern Swarm")


class FormEntityResolutionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.system = SolarSystem.objects.create(
            id=30000142,
            name="Jita",
            security_status=0.9,
        )

        cls.eve_char = EveCharacter.objects.create(
            character_id=90001,
            character_name="Alice Hostile",
            corporation_id=20001,
            corporation_name="Bad Guys Corp",
            alliance_id=30001,
            alliance_name="Hostile Coalition",
        )

        cls.eve_alliance = EveAllianceInfo.objects.create(
            alliance_id=30002,
            alliance_name="Northern Legion",
            alliance_ticker="NORTH",
            executor_corp_id=20002,
        )

    def test_alliance_form_name_only_resolution(self):
        form = HostileAllianceForm(data={"alliance_name": "Northern Legion"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["alliance_id"], 30002)
        self.assertEqual(form.cleaned_data["ticker"], "NORTH")

    def test_alliance_form_id_only_resolution(self):
        form = HostileAllianceForm(data={"alliance_id": 30002})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["alliance_name"], "Northern Legion")
        self.assertEqual(form.cleaned_data["ticker"], "NORTH")

    def test_alliance_form_empty_error(self):
        form = HostileAllianceForm(data={})
        self.assertFalse(form.is_valid())
        self.assertIn("Please provide an Alliance Name OR Alliance ID.", str(form.errors))

    def test_pilot_form_name_only_resolution_and_autofill(self):
        form = HostilePilotDossierForm(
            data={
                "character_name": "Alice Hostile",
                "associated_alliance_name": "Hostile Coalition",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["character_id"], 90001)
        self.assertEqual(form.cleaned_data["corporation_name"], "Bad Guys Corp")
        self.assertEqual(form.cleaned_data["alliance_name"], "Hostile Coalition")
        self.assertTrue(form.cleaned_data["is_out_of_corp"])

    def test_pilot_form_id_only_resolution(self):
        form = HostilePilotDossierForm(data={"character_id": 90001})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["character_name"], "Alice Hostile")
        self.assertEqual(form.cleaned_data["corporation_name"], "Bad Guys Corp")

    def test_solar_system_choice_field_name_resolution(self):
        field = SolarSystemChoiceField()
        cleaned = field.to_python("Jita")
        self.assertEqual(cleaned, self.system)


class EntityAPIEndpointTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = AuthUtils.create_user(username="agent")
        AuthUtils.add_main_character(cls.user, "Agent Char", 21100098)
        AuthUtils.add_permission_to_user_by_name("hostile.basic_access", cls.user)
        AuthUtils.add_permission_to_user_by_name("hostile.manage_intel", cls.user)
        EveCharacter.objects.create(
            character_id=90001,
            character_name="Alice Hostile",
            corporation_id=20001,
            corporation_name="Bad Guys Corp",
            alliance_id=30001,
            alliance_name="Hostile Coalition",
        )
        HostileAlliance.objects.create(
            alliance_id=30003,
            alliance_name="Southern Swarm",
            ticker="SOUTH",
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_api_character_search(self):
        url = reverse("hostile:api_character_search") + "?q=Alice"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(len(data["results"]) >= 1)
        self.assertEqual(data["results"][0]["character_name"], "Alice Hostile")

    def test_api_character_resolve(self):
        url = reverse("hostile:api_character_resolve") + "?query=Alice Hostile"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["character"]["character_id"], 90001)
        self.assertEqual(data["character"]["corporation_name"], "Bad Guys Corp")

    def test_api_alliance_search(self):
        url = reverse("hostile:api_alliance_search") + "?q=Southern"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(len(data["results"]) >= 1)
        self.assertEqual(data["results"][0]["alliance_name"], "Southern Swarm")

    def test_api_alliance_resolve(self):
        url = reverse("hostile:api_alliance_resolve") + "?query=Southern Swarm"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["alliance"]["alliance_id"], 30003)
        self.assertEqual(data["alliance"]["ticker"], "SOUTH")
