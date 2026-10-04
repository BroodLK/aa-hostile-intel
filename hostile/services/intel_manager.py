"""
Trust management, intel verification, auditing, and pilot dossier role tagging
"""

# Standard Library
import re
from typing import Any, Optional

# Django
from django.contrib.auth.models import User
from django.db import transaction

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
)


class IntelManager:
    """Handles operational moderation, verification lifecycle, audit logs, and automatic role tagging"""

    @classmethod
    def record_audit(
        cls,
        user: Optional[User],
        action: str,
        target: Any,
        details: str = "",
    ) -> IntelAuditLog:
        """Records an action in the immutable IntelAuditLog"""
        target_model = target.__class__.__name__ if target else "Unknown"
        raw_id = getattr(target, "id", None) or getattr(target, "pk", None)
        target_id = str(raw_id) if raw_id is not None else None
        target_repr = str(target)[:254] if target else ""

        return IntelAuditLog.objects.create(
            user=user,
            action=action.upper(),
            target_model=target_model,
            target_id=target_id,
            target_repr=target_repr,
            details=details,
        )

    @classmethod
    def verify_structure(cls, structure: HostileStructure, user: User) -> HostileStructure:
        """Marks a structure report as verified by an intelligence officer"""
        structure.is_verified = True
        structure.save(update_fields=["is_verified", "updated_at"])
        cls.record_audit(user, "VERIFY", structure, f"Structure '{structure.name}' verified.")
        return structure

    @classmethod
    def verify_observation(cls, observation: SystemObservation, user: User) -> SystemObservation:
        """Marks a system observation / gate camp report as verified"""
        observation.is_verified = True
        observation.save(update_fields=["is_verified", "updated_at"])
        cls.record_audit(user, "VERIFY", observation, f"Observation in {observation.solar_system.name} verified.")
        return observation

    @classmethod
    def toggle_pin_observation(cls, observation: SystemObservation, user: User) -> SystemObservation:
        """Pins or unpins an observation to the intel dashboard summary"""
        observation.is_pinned = not observation.is_pinned
        observation.save(update_fields=["is_pinned", "updated_at"])
        action = "PIN" if observation.is_pinned else "UNPIN"
        cls.record_audit(
            user, action, observation, f"Observation in {observation.solar_system.name} pin state changed."
        )
        return observation

    @classmethod
    def verify_timer(cls, timer: StructureTimer, user: User) -> StructureTimer:
        """Marks a structure / sov timer as verified"""
        timer.is_verified = True
        timer.save(update_fields=["is_verified", "updated_at"])
        cls.record_audit(user, "VERIFY", timer, f"Timer {timer.timer_type} verified.")
        return timer

    @classmethod
    def verify_pilot(cls, pilot: HostilePilotDossier, user: User) -> HostilePilotDossier:
        """Marks a hostile pilot dossier as verified"""
        pilot.is_verified = True
        pilot.save(update_fields=["is_verified", "updated_at"])
        cls.record_audit(user, "VERIFY", pilot, f"Pilot dossier {pilot.character_name} verified.")
        return pilot

    @classmethod
    def verify_staging(cls, staging: HostileStagingSystem, user: User) -> HostileStagingSystem:
        """Marks a hostile staging system report as verified"""
        staging.is_verified = True
        staging.save(update_fields=["is_verified", "updated_at"])
        cls.record_audit(user, "VERIFY", staging, f"Staging system {staging.solar_system.name} verified.")
        return staging

    @classmethod
    def verify_doctrine(cls, doctrine: HostileDoctrine, user: User) -> HostileDoctrine:
        """Marks a hostile doctrine report as verified"""
        doctrine.is_verified = True
        doctrine.save(update_fields=["is_verified", "updated_at"])
        cls.record_audit(user, "VERIFY", doctrine, f"Doctrine '{doctrine.name}' verified.")
        return doctrine

    @classmethod
    def auto_tag_pilot(cls, pilot: HostilePilotDossier) -> HostilePilotDossier:
        """Inspects notes and role descriptors to automatically apply pilot role tags"""
        notes_lower = (pilot.notes or "").lower()

        # Cyno alt heuristics
        if re.search(r"\b(cyno|cynosaural|beacon|dropper)\b", notes_lower):
            pilot.is_cyno_alt = True

        # Black Ops / Hunter heuristics
        blops_keywords = ["blops", "black ops", "panther", "redeemer", "widow", "sin", "hunter", "covert cyno", "stealth bomber"]
        if any(re.search(rf"\b{kw}\b", notes_lower) for kw in blops_keywords):
            pilot.is_blops_pilot = True

        # Titan heuristics
        titan_ships = ["avatar", "erebus", "ragnarok", "leviathan", "komodo", "vanquisher", "molok", "titan"]
        if any(re.search(rf"\b{ship}\b", notes_lower) for ship in titan_ships):
            pilot.is_titan_pilot = True
            pilot.is_super_pilot = True
            pilot.is_capital_pilot = True

        # Supercarrier heuristics
        super_ships = ["aeon", "hel", "nyx", "wyvern", "revenant", "supercarrier", "super pilot"]
        if any(re.search(rf"\b{ship}\b", notes_lower) for ship in super_ships):
            pilot.is_super_pilot = True
            pilot.is_capital_pilot = True

        # Dreadnought heuristics
        dread_ships = [
            "dread",
            "dreadnought",
            "naglfar",
            "nagelfar",
            "moros",
            "phoenix",
            "revelation",
            "zirnitra",
            "bane",
            "karura",
            "hubris",
            "valravn",
            "caiman",
            "chemosh",
            "vehement",
        ]
        if any(re.search(rf"\b{ship}\b", notes_lower) for ship in dread_ships):
            pilot.is_dread_pilot = True
            pilot.is_capital_pilot = True

        # Force Auxiliary / FAX heuristics
        fax_ships = [
            "fax",
            "force auxiliary",
            "apostle",
            "minokawa",
            "ninazu",
            "lif",
            "dagon",
            "loggerhead",
        ]
        if any(re.search(rf"\b{ship}\b", notes_lower) for ship in fax_ships):
            pilot.is_fax_pilot = True
            pilot.is_capital_pilot = True

        # Capital heuristics
        capital_ships = [
            "dread",
            "dreadnought",
            "carrier",
            "fax",
            "force auxiliary",
            "nagelfar",
            "revelation",
            "moros",
            "phoenix",
            "archon",
            "chimera",
            "thanatos",
            "nidhoggur",
            "apostle",
            "minokawa",
            "ninazu",
            "lif",
        ]
        if any(re.search(rf"\b{ship}\b", notes_lower) for ship in capital_ships):
            pilot.is_capital_pilot = True

        # FC heuristics
        fc_match = re.search(
            r"\bfc(?:\s*\(\s*(low|medium|high|candidate)(?:\s+(\d+))?\s*\)|\s+(low|medium|high|candidate)|\b|\s*fleet commander|\s*strat fc)",
            notes_lower,
        )
        if fc_match or re.search(
            r"\b(fleet commander|skirmish commander|strat fc)\b", notes_lower
        ):
            pilot.is_fc = True
            if fc_match and fc_match.group(1):
                pilot.fc_level = fc_match.group(1).upper()
            elif not pilot.fc_level:
                pilot.fc_level = "CANDIDATE"

        # AWOX heuristics
        if re.search(r"\b(alliance\s+awox)\b", notes_lower):
            pilot.is_alliance_awox = True
            m_a = re.search(r"alliance\s+awox\s*\(?(\d+)\)?", notes_lower)
            if m_a and int(m_a.group(1)) > pilot.alliance_awox_count:
                pilot.alliance_awox_count = int(m_a.group(1))
        elif re.search(r"\b(faction\s+awox)\b", notes_lower):
            pilot.is_faction_awox = True
            m_f = re.search(r"faction\s+awox\s*\(?(\d+)\)?", notes_lower)
            if m_f and int(m_f.group(1)) > pilot.faction_awox_count:
                pilot.faction_awox_count = int(m_f.group(1))
        elif re.search(r"\b(awox|awoxer|corp\s+awox)\b", notes_lower):
            pilot.is_awox = True
            m_c = re.search(r"(?:corp\s+)?awox\s*\(?(\d+)\)?", notes_lower)
            if m_c and int(m_c.group(1)) > pilot.awox_count:
                pilot.awox_count = int(m_c.group(1))

        # Bait heuristics
        bait_match = re.search(
            r"\bbait(?:\s+(low|medium|high))?(?:\s*\(\s*(low|medium|high)?(?:\s*(\d+))?\s*\)|\s*\((\d+)\))?",
            notes_lower,
        )
        if bait_match or re.search(r"\b(bait|baiter|tackle\s+bait)\b", notes_lower):
            pilot.is_bait = True
            if bait_match:
                lvl = bait_match.group(1) or bait_match.group(2)
                if lvl:
                    pilot.bait_level = lvl.upper()
                cnt = bait_match.group(3) or bait_match.group(4)
                if cnt and int(cnt) > pilot.bait_count:
                    pilot.bait_count = int(cnt)

        # Ganker heuristics
        if re.search(r"\b(gank|ganker|suicide\s+gank|highsec\s+gank)\b", notes_lower):
            pilot.is_ganker = True
            m_g = re.search(r"ganker?\s*\(?(\d+)\)?", notes_lower)
            if m_g and int(m_g.group(1)) > pilot.ganker_count:
                pilot.ganker_count = int(m_g.group(1))

        # Logistics heuristics
        if re.search(
            r"\b(logi|logistics|guardian|oneiros|basilisk|scimitar|deacon|kirin|thalia|scalpel)\b",
            notes_lower,
        ):
            pilot.is_logi_pilot = True
            m_l = re.search(r"logi(?:stics)?\s*\(?(\d+)\)?", notes_lower)
            if m_l and int(m_l.group(1)) > pilot.logi_count:
                pilot.logi_count = int(m_l.group(1))

        # Rookie heuristics
        if re.search(r"\b(rookie|newbro|newbie)\b", notes_lower):
            pilot.is_rookie = True

        return pilot

    @classmethod
    def run_alt_detection(
        cls, pilots: Optional[Any] = None
    ) -> int:
        """
        Executes automated alt detection across pilot dossiers.
        Correlates names, stems, suffix tokens, roman numerals, tactical cyno/cap roles,
        and intelligence notes to link alts to their main combat characters.
        Returns total count of alts linked/updated.
        """
        return AltDetectionEngine.run_detection(pilots=pilots)


class AltDetectionEngine:
    """
    Intelligent alt detection and character correlation engine for EVE Online pilots.
    Scans character names, suffixes, roman numerals, tactical capabilities (cyno alts, capitals,
    baits, out-of-corp holders), and activity patterns to infer and link alt characters to their
    primary combat mains.
    """

    ALT_SUFFIXES = {
        "cyno",
        "cyno1",
        "cyno2",
        "cyno3",
        "cynoa",
        "cynob",
        "dread",
        "dread1",
        "dread2",
        "dreadnought",
        "fax",
        "fax1",
        "fax2",
        "force",
        "super",
        "super1",
        "super2",
        "supercarrier",
        "titan",
        "titan1",
        "titan2",
        "cap",
        "capital",
        "alt",
        "alt1",
        "alt2",
        "alt3",
        "alts",
        "scout",
        "scout1",
        "scout2",
        "eyes",
        "eyes1",
        "eyes2",
        "tackle",
        "tackle1",
        "dictor",
        "hic",
        "dic",
        "hauler",
        "transport",
        "freighter",
        "jf",
        "jumpfreighter",
        "jita",
        "jitaalt",
        "trader",
        "trade",
        "market",
        "indy",
        "industry",
        "industrial",
        "pi",
        "mining",
        "miner",
        "rorq",
        "rorqual",
        "exhumer",
        "booster",
        "link",
        "links",
        "command",
        "hunter",
        "blops",
        "blackops",
        "covert",
        "logi",
        "logistics",
        "sub",
        "pod",
        "clone",
        "alpha",
        "beta",
        "gamma",
        "delta",
        "pvp",
        "pve",
        "krab",
        "ratting",
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
        "7",
        "8",
        "9",
        "01",
        "02",
        "03",
        "04",
        "05",
        "i",
        "ii",
        "iii",
        "iv",
        "v",
        "vi",
        "vii",
        "viii",
        "ix",
        "x",
    }

    @classmethod
    def clean_name_tokens(cls, name: str) -> list[str]:
        if not name:
            return []
        cleaned = re.sub(r"[^a-zA-Z0-9\s]", " ", name)
        return [t.lower() for t in cleaned.split() if t]

    @classmethod
    def extract_stem(cls, name: str) -> str:
        """Extracts the base character name stem by removing alt-like tokens, suffixes, and numbers"""
        tokens = cls.clean_name_tokens(name)
        if not tokens:
            return ""
        while tokens and tokens[-1] in cls.ALT_SUFFIXES:
            tokens.pop()
        while tokens and tokens[0] in cls.ALT_SUFFIXES:
            tokens.pop(0)
        return " ".join(tokens)

    @classmethod
    def calculate_main_score(cls, pilot: HostilePilotDossier) -> int:
        """
        Calculates priority score to identify whether a pilot is the main character vs an alt.
        Higher score indicates the primary/main combat character.
        """
        score = 0
        name_lower = pilot.character_name.lower()
        tokens = cls.clean_name_tokens(pilot.character_name)

        # Penalize obvious alt suffixes in the name
        if any(t in cls.ALT_SUFFIXES for t in tokens):
            score -= 15
        if re.search(r"\b(cyno|alt|eyes|scout|hauler|jita|miner)\b", name_lower):
            score -= 20

        # Primary corp/alliance membership
        if not pilot.is_out_of_corp:
            score += 15
        if pilot.alliance_name:
            score += 10
        if pilot.corporation_name and pilot.corporation_name != "Unknown Corp":
            score += 5

        # Leadership & combat roles
        if pilot.is_fc:
            score += 30
            if pilot.fc_level == "HIGH":
                score += 15
            elif pilot.fc_level == "MEDIUM":
                score += 10
        if pilot.is_titan_pilot:
            score += 25
        if pilot.is_super_pilot:
            score += 20
        if pilot.is_dread_pilot or pilot.is_fax_pilot:
            score += 15
        if pilot.is_capital_pilot:
            score += 10
        if pilot.is_blops_pilot:
            score += 5

        # Activity & behavioral metrics
        if pilot.fc_score > 0:
            score += min(20, pilot.fc_score // 5)
        if pilot.awox_count > 0 or pilot.alliance_awox_count > 0:
            score += 5

        # Cyno alt flag penalty
        if pilot.is_cyno_alt:
            score -= 10
        if pilot.is_bait:
            score -= 5

        return score

    @classmethod
    def evaluate_pair(
        cls, p1: HostilePilotDossier, p2: HostilePilotDossier
    ) -> Optional[dict]:
        """
        Evaluates whether p1 and p2 form an Alt-Main relationship.
        Returns dict with main, alt, reason, confidence, or None.
        """
        if p1.id == p2.id or p1.character_id == p2.character_id:
            return None

        stem1 = cls.extract_stem(p1.character_name)
        stem2 = cls.extract_stem(p2.character_name)

        tokens1 = cls.clean_name_tokens(p1.character_name)
        tokens2 = cls.clean_name_tokens(p2.character_name)

        if not stem1 or not stem2:
            return None

        matched = False
        reason = ""
        confidence = "MEDIUM"

        # 1. Exact stem match (e.g. "Pilot Cyno" vs "Pilot", or "Pilot II" vs "Pilot Dread")
        if stem1 == stem2 and len(stem1) >= 3:
            matched = True
            confidence = "HIGH"
            reason = f"Name Pattern: Suffix/stem matching root '{stem1.title()}'"

        # 2. One stem contains the other or token subset
        elif (stem1 in stem2 or stem2 in stem1) and (
            len(stem1) >= 4 and len(stem2) >= 4
        ):
            set1 = set(tokens1)
            set2 = set(tokens2)
            intersection = set1.intersection(set2)
            non_alt_common = [
                t for t in intersection if t not in cls.ALT_SUFFIXES and len(t) >= 3
            ]
            if len(non_alt_common) >= 1:
                matched = True
                confidence = (
                    "HIGH"
                    if len(non_alt_common) >= 2 or len(stem1) >= 6 or len(stem2) >= 6
                    else "MEDIUM"
                )
                reason = f"Name Pattern: Character name variant matching '{' '.join(non_alt_common).title()}'"

        # 3. Explicit recon notes correlation
        if not matched and (
            p1.is_cyno_alt or p2.is_cyno_alt or p1.is_out_of_corp or p2.is_out_of_corp
        ):
            if p1.character_name.lower() in (p2.notes or "").lower():
                matched = True
                confidence = "HIGH"
                reason = "Intelligence Intel: Linked in reconnaissance notes"
            elif p2.character_name.lower() in (p1.notes or "").lower():
                matched = True
                confidence = "HIGH"
                reason = "Intelligence Intel: Linked in reconnaissance notes"

        if matched:
            score1 = cls.calculate_main_score(p1)
            score2 = cls.calculate_main_score(p2)

            if score1 >= score2:
                main = p1
                alt = p2
            else:
                main = p2
                alt = p1

            return {
                "main": main,
                "alt": alt,
                "reason": reason,
                "confidence": confidence,
            }

        return None

    @classmethod
    def run_detection(cls, pilots: Optional[Any] = None) -> int:
        """
        Runs alt detection over provided pilots or all pilots in database.
        Resolves main/alt linkages and updates models.
        """
        all_pilots = list(HostilePilotDossier.objects.all().select_related("main_character"))
        if not all_pilots:
            return 0

        target_pilots = list(pilots) if pilots is not None else all_pilots
        linked_count = 0

        # Map character_id to pilot instance
        pilot_map = {p.id: p for p in all_pilots}
        alt_links: dict[int, dict] = {}

        for p_target in target_pilots:
            for p_other in all_pilots:
                if p_target.id == p_other.id:
                    continue
                match = cls.evaluate_pair(p_target, p_other)
                if match:
                    main_p = match["main"]
                    alt_p = match["alt"]

                    # Do not overwrite if alt has manually set main character with different id unless unlinked
                    curr_entry = alt_links.get(alt_p.id)
                    if not curr_entry or match["confidence"] == "HIGH":
                        alt_links[alt_p.id] = {
                            "main_id": main_p.id,
                            "reason": match["reason"],
                            "confidence": match["confidence"],
                        }

        # Resolve transitive chains (if A -> B and B -> C, then A -> C)
        for alt_id, info in list(alt_links.items()):
            visited = {alt_id}
            curr_main_id = info["main_id"]
            while curr_main_id in alt_links and curr_main_id not in visited:
                visited.add(curr_main_id)
                curr_main_id = alt_links[curr_main_id]["main_id"]
            if curr_main_id != alt_id:
                info["main_id"] = curr_main_id

        # Apply updates to database
        for alt_id, info in alt_links.items():
            alt_obj = pilot_map.get(alt_id)
            main_obj = pilot_map.get(info["main_id"])
            if alt_obj and main_obj and alt_obj.id != main_obj.id:
                changed = False
                if not alt_obj.is_alt:
                    alt_obj.is_alt = True
                    changed = True
                if alt_obj.main_character_id != main_obj.id:
                    alt_obj.main_character = main_obj
                    changed = True
                if alt_obj.alt_inference_reason != info["reason"]:
                    alt_obj.alt_inference_reason = info["reason"]
                    changed = True
                if alt_obj.alt_confidence != info["confidence"]:
                    alt_obj.alt_confidence = info["confidence"]
                    changed = True

                # Ensure main is not marked as alt of itself or another in this link
                if main_obj.is_alt and main_obj.main_character_id == alt_obj.id:
                    main_obj.is_alt = False
                    main_obj.main_character = None
                    main_obj.save(update_fields=["is_alt", "main_character", "updated_at"])

                if changed:
                    alt_obj.save(
                        update_fields=[
                            "is_alt",
                            "main_character",
                            "alt_inference_reason",
                            "alt_confidence",
                            "updated_at",
                        ]
                    )
                    linked_count += 1

        return linked_count
