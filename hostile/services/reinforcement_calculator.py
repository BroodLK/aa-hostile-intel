"""
EVE Online Upwell Structure & POCO Reinforcement Exit Timer Calculator Service.

Based on CCP's official Upwell 2.0 vulnerability mechanics and support documentation:
https://support.eveonline.com/hc/en-us/articles/208289385-Upwell-Structures-Vulnerability-States
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone as dt_timezone
from typing import Optional, Tuple

from django.utils import timezone

from hostile.models import HostileStructure, SolarSystem, ItemType


class StructureCategory:
    MEDIUM = "MEDIUM"
    LARGE_XL = "LARGE_XL"
    FLEX = "FLEX"
    POCO = "POCO"
    OTHER = "OTHER"

    CHOICES = (
        (MEDIUM, "Medium Upwell (Astrahus, Raitaru, Athanor)"),
        (LARGE_XL, "Large / Extra-Large Upwell (Fortizar, Azbel, Tatara, Keepstar, Sotiyo)"),
        (FLEX, "FLEX Structure (Ansiblex, Pharolux, Tenebrex, Metenox Moon Drill)"),
        (POCO, "Player Owned Customs Office (POCO)"),
    )


class LocationSecurity:
    NULLSEC_LOWSEC = "NULLSEC_LOWSEC"
    HIGHSEC = "HIGHSEC"
    WORMHOLE = "WORMHOLE"
    WAR_HQ = "WAR_HQ"

    CHOICES = (
        (NULLSEC_LOWSEC, "Nullsec / Lowsec Space"),
        (HIGHSEC, "High Security Space (0.5 - 1.0)"),
        (WORMHOLE, "Wormhole / J-Space (-1.0)"),
        (WAR_HQ, "Highsec War HQ (War Headquarters)"),
    )


class TimerStage:
    SHIELD_REINFORCE = "ARMOR"  # Shield dropped -> Armor timer
    ARMOR_REINFORCE = "HULL"   # Armor dropped -> Hull / Final timer

    CHOICES = (
        (SHIELD_REINFORCE, "Shield Depleted → Armor Timer Exit"),
        (ARMOR_REINFORCE, "Armor Depleted → Hull / Final Timer Exit"),
    )


class PowerState:
    FULL_POWER = "FULL_POWER"
    LOW_POWER = "LOW_POWER"
    ABANDONED = "ABANDONED"

    CHOICES = (
        (FULL_POWER, "Full Power (Fueled / Online Services)"),
        (LOW_POWER, "Low Power (No Online Services)"),
        (ABANDONED, "Abandoned (Unfueled 7+ Days)"),
    )


@dataclass
class ReinforcementCalculationResult:
    is_applicable: bool
    status_message: str
    structure_size: str
    location_type: str
    timer_stage: str
    power_state: str
    reinforced_at: datetime
    vulnerability_hour: Optional[int]
    vulnerability_window: str
    base_delay_hours: float
    base_delay_days: float
    jitter_hours: float
    earliest_exit: Optional[datetime]
    nominal_exit: Optional[datetime]
    latest_exit: Optional[datetime]
    exit_window_str: str
    duration_min_hours: float
    duration_max_hours: float
    time_remaining_nominal: Optional[timedelta]
    time_remaining_str: str
    notes: str


class ReinforcementCalculator:
    """
    Calculator engine for guesstimating and determining Upwell Structure reinforcement exit timers.
    """

    @classmethod
    def infer_structure_size(cls, item_type_or_name) -> str:
        """
        Determines the category size (MEDIUM, LARGE_XL, FLEX, POCO) from an ItemType, HostileStructure, or name string.
        """
        if not item_type_or_name:
            return StructureCategory.MEDIUM

        if hasattr(item_type_or_name, "structure_type") and getattr(item_type_or_name, "structure_type"):
            return cls.infer_structure_size(getattr(item_type_or_name, "structure_type"))

        name = ""
        group_name = ""
        group_id = None

        if isinstance(item_type_or_name, ItemType):
            name = item_type_or_name.name or ""
            if item_type_or_name.group:
                group_name = item_type_or_name.group.name or ""
                group_id = item_type_or_name.group_id
        elif isinstance(item_type_or_name, str):
            name = item_type_or_name
        elif hasattr(item_type_or_name, "name"):
            name = getattr(item_type_or_name, "name", "")
            if hasattr(item_type_or_name, "group") and getattr(item_type_or_name, "group"):
                group_obj = getattr(item_type_or_name, "group")
                group_name = getattr(group_obj, "name", "")
                group_id = getattr(group_obj, "id", None)
            if hasattr(item_type_or_name, "group_id"):
                group_id = getattr(item_type_or_name, "group_id", None)

        name_lower = name.lower()
        group_lower = group_name.lower()

        # Check FLEX Group IDs (2016: Jump Gate, 2017: Cyno Jammer, 2014: Cyno Beacon, 4807: Moon Drill)
        if group_id in [2016, 2017, 2014, 4807]:
            return StructureCategory.FLEX

        # Check Group Names for FLEX
        if any(f in group_lower for f in ["moon drill", "jump gate", "cynosural jammer", "cynosural beacon", "cyno jammer", "cyno beacon", "flex"]):
            return StructureCategory.FLEX

        # FLEX Structures by Name
        if any(f in name_lower for f in ["ansiblex", "pharolux", "tenebrex", "metenox", "moon drill", "drill", "flex", "jump gate", "cyno beacon", "cyno jammer"]):
            return StructureCategory.FLEX

        # Large / Extra-Large
        if any(
            l in name_lower
            for l in [
                "fortizar",
                "azbel",
                "tatara",
                "keepstar",
                "sotiyo",
                "faction fortizar",
                "palatine",
            ]
        ):
            return StructureCategory.LARGE_XL

        # Medium
        if any(
            m in name_lower
            for m in ["astrahus", "raitaru", "athanor", "citadel", "refinery", "engineering"]
        ):
            return StructureCategory.MEDIUM

        # POCO
        if "customs office" in name_lower or "poco" in name_lower or group_id == 511 or "customs" in group_lower:
            return StructureCategory.POCO

        return StructureCategory.MEDIUM

    @classmethod
    def infer_location_type(cls, solar_system: Optional[SolarSystem]) -> str:
        """
        Determines location security classification from a SolarSystem object.
        """
        if not solar_system:
            return LocationSecurity.NULLSEC_LOWSEC

        sec = solar_system.security_status
        name = solar_system.name or ""

        # Wormholes typically start with J followed by numbers or named w-space
        if sec < -0.98 and (
            name.startswith("J") and len(name) == 7 and name[1:].isdigit()
        ) or "wormhole" in str(getattr(solar_system, "system_id", "")).lower():
            return LocationSecurity.WORMHOLE

        if sec >= 0.45:
            return LocationSecurity.HIGHSEC
        elif sec > 0.0:
            return LocationSecurity.NULLSEC_LOWSEC  # Lowsec
        else:
            return LocationSecurity.NULLSEC_LOWSEC  # Nullsec

    @classmethod
    def get_valid_stages(cls, structure_size: str, is_low_power: bool = False):
        """
        Returns list of valid (value, label) tuples for timer_stage based on structure size and power state.
        """
        if structure_size in (StructureCategory.MEDIUM, StructureCategory.POCO):
            return [
                (TimerStage.SHIELD_REINFORCE, "Shield Depleted → Armor Timer Exit"),
            ]
        elif structure_size == StructureCategory.FLEX:
            return [
                (TimerStage.ARMOR_REINFORCE, "Armor Depleted → Hull / Final Timer Exit"),
            ]
        elif structure_size == StructureCategory.LARGE_XL:
            if is_low_power:
                return [
                    (TimerStage.ARMOR_REINFORCE, "Armor Depleted → Hull / Final Timer Exit (Low Power Skips Armor)"),
                ]
            return [
                (TimerStage.SHIELD_REINFORCE, "Shield Depleted → Armor Timer Exit"),
                (TimerStage.ARMOR_REINFORCE, "Armor Depleted → Hull / Final Timer Exit"),
            ]
        return list(TimerStage.CHOICES)

    @classmethod
    def calculate_exit_timer(
        cls,
        reinforced_at: Optional[datetime] = None,
        structure_size: str = StructureCategory.MEDIUM,
        location_type: str = LocationSecurity.NULLSEC_LOWSEC,
        timer_stage: str = TimerStage.SHIELD_REINFORCE,
        vulnerability_hour: Optional[int] = None,
        vulnerability_window: str = "",
        power_state: str = PowerState.FULL_POWER,
        is_low_power: bool = False,
    ) -> ReinforcementCalculationResult:
        """
        Calculates the earliest, nominal, and latest reinforce exit times.
        """
        if is_low_power and power_state != PowerState.ABANDONED:
            power_state = PowerState.LOW_POWER

        if reinforced_at is None:
            reinforced_at = timezone.now()
        elif timezone.is_naive(reinforced_at):
            reinforced_at = timezone.make_aware(reinforced_at, dt_timezone.utc)

        # 1. Check Abandoned Mode (No reinforcement timers)
        if power_state == PowerState.ABANDONED:
            return ReinforcementCalculationResult(
                is_applicable=False,
                status_message="Abandoned structures have no reinforcement timers. Hitpoints are immediately vulnerable and the structure can be destroyed in a single uninterrupted attack.",
                structure_size=structure_size,
                location_type=location_type,
                timer_stage=timer_stage,
                power_state=power_state,
                reinforced_at=reinforced_at,
                vulnerability_hour=vulnerability_hour,
                vulnerability_window=vulnerability_window,
                base_delay_hours=0.0,
                base_delay_days=0.0,
                jitter_hours=0.0,
                earliest_exit=reinforced_at,
                nominal_exit=reinforced_at,
                latest_exit=reinforced_at,
                exit_window_str="Immediate (No Timer)",
                duration_min_hours=0.0,
                duration_max_hours=0.0,
                time_remaining_nominal=timedelta(0),
                time_remaining_str="None (Abandoned)",
                notes="Abandoned structure skips both armor and hull reinforcement cycles.",
            )

        # 2. Check Medium Structure Hull Stage (Mediums have NO hull timer!)
        if structure_size == StructureCategory.MEDIUM and timer_stage == TimerStage.ARMOR_REINFORCE:
            return ReinforcementCalculationResult(
                is_applicable=False,
                status_message="Medium Upwell structures (Astrahus, Raitaru, Athanor) do not have a hull reinforcement cycle. Once armor hitpoints are depleted, the hull becomes immediately vulnerable with a standard 15-minute repair timer.",
                structure_size=structure_size,
                location_type=location_type,
                timer_stage=timer_stage,
                power_state=power_state,
                reinforced_at=reinforced_at,
                vulnerability_hour=vulnerability_hour,
                vulnerability_window=vulnerability_window,
                base_delay_hours=0.0,
                base_delay_days=0.0,
                jitter_hours=0.0,
                earliest_exit=reinforced_at,
                nominal_exit=reinforced_at,
                latest_exit=reinforced_at,
                exit_window_str="Immediate (No Hull Reinforce)",
                duration_min_hours=0.0,
                duration_max_hours=0.0,
                time_remaining_nominal=timedelta(0),
                time_remaining_str="0m (Immediate Hull Vulnerability)",
                notes="After dropping armor on a Medium structure, continue shooting to destroy hull.",
            )

        # 3. Check FLEX Structure Shield Stage (FLEX structures have NO armor timer!)
        if structure_size == StructureCategory.FLEX and timer_stage == TimerStage.SHIELD_REINFORCE:
            return ReinforcementCalculationResult(
                is_applicable=False,
                status_message="FLEX structures (Ansiblex Jump Gates, Pharolux Cyno Beacons, Tenebrex Cyno Jammers) do not have armor reinforcement. Depleting shield makes armor immediately vulnerable.",
                structure_size=structure_size,
                location_type=location_type,
                timer_stage=timer_stage,
                power_state=power_state,
                reinforced_at=reinforced_at,
                vulnerability_hour=vulnerability_hour,
                vulnerability_window=vulnerability_window,
                base_delay_hours=0.0,
                base_delay_days=0.0,
                jitter_hours=0.0,
                earliest_exit=reinforced_at,
                nominal_exit=reinforced_at,
                latest_exit=reinforced_at,
                exit_window_str="Immediate (No Armor Reinforce)",
                duration_min_hours=0.0,
                duration_max_hours=0.0,
                time_remaining_nominal=timedelta(0),
                time_remaining_str="0m (Immediate Armor Vulnerability)",
                notes="FLEX structures only possess a final hull reinforcement timer.",
            )

        # 4. Check Large/XL Low Power Shield Stage (Skips armor reinforcement)
        if (
            structure_size == StructureCategory.LARGE_XL
            and power_state == PowerState.LOW_POWER
            and timer_stage == TimerStage.SHIELD_REINFORCE
        ):
            return ReinforcementCalculationResult(
                is_applicable=False,
                status_message="Large/XL structures in Low Power mode skip armor reinforcement and proceed directly to vulnerable armor.",
                structure_size=structure_size,
                location_type=location_type,
                timer_stage=timer_stage,
                power_state=power_state,
                reinforced_at=reinforced_at,
                vulnerability_hour=vulnerability_hour,
                vulnerability_window=vulnerability_window,
                base_delay_hours=0.0,
                base_delay_days=0.0,
                jitter_hours=0.0,
                earliest_exit=reinforced_at,
                nominal_exit=reinforced_at,
                latest_exit=reinforced_at,
                exit_window_str="Immediate (Low Power Skips Armor)",
                duration_min_hours=0.0,
                duration_max_hours=0.0,
                time_remaining_nominal=timedelta(0),
                time_remaining_str="0m (Low Power Armor Vulnerable)",
                notes="No armor reinforcement cycle for unpowered Large/XL structures.",
            )

        # 5. Base Delays and Jitter by Structure and Location
        base_delay_hours = 0.0
        jitter_hours = 0.0

        if structure_size == StructureCategory.MEDIUM:
            # Medium Shield Reinforcement
            jitter_hours = 1.5
            if location_type == LocationSecurity.WORMHOLE:
                base_delay_hours = 60.0  # 2.5 days
            elif location_type == LocationSecurity.HIGHSEC:
                base_delay_hours = 108.0  # 4.5 days
            elif location_type == LocationSecurity.WAR_HQ:
                base_delay_hours = 24.0  # 24 hours
            else:  # NULLSEC_LOWSEC
                base_delay_hours = 84.0  # 3.5 days

        elif structure_size == StructureCategory.LARGE_XL:
            jitter_hours = 3.0
            if timer_stage == TimerStage.SHIELD_REINFORCE:
                base_delay_hours = 24.0  # 24 hours across all space
            else:  # ARMOR_REINFORCE -> Hull Timer
                if location_type == LocationSecurity.WORMHOLE:
                    base_delay_hours = 36.0  # 1.5 days
                elif location_type == LocationSecurity.HIGHSEC:
                    base_delay_hours = 108.0  # 4.5 days
                elif location_type == LocationSecurity.WAR_HQ:
                    base_delay_hours = 24.0  # 24 hours
                else:  # NULLSEC_LOWSEC
                    base_delay_hours = 60.0  # 2.5 days

        elif structure_size == StructureCategory.FLEX:
            # FLEX Hull Reinforcement
            base_delay_hours = 0.5  # 30 minutes
            jitter_hours = 0.5  # ±30 minutes

        elif structure_size == StructureCategory.POCO:
            base_delay_hours = 24.0  # 24 hours
            jitter_hours = 1.0

        base_delay = timedelta(hours=base_delay_hours)
        min_target_time = reinforced_at + base_delay

        # 6. Target Hour and Candidate Exit Time Calculation
        # If vulnerability hour is unknown, default to reinforced_at.hour or 0
        target_hour = vulnerability_hour if vulnerability_hour is not None else min_target_time.hour
        jitter_delta = timedelta(hours=jitter_hours)

        # Find the next candidate datetime on or after min_target_time with target_hour
        candidate_exit = min_target_time.replace(minute=0, second=0, microsecond=0)
        if candidate_exit < min_target_time:
            candidate_exit = candidate_exit.replace(hour=target_hour)
            if candidate_exit < min_target_time:
                candidate_exit += timedelta(days=1)
        else:
            candidate_exit = candidate_exit.replace(hour=target_hour)
            if candidate_exit < min_target_time:
                candidate_exit += timedelta(days=1)

        nominal_exit = candidate_exit
        earliest_exit = nominal_exit - jitter_delta
        latest_exit = nominal_exit + jitter_delta

        # Duration bounds from reinforced_at
        duration_min = (earliest_exit - reinforced_at).total_seconds() / 3600.0
        duration_max = (latest_exit - reinforced_at).total_seconds() / 3600.0

        # Time remaining from now
        now_utc = timezone.now()
        time_rem = nominal_exit - now_utc
        if time_rem.total_seconds() > 0:
            rem_days = time_rem.days
            rem_hours = int(time_rem.seconds // 3600)
            rem_mins = int((time_rem.seconds % 3600) // 60)
            if rem_days > 0:
                time_remaining_str = f"{rem_days}d {rem_hours}h {rem_mins}m"
            else:
                time_remaining_str = f"{rem_hours}h {rem_mins}m"
        else:
            time_remaining_str = "Expired / Ready"

        exit_window_str = (
            f"{earliest_exit.strftime('%Y-%m-%d %H:%M')} – {latest_exit.strftime('%H:%M')} UTC "
            f"(Nominal: {nominal_exit.strftime('%Y-%m-%d %H:%M')} UTC ±{jitter_hours}h)"
        )

        return ReinforcementCalculationResult(
            is_applicable=True,
            status_message="Reinforcement exit window calculated successfully based on CCP Upwell 2.0 mechanics.",
            structure_size=structure_size,
            location_type=location_type,
            timer_stage=timer_stage,
            power_state=power_state,
            reinforced_at=reinforced_at,
            vulnerability_hour=vulnerability_hour,
            vulnerability_window=vulnerability_window,
            base_delay_hours=base_delay_hours,
            base_delay_days=round(base_delay_hours / 24.0, 2),
            jitter_hours=jitter_hours,
            earliest_exit=earliest_exit,
            nominal_exit=nominal_exit,
            latest_exit=latest_exit,
            exit_window_str=exit_window_str,
            duration_min_hours=duration_min,
            duration_max_hours=duration_max,
            time_remaining_nominal=time_rem,
            time_remaining_str=time_remaining_str,
            notes=f"Base delay: {base_delay_hours}h. Jitter variance: ±{jitter_hours}h.",
        )

    @classmethod
    def calculate_from_structure(
        cls,
        structure: HostileStructure,
        reinforced_at: Optional[datetime] = None,
        timer_stage: str = TimerStage.SHIELD_REINFORCE,
        power_state: Optional[str] = None,
    ) -> ReinforcementCalculationResult:
        """
        Convenience calculator automatically extracting structure attributes, location, and scanned vulnerability hours.
        """
        size = cls.infer_structure_size(structure.structure_type)
        location = cls.infer_location_type(structure.solar_system)

        state = power_state
        if not state:
            if structure.state == "LOW_POWER":
                state = PowerState.LOW_POWER
            elif structure.state == "ABANDONED":
                state = PowerState.ABANDONED
            else:
                state = PowerState.FULL_POWER

        return cls.calculate_exit_timer(
            reinforced_at=reinforced_at or timezone.now(),
            structure_size=size,
            location_type=location,
            timer_stage=timer_stage,
            vulnerability_hour=structure.vulnerability_hour,
            vulnerability_window=structure.vulnerability_window or "",
            power_state=state,
        )
