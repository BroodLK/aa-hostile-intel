"""
Tests for Non-ESI Ingestion Parsers and Classifier
"""

# Django
from django.test import TestCase

# Third Party
from eve_sde.models import ItemCategory, ItemGroup, ItemType, SolarSystem

# AA Hostile Intel
from hostile.models import HostilePilotDossier, HostileStructure, StructureFitting, StructureModule
from hostile.parsers.classifier import IntelFormat, classify_intel_text, parse_intel_paste
from hostile.parsers.dscan import DScanParser
from hostile.parsers.eft import EFTParser, StructureFittingParser
from hostile.parsers.local import LocalThreatParser
from hostile.parsers.showinfo import StructureHackParser, StructureShowInfoParser
from hostile.services.threat_engine import ThreatEngine


class TestParsers(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category_structure = ItemCategory.objects.create(id=65, name="Structure", published=True)
        cls.category_module = ItemCategory.objects.create(id=7, name="Module", published=True)
        cls.category_ship = ItemCategory.objects.create(id=6, name="Ship", published=True)

        cls.group_citadel = ItemGroup.objects.create(
            id=1657, name="Citadel", category=cls.category_structure, published=True
        )
        cls.group_service = ItemGroup.objects.create(
            id=1322, name="Structure Service Module", category=cls.category_module, published=True
        )
        cls.group_battleship = ItemGroup.objects.create(
            id=27, name="Battleship", category=cls.category_ship, published=True
        )

        cls.keepstar = ItemType.objects.create(
            id=35834, name="Keepstar", group=cls.group_citadel, published=True
        )
        cls.fortizar = ItemType.objects.create(
            id=35833, name="Fortizar", group=cls.group_citadel, published=True
        )
        cls.rokh = ItemType.objects.create(
            id=638, name="Rokh", group=cls.group_battleship, published=True
        )
        cls.neut = ItemType.objects.create(
            id=35920, name="Standup Heavy Energy Neutralizer I", group=cls.group_citadel, published=True
        )
        cls.service_mod = ItemType.objects.create(
            id=35894, name="Standup Market Hub I", group=cls.group_service, published=True
        )
        cls.damage_control = ItemType.objects.create(
            id=35900, name="Standup Damage Control I", group=cls.group_citadel, published=True
        )
        cls.shield_extender = ItemType.objects.create(
            id=35910, name="Standup Variable Spectrum ECM I", group=cls.group_citadel, published=True
        )
        cls.mobile_depot = ItemType.objects.create(
            id=33474, name="Mobile Depot", published=True
        )

        # User example ship scanner structure fitting modules
        cls.standup_launcher = ItemType.objects.create(
            id=35930, name="Standup Anticapital Missile Launcher I", group=cls.group_citadel, published=True
        )
        cls.standup_xl_neut = ItemType.objects.create(
            id=35931, name="Standup XL Energy Neutralizer I", group=cls.group_citadel, published=True
        )
        cls.standup_point = ItemType.objects.create(
            id=35932, name="Standup Focused Warp Disruptor I", group=cls.group_citadel, published=True
        )
        cls.standup_tp = ItemType.objects.create(
            id=35933, name="Standup Target Painter I", group=cls.group_citadel, published=True
        )
        cls.standup_bcs = ItemType.objects.create(
            id=35934, name="Standup Ballistic Control System I", group=cls.group_citadel, published=True
        )
        cls.standup_mge = ItemType.objects.create(
            id=35935, name="Standup Missile Guidance Enhancer I", group=cls.group_citadel, published=True
        )
        cls.standup_clone = ItemType.objects.create(
            id=35936, name="Standup Cloning Center I", group=cls.group_service, published=True
        )

        cls.system_1dq = SolarSystem.objects.create(id=30004759, name="1DQ1-A", security_status=-0.5)
        cls.system_f9 = SolarSystem.objects.create(id=30001234, name="F9-FUV", security_status=-0.2)

    def test_dscan_parser(self):
        dscan_sample = (
            "35834\t1DQ1-A 1-HQ - Imperial Palace [CONDI]\tKeepstar\t14.2 AU\n"
            "638\tGoon Rokh 1\tRokh\t2,500 km\n"
            "638\tGoon Rokh 2\tRokh\t2,500 km\n"
        )
        res = DScanParser.parse(dscan_sample)
        self.assertEqual(len(res["structures"]), 1)
        self.assertEqual(res["structures"][0]["structure_type"], self.keepstar)
        self.assertEqual(res["structures"][0]["owner_ticker"], "CONDI")
        self.assertEqual(res["ship_counts"]["Rokh"], 2)
        self.assertEqual(res["solar_system"], self.system_1dq)

    def test_showinfo_parser(self):
        showinfo_sample = (
            "[Fortizar] Staging Citadel [TEST]\n"
            "Owner: Test Alliance Please Ignore\n"
            "Solar System: 1DQ1-A\n"
            "State: Online\n"
            "Quantum Core: Installed\n"
            "Vulnerability Window: 18:00 - 22:00 UTC\n"
            "Fitting:\n"
            "Standup Heavy Energy Neutralizer I\n"
            "Standup Market Hub I\n"
        )
        res = StructureShowInfoParser.parse(showinfo_sample)
        self.assertEqual(res["structure_type"], self.fortizar)
        self.assertEqual(res["solar_system"], self.system_1dq)
        self.assertEqual(res["owner_ticker"], "TEST")
        self.assertEqual(res["core_status"], "FITTED")
        self.assertEqual(res["state"], "ONLINE")
        self.assertEqual(res["vulnerability_window"], "18:00 - 22:00 UTC")
        self.assertEqual(res["vulnerability_hour"], 18)
        self.assertIsNotNone(res["fitting_data"])
        self.assertEqual(len(res["fitting_lines"]), 2)

    def test_eft_parser_and_export(self):
        eft_sample = (
            "[Keepstar, 1DQ1-A 1-HQ]\n"
            "Standup Damage Control I\n"
            "Standup Variable Spectrum ECM I\n"
            "Standup Heavy Energy Neutralizer I\n"
            "Standup Market Hub I\n"
        )
        parsed = EFTParser.parse(eft_sample)
        self.assertEqual(parsed["hull_item_type"], self.keepstar)
        self.assertEqual(parsed["fitting_name"], "1DQ1-A 1-HQ")
        self.assertEqual(len(parsed["slots"]["LOW"]), 1)
        self.assertEqual(len(parsed["slots"]["MED"]), 1)
        self.assertEqual(len(parsed["slots"]["HIGH"]), 1)
        self.assertEqual(len(parsed["slots"]["SERVICE"]), 1)

        # Test export
        struct = HostileStructure.objects.create(
            name="1DQ1-A 1-HQ",
            structure_type=self.keepstar,
            solar_system=self.system_1dq,
        )
        fitting = StructureFitting.objects.create(
            structure=struct,
            name="Primary Defense",
            eft_format=eft_sample,
        )
        StructureModule.objects.create(
            fitting=fitting,
            module_type=self.neut,
            slot_type="HIGH",
            slot_number=1,
        )
        StructureModule.objects.create(
            fitting=fitting,
            module_type=self.service_mod,
            slot_type="SERVICE",
            slot_number=1,
        )
        exported = EFTParser.export_eft(fitting)
        self.assertIn("[Keepstar, Primary Defense]", exported)
        self.assertIn("Standup Heavy Energy Neutralizer I", exported)
        self.assertIn("Standup Market Hub I", exported)

    def test_classifier(self):
        dscan_text = "35834\t1DQ1-A Citadel\tKeepstar\t150 km"
        self.assertEqual(classify_intel_text(dscan_text), IntelFormat.DSCAN)

        eft_text = "[Keepstar, 1DQ1-A]\nStandup Heavy Energy Neutralizer I"
        self.assertEqual(classify_intel_text(eft_text), IntelFormat.EFT)

        showinfo_text = "Owner: Goonswarm\nSolar System: 1DQ1-A\nQuantum Core: Installed"
        self.assertEqual(classify_intel_text(showinfo_text), IntelFormat.SHOWINFO)

        obs_text = "Hostile fleet roaming with 20 Kikimoras on gate"
        self.assertEqual(classify_intel_text(obs_text), IntelFormat.OBSERVATION)

        local_text = "[ 10:48:00 ] CynoAlt > cyno up on gate"
        self.assertEqual(classify_intel_text(local_text), IntelFormat.LOCAL)

        parsed_paste = parse_intel_paste(dscan_text)
        self.assertEqual(parsed_paste["format"], IntelFormat.DSCAN)
        self.assertEqual(len(parsed_paste["data"]["structures"]), 1)

    def test_local_threat_parser_and_drop_chance(self):
        # Create dossiers for known cyno alt and capital pilot
        HostilePilotDossier.objects.create(
            character_id=99001,
            character_name="HostileCynoAlt",
            is_cyno_alt=True,
            alliance_name="Hostile Alliance",
        )
        HostilePilotDossier.objects.create(
            character_id=99002,
            character_name="HostileSuperPilot",
            is_super_pilot=True,
            is_capital_pilot=True,
            alliance_name="Hostile Alliance",
        )

        chat_log = (
            "Channel Name: Local : 1DQ1-A\n"
            "[ 2026.10.01 18:20:00 ] HostileCynoAlt > cyno lit\n"
            "[ 2026.10.01 18:22:00 ] HostileSuperPilot > jumped\n"
            "[ 2026.10.01 18:25:00 ] RandomScout > tackle on gate\n"
        )
        dscan_sample = (
            "11993\tHostile Arazu\tArazu\t15 km\n"
            "23773\tHostile Hel\tHel\t25 km\n"
        )

        res = LocalThreatParser.parse(chat_log, dscan_text=dscan_sample, default_system=self.system_1dq)
        self.assertEqual(res["pilot_count"], 3)
        self.assertEqual(res["cynos_count"], 1)
        self.assertEqual(res["supers_count"], 1)
        self.assertGreaterEqual(res["blops_drop_chance"], 50)
        self.assertGreaterEqual(res["cap_drop_chance"], 75)
        self.assertEqual(res["threat_level"], "CRITICAL")
        self.assertEqual(res["activity_profile"]["primary_timezone"], "EUTZ")

        # Verify pilot paired to D-Scan ship
        super_entry = next((p for p in res["pilots"] if p["character_name"] == "HostileSuperPilot"), None)
        self.assertIsNotNone(super_entry)
        self.assertIn("Hel", super_entry["likely_ship"])

    def test_helios_and_onyx_not_classified_as_supers(self):
        """Verifies Helios (covert ops frigate) and Onyx (HIC) are not misclassified as supercapitals (Hel/Nyx)"""
        dscan_sample = (
            "11188\tScout Helios\tHelios\t100 km\n"
            "12017\tTackle Onyx\tOnyx\t50 km\n"
        )
        parsed_dscan = DScanParser.parse(dscan_sample)
        profile = ThreatEngine.categorize_dscan_ships(parsed_dscan)
        self.assertEqual(len(profile["supers"]), 0)
        self.assertEqual(len(profile["recons"]), 1)
        self.assertEqual(profile["recons"][0]["type_name"], "Helios")
        self.assertEqual(len(profile["bubbles"]), 1)
        self.assertEqual(profile["bubbles"][0]["type_name"], "Onyx")

    def test_ignored_friendly_alliance_in_local_parser(self):
        with self.settings(HOSTILE_IGNORED_ALLIANCE_NAMES=["Friendly Alliance"]):
            local_paste = (
                "FriendlyPilot\tFriendly Corp\tFriendly Alliance\n"
                "HostilePilot\tHostile Corp\tHostile Alliance\n"
            )
            res = LocalThreatParser.parse(local_paste)
            self.assertEqual(res["pilot_count"], 1)
            self.assertEqual(res["ignored_count"], 1)
            self.assertEqual(res["pilots"][0]["character_name"], "HostilePilot")

    def test_dscan_infer_solar_system_user_example(self):
        """Tests the user-provided D-Scan example inferring F9-FUV system name from Keepstar line"""
        dscan_sample = (
            "35834\tF9-FUV - ICS Ackaroth We Will Miss You\tKeepstar\t0 m\n"
            "33474\t<FIGL Roadhouse> Cold Beer / Live Entertainment >>>>>\tMobile Depot\t828 km\n"
            "33474\tJoin Southern Cross Monopoly {FIGL} Flying Dangerous! Actively Recruiting!\tMobile Depot\t878 km\n"
        )
        res = DScanParser.parse(dscan_sample)
        self.assertEqual(res["solar_system"], self.system_f9)
        self.assertEqual(len(res["structures"]), 1)
        self.assertEqual(res["structures"][0]["structure_type"], self.keepstar)
        self.assertEqual(res["structures"][0]["solar_system"], self.system_f9)

    def test_dscan_infer_solar_system_variations(self):
        """Tests various structure and celestial naming formats"""
        self.assertEqual(
            DScanParser.infer_solar_system_from_name("F9-FUV - Fortizar Primary"),
            self.system_f9,
        )
        self.assertEqual(
            DScanParser.infer_solar_system_from_name("[TICKER] 1DQ1-A - Keepstar"),
            self.system_1dq,
        )
        self.assertEqual(
            DScanParser.infer_solar_system_from_name("Stargate (1DQ1-A to T5ZI-S)"),
            self.system_1dq,
        )
        self.assertEqual(
            DScanParser.infer_solar_system_from_name("POCO (F9-FUV VI)"),
            self.system_f9,
        )
        self.assertEqual(
            DScanParser.infer_solar_system_from_name("1DQ1-A I - Moon 1"),
            self.system_1dq,
        )

    def test_solar_system_choice_field_no_id_display(self):
        from hostile.forms import SolarSystemChoiceField

        field = SolarSystemChoiceField()
        self.assertEqual(field.label_from_instance(self.system_f9), "F9-FUV")
        self.assertEqual(field.label_from_instance(self.system_1dq), "1DQ1-A")
        self.assertNotIn(str(self.system_f9.id), field.label_from_instance(self.system_f9))

    def test_structure_fitting_ship_scanner_user_example(self):
        """Tests parsing of the exact structure fitting format produced by ship scanners on structures"""
        scan_text = (
            "High Power Slots\n"
            "Standup Anticapital Missile Launcher I\n"
            "Standup Anticapital Missile Launcher I\n"
            "Standup XL Energy Neutralizer I\n"
            "Standup XL Energy Neutralizer I\n"
            "Medium Power Slots\n"
            "Standup Focused Warp Disruptor I\n"
            "Standup Target Painter I\n"
            "Standup Variable Spectrum ECM I\n"
            "Standup Variable Spectrum ECM I\n"
            "Low Power Slots\n"
            "Standup Ballistic Control System I\n"
            "Standup Ballistic Control System I\n"
            "Standup Missile Guidance Enhancer I\n"
            "Service Slots\n"
            "Standup Cloning Center I\n"
        )

        # Classification check
        self.assertEqual(classify_intel_text(scan_text), IntelFormat.STRUCTURE_FIT)

        # Parsing check
        parsed = StructureFittingParser.parse(scan_text)
        self.assertEqual(len(parsed["slots"]["HIGH"]), 4)
        self.assertEqual(len(parsed["slots"]["MED"]), 4)
        self.assertEqual(len(parsed["slots"]["LOW"]), 3)
        self.assertEqual(len(parsed["slots"]["SERVICE"]), 1)

        high_names = [m["module_name"] for m in parsed["slots"]["HIGH"]]
        self.assertEqual(high_names.count("Standup Anticapital Missile Launcher I"), 2)
        self.assertEqual(high_names.count("Standup XL Energy Neutralizer I"), 2)

        med_names = [m["module_name"] for m in parsed["slots"]["MED"]]
        self.assertIn("Standup Focused Warp Disruptor I", med_names)
        self.assertIn("Standup Target Painter I", med_names)
        self.assertEqual(med_names.count("Standup Variable Spectrum ECM I"), 2)

        low_names = [m["module_name"] for m in parsed["slots"]["LOW"]]
        self.assertEqual(low_names.count("Standup Ballistic Control System I"), 2)
        self.assertIn("Standup Missile Guidance Enhancer I", low_names)

        service_names = [m["module_name"] for m in parsed["slots"]["SERVICE"]]
        self.assertIn("Standup Cloning Center I", service_names)

    def test_structure_fitting_ship_scanner_variations_and_quantities(self):
        """Tests ship scanner format with quantities, scripts, and alternative header casings"""
        scan_text = (
            "High Power Slots:\n"
            "2x Standup Anticapital Missile Launcher I\n"
            "Standup XL Energy Neutralizer I x2\n"
            "Mid Slots\n"
            "Standup Focused Warp Disruptor I [Focused Warp Disruptor Script]\n"
            "[Empty Medium slot]\n"
            "Low Slots\n"
            "Standup Ballistic Control System I (2)\n"
            "Structure Service Slots\n"
            "Standup Cloning Center I\n"
        )
        parsed = StructureFittingParser.parse(scan_text)
        self.assertEqual(len(parsed["slots"]["HIGH"]), 4)
        self.assertEqual(len(parsed["slots"]["MED"]), 1)
        self.assertEqual(len(parsed["slots"]["LOW"]), 2)
        self.assertEqual(len(parsed["slots"]["SERVICE"]), 1)
        self.assertEqual(len(parsed["slots"]["CHARGE"]), 1)
        self.assertEqual(parsed["slots"]["CHARGE"][0]["module_name"], "Focused Warp Disruptor Script")

    def test_structure_hack_user_example(self):
        """Tests the exact Data Analyzer structure timer hack format provided by the user"""
        hack_sample = "F9-FUV - Darkside X\nHour (+- 3 hrs):\t02:00"

        # Classification check
        self.assertEqual(classify_intel_text(hack_sample), IntelFormat.STRUCTURE_HACK)
        self.assertTrue(StructureHackParser.is_structure_hack(hack_sample))

        # Parsing check
        res = StructureHackParser.parse(hack_sample)
        self.assertEqual(res["name"], "F9-FUV - Darkside X")
        self.assertEqual(res["solar_system"], self.system_f9)
        self.assertEqual(res["vulnerability_hour"], 2)
        self.assertEqual(res["vulnerability_window"], "02:00 (+- 3 hrs)")
        self.assertEqual(res["source"], "DATA_ANALYZER_HACK")

        # Unified parser check
        unified = parse_intel_paste(hack_sample)
        self.assertEqual(unified["format"], IntelFormat.STRUCTURE_HACK)
        self.assertEqual(unified["data"]["vulnerability_hour"], 2)
        self.assertEqual(unified["data"]["solar_system"], self.system_f9)

    def test_structure_hack_variations_and_formats(self):
        """Tests various syntax forms for Data Analyzer hack readouts"""
        # Variation with bracketed hull type and ticker
        v1 = "[Keepstar] 1DQ1-A 1-HQ [CONDI]\nHour (+- 3 hrs): 18:00"
        res1 = StructureHackParser.parse(v1)
        self.assertEqual(res1["name"], "1DQ1-A 1-HQ [CONDI]")
        self.assertEqual(res1["solar_system"], self.system_1dq)
        self.assertEqual(res1["owner_ticker"], "CONDI")
        self.assertEqual(res1["vulnerability_hour"], 18)
        self.assertEqual(res1["vulnerability_window"], "18:00 (+- 3 hrs)")
        self.assertEqual(res1["structure_type"], self.keepstar)

        # Variation with alternative jitter notations (+/- 3 hrs and ± 3 hrs)
        v2 = "Darkside Astrahus\nHour (+/- 3 hrs): 05:00"
        res2 = StructureHackParser.parse(v2)
        self.assertEqual(res2["name"], "Darkside Astrahus")
        self.assertEqual(res2["vulnerability_hour"], 5)
        self.assertIn("05:00", res2["vulnerability_window"])

        v3 = "POCO (F9-FUV VI)\nHour (± 3 hrs): 21:00"
        res3 = StructureHackParser.parse(v3)
        self.assertEqual(res3["solar_system"], self.system_f9)
        self.assertEqual(res3["vulnerability_hour"], 21)

    def test_local_threat_parser_bulk_resolution_and_combat_intel(self):
        """Tests that local threat scanner extracts character affiliation, combat stats, danger %, and top ships"""
        from unittest.mock import patch

        local_paste = (
            "April Springtime\n"
            "B1cBrute Cyno\n"
            "B1cBrute\n"
            "Montaire\n"
        )

        mock_resolved = {
            "april springtime": {
                "character_id": 91001,
                "character_name": "April Springtime",
                "corporation_id": 2001,
                "corporation_name": "Springtime Corp",
                "corporation_ticker": "SPRG",
                "alliance_id": 3001,
                "alliance_name": "Spring Alliance",
                "alliance_ticker": "SPRA",
            },
            "b1cbrute cyno": {
                "character_id": 91002,
                "character_name": "B1cBrute Cyno",
                "corporation_id": 2002,
                "corporation_name": "Brute Force Inc",
                "corporation_ticker": "BRUT",
                "alliance_id": 3002,
                "alliance_name": "Brutal Alliance",
                "alliance_ticker": "BRTA",
            },
            "b1cbrute": {
                "character_id": 91003,
                "character_name": "B1cBrute",
                "corporation_id": 2002,
                "corporation_name": "Brute Force Inc",
                "corporation_ticker": "BRUT",
                "alliance_id": 3002,
                "alliance_name": "Brutal Alliance",
                "alliance_ticker": "BRTA",
            },
            "montaire": {
                "character_id": 91004,
                "character_name": "Montaire",
                "corporation_id": 2003,
                "corporation_name": "Montaire Vanguard",
                "corporation_ticker": "MNTR",
                "alliance_id": None,
                "alliance_name": "",
                "alliance_ticker": "",
            },
        }

        def mock_combat_intel(cid):
            if cid == 91001:
                return {
                    "danger_ratio": 92,
                    "kills_count": 1420,
                    "losses_count": 112,
                    "solo_kills": 240,
                    "top_ships": ["Redeemer", "Widow", "Loki"],
                    "is_blops_pilot": True,
                    "is_fc": True,
                    "fc_level": "HIGH",
                    "fc_score": 105,
                }
            elif cid == 91002:
                return {
                    "danger_ratio": 10,
                    "kills_count": 5,
                    "losses_count": 45,
                    "top_ships": ["Arazu", "Falcon"],
                    "is_cyno_alt": True,
                    "cyno_count": 12,
                }
            elif cid == 91003:
                return {
                    "danger_ratio": 85,
                    "kills_count": 850,
                    "losses_count": 90,
                    "top_ships": ["Revelation", "Nagfar"],
                    "is_dread_pilot": True,
                    "is_capital_pilot": True,
                }
            return {
                "danger_ratio": 45,
                "kills_count": 50,
                "losses_count": 60,
                "top_ships": ["Caracal"],
            }

        with patch("hostile.services.entity_resolver.EntityResolver.resolve_bulk_characters", return_value=mock_resolved):
            with patch("hostile.services.zkill_client.zkill_client.get_character_combat_intelligence", side_effect=mock_combat_intel):
                res = LocalThreatParser.parse(local_paste, default_system=self.system_f9)

        self.assertEqual(res["pilot_count"], 4)
        april = next(p for p in res["pilots"] if p["character_name"] == "April Springtime")
        self.assertEqual(april["corporation_name"], "Springtime Corp")
        self.assertEqual(april["alliance_name"], "Spring Alliance")
        self.assertEqual(april["danger_ratio"], 92)
        self.assertEqual(april["kills_count"], 1420)
        self.assertTrue(april["is_blops_pilot"])
        self.assertTrue(april["is_fc"])
        self.assertEqual(april["fc_level"], "HIGH")
        self.assertIn("Redeemer", april["top_ships"])

        cyno_alt = next(p for p in res["pilots"] if p["character_name"] == "B1cBrute Cyno")
        self.assertTrue(cyno_alt["is_cyno_alt"])
        self.assertTrue(cyno_alt["is_alt"])
        self.assertEqual(cyno_alt["main_character_name"], "B1cBrute")

        dread_pilot = next(p for p in res["pilots"] if p["character_name"] == "B1cBrute")
        self.assertTrue(dread_pilot["is_dread_pilot"])
        self.assertTrue(dread_pilot["is_capital_pilot"])
        self.assertIn("Revelation", dread_pilot["top_ships"])

    def test_local_threat_parser_fast_mode_and_enrich_pilot_and_scan(self):
        """Tests that fast parsing skips synchronous zKill calls and enrich_scan dynamically enriches pilots"""
        from unittest.mock import patch
        from hostile.models import LocalThreatScan

        local_paste = "April Springtime\nCynoDropper"

        mock_resolved = {
            "april springtime": {
                "character_id": 91001,
                "character_name": "April Springtime",
                "corporation_id": 2001,
                "corporation_name": "Springtime Corp",
                "corporation_ticker": "SPRG",
                "alliance_id": 3001,
                "alliance_name": "Spring Alliance",
                "alliance_ticker": "SPRA",
            },
            "cynodropper": {
                "character_id": 91002,
                "character_name": "CynoDropper",
                "corporation_id": 2002,
                "corporation_name": "Brute Force Inc",
                "corporation_ticker": "BRUT",
                "alliance_id": 3002,
                "alliance_name": "Brutal Alliance",
                "alliance_ticker": "BRTA",
            },
        }

        # 1. Fast parse (fetch_zkill=False)
        with patch("hostile.services.entity_resolver.EntityResolver.resolve_bulk_characters", return_value=mock_resolved):
            res_fast = LocalThreatParser.parse(local_paste, default_system=self.system_f9, fetch_zkill=False)

        self.assertEqual(res_fast["pilot_count"], 2)
        self.assertFalse(res_fast["pilots"][0].get("zkill_synced"))
        self.assertFalse(res_fast["pilots"][1].get("zkill_synced"))

        # 2. Enrich pilot directly
        def mock_combat_intel(cid):
            if cid == 91001:
                return {
                    "danger_ratio": 92,
                    "kills_count": 1420,
                    "losses_count": 112,
                    "solo_kills": 240,
                    "top_ships": ["Redeemer", "Widow"],
                    "is_blops_pilot": True,
                    "is_fc": True,
                    "fc_level": "HIGH",
                    "fc_score": 105,
                    "activity": {"18": 150, "19": 200, "20": 180},
                }
            return {
                "danger_ratio": 15,
                "kills_count": 5,
                "losses_count": 30,
                "top_ships": ["Arazu"],
                "is_cyno_alt": True,
                "cyno_count": 10,
                "activity": {"19": 40, "20": 60},
            }

        with patch("hostile.services.entity_resolver.EntityResolver.resolve_character", side_effect=lambda q: mock_resolved.get(str(q).lower() if not isinstance(q, int) else ("april springtime" if q == 91001 else "cynodropper"))):
            with patch("hostile.services.zkill_client.zkill_client.get_character_combat_intelligence", side_effect=mock_combat_intel):
                pilot_enriched = LocalThreatParser.enrich_pilot(dict(res_fast["pilots"][0]))
                self.assertTrue(pilot_enriched["zkill_synced"])
                self.assertEqual(pilot_enriched["danger_ratio"], 92)
                self.assertTrue(pilot_enriched["is_blops_pilot"])
                self.assertIn("Redeemer", pilot_enriched["top_ships"])
                self.assertEqual(pilot_enriched["activity"]["18"], 150)

                # 3. Enrich scan
                scan = LocalThreatScan.objects.create(
                    solar_system=self.system_f9,
                    raw_local_text=local_paste,
                    pilot_count=2,
                    hostile_count=2,
                    blops_drop_chance=0,
                    cap_drop_chance=0,
                    threat_level="LOW",
                    pilots_data=res_fast["pilots"],
                )

                enrich_result = LocalThreatParser.enrich_scan(scan, batch_size=5)
                self.assertTrue(enrich_result["success"])
                self.assertEqual(enrich_result["synced_count"], 2)
                self.assertTrue(enrich_result["is_finished"])
                self.assertGreater(enrich_result["blops_drop_chance"], 0)
                self.assertEqual(enrich_result["activity_profile"]["primary_timezone"], "EUTZ")
                self.assertEqual(enrich_result["activity_profile"]["tz_breakdown"]["EUTZ"], 630)
                self.assertEqual(enrich_result["activity_profile"]["total_events"], 630)

                scan.refresh_from_db()
                self.assertGreater(scan.blops_drop_chance, 0)
                self.assertEqual(enrich_result["cynos_count"], 1)
                self.assertEqual(enrich_result["blops_count"], 1)
                self.assertEqual(scan.activity_profile["primary_timezone"], "EUTZ")

    def test_local_threat_activity_profile_aggregation(self):
        """Tests activity profile calculation across combat activity and timezones"""
        pilots = [
            {
                "character_name": "Pilot USTZ",
                "activity": {"1": 10, "2": 25, "3": 15},  # USTZ
            },
            {
                "character_name": "Pilot EUTZ",
                "activity": {"18": 100, "19": 150},  # EUTZ
            },
        ]
        profile = LocalThreatParser.calculate_activity_profile(pilots)
        self.assertEqual(profile["primary_timezone"], "EUTZ")
        self.assertEqual(profile["tz_breakdown"]["EUTZ"], 250)
        self.assertEqual(profile["tz_breakdown"]["USTZ"], 50)
        self.assertEqual(profile["tz_breakdown"]["AUTZ"], 0)
        self.assertEqual(profile["total_events"], 300)
