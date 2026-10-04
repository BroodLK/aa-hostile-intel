"""
Sovereignty Intelligence and Operational Timezone Inference Engine
"""

# Standard Library
from collections import Counter
from datetime import timedelta
import re
from typing import Any, Dict, List, Optional

# Django
from django.utils import dateparse, timezone

# Alliance Auth
from allianceauth.services.hooks import get_extension_logger

# Third Party
from eve_sde.models import SolarSystem

# AA Hostile Intel
from hostile.models import (
    HostileAlliance,
    HostileCorporation,
    StructureTimer,
)
from hostile.services.esi_client import esi_client

logger = get_extension_logger(__name__)


def _get_val(obj: Any, key: str, default: Any = None) -> Any:
    """Safely extracts a value from a dict, list, Pydantic model, Bravado object, or generic class instance."""
    if obj is None:
        return default
    if isinstance(obj, (list, tuple)) and len(obj) > 0 and isinstance(obj[0], (dict, object)):
        obj = obj[0]
    if isinstance(obj, dict):
        return obj.get(key, default)
    if hasattr(obj, key):
        val = getattr(obj, key)
        return val if val is not None else default
    if hasattr(obj, "get") and callable(getattr(obj, "get", None)):
        try:
            return obj.get(key, default)
        except Exception:
            pass
    return default


class SovAnalysisEngine:
    """Processes public ESI sovereignty data, discovers hostile structures/timers, and profiles alliance timezones"""

    @classmethod
    def get_hour_timezone(cls, hour: int) -> str:
        """Returns the standard EVE Online operational timezone for a specific UTC hour (0-23)"""
        try:
            h = int(hour) % 24
        except (ValueError, TypeError):
            return "UNKNOWN"

        if 0 <= h < 8:
            return "USTZ"
        elif 8 <= h < 14:
            return "AUTZ"
        elif 14 <= h < 22:
            return "EUTZ"
        elif 22 <= h < 24:
            return "USTZ"
        return "UNKNOWN"

    @classmethod
    def infer_timezone(cls, hourly_distribution: Dict[str, int]) -> str:
        """Determines primary operational timezone based on vulnerability hourly clusters"""
        if not hourly_distribution:
            return "UNKNOWN"

        ustz_count = 0  # 00:00 - 08:00 UTC
        autz_count = 0  # 08:00 - 14:00 UTC
        eutz_count = 0  # 14:00 - 22:00 UTC
        ustz_count_extra = 0  # 22:00 - 24:00 UTC (late EUTZ / early USTZ)

        for hour_str, count in hourly_distribution.items():
            try:
                hour = int(hour_str)
                if 0 <= hour < 8:
                    ustz_count += count
                elif 8 <= hour < 14:
                    autz_count += count
                elif 14 <= hour < 22:
                    eutz_count += count
                elif 22 <= hour < 24:
                    ustz_count += count
            except (ValueError, TypeError):
                continue

        scores = [
            ("EUTZ", eutz_count),
            ("USTZ", ustz_count),
            ("AUTZ", autz_count),
        ]
        scores.sort(key=lambda x: x[1], reverse=True)

        top_tz, top_score = scores[0]
        if top_score == 0:
            return "UNKNOWN"

        return top_tz

    @classmethod
    def infer_combined_timezone(
        cls,
        activity_data: Optional[Dict[str, Any]] = None,
        sov_distribution: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Determines primary operational timezone combining zKill kill distribution and structure timers"""
        combined: Dict[str, int] = {}
        if activity_data and isinstance(activity_data, dict):
            if "matrix" in activity_data and isinstance(activity_data["matrix"], (list, tuple)):
                matrix = activity_data["matrix"]
                for row in matrix:
                    if isinstance(row, (list, tuple)):
                        for h_int, val in enumerate(row):
                            try:
                                if 0 <= h_int < 24 and isinstance(val, (int, float)):
                                    combined[str(h_int)] = combined.get(str(h_int), 0) + int(val)
                            except (ValueError, TypeError):
                                pass
            else:
                for k, v in activity_data.items():
                    if isinstance(v, dict):
                        for hk, hv in v.items():
                            try:
                                h_int = int(hk)
                                combined[str(h_int)] = combined.get(str(h_int), 0) + int(hv)
                            except (ValueError, TypeError):
                                pass
                    elif isinstance(v, (list, tuple)):
                        for h_int, hv in enumerate(v):
                            try:
                                if 0 <= h_int < 24 and isinstance(hv, (int, float)):
                                    combined[str(h_int)] = combined.get(str(h_int), 0) + int(hv)
                            except (ValueError, TypeError):
                                pass
                    elif isinstance(v, (int, float)):
                        try:
                            h_int = int(k)
                            combined[str(h_int)] = combined.get(str(h_int), 0) + int(v)
                        except (ValueError, TypeError):
                            pass

        if sov_distribution and isinstance(sov_distribution, dict):
            for k, v in sov_distribution.items():
                try:
                    h_int = int(k)
                    # Weight structure timer windows heavily
                    combined[str(h_int)] = combined.get(str(h_int), 0) + (int(v) * 10)
                except (ValueError, TypeError):
                    pass

        return cls.infer_timezone(combined)

    @classmethod
    def aggregate_alliance_vulnerability_windows(
        cls, alliance: HostileAlliance
    ) -> Dict[str, int]:
        """
        Aggregates crowd-sourced structure vulnerability windows for an alliance
        and updates its sov_timer_distribution profile.
        """
        hours: List[int] = []
        structures = alliance.structures.all()
        for struct in structures:
            if struct.vulnerability_hour is not None:
                hours.append(struct.vulnerability_hour)
            elif struct.vulnerability_window:
                hour_match = re.search(r"\b(\d{1,2})(?::\d{2})?\b", struct.vulnerability_window)
                if hour_match:
                    try:
                        h = int(hour_match.group(1))
                        if 0 <= h <= 23:
                            hours.append(h)
                    except ValueError:
                        pass

        for timer in StructureTimer.objects.filter(structure__alliance=alliance):
            if timer.timer_datetime:
                hours.append(timer.timer_datetime.hour)

        distribution = {str(h): count for h, count in Counter(hours).items()}
        alliance.sov_timer_distribution = distribution
        if not getattr(alliance, "override_timezone", False) and distribution:
            alliance.primary_timezone = cls.infer_timezone(distribution)
        alliance.save(update_fields=["sov_timer_distribution", "primary_timezone", "updated_at"])
        return distribution

    @classmethod
    def process_sovereignty_structures(
        cls, sov_structures: List[Any]
    ) -> Dict[int, Dict[str, Any]]:
        """Aggregates structure vulnerability times for tracked alliances and updates vulnerability profiles"""
        tracked_alliances = {
            a.alliance_id: a for a in HostileAlliance.objects.all()
        }
        if not tracked_alliances:
            return {}

        alliance_timers: Dict[int, List[int]] = {}

        for s in (sov_structures or []):
            alliance_id = _get_val(s, "alliance_id")
            if not alliance_id:
                continue

            try:
                aid_int = int(alliance_id)
            except (ValueError, TypeError):
                continue

            if aid_int not in tracked_alliances:
                continue

            vulnerable_start = _get_val(s, "vulnerable_start_time")
            if vulnerable_start:
                if isinstance(vulnerable_start, str):
                    dt = dateparse.parse_datetime(vulnerable_start)
                else:
                    dt = vulnerable_start

                if dt:
                    hour = dt.hour
                    if aid_int not in alliance_timers:
                        alliance_timers[aid_int] = []
                    alliance_timers[aid_int].append(hour)

        results: Dict[int, Dict[str, Any]] = {}
        for alliance_id, hours in alliance_timers.items():
            distribution = {str(h): count for h, count in Counter(hours).items()}
            primary_tz = cls.infer_timezone(distribution)
            alliance = tracked_alliances.get(alliance_id)
            if alliance:
                if not getattr(alliance, "override_timezone", False):
                    alliance.primary_timezone = cls.infer_combined_timezone(
                        activity_data=alliance.activity_heatmap,
                        sov_distribution=distribution,
                    )
                    alliance.save(update_fields=["primary_timezone", "updated_at"])

            results[alliance_id] = {
                "distribution": distribution,
                "primary_timezone": primary_tz,
                "alliance": alliance,
            }

        return results

    @classmethod
    def get_alliance_sovereignty_intel(
        cls, alliance: HostileAlliance
    ) -> Dict[str, Any]:
        """
        Gathers complete sovereignty intelligence for a hostile alliance:
        systems held, ADMs, vulnerability windows, reinforced/contest status,
        active campaign timers, grouped by region.
        """
        if not alliance or not alliance.alliance_id:
            return {
                "regions": [],
                "total_systems_count": 0,
                "total_regions_count": 0,
                "avg_adm_overall": 0.0,
                "active_contests_total": 0,
            }

        aid = int(alliance.alliance_id)
        sov_systems_raw = esi_client.get_sovereignty_systems()
        sov_structures_raw = esi_client.get_sovereignty_structures()
        sov_map_raw = esi_client.get_sovereignty_map()

        systems_dict: Dict[int, Dict[str, Any]] = {}

        # 1. Parse modern sovereignty systems
        for s in (sov_systems_raw or []):
            if not isinstance(s, dict):
                continue
            sys_id = s.get("solar_system_id")
            if not sys_id:
                continue
            claim = s.get("claim") or {}
            all_data = claim.get("alliance") or {}
            s_aid = all_data.get("alliance_id") or s.get("alliance_id")
            if not s_aid:
                continue
            try:
                if int(s_aid) != aid:
                    continue
            except (ValueError, TypeError):
                continue

            hub = all_data.get("sovereignty_hub") or {}
            vuln = hub.get("vulnerability_window") or {}
            dev = all_data.get("development") or {}
            adm_val = dev.get("activity_defense_multiplier")
            is_cap = all_data.get("is_capital_system", False)

            systems_dict[int(sys_id)] = {
                "solar_system_id": int(sys_id),
                "is_capital": bool(is_cap),
                "adm": float(adm_val) if adm_val is not None else 1.0,
                "vulnerable_start_time": vuln.get("start"),
                "vulnerable_end_time": vuln.get("end"),
                "structure_id": hub.get("id"),
            }

        # 2. Augment from sovereignty structures (legacy or supplementary)
        for struct in (sov_structures_raw or []):
            if not isinstance(struct, dict):
                continue
            s_aid = struct.get("alliance_id")
            if not s_aid:
                continue
            try:
                if int(s_aid) != aid:
                    continue
            except (ValueError, TypeError):
                continue
            sys_id = struct.get("solar_system_id")
            if not sys_id:
                continue
            sys_id_int = int(sys_id)
            if sys_id_int not in systems_dict:
                systems_dict[sys_id_int] = {
                    "solar_system_id": sys_id_int,
                    "is_capital": False,
                    "adm": 1.0,
                    "vulnerable_start_time": None,
                    "vulnerable_end_time": None,
                    "structure_id": struct.get("structure_id"),
                }
            entry = systems_dict[sys_id_int]
            if struct.get("vulnerability_occupancy_level") is not None:
                try:
                    entry["adm"] = float(struct.get("vulnerability_occupancy_level"))
                except (ValueError, TypeError):
                    pass
            if struct.get("vulnerable_start_time") and not entry.get("vulnerable_start_time"):
                entry["vulnerable_start_time"] = struct.get("vulnerable_start_time")
            if struct.get("vulnerable_end_time") and not entry.get("vulnerable_end_time"):
                entry["vulnerable_end_time"] = struct.get("vulnerable_end_time")

        # 3. Augment from sovereignty map
        for m in (sov_map_raw or []):
            if not isinstance(m, dict):
                continue
            s_aid = m.get("alliance_id")
            if not s_aid:
                continue
            try:
                if int(s_aid) != aid:
                    continue
            except (ValueError, TypeError):
                continue
            sys_id = m.get("solar_system_id")
            if not sys_id:
                continue
            sys_id_int = int(sys_id)
            if sys_id_int not in systems_dict:
                systems_dict[sys_id_int] = {
                    "solar_system_id": sys_id_int,
                    "is_capital": bool(m.get("is_capital_system", False)),
                    "adm": 1.0,
                    "vulnerable_start_time": None,
                    "vulnerable_end_time": None,
                    "structure_id": None,
                }
            elif m.get("is_capital_system"):
                systems_dict[sys_id_int]["is_capital"] = True

        if not systems_dict:
            return {
                "regions": [],
                "total_systems_count": 0,
                "total_regions_count": 0,
                "avg_adm_overall": 0.0,
                "active_contests_total": 0,
            }

        # Query SolarSystem objects from DB
        sys_ids = list(systems_dict.keys())
        solar_systems = {
            ss.id: ss
            for ss in SolarSystem.objects.filter(id__in=sys_ids).select_related(
                "constellation__region"
            )
        }

        # Query active sovereignty campaign timers for these systems
        now = timezone.now()
        recent_cutoff = now - timedelta(hours=24)
        active_timers_qs = (
            StructureTimer.objects.filter(
                solar_system_id__in=sys_ids,
                is_sov_campaign=True,
                timer_datetime__gte=recent_cutoff,
            )
            .select_related("solar_system")
            .order_by("timer_datetime")
        )
        timers_by_system: Dict[int, List[StructureTimer]] = {}
        for t in active_timers_qs:
            timers_by_system.setdefault(t.solar_system_id, []).append(t)

        # Build region grouping
        regions_map: Dict[str, Dict[str, Any]] = {}
        total_adm_sum = 0.0
        total_contests = 0

        for sys_id, info in systems_dict.items():
            ss = solar_systems.get(sys_id)
            sys_name = ss.name if ss else f"System #{sys_id}"
            const_name = (
                ss.constellation.name
                if ss and ss.constellation
                else "Unknown Constellation"
            )
            reg_name = (
                ss.constellation.region.name
                if ss and ss.constellation and ss.constellation.region
                else "Unknown Region"
            )
            sec_status = ss.security_status if ss else 0.0
            is_cap = info["is_capital"] or (alliance.sov_capital_id == sys_id)

            adm = info["adm"]
            total_adm_sum += adm

            # Vulnerability window formatting
            v_start = info.get("vulnerable_start_time")
            v_end = info.get("vulnerable_end_time")
            v_start_dt = None
            v_end_dt = None
            v_display = "Unknown"
            is_vulnerable_now = False

            if v_start:
                if isinstance(v_start, str):
                    v_start_dt = dateparse.parse_datetime(v_start)
                else:
                    v_start_dt = v_start
            if v_end:
                if isinstance(v_end, str):
                    v_end_dt = dateparse.parse_datetime(v_end)
                else:
                    v_end_dt = v_end

            if v_start_dt and v_end_dt:
                v_display = f"{v_start_dt.strftime('%H:%M')} - {v_end_dt.strftime('%H:%M')} UTC"
                current_time_utc = now.time()
                st = v_start_dt.time()
                et = v_end_dt.time()
                if st <= et:
                    is_vulnerable_now = st <= current_time_utc <= et
                else:
                    is_vulnerable_now = (current_time_utc >= st) or (current_time_utc <= et)
            elif v_start_dt:
                v_display = f"{v_start_dt.strftime('%H:%M')} UTC"

            sys_timers = timers_by_system.get(sys_id, [])
            is_reinforced = len(sys_timers) > 0
            if is_reinforced:
                total_contests += len(sys_timers)

            primary_timer = sys_timers[0] if sys_timers else None

            system_intel = {
                "solar_system_id": sys_id,
                "solar_system_name": sys_name,
                "constellation_name": const_name,
                "region_name": reg_name,
                "security_status": sec_status,
                "is_capital": is_cap,
                "adm": round(adm, 1),
                "adm_percentage": min(100, int((adm / 6.0) * 100)),
                "vulnerability_window_display": v_display,
                "is_vulnerable_now": is_vulnerable_now,
                "is_reinforced": is_reinforced,
                "timers": sys_timers,
                "primary_timer": primary_timer,
            }

            if reg_name not in regions_map:
                regions_map[reg_name] = {
                    "region_name": reg_name,
                    "systems": [],
                    "systems_count": 0,
                    "total_adm": 0.0,
                    "active_contests_count": 0,
                }
            reg_entry = regions_map[reg_name]
            reg_entry["systems"].append(system_intel)
            reg_entry["systems_count"] += 1
            reg_entry["total_adm"] += adm
            if is_reinforced:
                reg_entry["active_contests_count"] += len(sys_timers)

        sorted_regions = []
        for reg_name in sorted(regions_map.keys()):
            r_data = regions_map[reg_name]
            r_data["avg_adm"] = (
                round(r_data["total_adm"] / r_data["systems_count"], 1)
                if r_data["systems_count"] > 0
                else 1.0
            )
            r_data["systems"].sort(
                key=lambda s: (not s["is_capital"], not s["is_reinforced"], s["solar_system_name"])
            )
            sorted_regions.append(r_data)

        total_systems = len(systems_dict)
        avg_adm_overall = (
            round(total_adm_sum / total_systems, 1) if total_systems > 0 else 1.0
        )

        return {
            "regions": sorted_regions,
            "total_systems_count": total_systems,
            "total_regions_count": len(sorted_regions),
            "avg_adm_overall": avg_adm_overall,
            "active_contests_total": total_contests,
        }

    @classmethod
    def get_alliance_hourly_schedule(
        cls, alliance: HostileAlliance
    ) -> List[Dict[str, Any]]:
        """
        Builds 24-hour UTC schedule groups of known structures and active structure timers
        with detailed items for clickable accordion expansion.
        """
        hours_map: Dict[int, Dict[str, Any]] = {}
        for h in range(24):
            hours_map[h] = {
                "hour": h,
                "hour_str": f"{h:02d}",
                "hour_display": f"{h:02d}:00 UTC",
                "count": 0,
                "structures_count": 0,
                "timers_count": 0,
                "items": [],
            }

        # 1. Add known structures
        for struct in alliance.structures.select_related("structure_type", "solar_system").all():
            vh = struct.vulnerability_hour
            if vh is None and struct.vulnerability_window:
                m = re.search(r"\b(\d{1,2})(?::\d{2})?\b", struct.vulnerability_window)
                if m:
                    try:
                        vh = int(m.group(1)) % 24
                    except ValueError:
                        pass
            if vh is not None and 0 <= vh <= 23:
                hours_map[vh]["structures_count"] += 1
                hours_map[vh]["count"] += 1
                hours_map[vh]["items"].append({
                    "item_type": "Structure",
                    "id": struct.id,
                    "name": struct.name,
                    "system_name": struct.solar_system.name if struct.solar_system else "Unknown",
                    "structure_type_name": struct.structure_type.name if struct.structure_type else "Structure",
                    "state": struct.get_state_display(),
                    "state_raw": struct.state,
                    "core_status": struct.core_status,
                    "details": struct.vulnerability_window or f"{vh:02d}:00 UTC (±3h)",
                    "edit_url": f"/structures/{struct.id}/edit/",
                    "is_timer": False,
                })

        # 2. Add active structure timers
        now = timezone.now()
        for timer in StructureTimer.objects.filter(
            structure__alliance=alliance,
            timer_datetime__gte=now - timedelta(hours=24),
        ).select_related("structure", "solar_system", "structure__structure_type").order_by("timer_datetime"):
            if timer.timer_datetime:
                th = timer.timer_datetime.hour
                hours_map[th]["timers_count"] += 1
                hours_map[th]["count"] += 1
                hours_map[th]["items"].append({
                    "item_type": "Active Timer",
                    "id": timer.id,
                    "name": timer.structure.name if timer.structure else timer.target_name,
                    "system_name": timer.solar_system.name if timer.solar_system else "Unknown",
                    "structure_type_name": timer.structure.structure_type.name if (timer.structure and timer.structure.structure_type) else timer.timer_type,
                    "state": f"{timer.get_timer_type_display()} Timer",
                    "state_raw": "ARMOR_REINFORCED" if timer.timer_type == "ARMOR" else "HULL_REINFORCED",
                    "core_status": timer.structure.core_status if timer.structure else None,
                    "details": timer.timer_datetime.strftime("%Y-%m-%d %H:%M UTC"),
                    "timer_datetime": timer.timer_datetime,
                    "is_expired": timer.is_expired,
                    "edit_url": f"/timers/{timer.id}/edit/",
                    "is_timer": True,
                })

        active_hours = [h_data for h_data in hours_map.values() if h_data["count"] > 0]
        return active_hours

    @classmethod
    def process_sovereignty_campaigns(
        cls, campaigns: List[Any]
    ) -> List[StructureTimer]:
        """Creates or updates upcoming sovereignty contest timers from public campaign feed"""
        created_timers = []
        if not campaigns:
            return created_timers

        # Bulk resolve defender alliance/corporation names
        defender_ids = set()
        for c in campaigns:
            did = _get_val(c, "defender_id")
            if did:
                try:
                    defender_ids.add(int(did))
                except (ValueError, TypeError):
                    pass

        known_defenders = {}
        if defender_ids:
            for ha in HostileAlliance.objects.filter(alliance_id__in=defender_ids):
                known_defenders[ha.alliance_id] = (ha.alliance_name, ha.ticker or "")
            for hc in HostileCorporation.objects.filter(corporation_id__in=defender_ids):
                if hc.corporation_id not in known_defenders:
                    known_defenders[hc.corporation_id] = (hc.corporation_name, hc.ticker or "")
            try:
                from allianceauth.eveonline.models import EveAllianceInfo, EveCorporationInfo

                for ea in EveAllianceInfo.objects.filter(alliance_id__in=defender_ids):
                    if ea.alliance_id not in known_defenders:
                        known_defenders[ea.alliance_id] = (ea.alliance_name, ea.alliance_ticker or "")
                for ec in EveCorporationInfo.objects.filter(corporation_id__in=defender_ids):
                    if ec.corporation_id not in known_defenders:
                        known_defenders[ec.corporation_id] = (ec.corporation_name, ec.corporation_ticker or "")
            except Exception:
                pass

            missing_dids = [d for d in defender_ids if d not in known_defenders]
            if missing_dids:
                try:
                    names_data = esi_client.post_universe_names(missing_dids)
                    for item in (names_data or []):
                        i_id = _get_val(item, "id")
                        i_name = _get_val(item, "name")
                        if i_id and i_name:
                            known_defenders[int(i_id)] = (str(i_name), "")
                except Exception as e:
                    logger.debug("Failed to resolve defender names: %s", e)

        active_timer_ids = set()
        for c in campaigns:
            system_id = _get_val(c, "solar_system_id")
            start_time_raw = _get_val(c, "start_time")
            event_type = _get_val(c, "event_type", "OTHER")
            defender_id_raw = _get_val(c, "defender_id")
            campaign_id_raw = _get_val(c, "campaign_id")

            if not start_time_raw or not system_id:
                continue

            if isinstance(start_time_raw, str):
                start_dt = dateparse.parse_datetime(start_time_raw)
            else:
                start_dt = start_time_raw

            if not start_dt:
                continue

            system = SolarSystem.objects.filter(id=system_id).first()
            defender_id = None
            if defender_id_raw:
                try:
                    defender_id = int(defender_id_raw)
                except (ValueError, TypeError):
                    defender_id = None

            campaign_id = None
            if campaign_id_raw:
                try:
                    campaign_id = int(campaign_id_raw)
                except (ValueError, TypeError):
                    campaign_id = None

            defender_name = ""
            defender_ticker = ""
            if defender_id and defender_id in known_defenders:
                defender_name, defender_ticker = known_defenders[defender_id]

            timer_type_map = {
                "ihub_defense": "SOV_IHUB",
                "tcu_defense": "SOV_TCU",
                "station_defense": "OTHER",
                "station_freeport": "OTHER",
            }
            mapped_type = timer_type_map.get(event_type, "OTHER")

            defender_score_raw = _get_val(c, "defender_score", 0.6)
            try:
                defender_score = (
                    float(defender_score_raw)
                    if defender_score_raw is not None
                    else 0.6
                )
            except (ValueError, TypeError):
                defender_score = 0.6

            notes = f"Sovereignty Contest: {event_type.replace('_', ' ').title()}"
            if defender_name:
                notes += f" | Defender: {defender_name}"
                if defender_ticker:
                    notes += f" [{defender_ticker}]"

            if campaign_id:
                timer = StructureTimer.objects.filter(
                    campaign_id=campaign_id, is_sov_campaign=True
                ).first()
                if timer:
                    timer.solar_system = system
                    timer.timer_type = mapped_type
                    timer.timer_datetime = start_dt
                    timer.is_verified = True
                    timer.is_concluded = False
                    timer.defender_id = defender_id
                    timer.defender_name = defender_name
                    timer.defender_ticker = defender_ticker
                    timer.defender_score = defender_score
                    timer.notes = notes
                    timer.save()
                    created_timers.append(timer)
                    active_timer_ids.add(timer.id)
                    continue

            timer, created = StructureTimer.objects.update_or_create(
                solar_system=system,
                timer_type=mapped_type,
                timer_datetime=start_dt,
                defaults={
                    "is_verified": True,
                    "is_sov_campaign": True,
                    "is_concluded": False,
                    "campaign_id": campaign_id,
                    "defender_id": defender_id,
                    "defender_name": defender_name,
                    "defender_ticker": defender_ticker,
                    "defender_score": defender_score,
                    "notes": notes,
                },
            )
            created_timers.append(timer)
            active_timer_ids.add(timer.id)

        # Mark past/started sov campaign timers no longer in ESI as concluded
        now = timezone.now()
        concluded_timers = StructureTimer.objects.filter(
            is_sov_campaign=True,
            is_concluded=False,
            timer_datetime__lte=now,
        ).exclude(id__in=active_timer_ids)

        for t in concluded_timers:
            t.is_concluded = True
            t.save(update_fields=["is_concluded", "updated_at"])

        # Discard old sov campaign timers concluded/expired more than 24 hours ago
        cutoff = now - timedelta(hours=24)
        StructureTimer.objects.filter(
            is_sov_campaign=True,
            is_concluded=True,
            timer_datetime__lt=cutoff,
        ).delete()

        return created_timers

    @classmethod
    def sync_alliance_members_and_corps(
        cls, alliance: HostileAlliance
    ) -> Dict[str, Any]:
        """
        Queries ESI for an alliance's member corporations and public corporation info (member_count).
        Stores member corporations, calculates total character member counts, and computes rolling deltas.
        """
        corp_ids = esi_client.get_alliance_corporations(alliance.alliance_id)
        if corp_ids is None or not isinstance(corp_ids, (list, tuple, set)):
            return {
                "member_count": alliance.member_count,
                "corps_count": alliance.member_corps_count,
                "success": False,
            }

        total_members = 0
        valid_corp_ids = []

        for cid in corp_ids:
            try:
                corp_id_int = int(cid)
            except (ValueError, TypeError):
                continue

            valid_corp_ids.append(corp_id_int)
            corp_info = esi_client.get_corporation_info(corp_id_int)
            name = _get_val(corp_info, "name") or f"Corporation {corp_id_int}"
            ticker = _get_val(corp_info, "ticker") or ""
            m_count_raw = _get_val(corp_info, "member_count", 0)
            try:
                m_count = int(m_count_raw) if m_count_raw is not None else 0
            except (ValueError, TypeError):
                m_count = 0

            total_members += m_count

            corp = HostileCorporation.objects.filter(corporation_id=corp_id_int).first()
            subcap_est = max(1, int(m_count * 0.15)) if m_count > 0 else 0
            cap_est = max(1, int(m_count * 0.03)) if m_count >= 50 else 0
            if corp:
                corp.corporation_name = name
                if ticker:
                    corp.ticker = ticker
                corp.alliance = alliance
                corp.prev_member_count = corp.member_count
                corp.member_count = m_count
                if corp.max_subcap_form == 0 and subcap_est > 0:
                    corp.max_subcap_form = subcap_est
                if corp.max_cap_form == 0 and cap_est > 0:
                    corp.max_cap_form = cap_est
                corp.save(
                    update_fields=[
                        "corporation_name",
                        "ticker",
                        "alliance",
                        "prev_member_count",
                        "member_count",
                        "max_subcap_form",
                        "max_cap_form",
                        "updated_at",
                    ]
                )
            else:
                corp = HostileCorporation.objects.create(
                    corporation_id=corp_id_int,
                    corporation_name=name,
                    ticker=ticker,
                    alliance=alliance,
                    member_count=m_count,
                    prev_member_count=m_count,
                    max_subcap_form=subcap_est,
                    max_cap_form=cap_est,
                )

            try:
                from hostile.services.zkill_client import zkill_client

                zkill_client.update_corporation_stats(corp)
            except Exception as e:
                logger.debug("Failed zkill update for corp %s: %s", corp_id_int, e)

        # Fetch alliance info for executor corporation designation
        try:
            alliance_info = esi_client.get_alliance_info(alliance.alliance_id)
            if alliance_info and _get_val(alliance_info, "executor_corporation_id"):
                alliance.executor_corp_id = int(
                    _get_val(alliance_info, "executor_corporation_id")
                )
        except Exception as e:
            logger.debug(
                "Failed to fetch alliance info for executor corp %s: %s",
                alliance.alliance_id,
                e,
            )

        # Disassociate corps that have left this alliance
        HostileCorporation.objects.filter(alliance=alliance).exclude(
            corporation_id__in=valid_corp_ids
        ).update(alliance=None)

        deltas = dict(alliance.zkill_deltas or {})
        prev_m_count = (
            alliance.prev_member_count
            if alliance.prev_member_count > 0
            else alliance.member_count
        )
        if prev_m_count > 0 and total_members != prev_m_count:
            diff = total_members - prev_m_count
            pct = int(round((diff / prev_m_count) * 100))
            sign = "+" if pct >= 0 else ""
            deltas["members"] = {
                "pct": pct,
                "text": f"{sign}{pct}% characters ({total_members:,})",
                "prev": prev_m_count,
                "curr": total_members,
            }
            alliance.prev_member_count = prev_m_count
        elif (
            alliance.member_count == 0
            and alliance.prev_member_count == 0
        ):
            # First load: do not generate fake 100% delta
            deltas.pop("members", None)
            alliance.prev_member_count = total_members
        else:
            alliance.prev_member_count = (
                alliance.member_count
                if alliance.member_count > 0
                else total_members
            )

        alliance.member_count = total_members
        alliance.member_corps_count = len(valid_corp_ids)
        alliance.zkill_deltas = deltas

        alliance.save(
            update_fields=[
                "member_count",
                "prev_member_count",
                "member_corps_count",
                "executor_corp_id",
                "zkill_deltas",
                "updated_at",
            ]
        )

        return {
            "member_count": total_members,
            "corps_count": len(valid_corp_ids),
            "success": True,
        }

    @classmethod
    def _update_alliance_sov_held(
        cls, alliance: HostileAlliance, new_held: int
    ) -> HostileAlliance:
        """Helper to calculate percentage deltas and update systems_held_count on an alliance"""
        deltas = dict(alliance.zkill_deltas or {})
        prev_held = (
            alliance.prev_systems_held_count
            if alliance.prev_systems_held_count > 0
            else alliance.systems_held_count
        )
        if prev_held > 0 and new_held != prev_held:
            diff = new_held - prev_held
            pct = int(round((diff / prev_held) * 100))
            sign = "+" if pct >= 0 else ""
            deltas["systems_held"] = {
                "pct": pct,
                "text": f"{sign}{pct}% systems held ({new_held})",
                "prev": prev_held,
                "curr": new_held,
            }
            alliance.prev_systems_held_count = prev_held
        elif (
            alliance.systems_held_count == 0
            and alliance.prev_systems_held_count == 0
        ):
            # First load: do not generate fake 100% delta
            deltas.pop("systems_held", None)
            alliance.prev_systems_held_count = new_held
        else:
            alliance.prev_systems_held_count = (
                alliance.systems_held_count
                if alliance.systems_held_count > 0
                else new_held
            )

        alliance.systems_held_count = new_held
        alliance.zkill_deltas = deltas

        alliance.save(
            update_fields=[
                "systems_held_count",
                "prev_systems_held_count",
                "zkill_deltas",
                "updated_at",
            ]
        )
        return alliance

    @classmethod
    def sync_sovereignty_held(
        cls, sov_map: Optional[List[Any]] = None
    ) -> Dict[int, int]:
        """
        Polls the public ESI sovereignty map and synchronizes systems_held_count across all tracked alliances.
        """
        if sov_map is None:
            sov_map = esi_client.get_sovereignty_map()

        if not sov_map:
            logger.warning(
                "Sovereignty map is empty or unavailable from ESI. Preserving existing systems held."
            )
            return {a.alliance_id: a.systems_held_count for a in HostileAlliance.objects.all()}

        held_counts = Counter()
        capitals = {}
        for item in (sov_map or []):
            aid = _get_val(item, "alliance_id")
            if aid:
                try:
                    aid_int = int(aid)
                    held_counts[aid_int] += 1
                    if _get_val(item, "is_capital_system"):
                        sys_id = _get_val(item, "solar_system_id") or _get_val(item, "system_id")
                        if sys_id:
                            capitals[aid_int] = int(sys_id)
                except (ValueError, TypeError):
                    pass

        results = {}
        for alliance in HostileAlliance.objects.all():
            count = held_counts.get(alliance.alliance_id, 0)
            cls._update_alliance_sov_held(alliance, count)
            if alliance.alliance_id in capitals:
                cap_sys_id = capitals[alliance.alliance_id]
                if not alliance.sov_capital or alliance.sov_capital_id != cap_sys_id:
                    sys_obj = SolarSystem.objects.filter(id=cap_sys_id).first()
                    if sys_obj:
                        alliance.sov_capital = sys_obj
                        alliance.save(update_fields=["sov_capital", "updated_at"])
            results[alliance.alliance_id] = count

        return results

    @classmethod
    def sync_alliance_sovereignty_held_single(
        cls, alliance: HostileAlliance, sov_map: Optional[List[Any]] = None
    ) -> int:
        """Synchronizes systems held for a single alliance instance"""
        if sov_map is None:
            sov_map = esi_client.get_sovereignty_map()

        if not sov_map:
            logger.warning(
                "Sovereignty map is empty or unavailable for %s. Preserving existing count (%d).",
                alliance.alliance_name,
                alliance.systems_held_count,
            )
            return alliance.systems_held_count

        count = 0
        cap_sys_id = None
        for item in (sov_map or []):
            aid = _get_val(item, "alliance_id")
            if aid and int(aid) == alliance.alliance_id:
                count += 1
                if _get_val(item, "is_capital_system"):
                    sys_id = _get_val(item, "solar_system_id") or _get_val(item, "system_id")
                    if sys_id:
                        cap_sys_id = int(sys_id)

        cls._update_alliance_sov_held(alliance, count)
        if cap_sys_id:
            if not alliance.sov_capital or alliance.sov_capital_id != cap_sys_id:
                sys_obj = SolarSystem.objects.filter(id=cap_sys_id).first()
                if sys_obj:
                    alliance.sov_capital = sys_obj
                    alliance.save(update_fields=["sov_capital", "updated_at"])
        return count
