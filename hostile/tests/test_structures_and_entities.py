"""
Tests for Hostile Structure workflows, fast SDE structure types, untracked entities,
vulnerability hour auto-fill, anchoring core status, automatic timer generation,
and coalition/alt affiliations for alliances and corporations.
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
from hostile.forms import HostileAllianceForm, HostileCorporationForm, HostileStructureForm
from hostile.models import (
    HostileAlliance,
    HostileCorporation,
    HostilePilotDossier,
    HostileStagingSystem,
    HostileStructure,
    StructureTimer,
)
from hostile.services.entity_resolver import EntityResolver


class TestStructuresAndEntities(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category_structure = ItemCategory.objects.create(id=65, name="Structure", published=True)
        cls.group_citadel = ItemGroup.objects.create(id=1657, name="Citadel", category=cls.category_structure, published=True)
        cls.group_refinery = ItemGroup.objects.create(id=1406, name="Refinery", category=cls.category_structure, published=True)

        cls.astrahus = ItemType.objects.create(id=35832, name="Astrahus", group=cls.group_citadel, published=True)
        cls.athanor = ItemType.objects.create(id=35835, name="Athanor", group=cls.group_refinery, published=True)

        cls.system = SolarSystem.objects.create(id=30004759, name="1DQ1-A", security_status=-0.5)
        cls.alliance = HostileAlliance.objects.create(
            alliance_id=99000099, alliance_name="Hostile Alliance", ticker="H-ALLI"
        )

        cls.user_officer = AuthUtils.create_user(username="officer_user")
        AuthUtils.add_main_character(cls.user_officer, "Officer Pilot", 21100099)
        AuthUtils.add_permission_to_user_by_name("hostile.basic_access", cls.user_officer)
        AuthUtils.add_permission_to_user_by_name("hostile.manage_intel", cls.user_officer)

        cls.client = Client()

    def setUp(self):
        self.client.force_login(self.user_officer)

    def test_structure_form_vulnerability_hour_calculation(self):
        """Vulnerability hour should auto-compute +- 3 hour window"""
        form = HostileStructureForm(
            data={
                "name": "Test Astrahus",
                "structure_type": self.astrahus.id,
                "solar_system": self.system.id,
                "state": "ONLINE",
                "core_status": "FITTED",
                "vulnerability_hour": 18,
                "vulnerability_window": "",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["vulnerability_window"], "15:00 - 21:00 UTC")

        # Test midnight wrap-around (e.g. 1 UTC -> 22:00 - 04:00 UTC)
        form_wrap = HostileStructureForm(
            data={
                "name": "Wrap Astrahus",
                "structure_type": self.astrahus.id,
                "solar_system": self.system.id,
                "state": "ONLINE",
                "core_status": "FITTED",
                "vulnerability_hour": 1,
                "vulnerability_window": "",
            }
        )
        self.assertTrue(form_wrap.is_valid(), form_wrap.errors)
        self.assertEqual(form_wrap.cleaned_data["vulnerability_window"], "22:00 - 04:00 UTC")

    def test_structure_form_anchoring_core_status(self):
        """Anchoring structures must always have core status set to UNFITTED (No Core)"""
        form = HostileStructureForm(
            data={
                "name": "Anchoring Fortizar",
                "structure_type": self.astrahus.id,
                "solar_system": self.system.id,
                "state": "ANCHORING",
                "core_status": "FITTED",  # User selected fitted by mistake
                "vulnerability_hour": 14,
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["core_status"], "UNFITTED")

    def test_structure_owner_ticker_prefilled_from_alliance_or_corp(self):
        """Owner ticker should pre-fill from selected alliance or corporation"""
        alliance = HostileAlliance.objects.create(alliance_id=99000101, alliance_name="Test Alliance", ticker="TALLI")
        corp = HostileCorporation.objects.create(corporation_id=98000101, corporation_name="Test Corp", ticker="TCORP")

        form_alliance = HostileStructureForm(
            data={
                "name": "Alliance Structure",
                "structure_type": self.astrahus.id,
                "solar_system": self.system.id,
                "alliance": alliance.id,
                "owner_ticker": "",
                "state": "ONLINE",
            }
        )
        self.assertTrue(form_alliance.is_valid(), form_alliance.errors)
        self.assertEqual(form_alliance.cleaned_data["owner_ticker"], "TALLI")

        form_corp = HostileStructureForm(
            data={
                "name": "Corp Structure",
                "structure_type": self.astrahus.id,
                "solar_system": self.system.id,
                "corporation": corp.id,
                "owner_ticker": "",
                "state": "ONLINE",
            }
        )
        self.assertTrue(form_corp.is_valid(), form_corp.errors)
        self.assertEqual(form_corp.cleaned_data["owner_ticker"], "TCORP")

    def test_structure_form_untracked_alliance_and_corp(self):
        """Untracked alliance and corporation inputs should create new tracked entities"""
        form = HostileStructureForm(
            data={
                "name": "Untracked Hostile Citadel",
                "structure_type": self.astrahus.id,
                "solar_system": self.system.id,
                "untracked_alliance": "Untracked Coalition Forces",
                "untracked_corporation": "Untracked Holding Corp",
                "state": "ONLINE",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        struct = form.save()
        self.assertIsNotNone(struct.alliance)
        self.assertEqual(struct.alliance.alliance_name, "Untracked Coalition Forces")
        self.assertIsNotNone(struct.corporation)
        self.assertEqual(struct.corporation.corporation_name, "Untracked Holding Corp")

    def test_structure_add_creates_timer_when_anchoring_or_reinforced(self):
        """Adding a structure with anchoring or reinforced state and datetime creates a StructureTimer"""
        future_time = (timezone.now() + timezone.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
        response = self.client.post(
            reverse("hostile:structure_add"),
            data={
                "name": "Anchoring Astrahus",
                "structure_type": self.astrahus.id,
                "solar_system": self.system.id,
                "state": "ANCHORING",
                "timer_stage": "ANCHORING",
                "timer_datetime": future_time,
                "vulnerability_hour": 16,
            },
        )
        self.assertEqual(response.status_code, 302)

        struct = HostileStructure.objects.get(name="Anchoring Astrahus")
        self.assertEqual(struct.state, "ANCHORING")
        self.assertEqual(struct.core_status, "UNFITTED")

        timer = StructureTimer.objects.filter(structure=struct).first()
        self.assertIsNotNone(timer)
        self.assertEqual(timer.timer_type, "ANCHORING")
        self.assertEqual(timer.solar_system, self.system)

    def test_alliance_coalition_and_alt_alliance(self):
        """HostileAlliance supports coalition tagging and alt alliance hierarchy"""
        main_alliance = HostileAlliance.objects.create(
            alliance_id=99000001,
            alliance_name="Main Imperium Member",
            ticker="MAIN1",
            coalition="Imperium",
        )
        alt_alliance = HostileAlliance.objects.create(
            alliance_id=99000002,
            alliance_name="Imperium Holding Alliance",
            ticker="HOLD1",
            coalition="Imperium",
            is_alt_alliance=True,
            main_alliance=main_alliance,
        )

        self.assertEqual(alt_alliance.coalition, "Imperium")
        self.assertTrue(alt_alliance.is_alt_alliance)
        self.assertEqual(alt_alliance.main_alliance, main_alliance)
        self.assertIn(alt_alliance, main_alliance.alt_alliances.all())

    def test_corporation_views_and_alt_corp(self):
        """HostileCorporation creation, editing, and alt corp hierarchy"""
        main_alliance = HostileAlliance.objects.create(
            alliance_id=99000010,
            alliance_name="Northern Coalition.",
            ticker="NC.",
            coalition="PanFam",
        )

        # Create corporation via view
        response = self.client.post(
            reverse("hostile:corporation_add"),
            data={
                "corporation_id": 98000555,
                "corporation_name": "Panfam Cyno Network",
                "ticker": "CYNO",
                "coalition": "PanFam",
                "is_alt_corp": True,
                "main_alliance": main_alliance.id,
                "max_subcap_form": 10,
                "max_cap_form": 0,
                "max_super_form": 0,
            },
        )
        self.assertEqual(response.status_code, 302)

        corp = HostileCorporation.objects.get(corporation_id=98000555)
        self.assertEqual(corp.corporation_name, "Panfam Cyno Network")
        self.assertTrue(corp.is_alt_corp)
        self.assertEqual(corp.main_alliance, main_alliance)
        self.assertEqual(corp.coalition, "PanFam")

        # Edit corporation
        edit_response = self.client.post(
            reverse("hostile:corporation_edit", kwargs={"corp_id": corp.id}),
            data={
                "corporation_id": corp.corporation_id,
                "corporation_name": "Panfam Cyno & Recon Network",
                "ticker": "CYNO2",
                "coalition": "PanFam Coalition",
                "is_alt_corp": True,
                "main_alliance": main_alliance.id,
                "max_subcap_form": 25,
            },
        )
        self.assertEqual(edit_response.status_code, 302)
        corp.refresh_from_db()
        self.assertEqual(corp.corporation_name, "Panfam Cyno & Recon Network")
        self.assertEqual(corp.ticker, "CYNO2")
        self.assertEqual(corp.coalition, "PanFam Coalition")
        self.assertEqual(corp.max_subcap_form, 25)

    def test_corporation_api_search_and_resolve(self):
        """API endpoints for corporation searching and resolution"""
        HostileCorporation.objects.create(
            corporation_id=98000888,
            corporation_name="Black Legion Holdings",
            ticker="MEN",
            coalition="WinterCo",
        )

        # Search endpoint
        search_res = self.client.get(reverse("hostile:api_corporation_search"), {"q": "Black"})
        self.assertEqual(search_res.status_code, 200)
        data = search_res.json()
        self.assertTrue(len(data.get("results", [])) > 0)
        self.assertEqual(data["results"][0]["corporation_name"], "Black Legion Holdings")

        # Resolve endpoint
        resolve_res = self.client.get(reverse("hostile:api_corporation_resolve"), {"query": "98000888"})
        self.assertEqual(resolve_res.status_code, 200)
        res_data = resolve_res.json()
        self.assertTrue(res_data["success"])
        self.assertEqual(res_data["corporation"]["corporation_name"], "Black Legion Holdings")
        self.assertEqual(res_data["corporation"]["ticker"], "MEN")

    def test_structures_and_alliances_pages_render(self):
        """Structure registry and Alliances & Sov pages render cleanly"""
        struct_res = self.client.get(reverse("hostile:structures"))
        self.assertEqual(struct_res.status_code, 200)
        self.assertContains(struct_res, "Hostile Structures Registry")
        self.assertContains(struct_res, "Structure Type")
        self.assertNotContains(struct_res, "Structure Type (SDE)")

        alliance_res = self.client.get(reverse("hostile:alliances"))
        self.assertEqual(alliance_res.status_code, 200)
        self.assertContains(alliance_res, "Hostile Corporations")

    def test_structure_type_choice_field_and_timer_stage_validation(self):
        """StructureTypeChoiceField should filter structure types and validate timer stages based on structure category"""
        from hostile.forms import StructureTypeChoiceField

        # Create valid structure types including faction citadels
        draccous_fort = ItemType.objects.create(id=47512, name="'Draccous' Fortizar", published=True)
        horizon_fort = ItemType.objects.create(id=47513, name="'Horizon' Fortizar", published=True)
        flex_jump_gate = ItemType.objects.create(id=35841, name="Ansiblex Jump Gate", published=True)

        # Create invalid items from SDE that should be filtered out
        ItemType.objects.create(id=587, name="Rifter", published=True)
        ItemType.objects.create(id=99001, name="'Citadella' 100mm Steel Plates", published=True)
        ItemType.objects.create(id=99002, name="'Citadella' 100mm Steel Plates Blueprint", published=True)
        ItemType.objects.create(id=99003, name="'Draccous' Fortizar Wreck", published=True)
        ItemType.objects.create(id=99004, name="'Draccous' Fortizar Wreck (copy)", published=True)
        ItemType.objects.create(id=99005, name="[AIR] Non-Interactable Athanor", published=True)
        ItemType.objects.create(id=99006, name="♦ Sotiyo", published=True)
        ItemType.objects.create(id=99007, name="Academy", published=True)
        ItemType.objects.create(id=99008, name="Advanced Large Ship Assembly Array", published=True)
        ItemType.objects.create(id=99009, name="Amarr Citadel", published=True)

        field = StructureTypeChoiceField()
        choice_names = [obj.name for obj in field.queryset]

        # Valid citadels / structures are present
        self.assertIn("Astrahus", choice_names)
        self.assertIn("Ansiblex Jump Gate", choice_names)
        self.assertIn("'Draccous' Fortizar", choice_names)
        self.assertIn("'Horizon' Fortizar", choice_names)

        # Non-structures / wrecks / blueprints / POS arrays / NPE / AIR items are filtered out
        self.assertNotIn("Rifter", choice_names)
        self.assertNotIn("'Citadella' 100mm Steel Plates", choice_names)
        self.assertNotIn("'Citadella' 100mm Steel Plates Blueprint", choice_names)
        self.assertNotIn("'Draccous' Fortizar Wreck", choice_names)
        self.assertNotIn("'Draccous' Fortizar Wreck (copy)", choice_names)
        self.assertNotIn("[AIR] Non-Interactable Athanor", choice_names)
        self.assertNotIn("♦ Sotiyo", choice_names)
        self.assertNotIn("Academy", choice_names)
        self.assertNotIn("Advanced Large Ship Assembly Array", choice_names)
        self.assertNotIn("Amarr Citadel", choice_names)

        # Medium structure with Hull timer stage should be rejected
        form_med_hull = HostileStructureForm(
            data={
                "name": "Invalid Stage Astrahus",
                "structure_type": self.astrahus.id,
                "solar_system": self.system.id,
                "state": "ONLINE",
                "timer_stage": "HULL",
                "timer_datetime": "2026-10-02T18:00",
            }
        )
        self.assertFalse(form_med_hull.is_valid())
        self.assertIn("timer_stage", form_med_hull.errors)

        # Medium structure with Armor timer stage should be valid
        form_med_armor = HostileStructureForm(
            data={
                "name": "Valid Stage Astrahus",
                "structure_type": self.astrahus.id,
                "solar_system": self.system.id,
                "state": "ARMOR",
                "timer_stage": "ARMOR",
                "timer_datetime": "2026-10-02T18:00",
            }
        )
        self.assertTrue(form_med_armor.is_valid())

        # FLEX structure with Armor timer stage should be rejected
        form_flex_armor = HostileStructureForm(
            data={
                "name": "Invalid Stage Ansiblex",
                "structure_type": flex_jump_gate.id,
                "solar_system": self.system.id,
                "state": "ONLINE",
                "timer_stage": "ARMOR",
                "timer_datetime": "2026-10-02T18:00",
            }
        )
        self.assertFalse(form_flex_armor.is_valid())
        self.assertIn("timer_stage", form_flex_armor.errors)

        # FLEX structure with Hull timer stage should be valid
        form_flex_hull = HostileStructureForm(
            data={
                "name": "Valid Stage Ansiblex",
                "structure_type": flex_jump_gate.id,
                "solar_system": self.system.id,
                "state": "HULL",
                "timer_stage": "HULL",
                "timer_datetime": "2026-10-02T18:00",
            }
        )
        self.assertTrue(form_flex_hull.is_valid())

    def test_timer_board_add_structure_creates_known_structure_and_timer(self):
        """Adding a new/untracked structure from Timer Board adds it to known structures and creates linked timer"""
        from hostile.forms import StructureTimerForm

        response = self.client.post(
            reverse("hostile:timer_add"),
            data={
                "structure": "",
                "name": "Timer Board Fortizar",
                "structure_type": self.astrahus.id,
                "solar_system": self.system.id,
                "alliance": self.alliance.id,
                "owner_ticker": "H-ALLI",
                "state": "ANCHORING",
                "core_status": "UNKNOWN",
                "vulnerability_hour": 14,
                "timer_type": "ANCHORING",
                "timer_datetime": "2026-10-03T14:00",
                "notes": "Timer board anchor intel",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)

        # Structure should be in known structures
        new_struct = HostileStructure.objects.filter(name="Timer Board Fortizar").first()
        self.assertIsNotNone(new_struct)
        self.assertEqual(new_struct.solar_system, self.system)
        self.assertEqual(new_struct.alliance, self.alliance)
        self.assertEqual(new_struct.owner_ticker, "H-ALLI")
        self.assertEqual(new_struct.state, "ANCHORING")
        self.assertEqual(new_struct.core_status, "UNFITTED")  # Anchoring defaults core to UNFITTED
        self.assertEqual(new_struct.vulnerability_window, "11:00 - 17:00 UTC")

        # Timer should exist and be linked to the newly created structure
        timer = StructureTimer.objects.filter(structure=new_struct).first()
        self.assertIsNotNone(timer)
        self.assertEqual(timer.timer_type, "ANCHORING")
        self.assertEqual(timer.solar_system, self.system)

    def test_timer_board_add_timer_for_existing_structure(self):
        """Adding a timer for an existing structure links to that structure"""
        struct = HostileStructure.objects.create(
            name="Existing Citadel",
            structure_type=self.astrahus,
            solar_system=self.system,
            alliance=self.alliance,
            owner_ticker="EXST",
        )

        response = self.client.post(
            reverse("hostile:timer_add"),
            data={
                "structure": struct.id,
                "solar_system": self.system.id,
                "timer_type": "ARMOR",
                "timer_datetime": "2026-10-04T20:00",
                "notes": "Armor timer on existing citadel",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)

        timer = StructureTimer.objects.filter(structure=struct).first()
        self.assertIsNotNone(timer)
        self.assertEqual(timer.timer_type, "ARMOR")
        self.assertEqual(timer.solar_system, self.system)

    def test_timer_board_add_sov_timer_without_structure(self):
        """Adding a sov or system timer without a structure creates a timer tied to solar system"""
        response = self.client.post(
            reverse("hostile:timer_add"),
            data={
                "structure": "",
                "name": "",
                "solar_system": self.system.id,
                "timer_type": "SOV_IHUB",
                "timer_datetime": "2026-10-05T18:00",
                "notes": "I-Hub contest timer",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)

        timer = StructureTimer.objects.filter(timer_type="SOV_IHUB", solar_system=self.system).first()
        self.assertIsNotNone(timer)
        self.assertIsNone(timer.structure)

    def test_timer_board_form_and_searchable_dropdown_assets(self):
        """Timer board UI includes full structure options and searchable select scripts"""
        response = self.client.get(reverse("hostile:timers"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "hostile_searchable_select.js")
        self.assertContains(response, "id_timer_structure")
        self.assertContains(response, "id_structure_name")
        self.assertContains(response, "id_structure_type")
        self.assertContains(response, "id_owner_ticker")
        self.assertContains(response, "id_vulnerability_hour")

    def test_structures_render_with_null_corporation_or_alliance(self):
        """Ensure structures page and dashboard render correctly when corporation is None or alliance is None"""
        # Structure with alliance only (corporation = None)
        struct_alliance = HostileStructure.objects.create(
            name="Alliance Only Astrahus",
            structure_type=self.astrahus,
            solar_system=self.system,
            alliance=self.alliance,
            corporation=None,
            owner_ticker="H-ALLI",
            state="ONLINE",
            core_status="FITTED",
        )
        self.assertEqual(struct_alliance.owner_name, "Hostile Alliance")

        # Structure with corporation only (alliance = None)
        corp = HostileCorporation.objects.create(
            corporation_id=98000001, corporation_name="Indie Corp", ticker="INDC"
        )
        struct_corp = HostileStructure.objects.create(
            name="Corp Only Athanor",
            structure_type=self.athanor,
            solar_system=self.system,
            alliance=None,
            corporation=corp,
            owner_ticker="INDC",
            state="ONLINE",
            core_status="UNFITTED",
        )
        self.assertEqual(struct_corp.owner_name, "Indie Corp")

        # Structure with neither (ticker only)
        struct_ticker = HostileStructure.objects.create(
            name="Ticker Only Raitaru",
            structure_type=self.astrahus,
            solar_system=self.system,
            alliance=None,
            corporation=None,
            owner_ticker="SOLO",
            state="ONLINE",
            core_status="UNKNOWN",
        )
        self.assertEqual(struct_ticker.owner_name, "SOLO")

        # Test structures page renders with 200 and displays all owner names
        response = self.client.get(reverse("hostile:structures"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Hostile Alliance")
        self.assertContains(response, "Indie Corp")
        self.assertContains(response, "SOLO")

        # Test index dashboard renders with 200 and displays structures
        response_index = self.client.get(reverse("hostile:index"))
        self.assertEqual(response_index.status_code, 200)
        self.assertContains(response_index, "Hostile Alliance")
        self.assertContains(response_index, "Indie Corp")
        self.assertContains(response_index, "SOLO")

    def test_structure_add_post_with_null_corporation_redirects_and_renders(self):
        """Posting a new structure with only alliance (corporation is null) saves and renders without error"""
        response = self.client.post(
            reverse("hostile:structure_add"),
            data={
                "name": "New Keepstar Added",
                "structure_type": self.astrahus.id,
                "solar_system": self.system.id,
                "alliance": self.alliance.id,
                "corporation": "",
                "owner_ticker": "H-ALLI",
                "state": "ONLINE",
                "core_status": "FITTED",
                "vulnerability_hour": 18,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "New Keepstar Added")
        self.assertContains(response, "Hostile Alliance")

    def test_coalition_alliances_and_leader_designation(self):
        """Alliances can be assigned to coalitions and marked as the coalition leader"""
        ally_leader = HostileAlliance.objects.create(
            alliance_id=99000100,
            alliance_name="Imperium Leader Alliance",
            ticker="GSF",
            coalition="Imperium",
            is_coalition_leader=True,
        )
        ally_member = HostileAlliance.objects.create(
            alliance_id=99000101,
            alliance_name="Imperium Member Alliance",
            ticker="INIT",
            coalition="Imperium",
            is_coalition_leader=False,
        )

        self.assertEqual(ally_leader.coalition, "Imperium")
        self.assertTrue(ally_leader.is_coalition_leader)
        self.assertEqual(ally_member.coalition, "Imperium")
        self.assertFalse(ally_member.is_coalition_leader)

        # Render alliances list and verify coalition leader badge
        response = self.client.get(reverse("hostile:alliances"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Imperium Leader Alliance")
        self.assertContains(response, "Imperium Member Alliance")
        self.assertContains(response, "Leader")

    def test_coalition_manage_view_batch_assignment(self):
        """Managing coalition alliances batch assigns coalition name and sets designated leader"""
        a1 = HostileAlliance.objects.create(
            alliance_id=99000201, alliance_name="WinterCo Member 1", ticker="FRT"
        )
        a2 = HostileAlliance.objects.create(
            alliance_id=99000202, alliance_name="WinterCo Member 2", ticker="SIXY"
        )
        a3 = HostileAlliance.objects.create(
            alliance_id=99000203, alliance_name="Old WinterCo Member", ticker="OLD", coalition="WinterCo"
        )

        response = self.client.post(
            reverse("hostile:coalition_manage"),
            data={
                "coalition_name": "WinterCo",
                "alliances": [a1.id, a2.id],
                "leader_alliance": a1.id,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)

        a1.refresh_from_db()
        a2.refresh_from_db()
        a3.refresh_from_db()

        self.assertEqual(a1.coalition, "WinterCo")
        self.assertTrue(a1.is_coalition_leader)
        self.assertEqual(a2.coalition, "WinterCo")
        self.assertFalse(a2.is_coalition_leader)
        # a3 was excluded so its coalition was cleared
        self.assertEqual(a3.coalition, "")
        self.assertFalse(a3.is_coalition_leader)

    def test_alliance_detail_view_comprehensive_intel(self):
        """Clicking on an alliance loads the full alliance dossier with all associated intel"""
        main_alliance = HostileAlliance.objects.create(
            alliance_id=99000301,
            alliance_name="Pandemic Horde",
            ticker="REKTD",
            coalition="PanFam",
            is_coalition_leader=True,
            primary_timezone="EUTZ",
            max_subcap_form=250,
            max_cap_form=60,
            max_super_form=20,
            sov_timer_distribution={"18": 12, "19": 8},
        )
        alt_alliance = HostileAlliance.objects.create(
            alliance_id=99000302,
            alliance_name="Horde Structure Holding",
            ticker="H-HOLD",
            is_alt_alliance=True,
            main_alliance=main_alliance,
        )
        member_corp = HostileCorporation.objects.create(
            corporation_id=98000301,
            corporation_name="Horde Vanguard Corp",
            ticker="HVG",
            alliance=main_alliance,
        )
        alt_corp = HostileCorporation.objects.create(
            corporation_id=98000302,
            corporation_name="Horde Cyno Beacon Network",
            ticker="HCYNO",
            is_alt_corp=True,
            main_alliance=main_alliance,
        )
        struct = HostileStructure.objects.create(
            name="1DQ1-A - Primary Keepstar",
            structure_type=self.astrahus,
            solar_system=self.system,
            alliance=main_alliance,
            owner_ticker="REKTD",
            state="ONLINE",
            core_status="FITTED",
        )
        timer = StructureTimer.objects.create(
            structure=struct,
            solar_system=self.system,
            timer_type="ARMOR",
            timer_datetime=timezone.now() + timezone.timedelta(days=2),
        )
        pilot = HostilePilotDossier.objects.create(
            character_id=91000301,
            character_name="Gobbins Commander",
            alliance_name="Pandemic Horde",
            corporation_name="Horde Vanguard Corp",
            is_fc=True,
        )

        response = self.client.get(
            reverse("hostile:alliance_detail", kwargs={"alliance_id": main_alliance.id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pandemic Horde")
        self.assertContains(response, "REKTD")
        self.assertContains(response, "PanFam")
        self.assertContains(response, "Coalition Leader")
        self.assertContains(response, "Horde Structure Holding")
        self.assertContains(response, "Horde Vanguard Corp")
        self.assertContains(response, "Horde Cyno Beacon Network")
        self.assertContains(response, "1DQ1-A - Primary Keepstar")
        self.assertContains(response, "Gobbins Commander")

    def test_corporation_detail_view_comprehensive_intel(self):
        """Clicking on a corporation loads the full corporation dossier with all associated intel"""
        parent_alliance = HostileAlliance.objects.create(
            alliance_id=99000401,
            alliance_name="Fraternity Alliance",
            ticker="FRT",
            coalition="WinterCo",
        )
        corp = HostileCorporation.objects.create(
            corporation_id=98000401,
            corporation_name="Chinese High Command",
            ticker="FRT-HC",
            alliance=parent_alliance,
            coalition="WinterCo",
            max_subcap_form=80,
            max_cap_form=30,
        )
        struct = HostileStructure.objects.create(
            name="4-HWWF - Staging Fortizar",
            structure_type=self.astrahus,
            solar_system=self.system,
            corporation=corp,
            owner_ticker="FRT-HC",
            state="ONLINE",
            core_status="FITTED",
        )
        pilot = HostilePilotDossier.objects.create(
            character_id=91000401,
            character_name="Noraus Super Pilot",
            corporation_name="Chinese High Command",
            is_super_pilot=True,
        )

        response = self.client.get(
            reverse("hostile:corporation_detail", kwargs={"corp_id": corp.id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Chinese High Command")
        self.assertContains(response, "FRT-HC")
        self.assertContains(response, "Fraternity Alliance")
        self.assertContains(response, "WinterCo")
        self.assertContains(response, "4-HWWF - Staging Fortizar")
        self.assertContains(response, "Noraus Super Pilot")

    def test_alliance_and_corporation_add_prefills(self):
        """Adding alliance or corporation with query params pre-populates initial form fields"""
        main_alliance = HostileAlliance.objects.create(
            alliance_id=99000501, alliance_name="Parent Main Alliance", ticker="PARENT"
        )
        # Prepopulate alliance add
        res_a = self.client.get(
            f"{reverse('hostile:alliance_add')}?main_alliance_id={main_alliance.id}&is_alt_alliance=1&coalition=TestCo"
        )
        self.assertEqual(res_a.status_code, 200)
        form_a = res_a.context["form"]
        self.assertEqual(form_a.initial.get("main_alliance"), str(main_alliance.id))
        self.assertTrue(form_a.initial.get("is_alt_alliance"))
        self.assertEqual(form_a.initial.get("coalition"), "TestCo")

        # Prepopulate corp add
        res_c = self.client.get(
            f"{reverse('hostile:corporation_add')}?alliance_id={main_alliance.id}&is_alt_corp=1&coalition=TestCo"
        )
        self.assertEqual(res_c.status_code, 200)
        form_c = res_c.context["form"]
        self.assertEqual(form_c.initial.get("alliance"), str(main_alliance.id))
        self.assertTrue(form_c.initial.get("is_alt_corp"))
        self.assertEqual(form_c.initial.get("coalition"), "TestCo")

    def test_structure_edit_get_and_post(self):
        """Test GET and POST workflows for editing an existing hostile structure"""
        struct = HostileStructure.objects.create(
            name="1DQ1-A - Original Staging Fort",
            structure_type=self.astrahus,
            solar_system=self.system,
            alliance=self.alliance,
            owner_ticker="H-ALLI",
            state="ONLINE",
            core_status="FITTED",
            vulnerability_hour=18,
            vulnerability_window="15:00 - 21:00 UTC",
            location_details="Planet V Moon 1",
            notes="Initial scouting notes",
        )

        # GET edit view
        res_get = self.client.get(reverse("hostile:structure_edit", args=[struct.id]))
        self.assertEqual(res_get.status_code, 200)
        self.assertContains(res_get, "Edit Hostile Structure:")
        self.assertContains(res_get, "1DQ1-A - Original Staging Fort")
        self.assertContains(res_get, "Planet V Moon 1")

        # POST edit view to update attributes
        future_timer = timezone.now() + timezone.timedelta(days=2)
        post_data = {
            "name": "1DQ1-A - Renamed Primary Keep",
            "structure_type": self.astrahus.id,
            "solar_system": self.system.id,
            "alliance": self.alliance.id,
            "owner_ticker": "H-ALLI",
            "state": "ARMOR",
            "core_status": "FITTED",
            "vulnerability_hour": 20,
            "vulnerability_window": "17:00 - 23:00 UTC",
            "location_details": "Planet VI Moon 3",
            "notes": "Updated combat intel: Armor reinforced",
            "timer_datetime": future_timer.strftime("%Y-%m-%dT%H:%M"),
            "timer_stage": "ARMOR",
        }
        res_post = self.client.post(reverse("hostile:structure_edit", args=[struct.id]), data=post_data)
        self.assertEqual(res_post.status_code, 302)
        self.assertRedirects(res_post, reverse("hostile:structures"))

        struct.refresh_from_db()
        self.assertEqual(struct.name, "1DQ1-A - Renamed Primary Keep")
        self.assertEqual(struct.state, "ARMOR")
        self.assertEqual(struct.vulnerability_hour, 20)
        self.assertEqual(struct.vulnerability_window, "17:00 - 23:00 UTC")
        self.assertEqual(struct.location_details, "Planet VI Moon 3")
        self.assertEqual(struct.notes, "Updated combat intel: Armor reinforced")

        # Verify timer was created
        created_timer = StructureTimer.objects.filter(structure=struct).first()
        self.assertIsNotNone(created_timer)
        self.assertEqual(created_timer.timer_type, "ARMOR")

    def test_structure_delete_get_and_post(self):
        """Test GET confirmation and POST deletion of a hostile structure"""
        struct_to_delete = HostileStructure.objects.create(
            name="1DQ1-A - Structure to Delete",
            structure_type=self.athanor,
            solar_system=self.system,
            alliance=self.alliance,
            owner_ticker="H-ALLI",
            state="ONLINE",
            core_status="UNFITTED",
            vulnerability_hour=14,
        )

        # GET confirm delete view
        res_get = self.client.get(reverse("hostile:structure_delete", args=[struct_to_delete.id]))
        self.assertEqual(res_get.status_code, 200)
        self.assertContains(res_get, "Confirm Structure Deletion")
        self.assertContains(res_get, "1DQ1-A - Structure to Delete")

        # POST delete view
        res_post = self.client.post(reverse("hostile:structure_delete", args=[struct_to_delete.id]))
        self.assertEqual(res_post.status_code, 302)
        self.assertRedirects(res_post, reverse("hostile:structures"))

        self.assertFalse(HostileStructure.objects.filter(id=struct_to_delete.id).exists())

    def test_structure_table_action_buttons_and_modals_render(self):
        """Test that structures registry and entity dossiers render Edit links and Delete modals"""
        struct = HostileStructure.objects.create(
            name="1DQ1-A - Action Check Citadel",
            structure_type=self.astrahus,
            solar_system=self.system,
            alliance=self.alliance,
            owner_ticker="H-ALLI",
            state="ONLINE",
            core_status="FITTED",
        )

        # Registry table
        res_reg = self.client.get(reverse("hostile:structures"))
        self.assertEqual(res_reg.status_code, 200)
        self.assertContains(res_reg, reverse("hostile:structure_edit", args=[struct.id]))
        self.assertContains(res_reg, f"deleteStructureModal{struct.id}")
        self.assertContains(res_reg, reverse("hostile:structure_delete", args=[struct.id]))

        # Alliance dossier structures table
        res_alli = self.client.get(reverse("hostile:alliance_detail", args=[self.alliance.id]))
        self.assertEqual(res_alli.status_code, 200)
        self.assertContains(res_alli, reverse("hostile:structure_edit", args=[struct.id]))
        self.assertContains(res_alli, f"deleteAllianceStructureModal{struct.id}")
        self.assertContains(res_alli, reverse("hostile:structure_delete", args=[struct.id]))

    def test_alliance_sov_capital_auto_staging_creation_and_migration(self):
        """Setting or changing an alliance sov_capital should automatically create/update staging systems"""
        from hostile.models import HostileStagingSystem

        system_t5z = SolarSystem.objects.create(id=30004760, name="T5ZI-S", security_status=-0.5)

        # Create alliance with sov_capital
        alliance = HostileAlliance.objects.create(
            alliance_id=99000200,
            alliance_name="Capital Test Alliance",
            ticker="CTA",
            sov_capital=self.system,
        )

        staging = HostileStagingSystem.objects.filter(alliance=alliance, solar_system=self.system).first()
        self.assertIsNotNone(staging)
        self.assertEqual(staging.staging_type, "CAPITAL")
        self.assertTrue(staging.is_primary)

        # Move sov_capital to T5ZI-S
        alliance.sov_capital = system_t5z
        alliance.save()

        # Check staging moved
        new_staging = HostileStagingSystem.objects.filter(alliance=alliance, solar_system=system_t5z).first()
        self.assertIsNotNone(new_staging)
        self.assertEqual(new_staging.staging_type, "CAPITAL")
        self.assertTrue(new_staging.is_primary)

    def test_sync_alliance_members_and_corps(self):
        """Testing ESI member corporation sync and character aggregation"""
        from hostile.services.sov_engine import SovAnalysisEngine
        from unittest.mock import patch

        alliance = HostileAlliance.objects.create(
            alliance_id=99000300,
            alliance_name="Member Sync Alliance",
            ticker="MSA",
            member_count=800,
            prev_member_count=800,
        )

        with patch("hostile.services.sov_engine.esi_client.get_alliance_corporations") as mock_get_corps, \
             patch("hostile.services.sov_engine.esi_client.get_corporation_info") as mock_corp_info, \
             patch("hostile.services.sov_engine.esi_client.get_alliance_info") as mock_alliance_info:

            mock_get_corps.return_value = [98000001, 98000002]
            mock_alliance_info.return_value = {"executor_corporation_id": 98000001}
            mock_corp_info.side_effect = lambda cid: {
                98000001: {"name": "Corp Alpha", "ticker": "CALPH", "member_count": 450},
                98000002: {"name": "Corp Beta", "ticker": "CBETA", "member_count": 550},
            }.get(cid)

            res = SovAnalysisEngine.sync_alliance_members_and_corps(alliance)
            self.assertTrue(res["success"])
            self.assertEqual(res["member_count"], 1000)
            self.assertEqual(res["corps_count"], 2)

            alliance.refresh_from_db()
            self.assertEqual(alliance.member_count, 1000)
            self.assertEqual(alliance.prev_member_count, 800)
            self.assertEqual(alliance.member_corps_count, 2)
            self.assertEqual(alliance.executor_corp_id, 98000001)
            self.assertIn("members", alliance.zkill_deltas)
            self.assertEqual(alliance.zkill_deltas["members"]["pct"], 25)

            corp1 = HostileCorporation.objects.get(corporation_id=98000001)
            self.assertEqual(corp1.corporation_name, "Corp Alpha")
            self.assertEqual(corp1.member_count, 450)
            self.assertEqual(corp1.alliance, alliance)
            self.assertTrue(corp1.is_executor)
            self.assertEqual(corp1.max_subcap_form, 67)
            self.assertEqual(corp1.max_cap_form, 13)

            corp2 = HostileCorporation.objects.get(corporation_id=98000002)
            self.assertEqual(corp2.corporation_name, "Corp Beta")
            self.assertEqual(corp2.member_count, 550)
            self.assertEqual(corp2.alliance, alliance)
            self.assertFalse(corp2.is_executor)
            self.assertEqual(corp2.max_subcap_form, 82)
            self.assertEqual(corp2.max_cap_form, 16)

    def test_sync_sovereignty_held_and_deltas(self):
        """Testing sovereignty systems held sync from ESI map"""
        from hostile.services.sov_engine import SovAnalysisEngine

        alliance = HostileAlliance.objects.create(
            alliance_id=99000400,
            alliance_name="Sov Held Alliance",
            ticker="SHA",
            systems_held_count=10,
        )

        dummy_map = [
            {"solar_system_id": 30000001, "alliance_id": 99000400},
            {"solar_system_id": 30000002, "alliance_id": 99000400},
            {"solar_system_id": 30000003, "alliance_id": 99000400},
            {"solar_system_id": 30000004, "alliance_id": 99000400},
            {"solar_system_id": 30000005, "alliance_id": 99000400},
            {"solar_system_id": 30000006, "alliance_id": 99000400},
            {"solar_system_id": 30000007, "alliance_id": 99000400},
            {"solar_system_id": 30000008, "alliance_id": 99000400},
            {"solar_system_id": 30000009, "alliance_id": 99000400},
            {"solar_system_id": 30000010, "alliance_id": 99000400},
            {"solar_system_id": 30000011, "alliance_id": 99000400},
            {"solar_system_id": 30000012, "alliance_id": 99000400},
        ]

        count = SovAnalysisEngine.sync_alliance_sovereignty_held_single(alliance, sov_map=dummy_map)
        self.assertEqual(count, 12)

        alliance.refresh_from_db()
        self.assertEqual(alliance.systems_held_count, 12)
        self.assertEqual(alliance.prev_systems_held_count, 10)
        self.assertIn("systems_held", alliance.zkill_deltas)
        self.assertEqual(alliance.zkill_deltas["systems_held"]["pct"], 20)

    def test_alliance_ui_renders_sov_capital_and_member_metrics(self):
        """Testing that alliance list and detail pages render sov capital and member counts"""
        alliance = HostileAlliance.objects.create(
            alliance_id=99000500,
            alliance_name="UI Metrics Alliance",
            ticker="UIMA",
            sov_capital=self.system,
            systems_held_count=25,
            member_count=3500,
            member_corps_count=12,
            zkill_deltas={
                "members": {"pct": 10, "text": "+10% characters (3,500)"},
                "systems_held": {"pct": 25, "text": "+25% systems held (25)"},
            },
        )

        res_list = self.client.get(reverse("hostile:alliances"))
        self.assertEqual(res_list.status_code, 200)
        self.assertContains(res_list, "UI Metrics Alliance")
        self.assertContains(res_list, "1DQ1-A")
        self.assertContains(res_list, "3,500")
        self.assertContains(res_list, "+10%")
        self.assertContains(res_list, "+25%")

        res_detail = self.client.get(reverse("hostile:alliance_detail", args=[alliance.id]))
        self.assertEqual(res_detail.status_code, 200)
        self.assertContains(res_detail, "Capital: 1DQ1-A")
        self.assertContains(res_detail, "3,500")
        self.assertContains(res_detail, "Sov Systems Held")

    def test_alliance_dossier_renders_member_corps_and_pilots(self):
        """Testing that alliance detail page properly displays member corp metrics and pilot roles"""
        alliance = HostileAlliance.objects.create(
            alliance_id=99000600,
            alliance_name="Dossier Test Alliance",
            ticker="DTA",
        )
        corp = HostileCorporation.objects.create(
            corporation_id=98000010,
            corporation_name="Dossier Member Corp",
            ticker="DMC",
            alliance=alliance,
            member_count=300,
            active_pilots_count=45,
            weekly_kills_count=120,
            max_subcap_form=50,
            max_cap_form=10,
            max_super_form=2,
        )
        pilot = HostilePilotDossier.objects.create(
            character_id=960005,
            character_name="Commander Orion",
            corporation_name="Dossier Member Corp",
            alliance_name="Dossier Test Alliance",
            is_fc=True,
            is_dread_pilot=True,
            notes="Auto-detected via zKillboard: Top Combat Pilot. Revelation.",
        )

        res = self.client.get(reverse("hostile:alliance_detail", args=[alliance.id]))
        self.assertEqual(res.status_code, 200)
        # Check member corp row
        self.assertContains(res, "Dossier Member Corp")
        self.assertContains(res, "[DMC]")
        self.assertContains(res, "50 Sub")
        self.assertContains(res, "10 Cap")
        self.assertContains(res, "2 Super")
        self.assertContains(res, "45")  # active pvp
        self.assertContains(res, "120")  # weekly kills

        # Check pilot row
        self.assertContains(res, "Commander Orion")
        self.assertContains(res, "Dossier Member Corp")
        self.assertContains(res, "FC")
        self.assertContains(res, "Dread")

    def test_alliance_dossier_renders_executor_and_deltas(self):
        """Testing that alliance detail page properly displays executor badge and member percentage deltas"""
        alliance = HostileAlliance.objects.create(
            alliance_id=99000700,
            alliance_name="Delta & Executor Alliance",
            ticker="DEA",
            executor_corp_id=98000020,
        )
        corp1 = HostileCorporation.objects.create(
            corporation_id=98000020,
            corporation_name="Executor Corporation",
            ticker="EXEC",
            alliance=alliance,
            member_count=500,
            prev_member_count=400,  # +25%
        )
        corp2 = HostileCorporation.objects.create(
            corporation_id=98000021,
            corporation_name="Line Corporation",
            ticker="LINE",
            alliance=alliance,
            member_count=180,
            prev_member_count=200,  # -10%
        )

        res = self.client.get(reverse("hostile:alliance_detail", args=[alliance.id]))
        self.assertEqual(res.status_code, 200)
        self.assertTrue(corp1.is_executor)
        self.assertFalse(corp2.is_executor)
        self.assertEqual(corp1.member_count_delta_percent, 25.0)
        self.assertEqual(corp2.member_count_delta_percent, -10.0)

        self.assertContains(res, "Executor Corporation")
        self.assertContains(res, "Executor")
        self.assertContains(res, "+25%")
        self.assertContains(res, "-10%")

    def test_corporation_detail_view_full_dossier(self):
        """Testing that corporation detail dossier displays timers, structures, stagings, doctrines, and pilots"""
        alliance = HostileAlliance.objects.create(
            alliance_id=99000800,
            alliance_name="Corp Parent Alliance",
            ticker="CPA",
            executor_corp_id=98000030,
        )
        corp = HostileCorporation.objects.create(
            corporation_id=98000030,
            corporation_name="Comprehensive Test Corp",
            ticker="CTC",
            alliance=alliance,
            member_count=350,
            prev_member_count=300,  # +16.7%
            active_pilots_count=60,
            weekly_kills_count=250,
            weekly_losses_count=35,
            max_subcap_form=70,
            max_cap_form=15,
        )

        structure = HostileStructure.objects.create(
            structure_id=1039999901,
            name="CTC HQ Keepstar",
            structure_type=self.astrahus,
            solar_system=self.system,
            corporation=corp,
            alliance=alliance,
            state="ONLINE",
            core_status="FITTED",
        )

        timer = StructureTimer.objects.create(
            structure=structure,
            solar_system=self.system,
            timer_type="ARMOR",
            timer_datetime=timezone.now() + timedelta(days=2),
        )

        staging = HostileStagingSystem.objects.create(
            solar_system=self.system,
            alliance=alliance,
            corporation=corp,
            staging_type="PRIMARY",
            is_primary=True,
            notes="Main staging system for CTC",
        )

        from hostile.models import HostileDoctrine
        doctrine = HostileDoctrine.objects.create(
            name="CTC Cerberus Fleet",
            corporation=corp,
            alliance=alliance,
            role_type="MAIN_FLEET",
            description="Main HAC doctrine.",
            created_by=self.user_officer,
        )

        pilot = HostilePilotDossier.objects.create(
            character_id=960099,
            character_name="Captain CTC",
            corporation_name="Comprehensive Test Corp",
            alliance_name="Corp Parent Alliance",
            is_fc=True,
            is_fax_pilot=True,
        )

        res = self.client.get(reverse("hostile:corporation_detail", args=[corp.id]))
        self.assertEqual(res.status_code, 200)

        # Check header & executor status
        self.assertContains(res, "Comprehensive Test Corp")
        self.assertContains(res, "[CTC]")
        self.assertContains(res, "Alliance Executor")
        self.assertContains(res, "Corp Parent Alliance")

        # Check KPI delta
        self.assertContains(res, "+16.7%")

        # Check structures and timers
        self.assertContains(res, "CTC HQ Keepstar")
        self.assertContains(res, "Armor")

        # Check staging
        self.assertContains(res, "1DQ1-A")
        self.assertContains(res, "Main staging system for CTC")

        # Check doctrine
        self.assertContains(res, "CTC Cerberus Fleet")

        # Check pilot
        self.assertContains(res, "Captain CTC")
        self.assertContains(res, "FC")
        self.assertContains(res, "FAX")

    def test_sov_timers_tab_rendering_and_details(self):
        """Testing dedicated Sovereignty Timers tab with text countdowns, ongoing contests, and concluded results"""
        now = timezone.now()

        # 1. Upcoming tracked hostile sov timer
        tracked_sov_timer = StructureTimer.objects.create(
            solar_system=self.system,
            timer_type="SOV_IHUB",
            timer_datetime=now + timedelta(hours=4),
            is_verified=True,
            is_sov_campaign=True,
            is_concluded=False,
            defender_id=99000099,
            defender_name="Hostile Alliance",
            defender_ticker="H-ALLI",
            defender_score=0.7,
            notes="Sovereignty Contest: Ihub Defense",
        )

        # 2. Upcoming public untracked sov timer
        public_sov_timer = StructureTimer.objects.create(
            solar_system=self.system,
            timer_type="SOV_TCU",
            timer_datetime=now + timedelta(hours=8),
            is_verified=True,
            is_sov_campaign=True,
            is_concluded=False,
            defender_id=88000088,
            defender_name="Random Public Alliance",
            defender_ticker="RPA",
            defender_score=0.35,
            notes="Sovereignty Contest: Tcu Defense",
        )

        # 3. Active ongoing sov timer (started 30 mins ago, not concluded in ESI, default 60/40 score)
        ongoing_sov_timer = StructureTimer.objects.create(
            solar_system=self.system,
            timer_type="SOV_IHUB",
            timer_datetime=now - timedelta(minutes=30),
            is_verified=True,
            is_sov_campaign=True,
            is_concluded=False,
            defender_id=99000099,
            defender_name="Hostile Alliance",
            defender_ticker="H-ALLI",
            defender_score=0.6,
            notes="Sovereignty Contest: Active Ihub Defense",
        )

        # 4. Concluded sov timer (within 24 hours)
        concluded_sov_timer = StructureTimer.objects.create(
            solar_system=self.system,
            timer_type="OTHER",
            timer_datetime=now - timedelta(hours=12),
            is_verified=True,
            is_sov_campaign=True,
            is_concluded=True,
            defender_id=99000099,
            defender_name="Hostile Alliance",
            defender_ticker="H-ALLI",
            defender_score=1.0,
            notes="Sovereignty Contest: Station Freeport",
        )

        # 5. Old concluded sov timer (> 24 hours ago - should not appear)
        old_sov_timer = StructureTimer.objects.create(
            solar_system=self.system,
            timer_type="SOV_IHUB",
            timer_datetime=now - timedelta(hours=36),
            is_verified=True,
            is_sov_campaign=True,
            is_concluded=True,
            defender_id=99000099,
            defender_name="Hostile Alliance",
            defender_ticker="H-ALLI",
            defender_score=0.0,
        )

        res = self.client.get(reverse("hostile:sov_timers"))
        self.assertEqual(res.status_code, 200)

        # Structure event names
        self.assertContains(res, "Infrastructure Hub (I-Hub)")
        self.assertContains(res, "Territorial Claim Unit (TCU)")
        self.assertContains(res, "Station Freeport")

        # Defenders & Tickers
        self.assertContains(res, "Hostile Alliance [H-ALLI]")
        self.assertContains(res, "Random Public Alliance [RPA]")

        # Tracked hostile badge exists, Public Sov Claim badge must NOT exist
        self.assertContains(res, "Tracked Hostile")
        self.assertNotContains(res, "Public Sov Claim")

        # Notice banner and Countdown script
        self.assertContains(res, "synchronized every minute from ESI")
        self.assertContains(res, "hostile_countdown.js")

        # Ongoing timer does NOT move to expired: remains in active list, shows ON-GOING, default 60/40 donut, and note
        self.assertTrue(ongoing_sov_timer.is_ongoing)
        self.assertFalse(ongoing_sov_timer.is_concluded)
        self.assertContains(res, "ON-GOING")
        self.assertContains(res, "Def: 60%")
        self.assertContains(res, "Atk: 40%")
        self.assertContains(res, "No progress has been made yet")

        # Scores: Future/unstarted contests show N/A and Not Started
        self.assertFalse(tracked_sov_timer.has_started)
        self.assertFalse(public_sov_timer.has_started)
        self.assertTrue(concluded_sov_timer.has_started)
        self.assertContains(res, "N/A")
        self.assertContains(res, "Not Started")

        # Concluded past 24 hours timer shows score and result badge
        self.assertContains(res, "Concluded Campaigns (Past 24 Hours)")
        self.assertEqual(concluded_sov_timer.defender_percent, 100)
        self.assertContains(res, "Defended")
        self.assertContains(res, "Def: 100%")

        # Timers on sov timer tab must NOT have edit or delete buttons
        self.assertNotContains(res, f"/timers/{tracked_sov_timer.id}/edit/")
        self.assertNotContains(res, f"/timers/{tracked_sov_timer.id}/delete/")

    def test_dashboard_only_shows_tracked_hostile_timers(self):
        """Dashboard must show hostile structure timers and all active sovereignty campaigns"""
        now = timezone.now()

        # Tracked hostile structure timer
        struct = HostileStructure.objects.create(
            name="1DQ Keepstar",
            structure_type=self.astrahus,
            solar_system=self.system,
            alliance=self.alliance,
            state="ARMOR",
        )
        hostile_struct_timer = StructureTimer.objects.create(
            structure=struct,
            solar_system=self.system,
            timer_type="ARMOR",
            timer_datetime=now + timedelta(hours=2),
        )

        # Tracked hostile sov timer (currently contested - started 30m ago)
        hostile_sov_timer = StructureTimer.objects.create(
            solar_system=self.system,
            timer_type="SOV_IHUB",
            timer_datetime=now - timedelta(minutes=30),
            is_sov_campaign=True,
            defender_id=self.alliance.alliance_id,
            defender_name=self.alliance.alliance_name,
            defender_ticker=self.alliance.ticker,
        )

        # Untracked public sov timer (currently contested - started 1h ago)
        untracked_sov_timer = StructureTimer.objects.create(
            solar_system=self.system,
            timer_type="SOV_TCU",
            timer_datetime=now - timedelta(hours=1),
            is_sov_campaign=True,
            defender_id=11223344,
            defender_name="Completely Untracked Neutral",
            defender_ticker="NEUT",
        )

        # Upcoming public sov timer (not yet contested - 4h in future)
        upcoming_sov_timer = StructureTimer.objects.create(
            solar_system=self.system,
            timer_type="SOV_TCU",
            timer_datetime=now + timedelta(hours=4),
            is_sov_campaign=True,
            defender_id=99000100,
            defender_name="Future Contested Alliance",
            defender_ticker="FUT",
        )

        res = self.client.get(reverse("hostile:index"))
        self.assertEqual(res.status_code, 200)

        # Hostile structure timer appears in urgent timers
        self.assertContains(res, "1DQ Keepstar")

        # Active sovereignty campaigns currently being contested (both tracked and public) appear in sov battles deck and KPI count
        self.assertEqual(res.context["stats"]["active_sov_contests_count"], 2)
        self.assertContains(res, "Active Sovereignty Battles")
        self.assertContains(res, "Completely Untracked Neutral")

    def test_pilot_dossier_display_corporation_name_and_healing(self):
        """Pilot dossier must display resolved corporation name and heal numeric/missing names"""
        corp = HostileCorporation.objects.create(
            corporation_id=98777001,
            corporation_name="Elite PVP Corp",
            ticker="EPC",
        )

        pilot1 = HostilePilotDossier.objects.create(
            character_id=9988771,
            character_name="Dread Commander Alpha",
            corporation_name="Elite PVP Corp",
            is_dread_pilot=True,
        )

        # Numeric corp id in corporation_name
        pilot2 = HostilePilotDossier.objects.create(
            character_id=9988772,
            character_name="Super Pilot Beta",
            corporation_name="98777001",
            is_super_pilot=True,
        )

        # Empty corp name
        pilot3 = HostilePilotDossier.objects.create(
            character_id=9988773,
            character_name="Unknown Pilot Gamma",
            corporation_name="",
            is_cyno_alt=True,
        )

        self.assertEqual(pilot1.display_corporation_name, "Elite PVP Corp")
        self.assertEqual(pilot2.display_corporation_name, "Elite PVP Corp")
        self.assertEqual(pilot3.display_corporation_name, "Unknown Corp")

        res = self.client.get(reverse("hostile:pilots"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Dread Commander Alpha")
        self.assertContains(res, "Super Pilot Beta")
        self.assertContains(res, "Elite PVP Corp")

    def test_esi_304_not_modified_cache_activation(self):
        """HTTP 304 Not Modified responses must activate and return cached data"""
        # Standard Library
        from unittest.mock import MagicMock
        from esi.openapi_clients import HTTPNotModified
        from django.core.cache import cache
        from hostile.services.esi_client import HostileESIClientProvider

        client_provider = HostileESIClientProvider()
        mock_op = MagicMock()

        # 1. First call returns fresh data (200 OK)
        mock_op.return_value = [{"solar_system_id": 30004759, "status": "active"}]
        result1 = client_provider.fetch_safely(mock_op, test_param=123)
        self.assertEqual(result1, [{"solar_system_id": 30004759, "status": "active"}])

        # 2. Second call raises HTTPNotModified (304)
        mock_op.side_effect = HTTPNotModified(MagicMock(), "Not Modified")
        result2 = client_provider.fetch_safely(mock_op, test_param=123)
        self.assertEqual(result2, [{"solar_system_id": 30004759, "status": "active"}])

    def test_alliance_sovereignty_intel_grouped_by_region_and_hourly_schedule(self):
        """Alliance sovereignty intel must be grouped by region and hourly schedule must support accordion expansion"""
        # Standard Library
        from unittest.mock import patch
        from eve_sde.models import Constellation, Region
        from hostile.services.sov_engine import SovAnalysisEngine

        region_delve = Region.objects.create(id=10000060, name="Delve")
        const_1dq = Constellation.objects.create(id=20000720, name="OK-0IB", region=region_delve)
        self.system.constellation = const_1dq
        self.system.save()

        # Add structure with vulnerability hour
        struct = HostileStructure.objects.create(
            name="1DQ Imperial Palace",
            structure_type=self.astrahus,
            solar_system=self.system,
            alliance=self.alliance,
            state="ONLINE",
            core_status="FITTED",
            vulnerability_hour=19,
            vulnerability_window="16:00 - 22:00 UTC",
        )

        # Add active structure timer
        now = timezone.now()
        timer = StructureTimer.objects.create(
            structure=struct,
            solar_system=self.system,
            timer_type="ARMOR",
            timer_datetime=now + timedelta(hours=3),
        )

        # 1. Test hourly schedule groupings
        hourly_schedule = SovAnalysisEngine.get_alliance_hourly_schedule(self.alliance)
        self.assertTrue(len(hourly_schedule) >= 1)
        hour_19_group = next((h for h in hourly_schedule if h["hour"] == 19), None)
        self.assertIsNotNone(hour_19_group)
        self.assertEqual(hour_19_group["hour_display"], "19:00 UTC")
        self.assertTrue(any(item["name"] == "1DQ Imperial Palace" for item in hour_19_group["items"]))

        # 2. Test sovereignty intel by region
        dummy_sov_systems = [
            {
                "solar_system_id": 30004759,
                "claim": {
                    "alliance": {
                        "alliance_id": self.alliance.alliance_id,
                        "is_capital_system": True,
                        "sovereignty_hub": {
                            "id": 103000001,
                            "vulnerability_window": {
                                "start": "2026-10-02T19:00:00Z",
                                "end": "2026-10-02T23:00:00Z",
                            },
                        },
                        "development": {
                            "activity_defense_multiplier": 5.4,
                        },
                    }
                },
            }
        ]

        with patch("hostile.services.sov_engine.esi_client.get_sovereignty_systems", return_value=dummy_sov_systems), \
             patch("hostile.services.sov_engine.esi_client.get_sovereignty_structures", return_value=[]), \
             patch("hostile.services.sov_engine.esi_client.get_sovereignty_map", return_value=[]):
            sov_intel = SovAnalysisEngine.get_alliance_sovereignty_intel(self.alliance)

            self.assertEqual(sov_intel["total_systems_count"], 1)
            self.assertEqual(sov_intel["total_regions_count"], 1)
            self.assertEqual(sov_intel["avg_adm_overall"], 5.4)
            self.assertEqual(len(sov_intel["regions"]), 1)

            delve_region = sov_intel["regions"][0]
            self.assertEqual(delve_region["region_name"], "Delve")
            self.assertEqual(delve_region["systems_count"], 1)
            self.assertEqual(delve_region["avg_adm"], 5.4)

            sys_intel = delve_region["systems"][0]
            self.assertEqual(sys_intel["solar_system_name"], "1DQ1-A")
            self.assertTrue(sys_intel["is_capital"])
            self.assertEqual(sys_intel["adm"], 5.4)
            self.assertIn("19:00 - 23:00 UTC", sys_intel["vulnerability_window_display"])

            # 3. Test alliance dossier view rendering
            res = self.client.get(reverse("hostile:alliance_detail", args=[self.alliance.id]))
            self.assertEqual(res.status_code, 200)

            # Sovereignty tab and region details
            self.assertContains(res, "Sovereignty & Systems")
            self.assertContains(res, "Delve")
            self.assertContains(res, "1DQ1-A")
            self.assertContains(res, "Capital")
            self.assertContains(res, "5.4")
            self.assertContains(res, "19:00 - 23:00 UTC")

            # Clickable accordion schedule
            self.assertContains(res, "Structure Vulnerability & Timer Schedule")
            self.assertContains(res, "data-bs-target=\"#hourCollapse-19\"")
            self.assertContains(res, "1DQ Imperial Palace")
