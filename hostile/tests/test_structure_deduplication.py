"""
Unit and integration tests for Hostile Structure deduplication, Ansiblex normalization,
smart system-scoped matching, merge utilities, and management commands.
"""

# Standard Library
from datetime import timedelta

# Third Party
from eve_sde.models import ItemCategory, ItemGroup, ItemType, SolarSystem

# Django
from django.core.management import call_command
from django.test import Client, TestCase
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
    StructureFitting,
    StructureTimer,
    extract_core_structure_name,
    normalize_structure_name,
)
from hostile.services.intel_manager import IntelManager


class TestStructureDeduplication(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Permissions & users
        cls.user_officer = AuthUtils.create_user("officer_dedup")
        AuthUtils.add_main_character(cls.user_officer, "Officer Dedup Pilot", 21100099)
        AuthUtils.add_permission_to_user_by_name(
            "hostile.basic_access", cls.user_officer
        )
        AuthUtils.add_permission_to_user_by_name(
            "hostile.manage_intel", cls.user_officer
        )

        cls.user_scout = AuthUtils.create_user("scout_dedup")
        AuthUtils.add_main_character(cls.user_scout, "Scout Dedup Pilot", 21100098)
        AuthUtils.add_permission_to_user_by_name("hostile.basic_access", cls.user_scout)

        # SDE entities
        cls.category_structure = ItemCategory.objects.create(
            id=65, name="Structure", published=True
        )
        cls.group_citadel = ItemGroup.objects.create(
            id=1657, name="Citadel", category=cls.category_structure, published=True
        )
        cls.group_nav = ItemGroup.objects.create(
            id=1408,
            name="Navigation Structure",
            category=cls.category_structure,
            published=True,
        )
        cls.group_drill = ItemGroup.objects.create(
            id=4139, name="Moon Drill", category=cls.category_structure, published=True
        )

        cls.type_ansiblex = ItemType.objects.create(
            id=35841, name="Ansiblex Jump Gate", group=cls.group_nav, published=True
        )
        cls.type_astrahus = ItemType.objects.create(
            id=35832, name="Astrahus", group=cls.group_citadel, published=True
        )
        cls.type_metenox = ItemType.objects.create(
            id=81143, name="Metenox Moon Drill", group=cls.group_drill, published=True
        )
        cls.type_mercenary_den = ItemType.objects.create(
            id=81200, name="Mercenary Den", group=cls.group_citadel, published=True
        )

        cls.system_ayb = SolarSystem.objects.create(
            id=30004759, name="A-YB15", security_status=0.0
        )
        cls.system_zsp = SolarSystem.objects.create(
            id=30004760, name="ZS-PNI", security_status=0.0
        )
        cls.system_bnx = SolarSystem.objects.create(
            id=30004761, name="BNX-AS", security_status=0.0
        )

        cls.alliance_hostile = HostileAlliance.objects.create(
            alliance_id=99001,
            alliance_name="Hostile Coalition",
            ticker="HOSTL",
            is_friendly=False,
        )
        cls.corp_hostile = HostileCorporation.objects.create(
            corporation_id=98001,
            corporation_name="Hostile Corp",
            ticker="HCORP",
            alliance=cls.alliance_hostile,
            is_friendly=False,
        )

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.user_officer)

    def test_normalize_structure_name(self):
        """Tests that various arrow notations and whitespace variations normalize to standard unicode double chevron »"""
        self.assertEqual(
            normalize_structure_name("A-YB15 >> ZS-PNI - 6 to 7 jumps"),
            "A-YB15 » ZS-PNI - 6 to 7 jumps",
        )
        self.assertEqual(
            normalize_structure_name("A-YB15 -> ZS-PNI - 6 to 7 jumps"),
            "A-YB15 » ZS-PNI - 6 to 7 jumps",
        )
        self.assertEqual(
            normalize_structure_name("A-YB15 --> ZS-PNI"),
            "A-YB15 » ZS-PNI",
        )
        self.assertEqual(
            normalize_structure_name("A-YB15 — ZS-PNI"),
            "A-YB15 » ZS-PNI",
        )
        self.assertEqual(
            normalize_structure_name("A-YB15 – ZS-PNI"),
            "A-YB15 » ZS-PNI",
        )
        self.assertEqual(
            normalize_structure_name("  A-YB15   »   ZS-PNI   "),
            "A-YB15 » ZS-PNI",
        )
        self.assertEqual(
            normalize_structure_name("Standard Citadel Name"),
            "Standard Citadel Name",
        )
        self.assertEqual(normalize_structure_name(""), "")

    def test_extract_core_structure_name(self):
        """Tests core structure name extraction with bracketed ticker stripping and system suffix removal"""
        self.assertEqual(
            extract_core_structure_name("[CONDI] Imperial Palace"),
            "Imperial Palace",
        )
        self.assertEqual(
            extract_core_structure_name("[GOONS] - Factory Fort"),
            "Factory Fort",
        )
        self.assertEqual(
            extract_core_structure_name(
                "[INIT] Staging Citadel (1DQ1-A)", solar_system="1DQ1-A"
            ),
            "Staging Citadel",
        )
        self.assertEqual(
            extract_core_structure_name(
                "Mercenary Den (BNX-AS)", solar_system=self.system_bnx
            ),
            "Mercenary Den",
        )
        self.assertEqual(
            extract_core_structure_name("Astrahus"),
            "Astrahus",
        )
        self.assertEqual(extract_core_structure_name(""), "")

    def test_model_save_auto_normalizes_name(self):
        """Creating/saving a HostileStructure model automatically normalizes its name"""
        struct = HostileStructure.objects.create(
            name="A-YB15 >> ZS-PNI - 6 to 7 jumps",
            solar_system=self.system_ayb,
            structure_type=self.type_ansiblex,
        )
        self.assertEqual(struct.name, "A-YB15 » ZS-PNI - 6 to 7 jumps")

    def test_find_matching_ansiblex_and_generic_names(self):
        """QuerySet find_matching detects existing Ansiblex gates and generic fallback names in the same system"""
        # Create base structures
        gate = HostileStructure.objects.create(
            name="A-YB15 » ZS-PNI - 6 to 7 jumps",
            solar_system=self.system_ayb,
            structure_type=self.type_ansiblex,
        )
        den = HostileStructure.objects.create(
            name="Mercenary Den (BNX-AS)",
            solar_system=self.system_bnx,
            structure_type=self.type_mercenary_den,
        )

        # 1. Match Ansiblex by ASCII >> variation and reverse direction
        match_gate = HostileStructure.objects.find_matching(
            solar_system=self.system_ayb,
            name="A-YB15 >> ZS-PNI",
        )
        self.assertEqual(match_gate, gate)

        match_gate_reversed = HostileStructure.objects.find_matching(
            solar_system=self.system_ayb,
            name="ZS-PNI » A-YB15",
        )
        self.assertEqual(match_gate_reversed, gate)

        # 2. Match generic "Mercenary Den" or bracket-prefixed "[HOSTL] Mercenary Den" to existing "Mercenary Den (BNX-AS)"
        match_den = HostileStructure.objects.find_matching(
            solar_system=self.system_bnx,
            name="Mercenary Den",
            structure_type=self.type_mercenary_den,
        )
        self.assertEqual(match_den, den)

        match_den_bracket = HostileStructure.objects.find_matching(
            solar_system=self.system_bnx,
            name="[HOSTL] - Mercenary Den",
            structure_type=self.type_mercenary_den,
        )
        self.assertEqual(match_den_bracket, den)

        # 3. Unrelated custom name does not match generic den
        match_custom = HostileStructure.objects.find_matching(
            solar_system=self.system_bnx,
            name="BNX-AS - p8m18",
            structure_type=self.type_mercenary_den,
        )
        self.assertIsNone(match_custom)

        # 4. Different system should not match
        match_other_sys = HostileStructure.objects.find_matching(
            solar_system=self.system_ayb,
            name="Mercenary Den",
            structure_type=self.type_mercenary_den,
        )
        self.assertIsNone(match_other_sys)

    def test_get_or_create_structure_deduplicates_and_updates(self):
        """IntelManager.get_or_create_structure reuses existing matched structure and updates metadata"""
        struct, created = IntelManager.get_or_create_structure(
            name="A-YB15 >> ZS-PNI",
            solar_system=self.system_ayb,
            structure_type=self.type_ansiblex,
            user=self.user_officer,
        )
        self.assertTrue(created)
        self.assertEqual(struct.name, "A-YB15 » ZS-PNI")

        # Ingest again with full route notes and alliance
        struct_updated, created_again = IntelManager.get_or_create_structure(
            name="A-YB15 » ZS-PNI - 6 to 7 jumps",
            solar_system=self.system_ayb,
            structure_type=self.type_ansiblex,
            defaults={
                "alliance": self.alliance_hostile,
                "owner_ticker": "HOSTL",
                "vulnerability_hour": 18,
            },
            user=self.user_officer,
        )
        self.assertFalse(created_again)
        self.assertEqual(struct_updated.id, struct.id)
        self.assertEqual(struct_updated.alliance, self.alliance_hostile)
        self.assertEqual(struct_updated.owner_ticker, "HOSTL")
        self.assertEqual(struct_updated.vulnerability_hour, 18)

    def test_smart_paste_dscan_deduplication(self):
        """Pasting D-Scan with Ansiblex or generic deployable links to existing structure rather than duplicating"""
        HostileStructure.objects.create(
            name="A-YB15 » ZS-PNI - 6 to 7 jumps",
            solar_system=self.system_ayb,
            structure_type=self.type_ansiblex,
            created_by=self.user_officer,
        )

        dscan_text = "A-YB15 >> ZS-PNI - 6 to 7 jumps\tAnsiblex Jump Gate\t14,500 km\n"
        post_data = {
            "intel_text": dscan_text,
            "solar_system": self.system_ayb.id,
        }

        response = self.client.post(reverse("hostile:smart_paste"), data=post_data)
        self.assertRedirects(response, reverse("hostile:structures"))

        # Verify no duplicate gate created in A-YB15
        total_gates = HostileStructure.objects.filter(
            solar_system=self.system_ayb
        ).count()
        self.assertEqual(total_gates, 1)

    def test_merge_structures_utility(self):
        """IntelManager.merge_structures moves fittings, timers, and audit logs to primary structure and deletes duplicate"""
        primary = HostileStructure.objects.create(
            name="Primary Keepstar",
            solar_system=self.system_ayb,
            structure_type=self.type_astrahus,
            is_verified=True,
        )
        duplicate = HostileStructure.objects.create(
            name="Duplicate Keepstar",
            solar_system=self.system_ayb,
            structure_type=self.type_astrahus,
            alliance=self.alliance_hostile,
            corporation=self.corp_hostile,
            vulnerability_window="18:00 - 22:00 UTC",
            vulnerability_hour=19,
        )

        fit = StructureFitting.objects.create(
            structure=duplicate,
            name="Scanned Fit",
            eft_format="[Astrahus, fit]",
        )
        timer = StructureTimer.objects.create(
            structure=duplicate,
            solar_system=self.system_ayb,
            timer_type="HULL",
            timer_datetime=timezone.now() + timedelta(days=1),
        )
        audit = IntelAuditLog.objects.create(
            user=self.user_officer,
            action="CREATE",
            target_model="HostileStructure",
            target_id=str(duplicate.id),
        )

        merged_count = IntelManager.merge_structures(
            primary, [duplicate], user=self.user_officer
        )
        self.assertEqual(merged_count, 1)

        # Verify duplicate is deleted
        self.assertFalse(HostileStructure.objects.filter(id=duplicate.id).exists())

        # Verify metadata transferred to primary
        primary.refresh_from_db()
        self.assertEqual(primary.alliance, self.alliance_hostile)
        self.assertEqual(primary.corporation, self.corp_hostile)
        self.assertEqual(primary.vulnerability_hour, 19)

        # Verify child relations re-linked
        fit.refresh_from_db()
        self.assertEqual(fit.structure_id, primary.id)

        timer.refresh_from_db()
        self.assertEqual(timer.structure_id, primary.id)

        audit.refresh_from_db()
        self.assertEqual(audit.target_id, str(primary.id))

    def test_auto_deduplicate_all_and_management_command(self):
        """auto_deduplicate_all scans all systems, picks best primary, and management command executes cleanly"""
        # Create duplicates in BNX-AS (bracket-prefixed vs base name)
        primary_den = HostileStructure.objects.create(
            name="[HOSTL] Metenox Moon Drill (BNX-AS)",
            solar_system=self.system_bnx,
            structure_type=self.type_metenox,
            is_verified=True,
        )
        duplicate_den = HostileStructure.objects.create(
            name="Metenox Moon Drill",
            solar_system=self.system_bnx,
            structure_type=self.type_metenox,
            is_verified=False,
        )

        # Call management command
        call_command("hostile_deduplicate_structures")

        # Verify duplicate was merged into primary_den
        self.assertEqual(
            HostileStructure.objects.filter(solar_system=self.system_bnx).count(), 1
        )
        self.assertTrue(HostileStructure.objects.filter(id=primary_den.id).exists())
        self.assertFalse(HostileStructure.objects.filter(id=duplicate_den.id).exists())

    def test_distinct_custom_named_structures_not_auto_merged(self):
        """Distinct custom structures of identical hull type in same system are NOT merged"""
        s1 = HostileStructure.objects.create(
            name="BNX-AS - p8m18",
            solar_system=self.system_bnx,
            structure_type=self.type_metenox,
            is_verified=True,
        )
        s2 = HostileStructure.objects.create(
            name="BNX-AS - p4m2",
            solar_system=self.system_bnx,
            structure_type=self.type_metenox,
            is_verified=False,
        )
        res = IntelManager.auto_deduplicate_all()
        self.assertEqual(res["total_merged"], 0)
        self.assertEqual(
            HostileStructure.objects.filter(solar_system=self.system_bnx).count(), 2
        )
        self.assertTrue(HostileStructure.objects.filter(id=s1.id).exists())
        self.assertTrue(HostileStructure.objects.filter(id=s2.id).exists())

    def test_find_matching_drill_by_moon_location_despite_name(self):
        """A drill is matched by system + planet/moon even when the incoming name differs"""
        drill = HostileStructure.objects.create(
            name="BNX-AS P6M2",
            solar_system=self.system_bnx,
            structure_type=self.type_metenox,
        )
        other_moon = HostileStructure.objects.create(
            name="BNX-AS P9M10",
            solar_system=self.system_bnx,
            structure_type=self.type_metenox,
        )

        for name, location in [
            ("[HOSTL] Moon Money", "P6M2"),
            ("Metenox Moon Drill", "Planet 6 Moon 2"),
            ("BNX-AS - Ref Drill", "BNX-AS VI - Moon 2"),
            ("BNX-AS - p6m2 drill", ""),
        ]:
            with self.subTest(name=name, location=location):
                match = HostileStructure.objects.find_matching(
                    solar_system=self.system_bnx,
                    name=name,
                    structure_type=self.type_metenox,
                    location_details=location,
                )
                self.assertEqual(match, drill)

        # A drill on a different moon is never matched, even by generic hull name
        self.assertIsNone(
            HostileStructure.objects.find_matching(
                solar_system=self.system_bnx,
                name="Metenox Moon Drill",
                structure_type=self.type_metenox,
                location_details="P8M18",
            )
        )
        self.assertNotEqual(
            HostileStructure.objects.find_matching(
                solar_system=self.system_bnx,
                name="Moon Money",
                structure_type=self.type_metenox,
                location_details="P6M2",
            ),
            other_moon,
        )

    def test_timer_style_drill_ingest_reuses_existing_structure(self):
        """Timer sync ingest (different name + location_details) updates instead of duplicating"""
        drill = HostileStructure.objects.create(
            name="BNX-AS P9M10",
            solar_system=self.system_bnx,
            structure_type=self.type_metenox,
            is_verified=True,
        )
        struct, created = IntelManager.get_or_create_structure(
            name="BNX-AS - Metenox Moon Drill",
            solar_system=self.system_bnx,
            structure_type=self.type_metenox,
            defaults={
                "owner_ticker": "HOSTL",
                "location_details": "BNX-AS IX - Moon 10",
            },
        )
        self.assertFalse(created)
        self.assertEqual(struct.id, drill.id)
        self.assertEqual(
            HostileStructure.objects.filter(solar_system=self.system_bnx).count(), 1
        )

    def test_auto_deduplicate_merges_correlated_and_uncorrelated_drill(self):
        """A moon-correlated drill and an uncorrelated copy of the same moon are merged"""
        correlated = HostileStructure.objects.create(
            name="BNX-AS P8M18",
            solar_system=self.system_bnx,
            structure_type=self.type_metenox,
            moonmining_moon_id=40000001,
            is_verified=True,
        )
        copy = HostileStructure.objects.create(
            name="BNX-AS - Moon Drill",
            location_details="P8M18",
            solar_system=self.system_bnx,
            structure_type=self.type_metenox,
        )
        res = IntelManager.auto_deduplicate_all()
        self.assertEqual(res["total_merged"], 1)
        self.assertTrue(HostileStructure.objects.filter(id=correlated.id).exists())
        self.assertFalse(HostileStructure.objects.filter(id=copy.id).exists())

    def test_structure_auto_deduplicate_and_merge_views(self):
        """POST to structure_auto_deduplicate and structure_merge views requires manage_intel permission and merges properly"""
        # Create duplicates
        HostileStructure.objects.create(
            name="A-YB15 » ZS-PNI",
            solar_system=self.system_ayb,
            structure_type=self.type_ansiblex,
            is_verified=True,
        )
        HostileStructure.objects.create(
            name="A-YB15 >> ZS-PNI - 6 to 7 jumps",
            solar_system=self.system_ayb,
            structure_type=self.type_ansiblex,
            is_verified=False,
        )

        # 1. Test auto-deduplicate view
        response = self.client.post(reverse("hostile:structure_auto_deduplicate"))
        self.assertRedirects(response, reverse("hostile:structures"))
        self.assertEqual(
            HostileStructure.objects.filter(solar_system=self.system_ayb).count(), 1
        )

        # Create another pair to test manual merge view
        m1 = HostileStructure.objects.create(
            name="Metenox Alpha",
            solar_system=self.system_bnx,
            structure_type=self.type_metenox,
        )
        m2 = HostileStructure.objects.create(
            name="Metenox Beta",
            solar_system=self.system_bnx,
            structure_type=self.type_metenox,
        )

        response_merge = self.client.post(
            reverse("hostile:structure_merge"),
            data={
                "primary_structure_id": m1.id,
                "duplicate_structure_ids": [m2.id],
            },
        )
        self.assertRedirects(response_merge, reverse("hostile:structures"))
        self.assertFalse(HostileStructure.objects.filter(id=m2.id).exists())

    def test_non_officer_cannot_auto_deduplicate(self):
        """Scout without manage_intel permission cannot trigger structure deduplication"""
        self.client.force_login(self.user_scout)
        response = self.client.post(reverse("hostile:structure_auto_deduplicate"))
        self.assertEqual(response.status_code, 302)
        # Should redirect to permission denied or login
