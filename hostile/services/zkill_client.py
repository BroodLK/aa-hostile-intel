"""
zKillboard statistics and intelligence client for hostile entities
"""

# Standard Library
import time
from typing import Any, Dict, Iterable, List, Optional, Set

# Third Party
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Django
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

# Alliance Auth
from allianceauth.eveonline.models import EveAllianceInfo, EveCorporationInfo
from allianceauth.services.hooks import get_extension_logger

# AA Hostile Intel
from eve_sde.models import ItemType, SolarSystem
from hostile.app_settings import (
    HOSTILE_ZKILL_MIN_REQUEST_INTERVAL,
    HOSTILE_ZKILL_USER_AGENT,
)
from hostile.models import HostileAlliance, HostileCorporation, HostilePilotDossier
from hostile.services.esi_client import esi_client
from hostile.services.intel_manager import IntelManager
from hostile.services.sov_engine import SovAnalysisEngine

logger = get_extension_logger(__name__)


def _get_val(obj: Any, key: str, default: Any = None) -> Any:
    """Helper to safely extract keys across dicts, Pydantic models, or objects"""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    if isinstance(obj, (list, tuple)) and obj:
        first = obj[0]
        if isinstance(first, dict):
            return first.get(key, default)
        return getattr(first, key, default)
    return getattr(obj, key, default)


class ZKillClient:
    """Client for querying zKillboard public entity statistics with polite rate-limiting and retry handling"""

    BASE_URL = "https://zkillboard.com/api/stats"
    TIMEOUT = 30

    def __init__(self) -> None:
        self._last_call = 0.0
        self._session = requests.Session()
        retries = Retry(
            total=3,
            backoff_factor=2,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        self._session.mount("https://", HTTPAdapter(max_retries=retries))

    def _get_user_agent(self) -> str:
        """Constructs a polite User-Agent with maintainer contact email per zKillboard requirements"""
        if HOSTILE_ZKILL_USER_AGENT:
            return HOSTILE_ZKILL_USER_AGENT
        contact_email = getattr(
            settings,
            "ESI_USER_CONTACT_EMAIL",
            getattr(settings, "DEFAULT_FROM_EMAIL", "admin@localhost"),
        )
        return f"Alliance Auth Hostile Intel Plugin - Maintainer: {contact_email}"

    def _get(self, url: str) -> Optional[requests.Response]:
        """
        Helper to perform GET requests to zKillboard with rate limiting.
        Enforces a minimum interval between calls and sets appropriate compression and contact headers.
        """
        now = time.time()
        elapsed = now - self._last_call
        min_interval = float(HOSTILE_ZKILL_MIN_REQUEST_INTERVAL)
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)

        headers = {
            "User-Agent": self._get_user_agent(),
            "Accept-Encoding": "gzip",
        }
        logger.debug("Fetching from zKillboard: %s", url)
        try:
            response = self._session.get(url, headers=headers, timeout=self.TIMEOUT)
            self._last_call = time.time()
            return response
        except Exception as e:
            self._last_call = time.time()
            logger.error("Failed to fetch from zKillboard URL %s: %s", url, e)
            return None

    def _fetch_stats(self, endpoint: str) -> Optional[Dict[str, Any]]:
        """Makes a GET request to zKillboard stats API with polite rate-limiting"""
        url = f"{self.BASE_URL}/{endpoint}/"
        response = self._get(url)
        if response is None:
            return None
        if response.status_code == 200:
            try:
                return response.json()
            except Exception as e:
                logger.error("Failed to decode JSON from zKillboard %s: %s", url, e)
                return None
        logger.warning(
            "zKillboard stats returned status %s for endpoint %s",
            response.status_code,
            endpoint,
        )
        return None

    @staticmethod
    def _get_rolling_3m_keys() -> List[str]:
        """Generates list of month strings in YYYYMM format for the last 3 rolling months"""
        now = timezone.now()
        y, m = now.year, now.month
        keys = []
        for _ in range(3):
            keys.append(f"{y:04d}{m:02d}")
            m -= 1
            if m == 0:
                m = 12
                y -= 1
        return keys

    @staticmethod
    def _parse_day_index(day_key: Any, all_keys: Optional[Iterable[Any]] = None) -> Optional[int]:
        """
        Parses various day representations into a canonical index:
        0 = Monday, 1 = Tuesday, 2 = Wednesday, 3 = Thursday, 4 = Friday, 5 = Saturday, 6 = Sunday.
        """
        if isinstance(day_key, str):
            day_str = day_key.strip().lower()
            named_days = {
                "mon": 0, "monday": 0,
                "tue": 1, "tues": 1, "tuesday": 1,
                "wed": 2, "wednesday": 2,
                "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
                "fri": 4, "friday": 4,
                "sat": 5, "saturday": 5,
                "sun": 6, "sunday": 6,
            }
            if day_str in named_days:
                return named_days[day_str]
            try:
                d_int = int(day_str)
            except ValueError:
                return None
        elif isinstance(day_key, int):
            d_int = day_key
        else:
            return None

        # Check numeric keys present in dataset
        num_keys = set()
        if all_keys:
            for k in all_keys:
                try:
                    num_keys.add(int(str(k).strip()))
                except (ValueError, TypeError):
                    pass

        # 1-indexed (1..7) ISO where 1=Mon, 7=Sun
        if 7 in num_keys and 0 not in num_keys:
            if 1 <= d_int <= 7:
                return d_int - 1

        # 0-indexed where 0=Sunday (0=Sun, 1=Mon, 2=Tue, ..., 6=Sat) - standard in zKillboard/JS/PHP
        if 0 <= d_int <= 6:
            return (d_int - 1) % 7

        if 1 <= d_int <= 7:
            return d_int - 1

        return None

    @staticmethod
    def _build_activity_heatmap(
        data: Dict[str, Any],
        sov_distribution: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Builds a 7-day x 24-hour activity heatmap matrix (Monday through Sunday x 0-23 UTC).
        Parses actual zKillboard activity / hourly data directly from the 3-month activity payload.
        """
        day_names = [
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        ]
        matrix = [[0] * 24 for _ in range(7)]

        activity_data = (
            data.get("activity")
            or data.get("hourly")
            or data.get("hours")
            or data.get("heatmap")
            or data.get("heatMap")
            or data.get("activity_heatmap")
            or data.get("hourlyStats")
            or data.get("days")
            or {}
        )

        if isinstance(activity_data, dict) and activity_data:
            all_day_keys = list(activity_data.keys())
            for day_key, val in activity_data.items():
                row_idx = ZKillClient._parse_day_index(day_key, all_day_keys)
                if row_idx is None or not (0 <= row_idx < 7):
                    continue

                if isinstance(val, (list, tuple)):
                    for h_idx, count in enumerate(val):
                        if 0 <= h_idx < 24 and isinstance(count, (int, float)) and count > 0:
                            matrix[row_idx][h_idx] += int(count)
                elif isinstance(val, dict):
                    for h_key, count in val.items():
                        try:
                            h_int = int(h_key)
                            if 0 <= h_int < 24 and isinstance(count, (int, float)) and count > 0:
                                matrix[row_idx][h_int] += int(count)
                        except (ValueError, TypeError):
                            pass
        elif isinstance(activity_data, (list, tuple)) and len(activity_data) == 7:
            for row_idx, val in enumerate(activity_data):
                if isinstance(val, (list, tuple)):
                    for h_idx, count in enumerate(val):
                        if 0 <= h_idx < 24 and isinstance(count, (int, float)) and count > 0:
                            matrix[row_idx][h_idx] += int(count)
                elif isinstance(val, dict):
                    for h_key, count in val.items():
                        try:
                            h_int = int(h_key)
                            if 0 <= h_int < 24 and isinstance(count, (int, float)) and count > 0:
                                matrix[row_idx][h_int] += int(count)
                        except (ValueError, TypeError):
                            pass

        max_val = 0
        peak_day_idx = 0
        peak_hour = 0
        total_kills = 0

        for d_idx, day_row in enumerate(matrix):
            for h_idx, val in enumerate(day_row):
                if val > max_val:
                    max_val = val
                    peak_day_idx = d_idx
                    peak_hour = h_idx
                total_kills += val

        peak_day_name = day_names[peak_day_idx] if max_val > 0 else "Unknown"

        return {
            "days": day_names,
            "matrix": matrix,
            "max_val": max_val,
            "peak_day": peak_day_name,
            "peak_hour": peak_hour,
            "total_kills_3m": total_kills,
        }

    @staticmethod
    def _extract_weekly_stats(data: Dict[str, Any]) -> tuple[int, int, int]:
        """
        Extracts active PvP characters, 7-day kills count, and 7-day losses count from zKillboard stats response.
        zKillboard API provides 7-day characters and total kills under `activepvp`, while PvP ship losses and kills
        are tracked under `weeklyLabels` (e.g. 'pvp' or 'cat:6').
        """
        activepvp = data.get("activepvp") or {}
        active_characters = (
            activepvp.get("characters", {}).get("count", 0)
            if isinstance(activepvp.get("characters"), dict)
            else (activepvp.get("characters") or 0)
        )

        weekly_kills = (
            activepvp.get("kills", {}).get("count", 0)
            if isinstance(activepvp.get("kills"), dict)
            else (activepvp.get("kills") or 0)
        )

        weekly_losses = 0
        weekly_labels = data.get("weeklyLabels") or {}
        if isinstance(weekly_labels, dict) and weekly_labels:
            if "pvp" in weekly_labels and isinstance(weekly_labels["pvp"], dict):
                weekly_losses = weekly_labels["pvp"].get("shipsLost", 0)
                if not weekly_kills:
                    weekly_kills = weekly_labels["pvp"].get("shipsDestroyed", 0)
            elif "cat:6" in weekly_labels and isinstance(weekly_labels["cat:6"], dict):
                weekly_losses = weekly_labels["cat:6"].get("shipsLost", 0)
                if not weekly_kills:
                    weekly_kills = weekly_labels["cat:6"].get("shipsDestroyed", 0)
            else:
                weekly_losses = max(
                    (v.get("shipsLost", 0) for v in weekly_labels.values() if isinstance(v, dict)),
                    default=0,
                )
                if not weekly_kills:
                    weekly_kills = max(
                        (v.get("shipsDestroyed", 0) for v in weekly_labels.values() if isinstance(v, dict)),
                        default=0,
                    )

        # Fallback to activepvp losses (if provided in custom payload / tests)
        if not weekly_losses and isinstance(activepvp.get("losses"), dict):
            weekly_losses = activepvp["losses"].get("count", 0)
        elif not weekly_losses and isinstance(activepvp.get("losses"), (int, float)):
            weekly_losses = int(activepvp["losses"])

        return int(active_characters or 0), int(weekly_kills or 0), int(weekly_losses or 0)

    def fetch_alliance_stats(self, alliance_id: int) -> Optional[Dict[str, Any]]:
        """Fetches public zKillboard statistics for an alliance"""
        return self._fetch_stats(f"allianceID/{alliance_id}")

    def fetch_corporation_stats(self, corporation_id: int) -> Optional[Dict[str, Any]]:
        """Fetches public zKillboard statistics for a corporation"""
        return self._fetch_stats(f"corporationID/{corporation_id}")

    PILOT_CATEGORIES = {
        "character",
        "characters",
        "pilot",
        "pilots",
        "topcharacters",
        "top_characters",
        "toppilots",
        "top_pilots",
        "kills",
        "damage",
        "points",
        "solo",
        "finalblows",
        "final_blows",
    }
    SHIP_CATEGORIES = {
        "shiptype",
        "ship_type",
        "ship",
        "ships",
        "topships",
        "top_ships",
        "types",
    }
    NON_PILOT_CATEGORIES = {
        "shiptype",
        "ship_type",
        "ship",
        "ships",
        "topships",
        "top_ships",
        "types",
        "solarsystem",
        "solar_system",
        "system",
        "systems",
        "topsystems",
        "location",
        "locations",
        "toplocations",
        "corporation",
        "corporations",
        "corp",
        "corps",
        "topcorporations",
        "alliance",
        "alliances",
        "topalliances",
        "group",
        "groups",
        "itemgroup",
        "region",
        "constellation",
        "celestial",
        "stargate",
    }

    KNOWN_SHIP_NAMES = {
        # Titans
        "avatar", "erebus", "ragnarok", "leviathan", "komodo", "vanquisher", "molok",
        # Supercarriers
        "aeon", "hel", "nyx", "wyvern", "revenant", "vendetta",
        # Dreadnoughts
        "revelation", "naglfar", "moros", "phoenix", "zirnitra", "bane", "karura",
        "hubris", "valravn", "caiman", "chemosh", "vehement", "dreadnought", "lancer",
        # FAX
        "apostle", "minokawa", "ninazu", "lif", "dagon", "loggerhead",
        # Carriers
        "archon", "chimera", "thanatos", "nidhoggur",
        # Black Ops
        "redeemer", "widow", "sin", "panther", "marshal",
        # Battleships & Capitals
        "dominix", "rokh", "megathron", "tempest", "typhoon", "apocalypse", "abaddon",
        "armageddon", "raven", "scorpion", "hyperion", "maelstrom", "rorqual", "orca", "bowhead",
        # Cruisers & Battlecruisers
        "drake", "ferox", "brutix", "myrmidon", "hurricane", "cyclone", "prophecy", "harbinger",
        "cerberus", "eagle", "deimos", "ishtar", "muninn", "vagabond", "zealot", "sacrilege",
        "tengu", "loki", "legion", "proteus", "scimitar", "basilisk", "guardian", "oneiros",
        "falcon", "rook", "rapier", "arazu", "pilgrim", "curse", "lachesis", "huginn",
        # Frigates & Destroyers
        "sabre", "flycatcher", "erisz", "dictor", "hic", "broadsword", "devoter", "onyx", "phobos",
        "stiletto", "malediction", "ares", "claw", "crusader", "taranis", "crow", "raptor",
        "dramiel", "daredevil", "cruor", "succubus", "worm", "orthrus", "gila", "machariel",
        "nightmare", "rattlesnake", "bhaalgorn", "nestor", "barghest", "capsule", "shuttle",
    }

    @classmethod
    def is_invalid_character_id(cls, cid: int, name: str = "") -> bool:
        """
        Validates whether a given ID or name corresponds to a non-pilot entity
        (ship type, solar system, corporation, alliance, stargate, or celestial).
        """
        if not cid or cid <= 0:
            return True

        # In EVE Online ID space:
        # 10M–20M: Regions, 20M–30M: Constellations, 30M–40M: Solar Systems,
        # 40M–50M: Celestials/Stargates, 60M–70M: NPC Stations
        if 10000000 <= cid < 70000000:
            return True

        n_clean = (name or "").strip()
        n_lower = n_clean.lower()

        if n_clean.startswith(
            ("Stargate (", "Moon ", "Planet ", "Sun ", "Asteroid ", "Station ", "Customs Office")
        ):
            return True

        if n_lower in cls.KNOWN_SHIP_NAMES:
            return True

        try:
            if ItemType.objects.filter(id=cid).exists() or (
                n_clean and ItemType.objects.filter(name__iexact=n_clean).exists()
            ):
                return True
            if SolarSystem.objects.filter(id=cid).exists() or (
                n_clean and SolarSystem.objects.filter(name__iexact=n_clean).exists()
            ):
                return True
            if (
                HostileAlliance.objects.filter(alliance_id=cid).exists()
                or EveAllianceInfo.objects.filter(alliance_id=cid).exists()
                or (n_clean and HostileAlliance.objects.filter(alliance_name__iexact=n_clean).exists())
                or (n_clean and EveAllianceInfo.objects.filter(alliance_name__iexact=n_clean).exists())
            ):
                return True
            if (
                HostileCorporation.objects.filter(corporation_id=cid).exists()
                or EveCorporationInfo.objects.filter(corporation_id=cid).exists()
                or (n_clean and HostileCorporation.objects.filter(corporation_name__iexact=n_clean).exists())
                or (n_clean and EveCorporationInfo.objects.filter(corporation_name__iexact=n_clean).exists())
            ):
                return True
        except Exception:
            pass
        return False

    @classmethod
    def cleanup_invalid_pilot_dossiers(cls) -> int:
        """
        Purges any non-pilot entities (ships, solar systems, alliances, corporations, stargates)
        erroneously registered as pilot dossiers. Returns count of deleted entries.
        """
        deleted_count = 0
        try:
            for d in HostilePilotDossier.objects.all():
                if cls.is_invalid_character_id(d.character_id, d.character_name):
                    d.delete()
                    deleted_count += 1
        except Exception as e:
            logger.warning("Error running cleanup_invalid_pilot_dossiers: %s", e)
        return deleted_count

    @staticmethod
    def _classify_ship(ship_name: str, ship_type_id: Optional[int] = None) -> Dict[str, bool]:
        """Classifies ship name into tactical pilot capabilities"""
        flags = {
            "is_titan_pilot": False,
            "is_super_pilot": False,
            "is_dread_pilot": False,
            "is_fax_pilot": False,
            "is_capital_pilot": False,
            "is_blops_pilot": False,
        }
        s = (ship_name or "").lower().strip()
        if not s and ship_type_id:
            try:
                item = ItemType.objects.filter(id=ship_type_id).first()
                if item and item.name:
                    s = item.name.lower().strip()
            except Exception:
                pass

        if not s:
            return flags

        titan_names = ["avatar", "erebus", "ragnarok", "leviathan", "komodo", "vanquisher", "molok", "titan"]
        super_names = ["aeon", "hel", "nyx", "wyvern", "revenant", "vendetta", "supercarrier"]
        dread_names = [
            "revelation", "naglfar", "moros", "phoenix", "zirnitra", "bane", "karura",
            "hubris", "valravn", "caiman", "chemosh", "vehement", "dreadnought", "dread", "lancer"
        ]
        fax_names = ["apostle", "minokawa", "ninazu", "lif", "dagon", "loggerhead", "fax", "force auxiliary"]
        carrier_names = ["archon", "chimera", "thanatos", "nidhoggur", "carrier"]
        blops_names = ["redeemer", "widow", "sin", "panther", "marshal", "black ops", "blops"]

        if any(n in s for n in titan_names):
            flags["is_titan_pilot"] = True
            flags["is_super_pilot"] = True
            flags["is_capital_pilot"] = True
        elif any(n in s for n in super_names):
            flags["is_super_pilot"] = True
            flags["is_capital_pilot"] = True
        elif any(n in s for n in dread_names):
            flags["is_dread_pilot"] = True
            flags["is_capital_pilot"] = True
        elif any(n in s for n in fax_names):
            flags["is_fax_pilot"] = True
            flags["is_capital_pilot"] = True
        elif any(n in s for n in carrier_names):
            flags["is_capital_pilot"] = True
        elif any(n in s for n in blops_names):
            flags["is_blops_pilot"] = True

        return flags

    @staticmethod
    def parse_pilot_labels(
        raw_labels: Any, stats: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Parses zKillboard character behavioral tags and labels.
        Recognized tags:
          - AWOX (10): 10+ corp friendly fire final blows in past year
          - ALLIANCE AWOX (15): 15+ alliance friendly fire final blows in past year
          - FACTION AWOX (20): 20+ faction friendly fire final blows in past year
          - FC (LOW/MEDIUM/HIGH): Fleet command signals (Monitor 20, Command Ship 2, 25+ fleet 1/5). Low (35+), Med (60+), High (100+)
          - BAIT (LOW/MEDIUM/HIGH): Low (4-6), Medium (7-11), High (12+)
          - CYNO (count): 1+ fitted cyno losses in past year
          - GANKER (count): 10+ highsec ganks in past year
          - BLOPS (count): 3+ Black Ops battleship appearances in past 90 days
          - LOGI (count): 12+ Logistics cruiser/frigate appearances in past 90 days
          - CAPITAL (count): 4+ Capital appearances in past 90 days
          - SUPER (count): 2+ Supercarrier appearances in past 90 days
          - TITAN (count): 2+ Titan appearances in past 90 days
          - ROOKIE: Character <180 days old with negative PvP ratio, without cap/super/titan/cyno/bait tags
        """
        # Standard Library
        import re

        result = {
            "is_fc": False,
            "fc_level": "",
            "fc_score": 0,
            "is_awox": False,
            "awox_count": 0,
            "is_alliance_awox": False,
            "alliance_awox_count": 0,
            "is_faction_awox": False,
            "faction_awox_count": 0,
            "is_bait": False,
            "bait_level": "",
            "bait_count": 0,
            "is_ganker": False,
            "ganker_count": 0,
            "is_logi_pilot": False,
            "logi_count": 0,
            "is_cyno_alt": False,
            "cyno_count": 0,
            "is_blops_pilot": False,
            "blops_count": 0,
            "is_capital_pilot": False,
            "capital_count": 0,
            "is_super_pilot": False,
            "super_count": 0,
            "is_titan_pilot": False,
            "titan_count": 0,
            "is_rookie": False,
            "zkill_labels": [],
        }

        label_strings = []
        if isinstance(raw_labels, list):
            for item in raw_labels:
                if isinstance(item, str):
                    label_strings.append(item.strip())
                elif isinstance(item, dict):
                    name = item.get("name") or item.get("label") or item.get("tag") or ""
                    lvl = item.get("level") or item.get("tier") or ""
                    cnt = item.get("count") or item.get("score") or item.get("value") or 0
                    if name and lvl and cnt:
                        label_strings.append(f"{name} {lvl} ({cnt})")
                    elif name and lvl:
                        label_strings.append(f"{name} ({lvl})")
                    elif name and cnt:
                        label_strings.append(f"{name} ({cnt})")
                    elif name:
                        label_strings.append(str(name))
        elif isinstance(raw_labels, dict):
            for k, v in raw_labels.items():
                if isinstance(v, dict):
                    lvl = v.get("level") or ""
                    cnt = v.get("score") or v.get("count") or 0
                    if lvl and cnt:
                        label_strings.append(f"{k} {lvl} ({cnt})")
                    elif lvl:
                        label_strings.append(f"{k} ({lvl})")
                    elif cnt:
                        label_strings.append(f"{k} ({cnt})")
                    else:
                        label_strings.append(str(k))
                elif isinstance(v, (int, float)):
                    label_strings.append(f"{k} ({int(v)})")
                elif isinstance(v, str):
                    label_strings.append(f"{k} ({v})")
                elif v is True:
                    label_strings.append(str(k))
        elif isinstance(raw_labels, str):
            label_strings.append(raw_labels.strip())

        result["zkill_labels"] = label_strings

        for lbl in label_strings:
            text = lbl.strip()
            text_lower = text.lower()

            # 1. Alliance AWOX
            if "alliance awox" in text_lower:
                result["is_alliance_awox"] = True
                m = re.search(r"\((\d+)\)", text)
                cnt = int(m.group(1)) if m else 15
                result["alliance_awox_count"] = max(result["alliance_awox_count"], cnt)
            # 2. Faction AWOX
            elif "faction awox" in text_lower:
                result["is_faction_awox"] = True
                m = re.search(r"\((\d+)\)", text)
                cnt = int(m.group(1)) if m else 20
                result["faction_awox_count"] = max(result["faction_awox_count"], cnt)
            # 3. Corp AWOX
            elif "awox" in text_lower:
                result["is_awox"] = True
                m = re.search(r"\((\d+)\)", text)
                cnt = int(m.group(1)) if m else 10
                result["awox_count"] = max(result["awox_count"], cnt)

            # 4. FC rating
            fc_m = re.search(r"\bfc\b(?:\s+(low|medium|high|candidate))?(?:\s*\(\s*(low|medium|high|candidate)?(?:\s*(\d+))?\s*\)|\s*\((\d+)\)|\s+(\d+))?", text, re.I)
            if fc_m:
                result["is_fc"] = True
                lvl = fc_m.group(1) or fc_m.group(2) or ""
                score = fc_m.group(3) or fc_m.group(4) or fc_m.group(5) or 0
                if lvl:
                    result["fc_level"] = lvl.upper()
                elif not result["fc_level"]:
                    result["fc_level"] = "LOW"
                if score:
                    result["fc_score"] = max(result["fc_score"], int(score))

            # 5. Bait
            bait_m = re.search(r"\bbait\b(?:\s+(low|medium|high))?(?:\s*\(\s*(low|medium|high)?(?:\s*(\d+))?\s*\)|\s*\((\d+)\)|\s+(\d+))?", text, re.I)
            if bait_m:
                result["is_bait"] = True
                lvl = bait_m.group(1) or bait_m.group(2) or ""
                cnt = bait_m.group(3) or bait_m.group(4) or bait_m.group(5) or 0
                if lvl:
                    result["bait_level"] = lvl.upper()
                elif not result["bait_level"]:
                    result["bait_level"] = "LOW"
                if cnt:
                    result["bait_count"] = max(result["bait_count"], int(cnt))

            # 6. Cyno
            cyno_m = re.search(r"\bcyno\b(?:\s*\((\d+)\))?", text, re.I)
            if cyno_m:
                result["is_cyno_alt"] = True
                cnt = int(cyno_m.group(1)) if cyno_m.group(1) else 1
                result["cyno_count"] = max(result["cyno_count"], cnt)

            # 7. Ganker
            ganker_m = re.search(r"\bganker\b(?:\s*\((\d+)\))?", text, re.I)
            if ganker_m:
                result["is_ganker"] = True
                cnt = int(ganker_m.group(1)) if ganker_m.group(1) else 10
                result["ganker_count"] = max(result["ganker_count"], cnt)

            # 8. Blops
            blops_m = re.search(r"\b(?:blops|black\s*ops)\b(?:\s+(low|medium|high))?(?:\s*\(\s*(low|medium|high)?(?:\s*(\d+))?\s*\)|\s*\((\d+)\)|\s+(\d+))?", text, re.I)
            if blops_m:
                result["is_blops_pilot"] = True
                cnt = blops_m.group(3) or blops_m.group(4) or blops_m.group(5) or (int(blops_m.group(1)) if blops_m.group(1) and blops_m.group(1).isdigit() else 3)
                result["blops_count"] = max(result["blops_count"], int(cnt) if str(cnt).isdigit() else 3)

            # 9. Logi
            logi_m = re.search(r"\blogi(?:stics)?\b(?:\s*\((\d+)\))?", text, re.I)
            if logi_m:
                result["is_logi_pilot"] = True
                cnt = int(logi_m.group(1)) if logi_m.group(1) else 12
                result["logi_count"] = max(result["logi_count"], cnt)

            # 10. Capital
            cap_m = re.search(r"\bcapital\b(?:\s*\((\d+)\))?", text, re.I)
            if cap_m:
                result["is_capital_pilot"] = True
                cnt = int(cap_m.group(1)) if cap_m.group(1) else 4
                result["capital_count"] = max(result["capital_count"], cnt)

            # 11. Super
            super_m = re.search(r"\bsuper(?:carrier)?\b(?:\s*\((\d+)\))?", text, re.I)
            if super_m:
                result["is_super_pilot"] = True
                result["is_capital_pilot"] = True
                cnt = int(super_m.group(1)) if super_m.group(1) else 2
                result["super_count"] = max(result["super_count"], cnt)

            # 12. Titan
            titan_m = re.search(r"\btitan\b(?:\s*\((\d+)\))?", text, re.I)
            if titan_m:
                result["is_titan_pilot"] = True
                result["is_super_pilot"] = True
                result["is_capital_pilot"] = True
                cnt = int(titan_m.group(1)) if titan_m.group(1) else 2
                result["titan_count"] = max(result["titan_count"], cnt)

            # 13. Rookie
            if re.search(r"\brookie\b", text, re.I):
                result["is_rookie"] = True

        # Process stats payload heuristics if provided
        if isinstance(stats, dict):
            # Evaluate Rookie logic: Character <180 days old, negative PvP ratio in past 90d, and no cap/super/titan/cyno/bait tags
            has_major_combat_tags = (
                result["is_capital_pilot"]
                or result["is_super_pilot"]
                or result["is_titan_pilot"]
                or result["is_cyno_alt"]
                or result["is_bait"]
            )
            if not has_major_combat_tags:
                info = stats.get("info") or {}
                ships_lost = stats.get("shipsLost", 0) or 0
                ships_destroyed = stats.get("shipsDestroyed", 0) or 0
                if ships_lost > ships_destroyed:
                    # Check age if available
                    sec_status = info.get("sec_status")
                    if "rookie" in str(stats).lower() or ships_lost >= 3:
                        result["is_rookie"] = True

        return result

    def fetch_character_stats(self, character_id: int) -> Optional[Dict[str, Any]]:
        """Queries zKillboard character statistics endpoint with rate limiting and caching"""
        cache_key = f"hostile_zkill_char_stats_{character_id}"
        cached = cache.get(cache_key)
        if cached:
            return cached
        stats = self._fetch_stats(f"characterID/{character_id}")
        if stats:
            cache.set(cache_key, stats, timeout=86400)
        return stats

    def get_character_combat_intelligence(self, character_id: int) -> Dict[str, Any]:
        """
        Fetches character statistics and extracts rich combat intelligence:
        danger ratio, kills/losses, gang/solo ratios, top flown ships, and behavioral tags.
        """
        default_res = {
            "danger_ratio": 0,
            "gang_ratio": 0,
            "solo_kills": 0,
            "kills_count": 0,
            "losses_count": 0,
            "isk_destroyed": 0,
            "isk_lost": 0,
            "top_ships": [],
            "likely_ship": "",
            "zkill_labels": [],
            "is_fc": False,
            "fc_level": "",
            "fc_score": 0,
            "is_awox": False,
            "awox_count": 0,
            "is_alliance_awox": False,
            "alliance_awox_count": 0,
            "is_faction_awox": False,
            "faction_awox_count": 0,
            "is_bait": False,
            "bait_level": "",
            "bait_count": 0,
            "is_ganker": False,
            "ganker_count": 0,
            "is_logi_pilot": False,
            "logi_count": 0,
            "is_cyno_alt": False,
            "cyno_count": 0,
            "is_blops_pilot": False,
            "blops_count": 0,
            "is_capital_pilot": False,
            "capital_count": 0,
            "is_super_pilot": False,
            "super_count": 0,
            "is_titan_pilot": False,
            "titan_count": 0,
            "is_dread_pilot": False,
            "is_fax_pilot": False,
            "is_rookie": False,
        }

        if not character_id:
            return default_res

        stats = self.fetch_character_stats(character_id)
        if not stats or not isinstance(stats, dict):
            return default_res

        ships_destroyed = stats.get("shipsDestroyed", 0) or 0
        ships_lost = stats.get("shipsLost", 0) or 0
        danger_ratio = stats.get("dangerRatio")
        if danger_ratio is None and (ships_destroyed + ships_lost) > 0:
            danger_ratio = int((ships_destroyed / (ships_destroyed + ships_lost)) * 100)
        danger_ratio = danger_ratio or 0

        labels = stats.get("labels") or stats.get("tags") or []
        parsed = self.parse_pilot_labels(labels, stats=stats)

        # Extract top flown ships
        top_ships = []
        top_lists = stats.get("topLists") or []
        if isinstance(top_lists, list):
            for t_item in top_lists:
                if isinstance(t_item, dict) and t_item.get("type") in ("ships", "shipType", "ship_types", "shipTypes"):
                    for val in t_item.get("values", []):
                        if isinstance(val, dict):
                            s_name = val.get("name") or val.get("ship_name") or val.get("typeName")
                            if s_name and s_name not in top_ships:
                                top_ships.append(s_name)

        groups_data = stats.get("groups") or {}
        if isinstance(groups_data, dict):
            for _gid, g_info in groups_data.items():
                if isinstance(g_info, dict):
                    g_name = g_info.get("name") or g_info.get("groupName")
                    if g_name and g_name not in top_ships and g_info.get("kills", 0) > 0:
                        top_ships.append(g_name)

        likely_ship = top_ships[0] if top_ships else ""

        # Check if ship classes imply dread or fax specifically
        for s in top_ships:
            cls_flags = self._classify_ship(s)
            for k, v in cls_flags.items():
                if v:
                    parsed[k] = True

        activity = stats.get("activity") or stats.get("hourly") or {}

        res = {
            **default_res,
            **parsed,
            "danger_ratio": danger_ratio,
            "gang_ratio": stats.get("gangRatio", 0) or 0,
            "solo_kills": stats.get("soloKills", 0) or stats.get("solo", 0) or 0,
            "kills_count": ships_destroyed,
            "losses_count": ships_lost,
            "isk_destroyed": stats.get("iskDestroyed", 0) or 0,
            "isk_lost": stats.get("iskLost", 0) or 0,
            "top_ships": top_ships[:5],
            "likely_ship": likely_ship,
            "activity": activity,
        }
        return res

    def sync_pilot_dossier_zkill(self, dossier: HostilePilotDossier) -> bool:
        """Fetches character statistics and behavioral labels for a specific pilot dossier"""
        if not dossier or not dossier.character_id:
            return False
        stats = self.fetch_character_stats(dossier.character_id)
        if not stats or not isinstance(stats, dict):
            return False

        combat_intel = self.get_character_combat_intelligence(dossier.character_id)
        parsed = combat_intel

        updated = False

        # 1. Update basic combat metrics
        if combat_intel.get("danger_ratio", 0) != dossier.danger_ratio:
            dossier.danger_ratio = combat_intel.get("danger_ratio", 0)
            updated = True
        if combat_intel.get("gang_ratio", 0) != dossier.gang_ratio:
            dossier.gang_ratio = combat_intel.get("gang_ratio", 0)
            updated = True
        if combat_intel.get("top_ships") and dossier.top_ships != combat_intel.get("top_ships"):
            dossier.top_ships = combat_intel.get("top_ships")
            updated = True
        if combat_intel.get("likely_ship") and dossier.likely_ship != combat_intel.get("likely_ship"):
            dossier.likely_ship = combat_intel.get("likely_ship")
            updated = True

        # 2. Extract character metadata (sec status, birthday, tickers)
        info = stats.get("info") or {}
        if isinstance(info, dict):
            if "sec_status" in info and info["sec_status"] is not None:
                try:
                    sec_val = round(float(info["sec_status"]), 2)
                    if dossier.security_status != sec_val:
                        dossier.security_status = sec_val
                        updated = True
                except (ValueError, TypeError):
                    pass
            if "birthday" in info and info["birthday"]:
                try:
                    from datetime import datetime
                    bday_str = str(info["birthday"]).split("T")[0].split(" ")[0]
                    bday_date = datetime.strptime(bday_str, "%Y-%m-%d").date()
                    if dossier.birthday != bday_date:
                        dossier.birthday = bday_date
                        updated = True
                except Exception:
                    pass

        # 3. Resolve tickers via EntityResolver if missing
        if not dossier.corporation_ticker or not dossier.alliance_ticker or dossier.security_status is None:
            try:
                from hostile.services.entity_resolver import EntityResolver

                resolved = EntityResolver.resolve_character(dossier.character_id)
                if resolved:
                    if resolved.get("corporation_ticker") and dossier.corporation_ticker != resolved["corporation_ticker"]:
                        dossier.corporation_ticker = resolved["corporation_ticker"]
                        updated = True
                    if resolved.get("alliance_ticker") and dossier.alliance_ticker != resolved["alliance_ticker"]:
                        dossier.alliance_ticker = resolved["alliance_ticker"]
                        updated = True
                    if dossier.security_status is None and resolved.get("security_status") is not None:
                        dossier.security_status = resolved["security_status"]
                        updated = True
                    if not dossier.birthday and resolved.get("birthday"):
                        dossier.birthday = resolved["birthday"]
                        updated = True
            except Exception as e:
                logger.debug("Failed entity resolver ticker lookup during pilot sync: %s", e)

        # 4. Behavioral classification tags
        if parsed["is_fc"] and not dossier.is_fc:
            dossier.is_fc = True
            updated = True
        if parsed["fc_level"] and dossier.fc_level != parsed["fc_level"]:
            dossier.fc_level = parsed["fc_level"]
            updated = True
        if parsed["fc_score"] > dossier.fc_score:
            dossier.fc_score = parsed["fc_score"]
            updated = True

        if parsed["is_awox"] and not dossier.is_awox:
            dossier.is_awox = True
            updated = True
        if parsed["awox_count"] > dossier.awox_count:
            dossier.awox_count = parsed["awox_count"]
            updated = True

        if parsed["is_alliance_awox"] and not dossier.is_alliance_awox:
            dossier.is_alliance_awox = True
            updated = True
        if parsed["alliance_awox_count"] > dossier.alliance_awox_count:
            dossier.alliance_awox_count = parsed["alliance_awox_count"]
            updated = True

        if parsed["is_faction_awox"] and not dossier.is_faction_awox:
            dossier.is_faction_awox = True
            updated = True
        if parsed["faction_awox_count"] > dossier.faction_awox_count:
            dossier.faction_awox_count = parsed["faction_awox_count"]
            updated = True

        if parsed["is_bait"] and not dossier.is_bait:
            dossier.is_bait = True
            updated = True
        if parsed["bait_level"] and dossier.bait_level != parsed["bait_level"]:
            dossier.bait_level = parsed["bait_level"]
            updated = True
        if parsed["bait_count"] > dossier.bait_count:
            dossier.bait_count = parsed["bait_count"]
            updated = True

        if parsed["is_ganker"] and not dossier.is_ganker:
            dossier.is_ganker = True
            updated = True
        if parsed["ganker_count"] > dossier.ganker_count:
            dossier.ganker_count = parsed["ganker_count"]
            updated = True

        if parsed["is_logi_pilot"] and not dossier.is_logi_pilot:
            dossier.is_logi_pilot = True
            updated = True
        if parsed["logi_count"] > dossier.logi_count:
            dossier.logi_count = parsed["logi_count"]
            updated = True

        if parsed["is_cyno_alt"] and not dossier.is_cyno_alt:
            dossier.is_cyno_alt = True
            updated = True
        if parsed["cyno_count"] > dossier.cyno_count:
            dossier.cyno_count = parsed["cyno_count"]
            updated = True

        if parsed["is_blops_pilot"] and not dossier.is_blops_pilot:
            dossier.is_blops_pilot = True
            updated = True
        if parsed["blops_count"] > dossier.blops_count:
            dossier.blops_count = parsed["blops_count"]
            updated = True

        if parsed["is_capital_pilot"] and not dossier.is_capital_pilot:
            dossier.is_capital_pilot = True
            updated = True
        if parsed["capital_count"] > dossier.capital_count:
            dossier.capital_count = parsed["capital_count"]
            updated = True

        if parsed["is_super_pilot"] and not dossier.is_super_pilot:
            dossier.is_super_pilot = True
            updated = True
        if parsed["super_count"] > dossier.super_count:
            dossier.super_count = parsed["super_count"]
            updated = True

        if parsed["is_titan_pilot"] and not dossier.is_titan_pilot:
            dossier.is_titan_pilot = True
            updated = True
        if parsed["titan_count"] > dossier.titan_count:
            dossier.titan_count = parsed["titan_count"]
            updated = True

        if parsed["is_rookie"] and not dossier.is_rookie:
            dossier.is_rookie = True
            updated = True

        if parsed["zkill_labels"]:
            merged_labels = list(set((dossier.zkill_labels or []) + parsed["zkill_labels"]))
            if merged_labels != dossier.zkill_labels:
                dossier.zkill_labels = merged_labels
                updated = True

        if updated:
            dossier.save()
        return updated

    def ingest_pilots_from_zkill_data(
        self,
        data: Dict[str, Any],
        alliance: Optional[HostileAlliance] = None,
        corporation: Optional[HostileCorporation] = None,
    ) -> int:
        """
        Parses zKillboard payload (supers, topLists, groups, pilots) and automatically
        creates or updates HostilePilotDossier entries for key combat pilots (Dread, FAX,
        Titan, Supercarrier, Carrier, Black Ops). Links detected ship hulls and capabilities
        directly to pilot dossiers while strictly filtering out non-pilot data (ships, systems,
        gates, corporations, alliances). Returns count of processed pilots.
        """
        if not isinstance(data, dict):
            return 0

        self.cleanup_invalid_pilot_dossiers()

        detected_pilots: Dict[int, Dict[str, Any]] = {}

        def add_pilot_candidate(
            char_id: Any,
            char_name: str = "",
            corp_name: str = "",
            all_name: str = "",
            ship_name: str = "",
            ship_type_id: Any = None,
            is_titan: bool = False,
            is_super: bool = False,
            is_dread: bool = False,
            is_fax: bool = False,
            is_capital: bool = False,
            is_blops: bool = False,
            is_cyno: bool = False,
            is_fc: bool = False,
            is_out_of_corp: bool = False,
            fc_level: str = "",
            fc_score: int = 0,
            is_awox: bool = False,
            awox_count: int = 0,
            is_alliance_awox: bool = False,
            alliance_awox_count: int = 0,
            is_faction_awox: bool = False,
            faction_awox_count: int = 0,
            is_bait: bool = False,
            bait_level: str = "",
            bait_count: int = 0,
            is_ganker: bool = False,
            ganker_count: int = 0,
            is_logi: bool = False,
            logi_count: int = 0,
            cyno_count: int = 0,
            blops_count: int = 0,
            capital_count: int = 0,
            super_count: int = 0,
            titan_count: int = 0,
            is_rookie: bool = False,
            raw_labels: Any = None,
            role_label: str = "",
            flown_ships: Optional[Iterable[str]] = None,
            stats_desc: str = "",
        ):
            try:
                cid = int(char_id)
            except (ValueError, TypeError):
                return
            if cid <= 0:
                return

            if self.is_invalid_character_id(cid, char_name):
                return

            parsed_lbl = {}
            if raw_labels:
                parsed_lbl = self.parse_pilot_labels(raw_labels)

            ship_flags = self._classify_ship(ship_name, ship_type_id)
            eff_titan = is_titan or ship_flags["is_titan_pilot"] or parsed_lbl.get("is_titan_pilot", False)
            eff_super = is_super or eff_titan or ship_flags["is_super_pilot"] or parsed_lbl.get("is_super_pilot", False)
            eff_dread = is_dread or ship_flags["is_dread_pilot"]
            eff_fax = is_fax or ship_flags["is_fax_pilot"]
            eff_cap = is_capital or eff_titan or eff_super or eff_dread or eff_fax or ship_flags["is_capital_pilot"] or parsed_lbl.get("is_capital_pilot", False)
            eff_blops = is_blops or ship_flags["is_blops_pilot"] or parsed_lbl.get("is_blops_pilot", False)
            eff_cyno = is_cyno or parsed_lbl.get("is_cyno_alt", False)
            eff_fc = is_fc or parsed_lbl.get("is_fc", False)
            eff_fc_level = fc_level or parsed_lbl.get("fc_level", "")
            if eff_fc and not eff_fc_level:
                eff_fc_level = "CANDIDATE"
            eff_fc_score = max(fc_score, parsed_lbl.get("fc_score", 0))

            eff_awox = is_awox or parsed_lbl.get("is_awox", False)
            eff_awox_cnt = max(awox_count, parsed_lbl.get("awox_count", 0))
            eff_all_awox = is_alliance_awox or parsed_lbl.get("is_alliance_awox", False)
            eff_all_awox_cnt = max(alliance_awox_count, parsed_lbl.get("alliance_awox_count", 0))
            eff_fac_awox = is_faction_awox or parsed_lbl.get("is_faction_awox", False)
            eff_fac_awox_cnt = max(faction_awox_count, parsed_lbl.get("faction_awox_count", 0))

            eff_bait = is_bait or parsed_lbl.get("is_bait", False)
            eff_bait_level = bait_level or parsed_lbl.get("bait_level", "")
            eff_bait_cnt = max(bait_count, parsed_lbl.get("bait_count", 0))

            eff_ganker = is_ganker or parsed_lbl.get("is_ganker", False)
            eff_ganker_cnt = max(ganker_count, parsed_lbl.get("ganker_count", 0))

            eff_logi = is_logi or parsed_lbl.get("is_logi_pilot", False)
            eff_logi_cnt = max(logi_count, parsed_lbl.get("logi_count", 0))

            eff_cyno_cnt = max(cyno_count, parsed_lbl.get("cyno_count", 0))
            eff_blops_cnt = max(blops_count, parsed_lbl.get("blops_count", 0))
            eff_cap_cnt = max(capital_count, parsed_lbl.get("capital_count", 0))
            eff_super_cnt = max(super_count, parsed_lbl.get("super_count", 0))
            eff_titan_cnt = max(titan_count, parsed_lbl.get("titan_count", 0))
            eff_rookie = is_rookie or parsed_lbl.get("is_rookie", False)

            lbls = parsed_lbl.get("zkill_labels", [])

            ships_set = set()
            if ship_name:
                ships_set.add(ship_name.strip())
            if flown_ships:
                for s in flown_ships:
                    if s and isinstance(s, str):
                        s_clean = s.strip()
                        if s_clean:
                            ships_set.add(s_clean)

            if cid not in detected_pilots:
                detected_pilots[cid] = {
                    "character_id": cid,
                    "character_name": char_name or "",
                    "corporation_name": corp_name or (corporation.corporation_name if corporation else ""),
                    "alliance_name": all_name or (alliance.alliance_name if alliance else ""),
                    "ships": ships_set,
                    "is_titan_pilot": eff_titan,
                    "is_super_pilot": eff_super,
                    "is_dread_pilot": eff_dread,
                    "is_fax_pilot": eff_fax,
                    "is_capital_pilot": eff_cap,
                    "is_blops_pilot": eff_blops,
                    "is_cyno_alt": eff_cyno,
                    "is_fc": eff_fc,
                    "fc_level": eff_fc_level,
                    "fc_score": eff_fc_score,
                    "is_awox": eff_awox,
                    "awox_count": eff_awox_cnt,
                    "is_alliance_awox": eff_all_awox,
                    "alliance_awox_count": eff_all_awox_cnt,
                    "is_faction_awox": eff_fac_awox,
                    "faction_awox_count": eff_fac_awox_cnt,
                    "is_bait": eff_bait,
                    "bait_level": eff_bait_level,
                    "bait_count": eff_bait_cnt,
                    "is_ganker": eff_ganker,
                    "ganker_count": eff_ganker_cnt,
                    "is_logi_pilot": eff_logi,
                    "logi_count": eff_logi_cnt,
                    "cyno_count": eff_cyno_cnt,
                    "blops_count": eff_blops_cnt,
                    "capital_count": eff_cap_cnt,
                    "super_count": eff_super_cnt,
                    "titan_count": eff_titan_cnt,
                    "is_rookie": eff_rookie,
                    "zkill_labels": list(lbls),
                    "is_out_of_corp": is_out_of_corp,
                    "role_labels": [role_label] if role_label else [],
                    "stats_desc": stats_desc,
                }
            else:
                p = detected_pilots[cid]
                if char_name and not p["character_name"]:
                    p["character_name"] = char_name
                if corp_name and not p["corporation_name"]:
                    p["corporation_name"] = corp_name
                if all_name and not p["alliance_name"]:
                    p["alliance_name"] = all_name
                p["ships"].update(ships_set)
                p["is_titan_pilot"] = p["is_titan_pilot"] or eff_titan
                p["is_super_pilot"] = p["is_super_pilot"] or eff_super
                p["is_dread_pilot"] = p["is_dread_pilot"] or eff_dread
                p["is_fax_pilot"] = p["is_fax_pilot"] or eff_fax
                p["is_capital_pilot"] = p["is_capital_pilot"] or eff_cap
                p["is_blops_pilot"] = p["is_blops_pilot"] or eff_blops
                p["is_cyno_alt"] = p["is_cyno_alt"] or eff_cyno
                p["is_fc"] = p["is_fc"] or eff_fc
                if eff_fc_level and not p.get("fc_level"):
                    p["fc_level"] = eff_fc_level
                p["fc_score"] = max(p.get("fc_score", 0), eff_fc_score)

                p["is_awox"] = p.get("is_awox", False) or eff_awox
                p["awox_count"] = max(p.get("awox_count", 0), eff_awox_cnt)
                p["is_alliance_awox"] = p.get("is_alliance_awox", False) or eff_all_awox
                p["alliance_awox_count"] = max(p.get("alliance_awox_count", 0), eff_all_awox_cnt)
                p["is_faction_awox"] = p.get("is_faction_awox", False) or eff_fac_awox
                p["faction_awox_count"] = max(p.get("faction_awox_count", 0), eff_fac_awox_cnt)

                p["is_bait"] = p.get("is_bait", False) or eff_bait
                if eff_bait_level and not p.get("bait_level"):
                    p["bait_level"] = eff_bait_level
                p["bait_count"] = max(p.get("bait_count", 0), eff_bait_cnt)

                p["is_ganker"] = p.get("is_ganker", False) or eff_ganker
                p["ganker_count"] = max(p.get("ganker_count", 0), eff_ganker_cnt)

                p["is_logi_pilot"] = p.get("is_logi_pilot", False) or eff_logi
                p["logi_count"] = max(p.get("logi_count", 0), eff_logi_cnt)

                p["cyno_count"] = max(p.get("cyno_count", 0), eff_cyno_cnt)
                p["blops_count"] = max(p.get("blops_count", 0), eff_blops_cnt)
                p["capital_count"] = max(p.get("capital_count", 0), eff_cap_cnt)
                p["super_count"] = max(p.get("super_count", 0), eff_super_cnt)
                p["titan_count"] = max(p.get("titan_count", 0), eff_titan_cnt)
                p["is_rookie"] = p.get("is_rookie", False) or eff_rookie

                if lbls:
                    p["zkill_labels"] = list(set(p.get("zkill_labels", []) + lbls))

                p["is_out_of_corp"] = p["is_out_of_corp"] or is_out_of_corp
                if stats_desc and not p.get("stats_desc"):
                    p["stats_desc"] = stats_desc
                if role_label and role_label not in p["role_labels"]:
                    p["role_labels"].append(role_label)

        # 1. Parse Supers Section
        supers_data = data.get("supers", {})
        if isinstance(supers_data, dict):
            super_configs = {
                "titan": {
                    "role_label": "Titan Pilot",
                    "is_titan": True,
                    "is_super": True,
                    "is_capital": True,
                },
                "supercarrier": {
                    "role_label": "Supercarrier Pilot",
                    "is_super": True,
                    "is_capital": True,
                    "is_titan": False,
                },
            }
            for s_key, cfg in super_configs.items():
                s_entries = supers_data.get(s_key, {})
                top_ships = []

                # Pass 1: Extract all ship types from supers section
                tl_list = []
                if isinstance(s_entries, dict):
                    tl_list = s_entries.get("topLists") or s_entries.get("top_lists") or []
                if isinstance(tl_list, list):
                    for tl in tl_list:
                        if isinstance(tl, dict):
                            t_type = (tl.get("type") or "").lower().strip()
                            if t_type in self.SHIP_CATEGORIES:
                                for v in tl.get("values", []):
                                    if isinstance(v, dict) and v.get("name"):
                                        top_ships.append(v["name"])
                if isinstance(s_entries, dict):
                    for t_item in s_entries.get("types", []) or s_entries.get("ships", []):
                        if isinstance(t_item, dict) and t_item.get("name"):
                            top_ships.append(t_item["name"])

                # Pass 2: Extract pilots from supers topLists
                if isinstance(tl_list, list):
                    for tl in tl_list:
                        if isinstance(tl, dict):
                            t_type = (tl.get("type") or "").lower().strip()
                            if t_type in self.PILOT_CATEGORIES:
                                for item in tl.get("values", []):
                                    if isinstance(item, dict):
                                        c_id = item.get("characterID") or item.get("character_id") or item.get("id")
                                        c_name = item.get("characterName") or item.get("character_name") or item.get("name") or ""
                                        s_name = item.get("shipName") or item.get("ship_name") or ""
                                        add_pilot_candidate(
                                            c_id,
                                            c_name,
                                            ship_name=s_name,
                                            flown_ships=top_ships if not s_name else None,
                                            is_titan=cfg["is_titan"],
                                            is_super=cfg["is_super"],
                                            is_capital=cfg["is_capital"],
                                            role_label=cfg["role_label"],
                                        )

                # Pass 3: Extract pilots from data / characters / pilots directly
                items_list = []
                if isinstance(s_entries, dict):
                    items_list = s_entries.get("data") or s_entries.get("characters") or s_entries.get("pilots") or []
                elif isinstance(s_entries, list):
                    items_list = s_entries

                for item in items_list:
                    if isinstance(item, dict):
                        if (item.get("typeID") or item.get("shipTypeID")) and not (
                            item.get("characterID") or item.get("character_id")
                        ):
                            s_name = item.get("shipName") or item.get("name") or ""
                            if s_name:
                                top_ships.append(s_name)
                            continue

                        c_id = item.get("characterID") or item.get("character_id")
                        if not c_id and "id" in item:
                            if not self.is_invalid_character_id(item["id"], item.get("name", "")):
                                c_id = item["id"]
                            else:
                                if item.get("name"):
                                    top_ships.append(item["name"])
                                continue

                        if c_id:
                            c_name = item.get("characterName") or item.get("character_name") or item.get("name") or ""
                            corp_n = item.get("corporationName") or item.get("corporation_name") or ""
                            all_n = item.get("allianceName") or item.get("alliance_name") or ""
                            s_name = item.get("shipName") or item.get("ship_name") or ""
                            add_pilot_candidate(
                                c_id,
                                c_name,
                                corp_n,
                                all_n,
                                ship_name=s_name,
                                flown_ships=top_ships if not s_name else None,
                                is_titan=cfg["is_titan"],
                                is_super=cfg["is_super"],
                                is_capital=cfg["is_capital"],
                                role_label=cfg["role_label"],
                            )

        # 2. Parse Groups Section
        groups_data = data.get("groups", {})
        if isinstance(groups_data, dict):
            group_map = {
                "30": {"titan": True, "super": True, "capital": True, "label": "Titan Pilot", "role_name": "Titan"},
                "659": {"super": True, "capital": True, "label": "Supercarrier Pilot", "role_name": "Supercarrier"},
                "485": {"dread": True, "capital": True, "label": "Dread Pilot", "role_name": "Dreadnought"},
                "4594": {"dread": True, "capital": True, "label": "Lancer Dread Pilot", "role_name": "Lancer Dreadnought"},
                "1538": {"fax": True, "capital": True, "label": "FAX Pilot", "role_name": "Force Auxiliary (FAX)"},
                "547": {"capital": True, "label": "Carrier Pilot", "role_name": "Carrier"},
                "898": {"blops": True, "label": "Black Ops Pilot", "role_name": "Black Ops"},
            }
            for g_id, g_info in groups_data.items():
                g_key = str(g_id)
                config = group_map.get(g_key, {})
                group_top_ships = []

                # Pass 1: Extract all ship types from group topLists & types
                tl_list = []
                if isinstance(g_info, dict):
                    tl_list = g_info.get("topLists") or g_info.get("top_lists") or g_info.get("top") or []
                if isinstance(tl_list, list):
                    for tl in tl_list:
                        if isinstance(tl, dict):
                            t_type = (tl.get("type") or "").lower().strip()
                            if t_type in self.SHIP_CATEGORIES:
                                for v in tl.get("values", []):
                                    if isinstance(v, dict) and v.get("name"):
                                        group_top_ships.append(v["name"])
                if isinstance(g_info, dict):
                    for t_item in g_info.get("types", []) or g_info.get("ships", []):
                        if isinstance(t_item, dict) and t_item.get("name"):
                            group_top_ships.append(t_item["name"])

                # Pass 2: Extract pilots from group topLists
                if isinstance(tl_list, list):
                    for tl in tl_list:
                        if isinstance(tl, dict):
                            t_type = (tl.get("type") or "").lower().strip()
                            if t_type in self.PILOT_CATEGORIES:
                                for item in tl.get("values", []):
                                    if isinstance(item, dict):
                                        c_id = item.get("characterID") or item.get("character_id") or item.get("id")
                                        c_name = item.get("characterName") or item.get("character_name") or item.get("name") or ""
                                        s_name = item.get("shipName") or item.get("ship_name") or ""
                                        add_pilot_candidate(
                                            c_id,
                                            c_name,
                                            ship_name=s_name,
                                            flown_ships=group_top_ships if not s_name else None,
                                            is_titan=config.get("titan", False),
                                            is_super=config.get("super", False),
                                            is_dread=config.get("dread", False),
                                            is_fax=config.get("fax", False),
                                            is_capital=config.get("capital", False),
                                            is_blops=config.get("blops", False),
                                            role_label=config.get("label", ""),
                                        )

                # Pass 3: Extract pilots from group data / characters / pilots lists
                g_items = []
                if isinstance(g_info, dict):
                    g_items = g_info.get("data") or g_info.get("characters") or g_info.get("pilots") or []
                elif isinstance(g_info, list):
                    g_items = g_info

                for item in g_items:
                    if isinstance(item, dict):
                        if (item.get("typeID") or item.get("shipTypeID")) and not (
                            item.get("characterID") or item.get("character_id")
                        ):
                            s_name = item.get("shipName") or item.get("name") or ""
                            if s_name:
                                group_top_ships.append(s_name)
                            continue

                        c_id = item.get("characterID") or item.get("character_id")
                        if not c_id and "id" in item:
                            if not self.is_invalid_character_id(item["id"], item.get("name", "")):
                                c_id = item["id"]
                            else:
                                if item.get("name"):
                                    group_top_ships.append(item["name"])
                                continue

                        if c_id:
                            c_name = item.get("characterName") or item.get("character_name") or item.get("name") or ""
                            corp_n = item.get("corporationName") or item.get("corporation_name") or ""
                            all_n = item.get("allianceName") or item.get("alliance_name") or ""
                            s_name = item.get("shipName") or item.get("ship_name") or ""
                            add_pilot_candidate(
                                c_id,
                                c_name,
                                corp_n,
                                all_n,
                                ship_name=s_name,
                                flown_ships=group_top_ships if not s_name else None,
                                is_titan=config.get("titan", False),
                                is_super=config.get("super", False),
                                is_dread=config.get("dread", False),
                                is_fax=config.get("fax", False),
                                is_capital=config.get("capital", False),
                                is_blops=config.get("blops", False),
                                role_label=config.get("label", ""),
                            )

        # 3. Parse TopLists Section
        top_lists = data.get("topLists") or data.get("top_lists") or data.get("top") or []
        tl_items = []
        if isinstance(top_lists, list):
            for entry in top_lists:
                if isinstance(entry, dict):
                    cat = (entry.get("type") or entry.get("title") or entry.get("category") or "").lower().strip()
                    vals = entry.get("values") or entry.get("data") or entry.get("characters") or []
                    tl_items.append((cat, vals))
        elif isinstance(top_lists, dict):
            for cat, vals in top_lists.items():
                tl_items.append((str(cat).lower().strip(), vals))

        entity_top_ships = []
        for cat, vals in tl_items:
            if not isinstance(vals, list):
                continue
            if cat in self.SHIP_CATEGORIES:
                for v in vals:
                    if isinstance(v, dict) and v.get("name"):
                        entity_top_ships.append(v["name"])

        for cat, vals in tl_items:
            if not isinstance(vals, list):
                continue
            # EXPLICITLY ignore non-pilot categories (ships, systems, locations, corporations, alliances)
            if cat in self.NON_PILOT_CATEGORIES and cat not in self.PILOT_CATEGORIES:
                continue

            is_titan = "titan" in cat
            is_super = "super" in cat or is_titan
            is_dread = "dread" in cat or "lancer" in cat
            is_fax = "fax" in cat or "force" in cat or "auxiliary" in cat
            is_capital = is_titan or is_super or is_dread or is_fax or "carrier" in cat or "cap" in cat
            is_blops = "blops" in cat or "blackops" in cat or "black_ops" in cat
            is_cyno = "cyno" in cat
            is_top_combat = cat in (
                "character",
                "characters",
                "topcharacters",
                "top_characters",
                "toppilots",
                "top_pilots",
                "pilot",
                "pilots",
                "kills",
                "damage",
                "points",
                "solo",
                "finalblows",
                "final_blows",
            )

            role_lbl = ""
            if is_titan:
                role_lbl = "Titan Pilot"
            elif is_super:
                role_lbl = "Super Pilot"
            elif is_dread:
                role_lbl = "Dread Pilot"
            elif is_fax:
                role_lbl = "FAX Pilot"
            elif is_capital:
                role_lbl = "Capital Pilot"
            elif is_blops:
                role_lbl = "Black Ops Pilot"
            elif is_cyno:
                role_lbl = "Cyno Alt"
            elif is_top_combat:
                role_lbl = "Fleet Commander Candidate / Top Combat Lead"

            for idx, item in enumerate(vals):
                if isinstance(item, dict):
                    c_id = item.get("characterID") or item.get("character_id")
                    if not c_id and "id" in item:
                        if not self.is_invalid_character_id(item["id"], item.get("name", "")):
                            c_id = item["id"]
                        else:
                            continue

                    if not c_id:
                        continue

                    c_name = item.get("characterName") or item.get("character_name") or item.get("name") or ""
                    corp_n = item.get("corporationName") or item.get("corporation_name") or ""
                    all_n = item.get("allianceName") or item.get("alliance_name") or ""
                    s_name = item.get("shipName") or item.get("ship_name") or ""
                    kills_cnt = item.get("kills") or item.get("killsCount")
                    losses_cnt = item.get("losses") or item.get("lossesCount")

                    stats_desc = ""
                    if is_top_combat:
                        if kills_cnt and losses_cnt:
                            stats_desc = f"Rank #{idx+1} on combat board ({kills_cnt} kills, {losses_cnt} losses)."
                        elif kills_cnt:
                            stats_desc = f"Rank #{idx+1} on combat board ({kills_cnt} kills recorded)."
                        else:
                            stats_desc = f"Rank #{idx+1} on combat board."

                    add_pilot_candidate(
                        c_id,
                        c_name,
                        corp_n,
                        all_n,
                        s_name,
                        is_titan=is_titan,
                        is_super=is_super,
                        is_dread=is_dread,
                        is_fax=is_fax,
                        is_capital=is_capital,
                        is_blops=is_blops,
                        is_cyno=is_cyno,
                        is_fc=is_top_combat,
                        raw_labels=item.get("labels") or item.get("tags") or item.get("badges"),
                        role_label=role_lbl,
                        stats_desc=stats_desc,
                    )

        # 4. Parse Pilots / Characters Array
        raw_pilots = data.get("pilots") or data.get("characters") or data.get("dossiers") or []
        if isinstance(raw_pilots, list):
            for item in raw_pilots:
                if isinstance(item, dict):
                    c_id = item.get("characterID") or item.get("character_id")
                    if not c_id and "id" in item:
                        if not self.is_invalid_character_id(item["id"], item.get("name", "")):
                            c_id = item["id"]
                        else:
                            continue
                    if not c_id:
                        continue

                    c_name = item.get("characterName") or item.get("character_name") or item.get("name") or ""
                    corp_n = item.get("corporationName") or item.get("corporation_name") or ""
                    all_n = item.get("allianceName") or item.get("alliance_name") or ""
                    s_name = item.get("shipName") or item.get("ship_name") or ""
                    add_pilot_candidate(
                        c_id,
                        c_name,
                        corp_n,
                        all_n,
                        s_name,
                        is_titan=bool(item.get("is_titan_pilot")),
                        is_super=bool(item.get("is_super_pilot")),
                        is_dread=bool(item.get("is_dread_pilot")),
                        is_fax=bool(item.get("is_fax_pilot")),
                        is_capital=bool(item.get("is_capital_pilot")),
                        is_blops=bool(item.get("is_blops_pilot")),
                        is_cyno=bool(item.get("is_cyno_alt")),
                        is_fc=bool(item.get("is_fc")),
                        fc_level=item.get("fc_level", ""),
                        fc_score=item.get("fc_score", 0),
                        is_awox=bool(item.get("is_awox")),
                        awox_count=item.get("awox_count", 0),
                        is_alliance_awox=bool(item.get("is_alliance_awox")),
                        alliance_awox_count=item.get("alliance_awox_count", 0),
                        is_faction_awox=bool(item.get("is_faction_awox")),
                        faction_awox_count=item.get("faction_awox_count", 0),
                        is_bait=bool(item.get("is_bait")),
                        bait_level=item.get("bait_level", ""),
                        bait_count=item.get("bait_count", 0),
                        is_ganker=bool(item.get("is_ganker")),
                        ganker_count=item.get("ganker_count", 0),
                        is_logi=bool(item.get("is_logi_pilot")),
                        logi_count=item.get("logi_count", 0),
                        is_rookie=bool(item.get("is_rookie")),
                        raw_labels=item.get("labels") or item.get("tags") or item.get("badges"),
                    )

        # 5. Bulk Character Affiliation Resolution
        all_cids = [cid for cid, p in detected_pilots.items() if not self.is_invalid_character_id(cid, p.get("character_name", ""))]
        if all_cids:
            try:
                affiliations = esi_client.post_characters_affiliation(all_cids)
                corp_id_map = {}
                alliance_id_map = {}
                missing_name_ids = set()
                for aff in (affiliations or []):
                    c_id = _get_val(aff, "character_id")
                    corp_id = _get_val(aff, "corporation_id")
                    all_id = _get_val(aff, "alliance_id")
                    if c_id:
                        if corp_id:
                            corp_id_map[c_id] = corp_id
                            missing_name_ids.add(corp_id)
                        if all_id:
                            alliance_id_map[c_id] = all_id
                            missing_name_ids.add(all_id)

                known_corps = {
                    int(c.corporation_id): c.corporation_name
                    for c in HostileCorporation.objects.filter(corporation_id__in=missing_name_ids)
                }
                known_alliances = {
                    int(a.alliance_id): a.alliance_name
                    for a in HostileAlliance.objects.filter(alliance_id__in=missing_name_ids)
                }
                try:
                    from allianceauth.eveonline.models import EveAllianceInfo, EveCorporationInfo

                    for ec in EveCorporationInfo.objects.filter(corporation_id__in=missing_name_ids):
                        if int(ec.corporation_id) not in known_corps and ec.corporation_name:
                            known_corps[int(ec.corporation_id)] = ec.corporation_name
                    for ea in EveAllianceInfo.objects.filter(alliance_id__in=missing_name_ids):
                        if int(ea.alliance_id) not in known_alliances and ea.alliance_name:
                            known_alliances[int(ea.alliance_id)] = ea.alliance_name
                except Exception:
                    pass

                unresolved_ids = [
                    int(i)
                    for i in missing_name_ids
                    if int(i) not in known_corps and int(i) not in known_alliances
                ]
                if unresolved_ids:
                    names_data = esi_client.post_universe_names(unresolved_ids)
                    for item in (names_data or []):
                        i_id = _get_val(item, "id")
                        i_name = _get_val(item, "name")
                        i_cat = _get_val(item, "category")
                        if i_id and i_name:
                            if i_cat == "alliance":
                                known_alliances[int(i_id)] = str(i_name)
                            else:
                                known_corps[int(i_id)] = str(i_name)

                for cid, p in detected_pilots.items():
                    corp_id = corp_id_map.get(cid)
                    all_id = alliance_id_map.get(cid)
                    if corp_id and not p.get("corporation_name"):
                        p["corporation_name"] = known_corps.get(int(corp_id), "")
                    if all_id and not p.get("alliance_name"):
                        p["alliance_name"] = known_alliances.get(int(all_id), "")
                    if not p.get("alliance_name") and alliance:
                        p["alliance_name"] = alliance.alliance_name

                    # Check out-of-corp / alt status
                    if alliance:
                        if all_id and all_id != alliance.alliance_id:
                            p["is_out_of_corp"] = True
                            p["associated_alliance_name"] = alliance.alliance_name
                    elif corporation:
                        if corp_id and corp_id != corporation.corporation_id:
                            p["is_out_of_corp"] = True
                            p["associated_alliance_name"] = (
                                corporation.alliance.alliance_name
                                if corporation.alliance
                                else ""
                            )
            except Exception as e:
                logger.debug("Failed bulk character affiliation resolution: %s", e)

        # 6. Persist to HostilePilotDossier Database
        saved_count = 0
        processed_dossiers = []
        for cid, p in detected_pilots.items():
            if self.is_invalid_character_id(cid, p["character_name"]):
                continue

            char_name = (p["character_name"] or "").strip()
            if not char_name or char_name.startswith("Pilot #"):
                try:
                    from hostile.services.entity_resolver import EntityResolver

                    resolved = EntityResolver.resolve_character(cid)
                    if resolved and resolved.get("character_name"):
                        char_name = resolved["character_name"]
                        if not p["corporation_name"] and resolved.get("corporation_name"):
                            p["corporation_name"] = resolved["corporation_name"]
                        if not p["alliance_name"] and resolved.get("alliance_name"):
                            p["alliance_name"] = resolved["alliance_name"]
                    else:
                        if self.is_invalid_character_id(cid):
                            continue
                except Exception as e:
                    logger.debug("Failed character resolution for %s: %s", cid, e)

            if not char_name or self.is_invalid_character_id(cid, char_name):
                continue

            flown_ships = sorted(p["ships"])
            dossier = HostilePilotDossier.objects.filter(character_id=cid).first()
            if dossier:
                updated = False
                if p["is_fc"] and not dossier.is_fc:
                    dossier.is_fc = True
                    updated = True
                if p.get("fc_level") and dossier.fc_level != p["fc_level"]:
                    dossier.fc_level = p["fc_level"]
                    updated = True
                if p.get("fc_score", 0) > dossier.fc_score:
                    dossier.fc_score = p["fc_score"]
                    updated = True
                if p["is_titan_pilot"] and not dossier.is_titan_pilot:
                    dossier.is_titan_pilot = True
                    dossier.is_super_pilot = True
                    dossier.is_capital_pilot = True
                    updated = True
                if p["is_super_pilot"] and not dossier.is_super_pilot:
                    dossier.is_super_pilot = True
                    dossier.is_capital_pilot = True
                    updated = True
                if p["is_dread_pilot"] and not dossier.is_dread_pilot:
                    dossier.is_dread_pilot = True
                    dossier.is_capital_pilot = True
                    updated = True
                if p["is_fax_pilot"] and not dossier.is_fax_pilot:
                    dossier.is_fax_pilot = True
                    dossier.is_capital_pilot = True
                    updated = True
                if p["is_capital_pilot"] and not dossier.is_capital_pilot:
                    dossier.is_capital_pilot = True
                    updated = True
                if p["is_blops_pilot"] and not dossier.is_blops_pilot:
                    dossier.is_blops_pilot = True
                    updated = True
                if p["is_cyno_alt"] and not dossier.is_cyno_alt:
                    dossier.is_cyno_alt = True
                    updated = True
                if p.get("is_awox") and not dossier.is_awox:
                    dossier.is_awox = True
                    updated = True
                if p.get("awox_count", 0) > dossier.awox_count:
                    dossier.awox_count = p["awox_count"]
                    updated = True
                if p.get("is_alliance_awox") and not dossier.is_alliance_awox:
                    dossier.is_alliance_awox = True
                    updated = True
                if p.get("alliance_awox_count", 0) > dossier.alliance_awox_count:
                    dossier.alliance_awox_count = p["alliance_awox_count"]
                    updated = True
                if p.get("is_faction_awox") and not dossier.is_faction_awox:
                    dossier.is_faction_awox = True
                    updated = True
                if p.get("faction_awox_count", 0) > dossier.faction_awox_count:
                    dossier.faction_awox_count = p["faction_awox_count"]
                    updated = True
                if p.get("is_bait") and not dossier.is_bait:
                    dossier.is_bait = True
                    updated = True
                if p.get("bait_level") and dossier.bait_level != p["bait_level"]:
                    dossier.bait_level = p["bait_level"]
                    updated = True
                if p.get("bait_count", 0) > dossier.bait_count:
                    dossier.bait_count = p["bait_count"]
                    updated = True
                if p.get("is_ganker") and not dossier.is_ganker:
                    dossier.is_ganker = True
                    updated = True
                if p.get("ganker_count", 0) > dossier.ganker_count:
                    dossier.ganker_count = p["ganker_count"]
                    updated = True
                if p.get("is_logi_pilot") and not dossier.is_logi_pilot:
                    dossier.is_logi_pilot = True
                    updated = True
                if p.get("logi_count", 0) > dossier.logi_count:
                    dossier.logi_count = p["logi_count"]
                    updated = True
                if p.get("cyno_count", 0) > dossier.cyno_count:
                    dossier.cyno_count = p["cyno_count"]
                    updated = True
                if p.get("blops_count", 0) > dossier.blops_count:
                    dossier.blops_count = p["blops_count"]
                    updated = True
                if p.get("capital_count", 0) > dossier.capital_count:
                    dossier.capital_count = p["capital_count"]
                    updated = True
                if p.get("super_count", 0) > dossier.super_count:
                    dossier.super_count = p["super_count"]
                    updated = True
                if p.get("titan_count", 0) > dossier.titan_count:
                    dossier.titan_count = p["titan_count"]
                    updated = True
                if p.get("is_rookie") and not dossier.is_rookie:
                    dossier.is_rookie = True
                    updated = True
                if p.get("zkill_labels"):
                    merged_labels = list(set((dossier.zkill_labels or []) + p["zkill_labels"]))
                    if merged_labels != dossier.zkill_labels:
                        dossier.zkill_labels = merged_labels
                        updated = True
                if p["is_out_of_corp"] and not dossier.is_out_of_corp:
                    dossier.is_out_of_corp = True
                    if p.get("associated_alliance_name") and not dossier.associated_alliance_name:
                        dossier.associated_alliance_name = p["associated_alliance_name"]
                    updated = True
                if not dossier.alliance_name and p.get("alliance_name"):
                    dossier.alliance_name = p["alliance_name"]
                    updated = True
                if p.get("corporation_name") and (
                    not dossier.corporation_name
                    or str(dossier.corporation_name).isdigit()
                    or dossier.corporation_name == "Unknown Corp"
                ):
                    dossier.corporation_name = p["corporation_name"]
                    updated = True
                if char_name and (dossier.character_name.startswith("Pilot #") or not dossier.character_name):
                    dossier.character_name = char_name
                    updated = True

                # Link new ships to notes if not already mentioned
                if flown_ships:
                    existing_notes = dossier.notes or ""
                    new_ships = [s for s in flown_ships if s.lower() not in existing_notes.lower()]
                    if new_ships:
                        ships_text = f"Auto-detected ships (zKillboard): {', '.join(new_ships)}."
                        if existing_notes:
                            dossier.notes = f"{existing_notes}\n{ships_text}"
                        else:
                            dossier.notes = ships_text
                        updated = True

                if updated:
                    dossier.save()
                    self.sync_pilot_dossier_zkill(dossier)
                    IntelManager.auto_tag_pilot(dossier)
                    dossier.save()
                    processed_dossiers.append(dossier)
                    saved_count += 1
                else:
                    self.sync_pilot_dossier_zkill(dossier)
                    IntelManager.auto_tag_pilot(dossier)
                    dossier.save()
                    processed_dossiers.append(dossier)
            else:
                roles_list = []
                if p["is_fc"]:
                    lvl_str = f" ({p['fc_level'].capitalize()})" if p.get("fc_level") and p["fc_level"] != "CANDIDATE" else ""
                    roles_list.append(f"Fleet Commander{lvl_str} / Top Combat Lead")
                if p["is_titan_pilot"]:
                    roles_list.append("Titan Pilot")
                if p["is_super_pilot"] and not p["is_titan_pilot"]:
                    roles_list.append("Supercarrier Pilot")
                if p["is_dread_pilot"]:
                    roles_list.append("Dreadnought Pilot")
                if p["is_fax_pilot"]:
                    roles_list.append("Force Auxiliary (FAX) Pilot")
                if p["is_capital_pilot"] and not (
                    p["is_titan_pilot"] or p["is_super_pilot"] or p["is_dread_pilot"] or p["is_fax_pilot"]
                ):
                    roles_list.append("Carrier / Capital Pilot")
                if p["is_blops_pilot"]:
                    roles_list.append("Black Ops Pilot")
                if p["is_cyno_alt"]:
                    roles_list.append("Cyno Alt")
                if p.get("is_awox"):
                    roles_list.append(f"Corp AWOX ({p['awox_count']})")
                if p.get("is_alliance_awox"):
                    roles_list.append(f"Alliance AWOX ({p['alliance_awox_count']})")
                if p.get("is_faction_awox"):
                    roles_list.append(f"Faction AWOX ({p['faction_awox_count']})")
                if p.get("is_bait"):
                    roles_list.append(f"Bait ({p.get('bait_level', '')} {p['bait_count']})".strip())
                if p.get("is_ganker"):
                    roles_list.append(f"Ganker ({p['ganker_count']})")
                if p.get("is_logi_pilot"):
                    roles_list.append(f"Logi Pilot ({p['logi_count']})")
                if p.get("is_rookie"):
                    roles_list.append("Rookie")
                if p["is_out_of_corp"]:
                    roles_list.append("Out of Corp Alt")

                role_summary = " / ".join(roles_list) if roles_list else "Combat Pilot"
                note_parts = [f"Auto-detected via zKillboard ({role_summary})."]
                if p.get("stats_desc"):
                    note_parts.append(p["stats_desc"])
                if p["corporation_name"]:
                    if p["is_out_of_corp"]:
                        note_parts.append(f"Operating in holding/alt corp '{p['corporation_name']}'.")
                    else:
                        note_parts.append(f"Corporation: {p['corporation_name']}.")
                if flown_ships:
                    note_parts.append(f"Detected ships: {', '.join(flown_ships)}.")
                note = " ".join(note_parts)

                new_dossier = HostilePilotDossier.objects.create(
                    character_id=cid,
                    character_name=char_name,
                    corporation_name=p["corporation_name"],
                    alliance_name=p["alliance_name"],
                    associated_alliance_name=p.get("associated_alliance_name", ""),
                    is_out_of_corp=p["is_out_of_corp"],
                    is_fc=p["is_fc"],
                    fc_level=p.get("fc_level", ""),
                    fc_score=p.get("fc_score", 0),
                    is_titan_pilot=p["is_titan_pilot"],
                    is_super_pilot=p["is_super_pilot"],
                    is_dread_pilot=p["is_dread_pilot"],
                    is_fax_pilot=p["is_fax_pilot"],
                    is_capital_pilot=p["is_capital_pilot"],
                    is_blops_pilot=p["is_blops_pilot"],
                    is_cyno_alt=p["is_cyno_alt"],
                    is_awox=p.get("is_awox", False),
                    awox_count=p.get("awox_count", 0),
                    is_alliance_awox=p.get("is_alliance_awox", False),
                    alliance_awox_count=p.get("alliance_awox_count", 0),
                    is_faction_awox=p.get("is_faction_awox", False),
                    faction_awox_count=p.get("faction_awox_count", 0),
                    is_bait=p.get("is_bait", False),
                    bait_level=p.get("bait_level", ""),
                    bait_count=p.get("bait_count", 0),
                    is_ganker=p.get("is_ganker", False),
                    ganker_count=p.get("ganker_count", 0),
                    is_logi_pilot=p.get("is_logi_pilot", False),
                    logi_count=p.get("logi_count", 0),
                    cyno_count=p.get("cyno_count", 0),
                    blops_count=p.get("blops_count", 0),
                    capital_count=p.get("capital_count", 0),
                    super_count=p.get("super_count", 0),
                    titan_count=p.get("titan_count", 0),
                    is_rookie=p.get("is_rookie", False),
                    zkill_labels=p.get("zkill_labels", []),
                    notes=note,
                )
                self.sync_pilot_dossier_zkill(new_dossier)
                IntelManager.auto_tag_pilot(new_dossier)
                new_dossier.save()
                processed_dossiers.append(new_dossier)
                saved_count += 1

        if processed_dossiers:
            try:
                IntelManager.run_alt_detection(pilots=processed_dossiers)
            except Exception as e:
                logger.debug("Failed running alt detection in ingest: %s", e)

        return saved_count

    def update_alliance_stats(self, alliance: HostileAlliance) -> bool:
        """
        Queries zKillboard stats API and updates active PvP pilot counts, weekly kills, losses,
        automatically infers primary operational timezone from zKill kill distribution and structure timers,
        determines max formup fleet capabilities using rolling 3-month lookback, tracks percentage deltas,
        and compiles a 3-month rolling activity heatmap.
        """
        lock_key_id = f"hostile-intel-zkill-syncing-{alliance.id}"
        lock_key_alliance_id = f"hostile-intel-zkill-syncing-{alliance.alliance_id}"
        cache.set(lock_key_id, True, 300)
        cache.set(lock_key_alliance_id, True, 300)
        try:
            data = self.fetch_alliance_stats(alliance.alliance_id)
            if not data:
                return False

            self.ingest_pilots_from_zkill_data(data, alliance=alliance)

            active_characters, weekly_kills, weekly_losses = self._extract_weekly_stats(data)

            # 1. Automatically determine operational Timezone from zKill distribution + timers
            activity_data = data.get("activity", {}) or data.get("hourly", {})
            if not getattr(alliance, "override_timezone", False):
                inferred_tz = SovAnalysisEngine.infer_combined_timezone(
                    activity_data, alliance.sov_timer_distribution
                )
                if inferred_tz != "UNKNOWN":
                    alliance.primary_timezone = inferred_tz

            # 2. Rolling 3-month lookback to determine peak formup numbers
            rolling_keys = self._get_rolling_3m_keys()
            months_data = data.get("months", {})
            historical_characters = []
            if isinstance(months_data, dict):
                has_rolling = any(str(k) in rolling_keys for k in months_data)
                for m_key, m in months_data.items():
                    m_str = str(m_key)
                    if (has_rolling and m_str in rolling_keys) or (not has_rolling):
                        if isinstance(m, dict):
                            count = (
                                m.get("characters", {}).get("count", 0)
                                if isinstance(m.get("characters"), dict)
                                else m.get("characters", 0)
                            )
                            if not count and isinstance(m.get("activepvp"), dict):
                                count = m.get("activepvp", {}).get("characters", {}).get("count", 0)
                            if isinstance(count, (int, float)) and count > 0:
                                historical_characters.append(int(count))
            elif isinstance(months_data, list):
                for m in months_data:
                    if isinstance(m, dict):
                        m_str = str(m.get("year", "")) + f"{int(m.get('month', 0)):02d}"
                        if not m_str or m_str in rolling_keys:
                            count = (
                                m.get("characters", {}).get("count", 0)
                                if isinstance(m.get("characters"), dict)
                                else m.get("characters", 0)
                            )
                            if isinstance(count, (int, float)) and count > 0:
                                historical_characters.append(int(count))

            peak_characters = (
                max([active_characters] + historical_characters)
                if historical_characters
                else active_characters
            )

            # Calculate supers count (Titan + Supercarrier)
            supers_data = data.get("supers", {})
            supercarrier_count = (
                supers_data.get("supercarrier", {}).get("count", 0)
                if isinstance(supers_data, dict)
                else 0
            )
            titan_count = (
                supers_data.get("titan", {}).get("count", 0)
                if isinstance(supers_data, dict)
                else 0
            )
            total_supers = supercarrier_count + titan_count

            # Calculate capital count (Dreadnoughts + FAX + Carriers)
            groups_data = data.get("groups", {})
            dread_count = (
                groups_data.get("485", {}).get("count", 0)
                if isinstance(groups_data, dict)
                else 0
            )
            fax_count = (
                groups_data.get("1538", {}).get("count", 0)
                if isinstance(groups_data, dict)
                else 0
            )
            carrier_count = (
                groups_data.get("547", {}).get("count", 0)
                if isinstance(groups_data, dict)
                else 0
            )
            total_caps = dread_count + fax_count + carrier_count

            new_subcap = max(10, int(peak_characters * 0.6)) if peak_characters > 0 else (
                max(1, int(alliance.member_count * 0.15)) if alliance.member_count > 0 else 0
            )
            new_cap = total_caps if total_caps > 0 else (
                max(5, int(peak_characters * 0.15)) if peak_characters >= 50 else (
                    max(1, int(alliance.member_count * 0.03)) if alliance.member_count >= 50 else 0
                )
            )
            new_super = total_supers

            # Detect changes and percentage increases
            deltas = dict(alliance.zkill_deltas or {})
            if (
                alliance.prev_max_subcap_form > 0
                and new_subcap != alliance.prev_max_subcap_form
            ):
                diff = new_subcap - alliance.prev_max_subcap_form
                pct = int(round((diff / alliance.prev_max_subcap_form) * 100))
                sign = "+" if pct >= 0 else ""
                deltas["subcap"] = {
                    "pct": pct,
                    "text": f"{sign}{pct}% subcaps detected",
                    "prev": alliance.prev_max_subcap_form,
                    "curr": new_subcap,
                }
            elif (
                alliance.max_subcap_form == 0
                or alliance.prev_max_subcap_form == 0
            ):
                deltas.pop("subcap", None)

            if (
                alliance.prev_max_cap_form > 0
                and new_cap != alliance.prev_max_cap_form
            ):
                diff = new_cap - alliance.prev_max_cap_form
                pct = int(round((diff / alliance.prev_max_cap_form) * 100))
                sign = "+" if pct >= 0 else ""
                deltas["cap"] = {
                    "pct": pct,
                    "text": f"{sign}{pct}% capitals detected",
                    "prev": alliance.prev_max_cap_form,
                    "curr": new_cap,
                }
            elif alliance.max_cap_form == 0 or alliance.prev_max_cap_form == 0:
                deltas.pop("cap", None)

            if (
                alliance.prev_max_super_form > 0
                and new_super != alliance.prev_max_super_form
            ):
                diff = new_super - alliance.prev_max_super_form
                pct = int(round((diff / alliance.prev_max_super_form) * 100))
                sign = "+" if pct >= 0 else ""
                deltas["super"] = {
                    "pct": pct,
                    "text": f"{sign}{pct}% supers detected",
                    "prev": alliance.prev_max_super_form,
                    "curr": new_super,
                }
            elif alliance.max_super_form == 0 or alliance.prev_max_super_form == 0:
                deltas.pop("super", None)

            if (
                alliance.active_pilots_count > 0
                and active_characters != alliance.active_pilots_count
            ):
                diff = active_characters - alliance.active_pilots_count
                pct = int(round((diff / alliance.active_pilots_count) * 100))
                sign = "+" if pct >= 0 else ""
                deltas["pilots"] = {
                    "pct": pct,
                    "text": f"{sign}{pct}% active PvP pilots",
                    "prev": alliance.active_pilots_count,
                    "curr": active_characters,
                }
            elif alliance.active_pilots_count == 0:
                deltas.pop("pilots", None)

            # 3. 3-Month Rolling Activity Heatmap (7x24 Matrix)
            heatmap = self._build_activity_heatmap(
                data, sov_distribution=alliance.sov_timer_distribution or {}
            )

            # Timezone inference
            if not alliance.override_timezone:
                hourly_sums = {
                    str(h): sum(heatmap["matrix"][d][h] for d in range(7))
                    for h in range(24)
                }
                inferred_tz = SovAnalysisEngine.infer_combined_timezone(
                    activity_data=hourly_sums,
                    sov_distribution=alliance.sov_timer_distribution or {},
                )
                if inferred_tz and inferred_tz != "UNKNOWN":
                    alliance.primary_timezone = inferred_tz

            if (
                alliance.max_subcap_form == 0
                or alliance.prev_max_subcap_form == 0
            ):
                alliance.prev_max_subcap_form = new_subcap
            else:
                alliance.prev_max_subcap_form = alliance.max_subcap_form

            if alliance.max_cap_form == 0 or alliance.prev_max_cap_form == 0:
                alliance.prev_max_cap_form = new_cap
            else:
                alliance.prev_max_cap_form = alliance.max_cap_form

            if alliance.max_super_form == 0 or alliance.prev_max_super_form == 0:
                alliance.prev_max_super_form = new_super
            else:
                alliance.prev_max_super_form = alliance.max_super_form

            alliance.max_subcap_form = new_subcap
            alliance.max_cap_form = new_cap
            alliance.max_super_form = new_super
            alliance.zkill_deltas = deltas
            alliance.activity_heatmap = heatmap
            alliance.active_pilots_count = active_characters
            alliance.weekly_kills_count = weekly_kills
            alliance.weekly_losses_count = weekly_losses
            alliance.zkill_stats_updated_at = timezone.now()

            alliance.save(
                update_fields=[
                    "active_pilots_count",
                    "weekly_kills_count",
                    "weekly_losses_count",
                    "zkill_stats_updated_at",
                    "max_subcap_form",
                    "max_cap_form",
                    "max_super_form",
                    "prev_max_subcap_form",
                    "prev_max_cap_form",
                    "prev_max_super_form",
                    "zkill_deltas",
                    "activity_heatmap",
                    "primary_timezone",
                ]
            )
            return True
        finally:
            cache.delete(lock_key_id)
            cache.delete(lock_key_alliance_id)

    def update_corporation_stats(self, corporation: HostileCorporation) -> bool:
        """
        Queries zKillboard stats API and updates corporation active PvP counts, weekly kills, losses,
        determines formup numbers using rolling 3-month lookback, tracks percentage deltas,
        and compiles a 3-month rolling activity heatmap.
        """
        lock_key_id = f"hostile-intel-zkill-syncing-{corporation.id}"
        lock_key_corp_id = f"hostile-intel-zkill-syncing-corp-{corporation.corporation_id}"
        cache.set(lock_key_id, True, 300)
        cache.set(lock_key_corp_id, True, 300)
        try:
            data = self.fetch_corporation_stats(corporation.corporation_id)
            if not data:
                return False

            self.ingest_pilots_from_zkill_data(data, corporation=corporation)

            active_characters, weekly_kills, weekly_losses = self._extract_weekly_stats(data)

            # 1. Rolling 3-month lookback
            rolling_keys = self._get_rolling_3m_keys()
            months_data = data.get("months", {})
            historical_characters = []
            if isinstance(months_data, dict):
                has_rolling = any(str(k) in rolling_keys for k in months_data)
                for m_key, m in months_data.items():
                    m_str = str(m_key)
                    if (has_rolling and m_str in rolling_keys) or (not has_rolling):
                        if isinstance(m, dict):
                            count = (
                                m.get("characters", {}).get("count", 0)
                                if isinstance(m.get("characters"), dict)
                                else m.get("characters", 0)
                            )
                            if not count and isinstance(m.get("activepvp"), dict):
                                count = m.get("activepvp", {}).get("characters", {}).get("count", 0)
                            if isinstance(count, (int, float)) and count > 0:
                                historical_characters.append(int(count))
            elif isinstance(months_data, list):
                for m in months_data:
                    if isinstance(m, dict):
                        m_str = str(m.get("year", "")) + f"{int(m.get('month', 0)):02d}"
                        if not m_str or m_str in rolling_keys:
                            count = (
                                m.get("characters", {}).get("count", 0)
                                if isinstance(m.get("characters"), dict)
                                else m.get("characters", 0)
                            )
                            if isinstance(count, (int, float)) and count > 0:
                                historical_characters.append(int(count))

            peak_characters = (
                max([active_characters] + historical_characters)
                if historical_characters
                else active_characters
            )

            supers_data = data.get("supers", {})
            supercarrier_count = (
                supers_data.get("supercarrier", {}).get("count", 0)
                if isinstance(supers_data, dict)
                else 0
            )
            titan_count = (
                supers_data.get("titan", {}).get("count", 0)
                if isinstance(supers_data, dict)
                else 0
            )
            total_supers = supercarrier_count + titan_count

            groups_data = data.get("groups", {})
            dread_count = (
                groups_data.get("485", {}).get("count", 0)
                if isinstance(groups_data, dict)
                else 0
            )
            fax_count = (
                groups_data.get("1538", {}).get("count", 0)
                if isinstance(groups_data, dict)
                else 0
            )
            carrier_count = (
                groups_data.get("547", {}).get("count", 0)
                if isinstance(groups_data, dict)
                else 0
            )
            total_caps = dread_count + fax_count + carrier_count

            new_subcap = max(5, int(peak_characters * 0.6)) if peak_characters > 0 else (
                max(1, int(corporation.member_count * 0.15)) if corporation.member_count > 0 else 0
            )
            new_cap = total_caps if total_caps > 0 else (
                max(3, int(peak_characters * 0.15)) if peak_characters >= 30 else (
                    max(1, int(corporation.member_count * 0.03)) if corporation.member_count >= 50 else 0
                )
            )
            new_super = total_supers

            deltas = dict(corporation.zkill_deltas or {})
            if (
                corporation.prev_max_subcap_form > 0
                and new_subcap != corporation.prev_max_subcap_form
            ):
                diff = new_subcap - corporation.prev_max_subcap_form
                pct = int(round((diff / corporation.prev_max_subcap_form) * 100))
                sign = "+" if pct >= 0 else ""
                deltas["subcap"] = {
                    "pct": pct,
                    "text": f"{sign}{pct}% subcaps detected",
                    "prev": corporation.prev_max_subcap_form,
                    "curr": new_subcap,
                }
            elif (
                corporation.max_subcap_form == 0
                or corporation.prev_max_subcap_form == 0
            ):
                deltas.pop("subcap", None)

            if (
                corporation.prev_max_cap_form > 0
                and new_cap != corporation.prev_max_cap_form
            ):
                diff = new_cap - corporation.prev_max_cap_form
                pct = int(round((diff / corporation.prev_max_cap_form) * 100))
                sign = "+" if pct >= 0 else ""
                deltas["cap"] = {
                    "pct": pct,
                    "text": f"{sign}{pct}% capitals detected",
                    "prev": corporation.prev_max_cap_form,
                    "curr": new_cap,
                }
            elif (
                corporation.max_cap_form == 0
                or corporation.prev_max_cap_form == 0
            ):
                deltas.pop("cap", None)

            if (
                corporation.prev_max_super_form > 0
                and new_super != corporation.prev_max_super_form
            ):
                diff = new_super - corporation.prev_max_super_form
                pct = int(round((diff / corporation.prev_max_super_form) * 100))
                sign = "+" if pct >= 0 else ""
                deltas["super"] = {
                    "pct": pct,
                    "text": f"{sign}{pct}% supers detected",
                    "prev": corporation.prev_max_super_form,
                    "curr": new_super,
                }
            elif (
                corporation.max_super_form == 0
                or corporation.prev_max_super_form == 0
            ):
                deltas.pop("super", None)

            # 2. Activity Heatmap
            heatmap = self._build_activity_heatmap(data)

            if (
                corporation.max_subcap_form == 0
                or corporation.prev_max_subcap_form == 0
            ):
                corporation.prev_max_subcap_form = new_subcap
            else:
                corporation.prev_max_subcap_form = corporation.max_subcap_form

            if (
                corporation.max_cap_form == 0
                or corporation.prev_max_cap_form == 0
            ):
                corporation.prev_max_cap_form = new_cap
            else:
                corporation.prev_max_cap_form = corporation.max_cap_form

            if (
                corporation.max_super_form == 0
                or corporation.prev_max_super_form == 0
            ):
                corporation.prev_max_super_form = new_super
            else:
                corporation.prev_max_super_form = corporation.max_super_form

            corporation.max_subcap_form = new_subcap
            corporation.max_cap_form = new_cap
            corporation.max_super_form = new_super
            corporation.zkill_deltas = deltas
            corporation.activity_heatmap = heatmap
            corporation.active_pilots_count = active_characters
            corporation.weekly_kills_count = weekly_kills
            corporation.weekly_losses_count = weekly_losses
            corporation.zkill_stats_updated_at = timezone.now()

            corporation.save(
                update_fields=[
                    "active_pilots_count",
                    "weekly_kills_count",
                    "weekly_losses_count",
                    "zkill_stats_updated_at",
                    "max_subcap_form",
                    "max_cap_form",
                    "max_super_form",
                    "prev_max_subcap_form",
                    "prev_max_cap_form",
                    "prev_max_super_form",
                    "zkill_deltas",
                    "activity_heatmap",
                ]
            )
            return True
        finally:
            cache.delete(lock_key_id)
            cache.delete(lock_key_corp_id)


zkill_client = ZKillClient()
