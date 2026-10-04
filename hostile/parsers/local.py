"""
Local Threat Parser for non-ESI Local chat window and member list intelligence
(localthreat.xyz style profiling with timestamps, friendly filtering, and D-Scan coupling)
"""

# Standard Library
from collections import Counter
from datetime import datetime
import logging
import re
from typing import Any, Dict, List, Optional, Set

# Django
from django.conf import settings

# Third Party
from eve_sde.models import SolarSystem

# AA Hostile Intel
from hostile.models import HostilePilotDossier, LocalThreatScan
from hostile.parsers.dscan import DScanParser
from hostile.services.entity_resolver import EntityResolver
from hostile.services.intel_manager import AltDetectionEngine
from hostile.services.threat_engine import ThreatEngine
from hostile.services.zkill_client import zkill_client

logger = logging.getLogger(__name__)


class LocalThreatParser:
    """Parses EVE Online Local chat logs and member lists to extract hostile rosters, timestamps, and drop risks"""

    TIMESTAMP_CHAT_PATTERN = re.compile(
        r"^\[\s*(\d{4}[.\-/]\d{2}[.\-/]\d{2}\s+)?(\d{1,2}:\d{2}(?::\d{2})?)\s*\]\s*([^>]+?)\s*>\s*(.*)$"
    )
    CHANNEL_HEADER_PATTERN = re.compile(
        r"^(Channel ID:|Channel Name:|Listener:|Session started:|Channel MOTD:)", re.I
    )

    @classmethod
    def get_ignored_alliances(cls) -> Set[str]:
        """Collects lower-case list of friendly/ignored alliances and corporations dynamically from settings and database"""
        ignored = set()
        main_alliance_name = getattr(settings, "HOSTILE_MAIN_ALLIANCE_NAME", getattr(settings, "ALLIANCE_NAME", None))
        main_alliance_id = getattr(settings, "HOSTILE_MAIN_ALLIANCE_ID", getattr(settings, "ALLIANCE_ID", None))

        if main_alliance_name:
            ignored.add(str(main_alliance_name).lower().strip())
        if main_alliance_id:
            ignored.add(str(main_alliance_id).lower().strip())

        for item in getattr(settings, "HOSTILE_IGNORED_ALLIANCE_NAMES", []):
            if item:
                ignored.add(str(item).lower().strip())
        for item in getattr(settings, "HOSTILE_IGNORED_ALLIANCE_IDS", []):
            if item:
                ignored.add(str(item).lower().strip())
        for item in getattr(settings, "HOSTILE_IGNORED_ALLIANCE_TICKERS", []):
            if item:
                ignored.add(str(item).lower().strip())
        for item in getattr(settings, "HOSTILE_IGNORED_CORP_NAMES", []):
            if item:
                ignored.add(str(item).lower().strip())
        for item in getattr(settings, "HOSTILE_IGNORED_CORP_IDS", []):
            if item:
                ignored.add(str(item).lower().strip())

        # Include database-flagged friendly / ignored alliances and corporations
        try:
            from hostile.models import HostileAlliance, HostileCorporation

            for a in HostileAlliance.objects.filter(is_friendly=True).values("alliance_id", "alliance_name", "ticker"):
                if a.get("alliance_id"):
                    ignored.add(str(a["alliance_id"]).lower().strip())
                if a.get("alliance_name"):
                    ignored.add(str(a["alliance_name"]).lower().strip())
                if a.get("ticker"):
                    ignored.add(str(a["ticker"]).lower().strip())

            for c in HostileCorporation.objects.filter(is_friendly=True).values("corporation_id", "corporation_name", "ticker"):
                if c.get("corporation_id"):
                    ignored.add(str(c["corporation_id"]).lower().strip())
                if c.get("corporation_name"):
                    ignored.add(str(c["corporation_name"]).lower().strip())
                if c.get("ticker"):
                    ignored.add(str(c["ticker"]).lower().strip())
        except Exception:
            pass

        return ignored

    @classmethod
    def is_ignored_entity(
        cls,
        corp_name: str = "",
        alliance_name: str = "",
        ticker: str = "",
        corp_id: Any = None,
        alliance_id: Any = None,
    ) -> bool:
        """Determines if a pilot's corporation or alliance should be ignored to avoid contamination"""
        ignored_set = cls.get_ignored_alliances()
        if not ignored_set:
            return False

        if corp_name and corp_name.lower().strip() in ignored_set:
            return True
        if alliance_name and alliance_name.lower().strip() in ignored_set:
            return True
        if ticker and ticker.lower().strip() in ignored_set:
            return True
        if corp_id and str(corp_id).lower().strip() in ignored_set:
            return True
        if alliance_id and str(alliance_id).lower().strip() in ignored_set:
            return True

        return False

    @classmethod
    def enrich_pilot(
        cls, pilot: Dict[str, Any], save_dossier: bool = True
    ) -> Dict[str, Any]:
        """
        Enriches a single pilot's dictionary with full entity affiliation, zKillboard combat intelligence,
        top ships flown, behavioral tags (cyno, blops, fc, capital, dread, fax, super, titan, bait, etc.),
        and links to internal dossiers.
        """
        char_id = pilot.get("character_id")
        char_name = pilot.get("character_name") or ""

        # 1. Resolve character and affiliation if missing
        if not char_id or not pilot.get("corporation_name"):
            res_info = EntityResolver.resolve_character(char_id or char_name)
            if res_info:
                char_id = res_info.get("character_id") or char_id
                char_name = res_info.get("character_name") or char_name
                pilot["character_id"] = char_id
                pilot["character_name"] = char_name
                pilot["corporation_id"] = res_info.get("corporation_id") or pilot.get("corporation_id")
                pilot["corporation_name"] = res_info.get("corporation_name") or pilot.get("corporation_name") or ""
                pilot["corporation_ticker"] = res_info.get("corporation_ticker") or pilot.get("corporation_ticker") or ""
                pilot["alliance_id"] = res_info.get("alliance_id") or pilot.get("alliance_id")
                pilot["alliance_name"] = res_info.get("alliance_name") or pilot.get("alliance_name") or ""
                pilot["alliance_ticker"] = res_info.get("alliance_ticker") or pilot.get("alliance_ticker") or ""

        # 2. Check dossier
        dossier = None
        if char_id:
            dossier = HostilePilotDossier.objects.filter(character_id=char_id).first()
        if not dossier and char_name:
            dossier = HostilePilotDossier.objects.filter(character_name__iexact=char_name).first()

        # 3. Fetch zKill combat intelligence
        combat_intel = {}
        if char_id:
            try:
                combat_intel = zkill_client.get_character_combat_intelligence(char_id)
            except Exception as exc:
                logger.debug("Failed to fetch zKill combat intel for %s: %s", char_id, exc)

        is_cyno_alt = bool(
            (dossier and dossier.is_cyno_alt)
            or combat_intel.get("is_cyno_alt")
            or pilot.get("is_cyno_alt")
        )
        is_blops_pilot = bool(
            (dossier and getattr(dossier, "is_blops_pilot", False))
            or combat_intel.get("is_blops_pilot")
            or pilot.get("is_blops_pilot")
        )
        is_dread_pilot = bool(
            (dossier and getattr(dossier, "is_dread_pilot", False))
            or combat_intel.get("is_dread_pilot")
            or pilot.get("is_dread_pilot")
        )
        is_fax_pilot = bool(
            (dossier and getattr(dossier, "is_fax_pilot", False))
            or combat_intel.get("is_fax_pilot")
            or pilot.get("is_fax_pilot")
        )
        is_super_pilot = bool(
            (dossier and dossier.is_super_pilot)
            or combat_intel.get("is_super_pilot")
            or pilot.get("is_super_pilot")
        )
        is_titan_pilot = bool(
            (dossier and dossier.is_titan_pilot)
            or combat_intel.get("is_titan_pilot")
            or pilot.get("is_titan_pilot")
        )
        is_capital_pilot = bool(
            (dossier and dossier.is_capital_pilot)
            or combat_intel.get("is_capital_pilot")
            or is_dread_pilot
            or is_fax_pilot
            or is_super_pilot
            or is_titan_pilot
            or pilot.get("is_capital_pilot")
        )
        is_fc = bool((dossier and dossier.is_fc) or combat_intel.get("is_fc") or pilot.get("is_fc"))
        fc_level = (
            dossier.fc_level
            if (dossier and dossier.fc_level)
            else (combat_intel.get("fc_level") or pilot.get("fc_level", ""))
        )
        fc_score = max((dossier.fc_score if dossier else 0), combat_intel.get("fc_score", 0), pilot.get("fc_score", 0))

        is_bait = bool((dossier and dossier.is_bait) or combat_intel.get("is_bait") or pilot.get("is_bait"))
        bait_level = (
            dossier.bait_level
            if (dossier and dossier.bait_level)
            else (combat_intel.get("bait_level") or pilot.get("bait_level", ""))
        )
        bait_count = max(
            (dossier.bait_count if dossier else 0), combat_intel.get("bait_count", 0), pilot.get("bait_count", 0)
        )

        is_ganker = bool((dossier and dossier.is_ganker) or combat_intel.get("is_ganker") or pilot.get("is_ganker"))
        ganker_count = max(
            (dossier.ganker_count if dossier else 0), combat_intel.get("ganker_count", 0), pilot.get("ganker_count", 0)
        )

        is_logi_pilot = bool(
            (dossier and dossier.is_logi_pilot) or combat_intel.get("is_logi_pilot") or pilot.get("is_logi_pilot")
        )
        logi_count = max(
            (dossier.logi_count if dossier else 0), combat_intel.get("logi_count", 0), pilot.get("logi_count", 0)
        )

        is_awox = bool((dossier and dossier.is_awox) or combat_intel.get("is_awox") or pilot.get("is_awox"))
        awox_count = max(
            (dossier.awox_count if dossier else 0), combat_intel.get("awox_count", 0), pilot.get("awox_count", 0)
        )

        is_alliance_awox = bool(
            (dossier and dossier.is_alliance_awox)
            or combat_intel.get("is_alliance_awox")
            or pilot.get("is_alliance_awox")
        )
        alliance_awox_count = max(
            (dossier.alliance_awox_count if dossier else 0),
            combat_intel.get("alliance_awox_count", 0),
            pilot.get("alliance_awox_count", 0),
        )

        is_faction_awox = bool(
            (dossier and dossier.is_faction_awox)
            or combat_intel.get("is_faction_awox")
            or pilot.get("is_faction_awox")
        )
        faction_awox_count = max(
            (dossier.faction_awox_count if dossier else 0),
            combat_intel.get("faction_awox_count", 0),
            pilot.get("faction_awox_count", 0),
        )

        is_rookie = bool((dossier and dossier.is_rookie) or combat_intel.get("is_rookie") or pilot.get("is_rookie"))

        danger_ratio = combat_intel.get("danger_ratio", pilot.get("danger_ratio", 0))
        gang_ratio = combat_intel.get("gang_ratio", pilot.get("gang_ratio", 0))
        solo_kills = combat_intel.get("solo_kills", pilot.get("solo_kills", 0))
        kills_count = combat_intel.get("kills_count", pilot.get("kills_count", 0))
        losses_count = combat_intel.get("losses_count", pilot.get("losses_count", 0))
        top_ships = combat_intel.get("top_ships") or pilot.get("top_ships", [])
        likely_ship = combat_intel.get("likely_ship") or pilot.get("likely_ship", "")

        merged_labels = list(
            dict.fromkeys(
                (dossier.zkill_labels if dossier and dossier.zkill_labels else [])
                + combat_intel.get("zkill_labels", [])
                + pilot.get("zkill_labels", [])
            )
        )

        is_alt = dossier.is_alt if dossier else pilot.get("is_alt", False)
        main_character_name = (
            dossier.main_character.character_name
            if dossier and dossier.main_character
            else pilot.get("main_character_name", "")
        )

        # Auto-persist dossier if character ID is known and save_dossier is True
        if save_dossier and not dossier and char_id:
            try:
                dossier = HostilePilotDossier.objects.create(
                    character_id=char_id,
                    character_name=char_name,
                    corporation_name=pilot.get("corporation_name", ""),
                    alliance_name=pilot.get("alliance_name", ""),
                    is_cyno_alt=is_cyno_alt,
                    is_blops_pilot=is_blops_pilot,
                    is_capital_pilot=is_capital_pilot,
                    is_dread_pilot=is_dread_pilot,
                    is_fax_pilot=is_fax_pilot,
                    is_super_pilot=is_super_pilot,
                    is_titan_pilot=is_titan_pilot,
                    is_fc=is_fc,
                    fc_level=fc_level,
                    fc_score=fc_score,
                    is_bait=is_bait,
                    bait_level=bait_level,
                    bait_count=bait_count,
                    is_ganker=is_ganker,
                    ganker_count=ganker_count,
                    is_logi_pilot=is_logi_pilot,
                    logi_count=logi_count,
                    is_awox=is_awox,
                    awox_count=awox_count,
                    is_alliance_awox=is_alliance_awox,
                    alliance_awox_count=alliance_awox_count,
                    is_faction_awox=is_faction_awox,
                    faction_awox_count=faction_awox_count,
                    is_rookie=is_rookie,
                    zkill_labels=merged_labels,
                )
            except Exception as exc:
                logger.debug("Could not auto-create dossier for %s: %s", char_name, exc)

        pilot.update(
            {
                "character_id": char_id,
                "character_name": char_name,
                "is_cyno_alt": is_cyno_alt,
                "is_blops_pilot": is_blops_pilot,
                "is_dread_pilot": is_dread_pilot,
                "is_fax_pilot": is_fax_pilot,
                "is_capital_pilot": is_capital_pilot,
                "is_super_pilot": is_super_pilot,
                "is_titan_pilot": is_titan_pilot,
                "is_fc": is_fc,
                "fc_level": fc_level,
                "fc_score": fc_score,
                "is_bait": is_bait,
                "bait_level": bait_level,
                "bait_count": bait_count,
                "is_ganker": is_ganker,
                "ganker_count": ganker_count,
                "is_logi_pilot": is_logi_pilot,
                "logi_count": logi_count,
                "is_awox": is_awox,
                "awox_count": awox_count,
                "is_alliance_awox": is_alliance_awox,
                "alliance_awox_count": alliance_awox_count,
                "is_faction_awox": is_faction_awox,
                "faction_awox_count": faction_awox_count,
                "is_rookie": is_rookie,
                "danger_ratio": danger_ratio,
                "gang_ratio": gang_ratio,
                "solo_kills": solo_kills,
                "kills_count": kills_count,
                "losses_count": losses_count,
                "top_ships": top_ships,
                "likely_ship": likely_ship,
                "zkill_labels": merged_labels,
                "is_alt": is_alt,
                "main_character_name": main_character_name,
                "is_out_of_corp": (
                    dossier.is_out_of_corp if dossier else pilot.get("is_out_of_corp", False)
                ),
                "is_verified": (
                    dossier.is_verified if dossier else pilot.get("is_verified", False)
                ),
                "notes": (dossier.notes if dossier else pilot.get("notes", "")),
                "in_database": bool(dossier),
                "dossier_id": dossier.id if dossier else pilot.get("dossier_id"),
                "activity": combat_intel.get("activity") or pilot.get("activity") or {},
                "zkill_synced": True,
            }
        )
        return pilot

    @classmethod
    def calculate_activity_profile(
        cls,
        pilots: List[Dict[str, Any]],
        timestamps_found: Optional[List[str]] = None,
        initial_hourly: Optional[Dict[int, int]] = None,
    ) -> Dict[str, Any]:
        """
        Calculates aggregate 24-hour UTC activity distribution, timezone breakdown (EUTZ/USTZ/AUTZ),
        and dominant operational timezone across chat timestamps and pilot combat histories.
        """
        hourly_distribution: Dict[int, int] = {h: 0 for h in range(24)}
        if initial_hourly:
            for h, count in initial_hourly.items():
                try:
                    h_int = int(h) % 24
                    hourly_distribution[h_int] += int(count)
                except (ValueError, TypeError):
                    pass

        # Aggregate pilot zKill activities / timestamps
        for p in pilots:
            # 1. Pilot direct timestamps from chat
            p_timestamps = p.get("timestamps") or []
            for ts in p_timestamps:
                parts = ts.split()
                time_token = parts[-1] if parts else ts
                t_parts = time_token.split(":")
                if t_parts and t_parts[0].isdigit():
                    h_int = int(t_parts[0]) % 24
                    hourly_distribution[h_int] += 1

            # 2. Pilot zKill hourly activity
            p_act = p.get("activity") or {}
            if isinstance(p_act, dict):
                if "matrix" in p_act and isinstance(p_act["matrix"], (list, tuple)):
                    for row in p_act["matrix"]:
                        if isinstance(row, (list, tuple)):
                            for h_int, val in enumerate(row):
                                if 0 <= h_int < 24 and isinstance(val, (int, float)):
                                    hourly_distribution[h_int] += int(val)
                else:
                    for k, v in p_act.items():
                        if isinstance(v, (int, float)):
                            try:
                                h_int = int(k) % 24
                                hourly_distribution[h_int] += int(v)
                            except (ValueError, TypeError):
                                pass
                        elif isinstance(v, dict):
                            for sub_k, sub_v in v.items():
                                try:
                                    h_int = int(sub_k) % 24
                                    hourly_distribution[h_int] += int(sub_v)
                                except (ValueError, TypeError):
                                    pass

        # Standard EVE Online operational timezones:
        # EUTZ: 14:00 - 22:00 UTC (hours 14..21)
        # AUTZ: 08:00 - 14:00 UTC (hours 8..13)
        # USTZ: 22:00 - 08:00 UTC (hours 22..23 and 0..7)
        tz_counts = {"EUTZ": 0, "USTZ": 0, "AUTZ": 0}
        for h, count in hourly_distribution.items():
            if 14 <= h < 22:
                tz_counts["EUTZ"] += count
            elif 8 <= h < 14:
                tz_counts["AUTZ"] += count
            else:
                tz_counts["USTZ"] += count

        total_timestamped_events = sum(hourly_distribution.values())

        primary_tz = "UNKNOWN"
        if total_timestamped_events > 0:
            primary_tz = max(tz_counts, key=tz_counts.get)

        first_seen = None
        last_seen = None
        if timestamps_found:
            first_seen = timestamps_found[0]
            last_seen = timestamps_found[-1]

        return {
            "total_events": total_timestamped_events,
            "hourly_distribution": hourly_distribution,
            "primary_timezone": primary_tz,
            "tz_breakdown": tz_counts,
            "first_seen": first_seen,
            "last_seen": last_seen,
        }

    @classmethod
    def enrich_scan(
        cls,
        scan: Any,
        character_names: Optional[List[str]] = None,
        batch_size: int = 5,
    ) -> Dict[str, Any]:
        """
        Progressively enriches a scan's pilot roster via zKillboard & ESI,
        recalculating Black Ops and Capital drop probabilities, threat level, and factors.
        """
        pilots = scan.pilots_data or []
        updated_pilots = []

        # Find candidate pilots to enrich
        targets = []
        if character_names:
            name_set = {n.lower().strip() for n in character_names}
            for p in pilots:
                if p.get("character_name", "").lower().strip() in name_set:
                    targets.append(p)
        else:
            for p in pilots:
                if not p.get("zkill_synced"):
                    targets.append(p)
                    if len(targets) >= batch_size:
                        break

        for p in targets:
            cls.enrich_pilot(p)
            updated_pilots.append(p)

        # Re-pair with D-Scan if available
        dscan_data = None
        if scan.raw_dscan_text and scan.raw_dscan_text.strip():
            dscan_data = DScanParser.parse(
                scan.raw_dscan_text, default_system=scan.solar_system
            )

        # Recalculate drop probabilities
        threat_results = ThreatEngine.calculate_drop_probabilities(
            pilots=pilots,
            solar_system=scan.solar_system,
            dscan_data=dscan_data,
        )
        pilots = ThreatEngine.pair_pilots_to_ships(pilots, dscan_data)

        # Update counters
        cynos_count = sum(1 for p in pilots if p.get("is_cyno_alt"))
        blops_count = sum(1 for p in pilots if p.get("is_blops_pilot"))
        supers_count = sum(
            1 for p in pilots if p.get("is_super_pilot") or p.get("is_titan_pilot")
        )
        capitals_count = sum(1 for p in pilots if p.get("is_capital_pilot"))

        # Recalculate activity profile
        activity_profile = cls.calculate_activity_profile(pilots=pilots)

        scan.pilots_data = pilots
        scan.blops_drop_chance = threat_results["blops_drop_chance"]
        scan.cap_drop_chance = threat_results["cap_drop_chance"]
        scan.threat_level = threat_results["threat_level"]
        scan.threat_factors = threat_results["threat_factors"]
        scan.ship_profile = threat_results["ship_profile"]
        scan.activity_profile = activity_profile
        scan.save()

        total_pilots = len(pilots)
        synced_count = sum(1 for p in pilots if p.get("zkill_synced"))
        remaining_count = total_pilots - synced_count

        return {
            "success": True,
            "scan_id": str(scan.id),
            "blops_drop_chance": scan.blops_drop_chance,
            "cap_drop_chance": scan.cap_drop_chance,
            "threat_level": scan.threat_level,
            "threat_factors": scan.threat_factors,
            "ship_profile": scan.ship_profile,
            "activity_profile": scan.activity_profile,
            "cynos_count": cynos_count,
            "blops_count": blops_count,
            "supers_count": supers_count,
            "capitals_count": capitals_count,
            "pilots": updated_pilots,
            "all_pilots": pilots,
            "total_pilots": total_pilots,
            "synced_count": synced_count,
            "remaining_count": remaining_count,
            "is_finished": remaining_count == 0,
        }

    @classmethod
    def parse(
        cls,
        local_text: str,
        dscan_text: Optional[str] = None,
        default_system: Optional[SolarSystem] = None,
        fetch_zkill: bool = True,
    ) -> Dict[str, Any]:
        """
        Parses raw local chat or pilot roster text, extracts timestamps & activity profile,
        filters out friendly alliances, pairs with D-Scan, and computes drop chances.
        """
        lines = [line.strip() for line in local_text.strip().splitlines() if line.strip()]

        raw_pilots: Dict[str, Dict[str, Any]] = {}
        hourly_distribution: Dict[int, int] = {h: 0 for h in range(24)}
        timestamps_found: List[str] = []
        ignored_pilots_count = 0
        detected_system = default_system

        # Check for system name in channel header lines e.g. "Channel Name: Local : 1DQ1-A"
        for line in lines[:10]:
            if "channel name:" in line.lower() and "local" in line.lower():
                parts = line.split(":")
                if len(parts) >= 3:
                    cand_name = parts[-1].strip()
                    if cand_name and not detected_system:
                        detected_system = SolarSystem.objects.filter(name__iexact=cand_name).first()

        for line in lines:
            if cls.CHANNEL_HEADER_PATTERN.match(line):
                continue

            char_name = ""
            corp_name = ""
            alliance_name = ""
            timestamp_str = ""

            # 1. Match chat log with timestamp format: [ 2026.10.01 10:48:00 ] Pilot Name > Message
            chat_match = cls.TIMESTAMP_CHAT_PATTERN.match(line)
            if chat_match:
                date_part = chat_match.group(1) or ""
                time_part = chat_match.group(2) or ""
                char_name = chat_match.group(3).strip()
                timestamp_str = f"{date_part}{time_part}".strip()

                # Extract hour for activity profile
                time_tokens = time_part.split(":")
                if time_tokens and time_tokens[0].isdigit():
                    hour = int(time_tokens[0])
                    if 0 <= hour < 24:
                        hourly_distribution[hour] += 1
                        timestamps_found.append(timestamp_str)
            else:
                # 2. Member list tab or column format
                parts = [p.strip() for p in line.split("\t") if p.strip()]
                if not parts:
                    parts = [p.strip() for p in re.split(r"\s{2,}", line) if p.strip()]

                if len(parts) >= 3:
                    char_name = parts[0]
                    corp_name = parts[1]
                    alliance_name = parts[2]
                elif len(parts) == 2:
                    char_name = parts[0]
                    corp_name = parts[1]
                elif len(parts) == 1:
                    char_name = parts[0]

            if not char_name or len(char_name) < 2 or char_name.startswith("---") or char_name.startswith("==="):
                continue

            # Check if this pilot or corp/alliance is friendly/ignored
            if cls.is_ignored_entity(corp_name=corp_name, alliance_name=alliance_name):
                ignored_pilots_count += 1
                continue

            if char_name not in raw_pilots:
                raw_pilots[char_name] = {
                    "character_name": char_name,
                    "corporation_name": corp_name,
                    "alliance_name": alliance_name,
                    "timestamps": [timestamp_str] if timestamp_str else [],
                    "message_count": 1 if chat_match else 0,
                }
            else:
                if timestamp_str:
                    raw_pilots[char_name]["timestamps"].append(timestamp_str)
                if chat_match:
                    raw_pilots[char_name]["message_count"] += 1
                if corp_name and not raw_pilots[char_name]["corporation_name"]:
                    raw_pilots[char_name]["corporation_name"] = corp_name
                if alliance_name and not raw_pilots[char_name]["alliance_name"]:
                    raw_pilots[char_name]["alliance_name"] = alliance_name

        # Bulk resolve pilots via internal DB and ESI
        char_names = list(raw_pilots.keys())
        resolved_entities = EntityResolver.resolve_bulk_characters(char_names)

        # Query database dossiers for detected pilots (by name and by resolved character ID)
        dossiers_by_name = {
            d.character_name.lower(): d
            for d in HostilePilotDossier.objects.filter(character_name__in=char_names)
        }
        resolved_cids = [
            e.get("character_id")
            for e in resolved_entities.values()
            if e.get("character_id")
        ]
        dossiers_by_id = {
            d.character_id: d
            for d in HostilePilotDossier.objects.filter(character_id__in=resolved_cids)
            if d.character_id
        }

        pilots_list: List[Dict[str, Any]] = []
        cynos_count = 0
        supers_count = 0
        capitals_count = 0
        blops_count = 0

        for name, data in raw_pilots.items():
            res_info = resolved_entities.get(name.lower()) or {}
            char_id = res_info.get("character_id")
            char_name = res_info.get("character_name") or name
            corp_id = res_info.get("corporation_id")
            corp_name = res_info.get("corporation_name") or data.get("corporation_name") or ""
            corp_ticker = res_info.get("corporation_ticker", "")
            alliance_id = res_info.get("alliance_id")
            alliance_name = res_info.get("alliance_name") or data.get("alliance_name") or ""
            alliance_ticker = res_info.get("alliance_ticker", "")

            # Check if this pilot or corp/alliance is friendly/ignored
            if cls.is_ignored_entity(
                corp_name=corp_name,
                alliance_name=alliance_name,
                ticker=alliance_ticker or corp_ticker,
            ):
                ignored_pilots_count += 1
                continue

            dossier = None
            if char_id and char_id in dossiers_by_id:
                dossier = dossiers_by_id[char_id]
            elif name.lower() in dossiers_by_name:
                dossier = dossiers_by_name[name.lower()]

            if dossier:
                if cls.is_ignored_entity(
                    corp_name=dossier.corporation_name,
                    alliance_name=dossier.alliance_name,
                ):
                    ignored_pilots_count += 1
                    continue

            pilot_entry = {
                "character_id": char_id,
                "character_name": char_name,
                "corporation_id": corp_id,
                "corporation_name": (
                    dossier.corporation_name
                    if (dossier and dossier.corporation_name)
                    else corp_name
                ),
                "corporation_ticker": corp_ticker,
                "alliance_id": alliance_id,
                "alliance_name": (
                    dossier.alliance_name
                    if (dossier and dossier.alliance_name)
                    else alliance_name
                ),
                "alliance_ticker": alliance_ticker,
                "associated_alliance_name": (
                    dossier.associated_alliance_name if dossier else ""
                ),
                "is_cyno_alt": dossier.is_cyno_alt if dossier else False,
                "is_blops_pilot": (
                    getattr(dossier, "is_blops_pilot", False) if dossier else False
                ),
                "is_dread_pilot": (
                    getattr(dossier, "is_dread_pilot", False) if dossier else False
                ),
                "is_fax_pilot": (
                    getattr(dossier, "is_fax_pilot", False) if dossier else False
                ),
                "is_capital_pilot": dossier.is_capital_pilot if dossier else False,
                "is_super_pilot": dossier.is_super_pilot if dossier else False,
                "is_titan_pilot": dossier.is_titan_pilot if dossier else False,
                "is_fc": dossier.is_fc if dossier else False,
                "fc_level": dossier.fc_level if dossier else "",
                "fc_score": dossier.fc_score if dossier else 0,
                "is_bait": dossier.is_bait if dossier else False,
                "bait_level": dossier.bait_level if dossier else "",
                "bait_count": dossier.bait_count if dossier else 0,
                "is_ganker": dossier.is_ganker if dossier else False,
                "ganker_count": dossier.ganker_count if dossier else 0,
                "is_logi_pilot": dossier.is_logi_pilot if dossier else False,
                "logi_count": dossier.logi_count if dossier else 0,
                "is_awox": dossier.is_awox if dossier else False,
                "awox_count": dossier.awox_count if dossier else 0,
                "is_alliance_awox": dossier.is_alliance_awox if dossier else False,
                "alliance_awox_count": dossier.alliance_awox_count if dossier else 0,
                "is_faction_awox": dossier.is_faction_awox if dossier else False,
                "faction_awox_count": dossier.faction_awox_count if dossier else 0,
                "is_rookie": dossier.is_rookie if dossier else False,
                "danger_ratio": 0,
                "gang_ratio": 0,
                "solo_kills": 0,
                "kills_count": 0,
                "losses_count": 0,
                "top_ships": [],
                "likely_ship": "",
                "zkill_labels": (
                    list(dossier.zkill_labels) if dossier and dossier.zkill_labels else []
                ),
                "is_alt": dossier.is_alt if dossier else False,
                "main_character_name": (
                    dossier.main_character.character_name
                    if dossier and dossier.main_character
                    else ""
                ),
                "is_out_of_corp": dossier.is_out_of_corp if dossier else False,
                "is_verified": dossier.is_verified if dossier else False,
                "notes": dossier.notes if dossier else "",
                "timestamps": data["timestamps"],
                "message_count": data["message_count"],
                "in_database": bool(dossier),
                "zkill_synced": bool(
                    dossier
                    and (
                        dossier.zkill_labels
                        or dossier.cyno_count
                        or dossier.fc_score
                        or dossier.bait_count
                        or dossier.awox_count
                        or dossier.is_cyno_alt
                        or dossier.is_blops_pilot
                        or dossier.is_capital_pilot
                    )
                ),
            }

            if fetch_zkill:
                pilot_entry = cls.enrich_pilot(pilot_entry)

            if pilot_entry["is_cyno_alt"]:
                cynos_count += 1
            if pilot_entry["is_blops_pilot"]:
                blops_count += 1
            if pilot_entry["is_super_pilot"] or pilot_entry["is_titan_pilot"]:
                supers_count += 1
            if pilot_entry["is_capital_pilot"]:
                capitals_count += 1

            pilots_list.append(pilot_entry)

        # Alt-main pattern matching within current scan roster
        for i, p1 in enumerate(pilots_list):
            for j, p2 in enumerate(pilots_list):
                if i != j and not p1.get("is_alt"):
                    stem1 = AltDetectionEngine.extract_stem(p1.get("character_name", ""))
                    stem2 = AltDetectionEngine.extract_stem(p2.get("character_name", ""))
                    if stem1 and stem2 and stem1 == stem2 and len(stem1) >= 3:
                        p1_is_alt_suffix = any(
                            s in p1.get("character_name", "").lower()
                            for s in ("cyno", "alt", "dread", "fax", "eyes", "scout")
                        )
                        if p1_is_alt_suffix:
                            p1["is_alt"] = True
                            p1["main_character_name"] = p2.get("character_name")

        # Activity Profile Analysis
        activity_profile = cls.calculate_activity_profile(
            pilots=pilots_list,
            timestamps_found=timestamps_found,
            initial_hourly=hourly_distribution,
        )

        # Couple with D-Scan
        dscan_data = None
        if dscan_text and dscan_text.strip():
            dscan_data = DScanParser.parse(dscan_text, default_system=detected_system)
            if not detected_system and dscan_data.get("solar_system"):
                detected_system = dscan_data.get("solar_system")

        # Threat Drop Probabilities & Pilot Ship Profile
        threat_results = ThreatEngine.calculate_drop_probabilities(
            pilots=pilots_list,
            solar_system=detected_system,
            dscan_data=dscan_data,
        )

        pilots_list = ThreatEngine.pair_pilots_to_ships(pilots_list, dscan_data)

        # Sort pilots: dangerous pilots first (supers > capitals > cynos > blops > fcs > others)
        def pilot_sort_key(p: Dict[str, Any]) -> int:
            score = 0
            if p.get("is_titan_pilot") or p.get("is_super_pilot"):
                score += 100
            if p.get("is_capital_pilot"):
                score += 80
            if p.get("is_cyno_alt"):
                score += 60
            if p.get("is_blops_pilot"):
                score += 50
            if p.get("is_fc"):
                score += 40
            if p.get("is_out_of_corp"):
                score += 20
            if p.get("in_database"):
                score += 10
            return score

        pilots_list.sort(key=pilot_sort_key, reverse=True)

        return {
            "solar_system": detected_system,
            "pilot_count": len(pilots_list),
            "hostile_count": len(pilots_list),
            "ignored_count": ignored_pilots_count,
            "cynos_count": cynos_count,
            "blops_count": blops_count,
            "supers_count": supers_count,
            "capitals_count": capitals_count,
            "pilots": pilots_list,
            "activity_profile": activity_profile,
            "blops_drop_chance": threat_results["blops_drop_chance"],
            "cap_drop_chance": threat_results["cap_drop_chance"],
            "threat_level": threat_results["threat_level"],
            "threat_factors": threat_results["threat_factors"],
            "ship_profile": threat_results["ship_profile"],
            "dscan_data": dscan_data,
            "raw_local_text": local_text,
            "raw_dscan_text": dscan_text or "",
        }
