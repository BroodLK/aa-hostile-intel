"""
Threat Engine for calculating Black Ops & Capital drop probabilities and analyzing threat signatures
"""

# Standard Library
from typing import Any, Dict, List, Optional

# Third Party
from eve_sde.models import SolarSystem

# AA Hostile Intel
from hostile.models import HostileDoctrine, HostileStagingSystem, SystemObservation

BLOPS_HULLS = {"panther", "redeemer", "widow", "sin"}

COVERT_RECON_HULLS = {
    "arazu",
    "falcon",
    "pilgrim",
    "rapier",
    "hound",
    "manticore",
    "nemesis",
    "purifier",
    "astero",
    "stratios",
    "anathema",
    "cheetah",
    "covops",
    "helios",
    "buzzard",
    "prospect",
    "prorator",
    "crane",
    "prowler",
    "viator",
}

CYNO_HULLS = {
    "arazu",
    "falcon",
    "pilgrim",
    "rapier",
    "venture",
    "prospect",
    "sigil",
    "badger",
    "epithal",
    "iteron mark v",
    "kestrel",
    "loki",
    "proteus",
    "legion",
    "tengu",
}

HIC_DIC_HULLS = {
    "devoter",
    "onyx",
    "phobos",
    "broadsword",
    "sabre",
    "flycatcher",
    "erisma",
    "heretic",
}

SUPER_TITAN_HULLS = {
    "erebus",
    "avatar",
    "ragnarok",
    "leviathan",
    "vanquisher",
    "komodo",
    "molok",
    "nyx",
    "aeon",
    "hel",
    "wyvern",
    "vendetta",
    "revenant",
}

CAPITAL_HULLS = {
    "naglfar",
    "revelation",
    "moros",
    "phoenix",
    "zirnitra",
    "archon",
    "chimera",
    "thanatos",
    "nidhoggur",
    "apostle",
    "minokawa",
    "lif",
    "ninazu",
    "dagon",
    "caiman",
    "loggerhead",
    "vehement",
}


class ThreatEngine:
    """Calculates tactical threat parameters, Blops drop chance, and Capital drop chance"""

    @classmethod
    def categorize_dscan_ships(cls, dscan_data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Categorizes ships detected on D-Scan into tactical threat groupings"""
        profile = {
            "blops": [],
            "recons": [],
            "cynos": [],
            "bubbles": [],
            "supers": [],
            "capitals": [],
            "battleships": [],
            "cruisers": [],
            "frigates": [],
            "others": [],
            "total_ships": 0,
        }

        if not dscan_data or "all_items" not in dscan_data:
            return profile

        for item in dscan_data.get("all_items", []):
            if item.get("is_structure"):
                continue

            type_name = (item.get("type_name") or "").lower()
            name = (item.get("name") or "").lower()
            entry = {"name": item.get("name"), "type_name": item.get("type_name"), "distance": item.get("distance")}

            if not type_name:
                continue

            profile["total_ships"] += 1

            if any(h in type_name for h in SUPER_TITAN_HULLS):
                profile["supers"].append(entry)
            elif any(h in type_name for h in CAPITAL_HULLS):
                profile["capitals"].append(entry)
            elif any(h in type_name for h in BLOPS_HULLS):
                profile["blops"].append(entry)
            elif any(h in type_name for h in COVERT_RECON_HULLS):
                profile["recons"].append(entry)
            elif any(h in type_name for h in HIC_DIC_HULLS) or "warp disruption" in name or "interdiction" in name:
                profile["bubbles"].append(entry)
            elif any(h in type_name for h in CYNO_HULLS) or "cyno" in name:
                profile["cynos"].append(entry)
            elif "battleship" in type_name or any(
                bs in type_name
                for bs in [
                    "tempest",
                    "megathron",
                    "apocalypse",
                    "raven",
                    "rokh",
                    "typhoon",
                    "machariel",
                    "nightmare",
                    "vindicator",
                    "bhaalgorn",
                    "praxis",
                ]
            ):
                profile["battleships"].append(entry)
            elif "cruiser" in type_name or "battlecruiser" in type_name:
                profile["cruisers"].append(entry)
            elif "frigate" in type_name or "destroyer" in type_name:
                profile["frigates"].append(entry)
            else:
                profile["others"].append(entry)

        return profile

    @classmethod
    def calculate_drop_probabilities(
        cls,
        pilots: List[Dict[str, Any]],
        solar_system: Optional[SolarSystem] = None,
        dscan_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Computes Black Ops drop chance %, Capital drop chance %, threat level, and contributing factors
        """
        blops_score = 0
        cap_score = 0
        factors: List[str] = []

        # 1. Pilot Roster Indicators
        cyno_pilots = [p for p in pilots if p.get("is_cyno_alt")]
        blops_pilots = [p for p in pilots if p.get("is_blops_pilot")]
        cap_pilots = [p for p in pilots if p.get("is_capital_pilot") or p.get("is_dread_pilot") or p.get("is_fax_pilot")]
        super_pilots = [p for p in pilots if p.get("is_super_pilot") or p.get("is_titan_pilot")]
        fc_pilots = [p for p in pilots if p.get("is_fc")]
        out_of_corp_pilots = [p for p in pilots if p.get("is_out_of_corp")]

        if cyno_pilots:
            names = ", ".join(p.get("character_name", "") for p in cyno_pilots[:3])
            blops_score += min(60, len(cyno_pilots) * 30)
            cap_score += min(40, len(cyno_pilots) * 20)
            factors.append(f"{len(cyno_pilots)} confirmed Cyno Alt(s) in local ({names})")

        if blops_pilots:
            names = ", ".join(p.get("character_name", "") for p in blops_pilots[:3])
            blops_score += min(45, len(blops_pilots) * 25)
            factors.append(f"{len(blops_pilots)} known Black Ops hunter(s) in local ({names})")

        if super_pilots:
            names = ", ".join(p.get("character_name", "") for p in super_pilots[:3])
            cap_score += 65
            factors.append(f"Supercapital/Titan pilot(s) active in local ({names})")

        if cap_pilots:
            names = ", ".join(p.get("character_name", "") for p in cap_pilots[:3])
            cap_score += min(50, len(cap_pilots) * 25)
            factors.append(f"{len(cap_pilots)} Capital/Dread pilot(s) in local ({names})")

        if fc_pilots:
            names = ", ".join(p.get("character_name", "") for p in fc_pilots[:2])
            blops_score += 15
            cap_score += 20
            factors.append(f"Hostile Fleet Commander(s) identified in local ({names})")

        high_danger_pilots = [
            p for p in pilots if p.get("danger_ratio", 0) >= 70 and p.get("kills_count", 0) >= 15
        ]
        if high_danger_pilots:
            names = ", ".join(
                f"{p.get('character_name')} ({p.get('danger_ratio')}% Danger)"
                for p in high_danger_pilots[:3]
            )
            factors.append(
                f"{len(high_danger_pilots)} high-danger PvP pilot(s) in local ({names})"
            )
            blops_score += min(25, len(high_danger_pilots) * 8)
            cap_score += min(20, len(high_danger_pilots) * 5)

        bait_pilots = [p for p in pilots if p.get("is_bait")]
        if bait_pilots:
            names = ", ".join(p.get("character_name", "") for p in bait_pilots[:3])
            blops_score += 15
            factors.append(f"{len(bait_pilots)} Bait pilot(s) active in local ({names})")

        ganker_pilots = [p for p in pilots if p.get("is_ganker")]
        if ganker_pilots:
            names = ", ".join(p.get("character_name", "") for p in ganker_pilots[:3])
            factors.append(f"{len(ganker_pilots)} Highsec Ganker(s) detected in local ({names})")

        if out_of_corp_pilots:
            factors.append(f"{len(out_of_corp_pilots)} Out-of-Corp/Undercover alt(s) detected in local")
            blops_score += 10

        # 2. Coupled D-Scan Analysis
        ship_profile = cls.categorize_dscan_ships(dscan_data)

        if ship_profile["blops"]:
            blops_score += 60
            b_names = ", ".join(f"{s['type_name']} ({s['distance']})" for s in ship_profile["blops"][:2])
            factors.append(f"Black Ops Battleship(s) detected on D-Scan: {b_names}")

        if ship_profile["recons"]:
            blops_score += 30
            r_names = ", ".join(s["type_name"] for s in ship_profile["recons"][:3])
            factors.append(f"Covert Recon / Stealth Bomber(s) on D-Scan: {r_names}")

        if ship_profile["supers"]:
            cap_score += 80
            s_names = ", ".join(s["type_name"] for s in ship_profile["supers"][:2])
            factors.append(f"Supercapital hull(s) confirmed on grid/D-Scan: {s_names}")

        if ship_profile["capitals"]:
            cap_score += 60
            c_names = ", ".join(s["type_name"] for s in ship_profile["capitals"][:3])
            factors.append(f"Capital ship(s) on D-Scan: {c_names}")

        if ship_profile["bubbles"]:
            cap_score += 25
            blops_score += 15
            factors.append(f"Interdictor bubble(s) / HICs active on D-Scan ({len(ship_profile['bubbles'])})")

        # 3. System Staging and Observation Intel
        if solar_system:
            stagings = HostileStagingSystem.objects.filter(solar_system=solar_system)
            if stagings.exists():
                st_types = ", ".join(st.get_staging_type_display() for st in stagings)
                factors.append(f"System is a confirmed hostile staging ({st_types})")
                for st in stagings:
                    if st.max_cap_form > 0 or st.max_subcap_form > 0:
                        factors.append(
                            f"Staging escalation profile: ~{st.max_cap_form} capitals, ~{st.max_subcap_form} subcaps"
                        )
                if stagings.filter(staging_type__in=["HUNTING", "SPECIALTY"]).exists():
                    blops_score += 35
                if stagings.filter(staging_type__in=["CAPITAL", "PRIMARY"]).exists():
                    cap_score += 35

            obs_qs = SystemObservation.objects.filter(solar_system=solar_system).order_by("-created_at")[:5]
            for obs in obs_qs:
                tag_names = list(obs.tags.values_list("name", flat=True))
                if "Cyno Beacon" in tag_names:
                    blops_score += 25
                    cap_score += 20
                    factors.append(f"System observation notes active Cyno Beacon: {obs.observation_text[:80]}")
                if "Capital Standby" in tag_names:
                    cap_score += 30
                    factors.append(f"Observation flagged Capital Standby: {obs.observation_text[:80]}")
                if "Gate Camp" in tag_names or "Smartbombing" in tag_names:
                    blops_score += 15
                    factors.append("Gate camp / Smartbombing active in system")

            # Check doctrines for active hostile alliances
            active_alliances = {p.get("alliance_name") for p in pilots if p.get("alliance_name")}
            if active_alliances:
                blops_doctrines = HostileDoctrine.objects.filter(
                    alliance__alliance_name__in=active_alliances, role_type="SPECIALTY"
                )
                if blops_doctrines.exists():
                    blops_score += 15
                    factors.append(
                        f"Hostile alliance has registered Black Ops/T3C doctrines: {blops_doctrines.first().name}"
                    )

                cap_doctrines = HostileDoctrine.objects.filter(
                    alliance__alliance_name__in=active_alliances, role_type="CAPITAL_SUPER"
                )
                if cap_doctrines.exists():
                    cap_score += 20
                    factors.append(
                        f"Hostile alliance maintains Capital/Super doctrines: {cap_doctrines.first().name}"
                    )

        # Baseline noise floor if unknown hostiles present
        if len(pilots) > 5 and blops_score == 0:
            blops_score = 10
        if len(pilots) > 10 and cap_score == 0:
            cap_score = 10

        blops_chance = min(99, max(0, blops_score))
        cap_chance = min(99, max(0, cap_score))

        if ship_profile["blops"] and (cyno_pilots or ship_profile["recons"]):
            blops_chance = 99
        if ship_profile["supers"] or (ship_profile["capitals"] and cyno_pilots):
            cap_chance = 99

        # Overall Threat Level
        max_chance = max(blops_chance, cap_chance)
        if max_chance >= 75 or super_pilots:
            threat_level = "CRITICAL"
        elif max_chance >= 50 or (cyno_pilots and (blops_pilots or cap_pilots)):
            threat_level = "HIGH"
        elif max_chance >= 25 or len(cyno_pilots) > 0 or len(out_of_corp_pilots) > 1:
            threat_level = "MODERATE"
        else:
            threat_level = "LOW"

        if not factors:
            factors.append("No active cynos, capital pilots, or combat recons detected in current scan.")

        return {
            "blops_drop_chance": blops_chance,
            "cap_drop_chance": cap_chance,
            "threat_level": threat_level,
            "threat_factors": factors,
            "ship_profile": ship_profile,
        }

    @classmethod
    def pair_pilots_to_ships(
        cls, pilots: List[Dict[str, Any]], dscan_data: Optional[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Cross-references pilots in local with detected D-Scan hulls to build likely ship assignments
        """
        if not dscan_data:
            return pilots

        ship_profile = cls.categorize_dscan_ships(dscan_data)
        available_blops = list(ship_profile["blops"])
        available_recons = list(ship_profile["recons"])
        available_supers = list(ship_profile["supers"])
        available_capitals = list(ship_profile["capitals"])
        available_cynos = list(ship_profile["cynos"])

        for pilot in pilots:
            assigned_ship = ""
            if (pilot.get("is_super_pilot") or pilot.get("is_titan_pilot")) and available_supers:
                match = available_supers.pop(0)
                assigned_ship = f"{match['type_name']} (Supercapital)"
            elif (pilot.get("is_capital_pilot") or pilot.get("is_dread_pilot") or pilot.get("is_fax_pilot")) and available_capitals:
                match = available_capitals.pop(0)
                assigned_ship = f"{match['type_name']} (Capital)"
            elif pilot.get("is_blops_pilot") and available_blops:
                match = available_blops.pop(0)
                assigned_ship = f"{match['type_name']} (Black Ops)"
            elif pilot.get("is_cyno_alt") and available_recons:
                match = available_recons.pop(0)
                assigned_ship = f"{match['type_name']} (Covert Cyno)"
            elif pilot.get("is_cyno_alt") and available_cynos:
                match = available_cynos.pop(0)
                assigned_ship = f"{match['type_name']} (Cyno Ship)"

            if assigned_ship:
                pilot["dscan_match"] = assigned_ship
                pilot["likely_ship"] = assigned_ship
            elif not pilot.get("likely_ship") and pilot.get("top_ships"):
                pilot["likely_ship"] = ", ".join(pilot.get("top_ships", [])[:2])

        return pilots
