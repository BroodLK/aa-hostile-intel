"""
Trust management, intel verification, auditing, and pilot dossier role tagging
"""

# Standard Library
import re
from typing import Any

# Third Party
from eve_sde.models import ItemType, SolarSystem

# Django
from django.contrib.auth.models import User
from django.db import transaction

# AA Hostile Intel
from hostile.models import (
    HostileDoctrine,
    HostilePilotDossier,
    HostileStagingSystem,
    HostileStructure,
    IntelAuditLog,
    StructureFitting,
    StructureTimer,
    SystemObservation,
    extract_core_structure_name,
    normalize_structure_name,
)


class IntelManager:
    """Handles operational moderation, verification lifecycle, audit logs, and automatic role tagging"""

    @classmethod
    def record_audit(
        cls,
        user: User | None,
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
    def verify_structure(
        cls, structure: HostileStructure, user: User
    ) -> HostileStructure:
        """Marks a structure report as verified by an intelligence officer"""
        structure.is_verified = True
        structure.save(update_fields=["is_verified", "updated_at"])
        cls.record_audit(
            user, "VERIFY", structure, f"Structure '{structure.name}' verified."
        )
        return structure

    @classmethod
    def verify_observation(
        cls, observation: SystemObservation, user: User
    ) -> SystemObservation:
        """Marks a system observation / gate camp report as verified"""
        observation.is_verified = True
        observation.save(update_fields=["is_verified", "updated_at"])
        cls.record_audit(
            user,
            "VERIFY",
            observation,
            f"Observation in {observation.solar_system.name} verified.",
        )
        return observation

    @classmethod
    def toggle_pin_observation(
        cls, observation: SystemObservation, user: User
    ) -> SystemObservation:
        """Pins or unpins an observation to the intel dashboard summary"""
        observation.is_pinned = not observation.is_pinned
        observation.save(update_fields=["is_pinned", "updated_at"])
        action = "PIN" if observation.is_pinned else "UNPIN"
        cls.record_audit(
            user,
            action,
            observation,
            f"Observation in {observation.solar_system.name} pin state changed.",
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
    def verify_pilot(
        cls, pilot: HostilePilotDossier, user: User
    ) -> HostilePilotDossier:
        """Marks a hostile pilot dossier as verified"""
        pilot.is_verified = True
        pilot.save(update_fields=["is_verified", "updated_at"])
        cls.record_audit(
            user, "VERIFY", pilot, f"Pilot dossier {pilot.character_name} verified."
        )
        return pilot

    @classmethod
    def verify_staging(
        cls, staging: HostileStagingSystem, user: User
    ) -> HostileStagingSystem:
        """Marks a hostile staging system report as verified"""
        staging.is_verified = True
        staging.save(update_fields=["is_verified", "updated_at"])
        cls.record_audit(
            user,
            "VERIFY",
            staging,
            f"Staging system {staging.solar_system.name} verified.",
        )
        return staging

    @classmethod
    def verify_doctrine(cls, doctrine: HostileDoctrine, user: User) -> HostileDoctrine:
        """Marks a hostile doctrine report as verified"""
        doctrine.is_verified = True
        doctrine.save(update_fields=["is_verified", "updated_at"])
        cls.record_audit(
            user, "VERIFY", doctrine, f"Doctrine '{doctrine.name}' verified."
        )
        return doctrine

    @classmethod
    def auto_tag_pilot(cls, pilot: HostilePilotDossier) -> HostilePilotDossier:
        """Inspects notes and role descriptors to automatically apply pilot role tags"""
        notes_lower = (pilot.notes or "").lower()

        # Cyno alt heuristics
        if re.search(r"\b(cyno|cynosaural|beacon|dropper)\b", notes_lower):
            pilot.is_cyno_alt = True

        # Black Ops / Hunter heuristics
        blops_keywords = [
            "blops",
            "black ops",
            "panther",
            "redeemer",
            "widow",
            "sin",
            "hunter",
            "covert cyno",
            "stealth bomber",
        ]
        if any(re.search(rf"\b{kw}\b", notes_lower) for kw in blops_keywords):
            pilot.is_blops_pilot = True

        # Titan heuristics
        titan_ships = [
            "avatar",
            "erebus",
            "ragnarok",
            "leviathan",
            "komodo",
            "vanquisher",
            "molok",
            "titan",
        ]
        if any(re.search(rf"\b{ship}\b", notes_lower) for ship in titan_ships):
            pilot.is_titan_pilot = True
            pilot.is_super_pilot = True
            pilot.is_capital_pilot = True

        # Supercarrier heuristics
        super_ships = [
            "aeon",
            "hel",
            "nyx",
            "wyvern",
            "revenant",
            "supercarrier",
            "super pilot",
        ]
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
    def get_or_create_structure(
        cls,
        name: str,
        solar_system: SolarSystem | None = None,
        structure_type: ItemType | None = None,
        structure_id: int | None = None,
        defaults: dict[str, Any] | None = None,
        user: User | None = None,
    ) -> tuple[HostileStructure, bool]:
        """
        Gets an existing structure using intelligent deduplication or creates a new one.
        """
        defaults_dict = dict(defaults or {})
        norm_name = normalize_structure_name(name)
        if not norm_name and structure_type and solar_system:
            norm_name = f"{structure_type.name} ({solar_system.name})"
        elif not norm_name:
            norm_name = "Hostile Structure"

        existing = HostileStructure.objects.find_matching(
            solar_system=solar_system,
            name=norm_name,
            structure_type=structure_type,
            structure_id=structure_id,
            location_details=defaults_dict.get("location_details", ""),
        )

        if existing:
            updated_fields = []
            if structure_type and not existing.structure_type:
                existing.structure_type = structure_type
                updated_fields.append("structure_type")
            if solar_system and not existing.solar_system:
                existing.solar_system = solar_system
                updated_fields.append("solar_system")
            if defaults_dict.get("alliance") and not existing.alliance:
                existing.alliance = defaults_dict["alliance"]
                updated_fields.append("alliance")
            if defaults_dict.get("corporation") and not existing.corporation:
                existing.corporation = defaults_dict["corporation"]
                updated_fields.append("corporation")
            if defaults_dict.get("owner_ticker") and not existing.owner_ticker:
                existing.owner_ticker = defaults_dict["owner_ticker"][:32]
                updated_fields.append("owner_ticker")
            if defaults_dict.get("location_details") and not existing.location_details:
                existing.location_details = defaults_dict["location_details"]
                updated_fields.append("location_details")
            if defaults_dict.get("core_status") and existing.core_status == "UNKNOWN":
                existing.core_status = defaults_dict["core_status"]
                updated_fields.append("core_status")
            if (
                defaults_dict.get("state")
                and defaults_dict["state"] != "ONLINE"
                and existing.state == "ONLINE"
            ):
                existing.state = defaults_dict["state"]
                updated_fields.append("state")
            if (
                defaults_dict.get("vulnerability_window")
                and not existing.vulnerability_window
            ):
                existing.vulnerability_window = defaults_dict["vulnerability_window"]
                updated_fields.append("vulnerability_window")
            if (
                defaults_dict.get("vulnerability_hour") is not None
                and existing.vulnerability_hour is None
            ):
                existing.vulnerability_hour = defaults_dict["vulnerability_hour"]
                updated_fields.append("vulnerability_hour")
            if (
                solar_system
                and existing.name.endswith(f"({solar_system.name})")
                and not norm_name.endswith(f"({solar_system.name})")
                and "(" not in norm_name
            ):
                existing.name = norm_name
                updated_fields.append("name")

            if updated_fields:
                existing.save(update_fields=list(set(updated_fields)) + ["updated_at"])
            return existing, False

        create_kwargs = {
            "name": norm_name,
            "solar_system": solar_system,
            "structure_type": structure_type,
            "structure_id": structure_id,
            "created_by": user,
            **defaults_dict,
        }
        struct = HostileStructure.objects.create(**create_kwargs)
        return struct, True

    @classmethod
    @transaction.atomic
    def merge_structures(
        cls,
        primary: HostileStructure,
        duplicates: list[HostileStructure],
        user: User | None = None,
        reason: str = "",
    ) -> int:
        """
        Merges duplicate HostileStructure objects into primary HostileStructure.
        Re-points all fittings, timers, audit logs, and fills missing metadata.
        """
        if not duplicates:
            return 0

        dup_ids = [d.id for d in duplicates if d.id != primary.id]
        if not dup_ids:
            return 0

        dup_objs = list(HostileStructure.objects.filter(id__in=dup_ids))
        dup_names = [d.name for d in dup_objs]
        merged_count = 0

        update_fields = []
        for d in dup_objs:
            if not primary.structure_type and d.structure_type:
                primary.structure_type = d.structure_type
                update_fields.append("structure_type")
            if not primary.solar_system and d.solar_system:
                primary.solar_system = d.solar_system
                update_fields.append("solar_system")
            if not primary.alliance and d.alliance:
                primary.alliance = d.alliance
                update_fields.append("alliance")
            if not primary.corporation and d.corporation:
                primary.corporation = d.corporation
                update_fields.append("corporation")
            if not primary.owner_ticker and d.owner_ticker:
                primary.owner_ticker = d.owner_ticker
                update_fields.append("owner_ticker")
            if not primary.location_details and d.location_details:
                primary.location_details = d.location_details
                update_fields.append("location_details")
            if primary.core_status == "UNKNOWN" and d.core_status != "UNKNOWN":
                primary.core_status = d.core_status
                update_fields.append("core_status")
            if not primary.vulnerability_window and d.vulnerability_window:
                primary.vulnerability_window = d.vulnerability_window
                update_fields.append("vulnerability_window")
            if primary.vulnerability_hour is None and d.vulnerability_hour is not None:
                primary.vulnerability_hour = d.vulnerability_hour
                update_fields.append("vulnerability_hour")
            if not primary.notes and d.notes:
                primary.notes = d.notes
                update_fields.append("notes")
            elif d.notes and d.notes not in primary.notes:
                primary.notes = f"{primary.notes}\n{d.notes}".strip()
                update_fields.append("notes")

        if update_fields:
            primary.save(update_fields=list(set(update_fields)) + ["updated_at"])

        # Re-link child records
        StructureFitting.objects.filter(structure_id__in=dup_ids).update(
            structure=primary
        )
        StructureTimer.objects.filter(structure_id__in=dup_ids).update(
            structure=primary
        )
        IntelAuditLog.objects.filter(
            target_model="HostileStructure", target_id__in=[str(i) for i in dup_ids]
        ).update(target_id=str(primary.id))

        # Delete duplicates
        for d in dup_objs:
            d.delete()
            merged_count += 1

        details_msg = f"Merged {merged_count} duplicate structure(s) [{', '.join(dup_names)}] into '{primary.name}'"
        if reason:
            details_msg += f" (Reason: {reason})"
        cls.record_audit(
            user,
            "MERGE",
            primary,
            details_msg,
        )
        return merged_count

    @classmethod
    def auto_deduplicate_all(
        cls,
        user: User | None = None,
        system: Any = None,
        dry_run: bool = False,
        verbose: bool = False,
    ) -> dict[str, Any]:
        """
        Scans solar systems and merges duplicate structure records.
        Supports targeted solar system filtering and dry-run preview mode.
        """
        qs = HostileStructure.objects.filter(solar_system__isnull=False)
        if system:
            if isinstance(system, SolarSystem):
                qs = qs.filter(solar_system=system)
            elif isinstance(system, int) or (
                isinstance(system, str) and system.isdigit()
            ):
                qs = qs.filter(solar_system_id=int(system))
            elif isinstance(system, str):
                qs = qs.filter(solar_system__name__iexact=system.strip())

        system_ids = qs.order_by().values_list("solar_system_id", flat=True).distinct()

        total_merged = 0
        systems_affected = 0
        details = []

        for sys_id in system_ids:
            solar_sys = SolarSystem.objects.filter(id=sys_id).first()
            if not solar_sys:
                continue

            structs = list(
                HostileStructure.objects.filter(solar_system=solar_sys)
                .prefetch_related("fittings", "timers")
                .order_by("id")
            )
            if len(structs) <= 1:
                continue

            processed_ids = set()
            system_merged_any = False

            for i, s1 in enumerate(structs):
                if s1.id in processed_ids:
                    continue

                dups_for_s1 = []
                match_reasons = []
                for s2 in structs[i + 1 :]:
                    if s2.id in processed_ids:
                        continue

                    # Check if s2 matches s1
                    is_dup = False
                    reason = ""
                    norm1 = normalize_structure_name(s1.name).lower()
                    norm2 = normalize_structure_name(s2.name).lower()

                    # Exact normalized name
                    if norm1 == norm2:
                        is_dup = True
                        reason = f"Exact normalized name match ('{norm1}')"
                    # Ansiblex bidirectional route matching
                    elif "»" in norm1 and "»" in norm2:
                        parts1 = [
                            p.strip().split()[0]
                            for p in norm1.split("»")
                            if p.strip().split()
                        ]
                        parts2 = [
                            p.strip().split()[0]
                            for p in norm2.split("»")
                            if p.strip().split()
                        ]
                        if len(parts1) >= 2 and len(parts2) >= 2:
                            pair1 = {parts1[0].upper(), parts1[1].upper()}
                            pair2 = {parts2[0].upper(), parts2[1].upper()}
                            if pair1 == pair2:
                                is_dup = True
                                reason = f"Ansiblex gate pair match ('{parts1[0].upper()}' <-> '{parts1[1].upper()}')"
                    # Ticker-stripped core name and system suffix matching
                    else:
                        core1 = extract_core_structure_name(
                            s1.name, solar_system=solar_sys
                        ).lower()
                        core2 = extract_core_structure_name(
                            s2.name, solar_system=solar_sys
                        ).lower()
                        if core1 and core2 and core1 == core2:
                            is_dup = True
                            reason = f"Core structure name match ('{core1}')"

                    if is_dup:
                        dups_for_s1.append(s2)
                        match_reasons.append(reason)
                        processed_ids.add(s2.id)

                if dups_for_s1:
                    candidates = [s1] + dups_for_s1

                    def struct_score(st):
                        score = 0
                        if st.is_verified:
                            score += 100
                        score += st.fittings.count() * 10
                        score += st.timers.count() * 10
                        if st.alliance_id:
                            score += 5
                        if st.corporation_id:
                            score += 5
                        if st.vulnerability_window:
                            score += 5
                        if not st.name.endswith(f"({solar_sys.name})"):
                            score += 2
                        return score

                    best_candidate = max(candidates, key=struct_score)
                    other_candidates = [
                        c for c in candidates if c.id != best_candidate.id
                    ]
                    combined_reason = "; ".join(dict.fromkeys(match_reasons))

                    details.append(
                        {
                            "system_id": solar_sys.id,
                            "system_name": solar_sys.name,
                            "primary_id": best_candidate.id,
                            "primary_name": best_candidate.name,
                            "duplicate_ids": [c.id for c in other_candidates],
                            "duplicate_names": [c.name for c in other_candidates],
                            "reason": combined_reason,
                            "primary_score": struct_score(best_candidate),
                        }
                    )

                    if not dry_run:
                        count = cls.merge_structures(
                            best_candidate,
                            other_candidates,
                            user=user,
                            reason=combined_reason,
                        )
                        total_merged += count
                    else:
                        total_merged += len(other_candidates)

                    system_merged_any = True
                    for c in candidates:
                        processed_ids.add(c.id)

            if system_merged_any:
                systems_affected += 1

        return {
            "total_merged": total_merged,
            "systems_affected": systems_affected,
            "details": details,
        }

    @classmethod
    def find_suspected_duplicates(cls, system: Any = None) -> list[dict[str, Any]]:
        """
        Scans for suspected duplicate structures that fell below automatic merge criteria.
        Groups structures in the same solar system that share identical or similar types,
        high name token overlap, or similar names across differing owners or unverified records.
        """
        qs = (
            HostileStructure.objects.filter(solar_system__isnull=False)
            .select_related("solar_system", "structure_type", "alliance", "corporation")
            .prefetch_related("fittings", "timers")
        )

        if system:
            if isinstance(system, SolarSystem):
                qs = qs.filter(solar_system=system)
            elif isinstance(system, int) or (
                isinstance(system, str) and system.isdigit()
            ):
                qs = qs.filter(solar_system_id=int(system))
            elif isinstance(system, str):
                qs = qs.filter(solar_system__name__iexact=system.strip())

        system_ids = qs.order_by().values_list("solar_system_id", flat=True).distinct()
        suspected_groups = []

        for sys_id in system_ids:
            structs = list(
                qs.filter(solar_system_id=sys_id).order_by("-is_verified", "id")
            )
            if len(structs) <= 1:
                continue

            processed_ids = set()
            for i, s1 in enumerate(structs):
                if s1.id in processed_ids:
                    continue

                candidates = []
                for s2 in structs[i + 1 :]:
                    if s2.id in processed_ids:
                        continue

                    # Evaluate similarity
                    is_suspected = False
                    reason = ""
                    confidence = "MEDIUM"

                    tokens1 = set(
                        re.findall(
                            r"\w+",
                            extract_core_structure_name(
                                s1.name, solar_system=s1.solar_system
                            ).lower(),
                        )
                    )
                    tokens2 = set(
                        re.findall(
                            r"\w+",
                            extract_core_structure_name(
                                s2.name, solar_system=s2.solar_system
                            ).lower(),
                        )
                    )
                    common_tokens = tokens1.intersection(tokens2)

                    # Same structure type in same system
                    if (
                        s1.structure_type
                        and s2.structure_type
                        and s1.structure_type_id == s2.structure_type_id
                    ):
                        if common_tokens:
                            is_suspected = True
                            confidence = "HIGH" if len(common_tokens) >= 2 else "MEDIUM"
                            reason = f"Same structure type ({s1.structure_type.name}) with overlapping keywords: {', '.join(common_tokens)}"
                        elif (
                            s1.owner_ticker
                            and s2.owner_ticker
                            and s1.owner_ticker == s2.owner_ticker
                        ):
                            is_suspected = True
                            confidence = "MEDIUM"
                            reason = f"Same structure type ({s1.structure_type.name}) and owner ticker [{s1.owner_ticker}]"
                    # High token overlap regardless of type
                    elif len(common_tokens) >= 3 or (
                        len(tokens1) > 0 and len(common_tokens) / len(tokens1) >= 0.7
                    ):
                        is_suspected = True
                        confidence = "MEDIUM"
                        reason = f"High name similarity ({len(common_tokens)} common keywords): {', '.join(common_tokens)}"

                    if is_suspected:
                        candidates.append((s2, reason, confidence))
                        processed_ids.add(s2.id)

                if candidates:
                    processed_ids.add(s1.id)
                    reasons = [c[1] for c in candidates]
                    conf = (
                        "HIGH" if any(c[2] == "HIGH" for c in candidates) else "MEDIUM"
                    )
                    suspected_groups.append(
                        {
                            "system": s1.solar_system,
                            "primary": s1,
                            "duplicates": [c[0] for c in candidates],
                            "reason": "; ".join(dict.fromkeys(reasons)),
                            "confidence": conf,
                        }
                    )

        return suspected_groups

    @classmethod
    def run_alt_detection(cls, pilots: Any | None = None) -> int:
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
    ) -> dict | None:
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
    def run_detection(cls, pilots: Any | None = None) -> int:
        """
        Runs alt detection over provided pilots or all pilots in database.
        Resolves main/alt linkages and updates models.
        """
        all_pilots = list(
            HostilePilotDossier.objects.all().select_related("main_character")
        )
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
                    main_obj.save(
                        update_fields=["is_alt", "main_character", "updated_at"]
                    )

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
