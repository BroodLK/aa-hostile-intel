"""
Tests for Hostile Intelligence models and managers
"""

# Standard Library
from datetime import timedelta

# Django
from django.contrib.admin.sites import site
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

# Third Party
from eve_sde.models import ItemCategory, ItemGroup, ItemType, Region, SolarSystem

# AA Hostile Intel
from hostile.models import (
    DoctrineFit,
    HostileAlliance,
    HostileCorporation,
    HostileDoctrine,
    HostilePilotDossier,
    HostileStagingSystem,
    HostileStructure,
    IntelAuditLog,
    StructureFitting,
    StructureModule,
    StructureTimer,
    SystemObservation,
    SystemTag,
)
from hostile.services.sov_engine import SovAnalysisEngine


class TestHostileModels(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="intel_officer", password="password")
        cls.category = ItemCategory.objects.create(id=6, name="Ship", published=True)
        cls.group = ItemGroup.objects.create(id=25, name="Frigate", category=cls.category, published=True)
        cls.structure_group = ItemGroup.objects.create(id=1657, name="Citadel", category=cls.category, published=True)
        cls.module_group = ItemGroup.objects.create(
            id=1660, name="Structure Module", category=cls.category, published=True
        )

        cls.item_keepstar = ItemType.objects.create(
            id=35834, name="Keepstar", group=cls.structure_group, published=True
        )
        cls.item_gun = ItemType.objects.create(
            id=35920, name="Standup Heavy Energy Neutralizer I", group=cls.module_group, published=True
        )

        cls.region = Region.objects.create(id=10000002, name="The Forge")
        cls.system_jita = SolarSystem.objects.create(id=30000142, name="Jita", security_status=0.9)
        cls.system_perimeter = SolarSystem.objects.create(id=30000144, name="Perimeter", security_status=1.0)

        cls.alliance = HostileAlliance.objects.create(
            alliance_id=99000001,
            alliance_name="Hostile Alliance Core",
            ticker="HAC",
            primary_timezone="EUTZ",
            sov_timer_distribution={"18": 5, "19": 8},
        )
        cls.corporation = HostileCorporation.objects.create(
            corporation_id=98000001,
            corporation_name="Hostile Holding Corp",
            ticker="HHC",
            alliance=cls.alliance,
        )
        cls.tag_camp = SystemTag.objects.create(name="Gate Camp", color_class="danger")

    def test_alliance_and_corporation_str(self):
        self.assertEqual(str(self.alliance), "Hostile Alliance Core [HAC]")
        self.assertEqual(str(self.corporation), "Hostile Holding Corp [HHC]")

    def test_hostile_structure_and_fitting(self):
        structure = HostileStructure.objects.create(
            structure_id=1030000000001,
            name="1DQ1-A 1-HQ - Imperial Palace",
            structure_type=self.item_keepstar,
            solar_system=self.system_jita,
            corporation=self.corporation,
            alliance=self.alliance,
            owner_ticker="HAC",
            core_status="FITTED",
            state="ONLINE",
            created_by=self.user,
            is_verified=True,
        )
        self.assertIn("1DQ1-A 1-HQ - Imperial Palace", str(structure))
        self.assertEqual(HostileStructure.objects.verified().count(), 1)
        self.assertEqual(HostileStructure.objects.active().count(), 1)
        self.assertEqual(HostileStructure.objects.in_system(self.system_jita).count(), 1)
        self.assertEqual(HostileStructure.objects.by_alliance(self.alliance).count(), 1)

        fitting = StructureFitting.objects.create(
            structure=structure,
            name="Anti-Cap Fit",
            eft_format="[Keepstar, 1DQ1-A]",
            created_by=self.user,
        )
        module = StructureModule.objects.create(
            fitting=fitting,
            module_type=self.item_gun,
            slot_type="HIGH",
            slot_number=1,
            is_online=True,
        )
        self.assertEqual(structure.latest_fitting, fitting)
        self.assertIn("Standup Heavy Energy Neutralizer I", str(module))

    def test_structure_timer_queryset(self):
        future_time = timezone.now() + timedelta(days=2)
        past_time = timezone.now() - timedelta(days=2)

        timer_upcoming = StructureTimer.objects.create(
            solar_system=self.system_jita,
            timer_type="ARMOR",
            timer_datetime=future_time,
            created_by=self.user,
            is_verified=True,
        )
        timer_expired = StructureTimer.objects.create(
            solar_system=self.system_perimeter,
            timer_type="HULL",
            timer_datetime=past_time,
            created_by=self.user,
            is_verified=False,
        )

        self.assertIn(timer_upcoming, StructureTimer.objects.upcoming())
        self.assertNotIn(timer_expired, StructureTimer.objects.upcoming())
        self.assertIn(timer_expired, StructureTimer.objects.expired())
        self.assertEqual(StructureTimer.objects.verified().count(), 1)

    def test_system_observation_and_tags(self):
        obs = SystemObservation.objects.create(
            solar_system=self.system_jita,
            threat_level="EXTREME",
            observation_text="Smartbombing Rokh camp on 4-4 gate",
            active_hours="19:00 - 21:00 UTC",
            is_pinned=True,
            is_verified=True,
            created_by=self.user,
        )
        obs.tags.add(self.tag_camp)

        self.assertEqual(SystemObservation.objects.pinned().count(), 1)
        self.assertEqual(SystemObservation.objects.verified().count(), 1)
        self.assertEqual(SystemObservation.objects.high_threat().count(), 1)
        self.assertEqual(SystemObservation.objects.active_recent().count(), 1)

    def test_pilot_dossier_queryset(self):
        pilot_cyno = HostilePilotDossier.objects.create(
            character_id=2110000001,
            character_name="Cyno Alt 01",
            corporation_name="Hostile Holding Corp",
            alliance_name="Hostile Alliance Core",
            is_cyno_alt=True,
            created_by=self.user,
            is_verified=True,
        )
        pilot_super = HostilePilotDossier.objects.create(
            character_id=2110000002,
            character_name="Super Titan Pilot 01",
            corporation_name="Hostile Holding Corp",
            is_super_pilot=True,
            is_titan_pilot=True,
            is_fc=True,
            created_by=self.user,
            is_verified=True,
        )

        pilot_dread = HostilePilotDossier.objects.create(
            character_id=2110000003,
            character_name="Dread Pilot 01",
            corporation_name="Hostile Holding Corp",
            is_dread_pilot=True,
            created_by=self.user,
            is_verified=True,
        )
        pilot_fax = HostilePilotDossier.objects.create(
            character_id=2110000004,
            character_name="FAX Pilot 01",
            corporation_name="Hostile Holding Corp",
            is_fax_pilot=True,
            created_by=self.user,
            is_verified=True,
        )

        self.assertIn(pilot_cyno, HostilePilotDossier.objects.cynos())
        self.assertIn(pilot_super, HostilePilotDossier.objects.capitals())
        self.assertIn(pilot_dread, HostilePilotDossier.objects.capitals())
        self.assertIn(pilot_dread, HostilePilotDossier.objects.dreads())
        self.assertIn(pilot_fax, HostilePilotDossier.objects.capitals())
        self.assertIn(pilot_fax, HostilePilotDossier.objects.faxes())
        self.assertIn(pilot_super, HostilePilotDossier.objects.supers_and_titans())
        self.assertIn(pilot_super, HostilePilotDossier.objects.fcs())

    def test_audit_log(self):
        log = IntelAuditLog.objects.create(
            action="VERIFY",
            target_model="HostileStructure",
            target_id=1,
            target_repr="1DQ1-A 1-HQ",
            user=self.user,
            details="Verified structure location",
        )
        self.assertIn("intel_officer", str(log))

    def test_staging_system_and_doctrine_models(self):
        staging = HostileStagingSystem.objects.create(
            solar_system=self.system_jita,
            alliance=self.alliance,
            staging_type="PRIMARY",
            is_primary=True,
            max_subcap_form=180,
            max_cap_form=45,
            max_super_form=12,
            notes="Forward operating hub",
            created_by=self.user,
            is_verified=True,
        )
        self.assertIn("Jita", str(staging))
        self.assertEqual(staging.max_subcap_form, 180)
        self.assertEqual(staging.max_cap_form, 45)
        self.assertEqual(staging.max_super_form, 12)
        self.assertEqual(HostileStagingSystem.objects.verified().count(), 1)
        self.assertEqual(HostileStagingSystem.objects.primary().count(), 1)
        self.assertEqual(HostileStagingSystem.objects.by_alliance(self.alliance).count(), 1)

        doctrine = HostileDoctrine.objects.create(
            name="Armor Battleships (TFI)",
            alliance=self.alliance,
            role_type="MAIN_FLEET",
            is_general_doctrine=True,
            primary_ship_types="Tempest Fleet Issue, Guardian",
            description="Main battleship fleet",
            created_by=self.user,
            is_verified=True,
        )
        doctrine.stagings.add(staging)

        self.assertIn("Armor Battleships (TFI)", str(doctrine))
        self.assertEqual(HostileDoctrine.objects.verified().count(), 1)
        self.assertEqual(HostileDoctrine.objects.general().count(), 1)
        self.assertEqual(HostileDoctrine.objects.by_alliance(self.alliance).count(), 1)
        self.assertEqual(HostileDoctrine.objects.by_role("MAIN_FLEET").count(), 1)

        fit = DoctrineFit.objects.create(
            doctrine=doctrine,
            name="Main DPS TFI",
            role="DPS",
            zkill_link="https://zkillboard.com/kill/12345678/",
            eft_format="[Tempest Fleet Issue, Main DPS]\nStandup Heavy Energy Neutralizer I\n",
        )
        self.assertIn("Main DPS TFI", str(fit))

    def test_admin_registration(self):
        self.assertIn(HostileStructure, site._registry)
        self.assertIn(HostileAlliance, site._registry)
        self.assertIn(SystemObservation, site._registry)
        self.assertIn(HostilePilotDossier, site._registry)
        self.assertIn(HostileStagingSystem, site._registry)
        self.assertIn(HostileDoctrine, site._registry)
        self.assertIn(DoctrineFit, site._registry)

    def test_crowdsourced_vulnerability_windows_aggregation(self):
        new_alliance = HostileAlliance.objects.create(
            alliance_id=99000099,
            alliance_name="Vulnerability Test Alliance",
            ticker="VTA",
            primary_timezone="UNKNOWN",
        )
        HostileStructure.objects.create(
            structure_id=1030000000091,
            name="Fortizar Alpha",
            structure_type=self.item_keepstar,
            solar_system=self.system_jita,
            alliance=new_alliance,
            vulnerability_window="19:00 - 23:00 UTC",
            vulnerability_hour=19,
            created_by=self.user,
        )
        HostileStructure.objects.create(
            structure_id=1030000000092,
            name="Astrahus Beta",
            structure_type=self.item_keepstar,
            solar_system=self.system_jita,
            alliance=new_alliance,
            vulnerability_window="19:00 UTC",
            vulnerability_hour=19,
            created_by=self.user,
        )
        HostileStructure.objects.create(
            structure_id=1030000000093,
            name="Raitaru Gamma",
            structure_type=self.item_keepstar,
            solar_system=self.system_jita,
            alliance=new_alliance,
            vulnerability_window="20:00 - 24:00 UTC",
            vulnerability_hour=20,
            created_by=self.user,
        )

        dist = SovAnalysisEngine.aggregate_alliance_vulnerability_windows(new_alliance)
        new_alliance.refresh_from_db()
        self.assertEqual(dist.get("19"), 2)
        self.assertEqual(dist.get("20"), 1)
        self.assertEqual(new_alliance.sov_timer_distribution.get("19"), 2)
        self.assertEqual(new_alliance.primary_timezone, "EUTZ")
