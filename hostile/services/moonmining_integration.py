"""Optional aa-moonmining integration for the hostile structure registry."""

# Standard Library
import re
from types import SimpleNamespace
from typing import Any

# Django
from django.db.models import Q

_LOCATION_RE = re.compile(
    r"(?:\bP\s*(\d+)\s*M\s*(\d+)\b"
    r"|\bPlanet\s*(\d+)\s*-?\s*Moon\s*(\d+)\b"
    r"|\b([IVXL]+)\s*-\s*Moon\s*(\d+)\b)",
    re.IGNORECASE,
)
# The planet numeral is the last word of an EVE planet name ("I-E3TG VII"); reading it
# from the end keeps Roman-looking system-name prefixes (I-, V-, X-) out of the match.
_PLANET_NUMERAL_RE = re.compile(r"\b([IVXL]+)\s*$", re.IGNORECASE)
_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50}


def _roman_to_int(numeral: str) -> int:
    """Convert a Roman numeral (I..L range, as used for EVE planets) to an int."""
    total = 0
    values = [_ROMAN_VALUES[c] for c in numeral.upper()]
    for i, value in enumerate(values):
        total += -value if i + 1 < len(values) and value < values[i + 1] else value
    return total


def _location(value: str | None) -> tuple[int, int] | None:
    match = _LOCATION_RE.search(value or "")
    if not match:
        return None
    if match.group(5):
        # EVE celestial name, e.g. "BNX-AS VI - Moon 2"
        return _roman_to_int(match.group(5)), int(match.group(6))
    return (
        int(match.group(1) or match.group(3)),
        int(match.group(2) or match.group(4)),
    )


def _moon_location(moon: Any) -> tuple[int, int] | None:
    planet_name = getattr(getattr(moon.eve_moon, "eve_planet", None), "name", "")
    moon_name = getattr(moon.eve_moon, "name", "")
    planet_match = _PLANET_NUMERAL_RE.search(planet_name or "")
    moon_match = re.search(r"(?:-|\s)(\d+)\s*$", moon_name)
    if not planet_match or not moon_match:
        return None
    return _roman_to_int(planet_match.group(1)), int(moon_match.group(1))


def is_drill(structure: Any) -> bool:
    type_name = getattr(getattr(structure, "structure_type", None), "name", "")
    return "metenox" in type_name.lower() or "moon drill" in type_name.lower()


def format_isk(value: float | int | None) -> str:
    """Format ISK compactly for registry display without changing the value."""
    if value is None:
        return "—"
    value = float(value)
    # Thresholds sit just under each unit so e.g. 999.96b shows as 1.0t, not 1000.0b.
    if abs(value) >= 999_950_000_000:
        return f"{value / 1_000_000_000_000:.1f}t".rstrip("0").rstrip(".")
    if abs(value) >= 999_950_000:
        return f"{value / 1_000_000_000:.1f}b".rstrip("0").rstrip(".")
    if abs(value) >= 999_950:
        return f"{value / 1_000_000:.1f}m".rstrip("0").rstrip(".")
    if abs(value) >= 999.5:
        return f"{value / 1_000:.1f}k".rstrip("0").rstrip(".")
    return f"{value:.0f}"


# Moon values are monthly; a month is treated as 30 days for per-hour denial math.
HOURS_PER_MONTH = 720
# A Metenox drill yields this share of the moon's full monthly value.
DRILL_SHARE = 0.4
REFINERY_GROUP_ID = 1406
MOON_DRILL_GROUP_ID = 4807
REFINERY_TYPE_NAMES = ("athanor", "tatara")
MOON_STRUCTURE_NAME_REGEX = r"metenox|moon drill|athanor|tatara"
# Bootstrap 5 has no "default" contextual class; moonmining's grey label maps to secondary.
LABEL_STYLE_FALLBACK = "secondary"
# moonmining ore group id -> rarity class (R4..R64)
ORE_GROUP_RARITY = {1884: 4, 1920: 8, 1921: 16, 1922: 32, 1923: 64}
REINFORCE_TIMER_TYPES = ("ARMOR", "HULL")


def drill_hourly_value(drill_value: float | int | None) -> float | None:
    """Monthly drill output converted to ISK per hour."""
    if drill_value is None:
        return None
    return float(drill_value) / HOURS_PER_MONTH


def reinforcement_isk_loss(
    structure: Any, drill_value: float | int | None
) -> float | None:
    """ISK denied by the structure's latest reinforcement timer.

    Assumes the timer was submitted the moment the structure was reinforced
    and that the owner repairs it the instant it comes out, so the loss is the
    drill's hourly output times (timer exit - timer submission). Callers decide
    whether to display it based on the structure's current state.
    """
    hourly = drill_hourly_value(drill_value)
    if hourly is None:
        return None
    timers = [
        t
        for t in structure.timers.all()
        if t.timer_type in REINFORCE_TIMER_TYPES
        and t.created_at
        and t.timer_datetime
        and t.timer_datetime > t.created_at
    ]
    if not timers:
        return None
    timer = max(timers, key=lambda t: t.created_at)
    hours = (timer.timer_datetime - timer.created_at).total_seconds() / 3600
    return hourly * hours


def structure_location(structure: Any) -> tuple[int, int] | None:
    """Read PnMm from either the location annotation or the structure name."""
    return _location(getattr(structure, "location_details", "")) or _location(
        getattr(structure, "name", "")
    )


def same_drill(a: Any, b: Any) -> bool:
    """True when two structures are the same moon drill: same system and same moon."""
    if not is_drill(a) or not is_drill(b):
        return False
    system_a = getattr(getattr(a, "solar_system", None), "name", "")
    system_b = getattr(getattr(b, "solar_system", None), "name", "")
    if not system_a or system_a.lower() != system_b.lower():
        return False
    moon_a = getattr(a, "moonmining_moon_id", None)
    moon_b = getattr(b, "moonmining_moon_id", None)
    if moon_a is not None and moon_b is not None:
        return moon_a == moon_b
    location = structure_location(a)
    return location is not None and location == structure_location(b)


def structure_display_name(structure: Any) -> str:
    """Return a consistent drill name while preserving non-drill names."""
    if not is_drill(structure):
        return structure.name
    location = structure_location(structure)
    system = getattr(getattr(structure, "solar_system", None), "name", "")
    return (
        f"{system} P{location[0]}M{location[1]}"
        if location and system
        else structure.name
    )


def is_refinery(structure: Any) -> bool:
    """Athanor / Tatara (Upwell refinery group) — anchored on a moon like a drill."""
    structure_type = getattr(structure, "structure_type", None)
    if getattr(structure_type, "group_id", None) == REFINERY_GROUP_ID:
        return True
    type_name = getattr(structure_type, "name", "") or ""
    return any(name in type_name.lower() for name in REFINERY_TYPE_NAMES)


def is_moon_structure(structure: Any) -> bool:
    """True for structures that sit on a moon: Metenox drills and refineries."""
    return is_drill(structure) or is_refinery(structure)


def moonmining_available() -> bool:
    """True when aa-moonmining is installed and its models can be imported."""
    try:
        # Django
        from django.apps import apps

        if not apps.is_installed("moonmining"):
            return False
        # Third Party
        from moonmining.models import Moon  # noqa: F401
    except (ImportError, LookupError):
        return False
    return True


def _moon_system(moon: Any) -> Any:
    return getattr(getattr(moon.eve_moon, "eve_planet", None), "eve_solar_system", None)


def _moon_system_name(moon: Any) -> str:
    return getattr(_moon_system(moon), "name", "") or ""


def _structure_system_name(structure: Any) -> str:
    return getattr(getattr(structure, "solar_system", None), "name", "") or ""


def match_structure_moons(moons: Any, structures: Any) -> dict[int, Any]:
    """Map structure id -> moonmining Moon for every drill or refinery we can place.

    A manual ``moonmining_moon_id`` wins unless it points at a moon in another system
    (stale correlation); otherwise the planet/moon parsed from the location or name is used.
    """
    moons = list(moons)
    by_id = {m.eve_moon_id: m for m in moons}
    by_key = {}
    for moon in moons:
        location = _moon_location(moon)
        if location:
            by_key[(_moon_system_name(moon).lower(), location)] = moon
    result = {}
    for structure in structures:
        if not is_moon_structure(structure):
            continue
        system = _structure_system_name(structure)
        moon = by_id.get(getattr(structure, "moonmining_moon_id", None))
        if moon and (not system or _moon_system_name(moon).lower() != system.lower()):
            # Do not display a stale/manual correlation from another system.
            moon = None
        if not moon:
            location = structure_location(structure)
            moon = (
                by_key.get((system.lower(), location)) if location and system else None
            )
        if moon:
            result[structure.id] = moon
    return result


def match_structures_to_moons(moons: Any, structures: Any) -> dict[int, list[Any]]:
    """Map moon id -> structures placed on it (see ``match_structure_moons``)."""
    by_structure_id = {s.id: s for s in structures}
    result: dict[int, list[Any]] = {}
    for structure_id, moon in match_structure_moons(moons, structures).items():
        result.setdefault(moon.eve_moon_id, []).append(by_structure_id[structure_id])
    return result


def _moons_in_systems(structures: Any) -> list[Any]:
    # Third Party
    from moonmining.models import Moon

    systems = {s.solar_system.name for s in structures if s.solar_system}
    if not systems:
        return []
    system_filter = Q()
    for system in systems:
        system_filter |= Q(eve_moon__eve_planet__eve_solar_system__name__iexact=system)
    return list(
        Moon.objects.select_related("eve_moon__eve_planet__eve_solar_system").filter(
            system_filter
        )
    )


def moon_match_data(structure: Any, moon: Any) -> dict[str, Any]:
    """Display data for a structure placed on a moon. Drill values only apply to drills."""
    value = moon.value
    drill_value = (
        value * DRILL_SHARE if value is not None and is_drill(structure) else None
    )
    hourly = drill_hourly_value(drill_value)
    return {
        "moon": moon,
        "name": structure_display_name(structure),
        "value": value,
        "drill_value": drill_value,
        "display_value": format_isk(value),
        "display_drill_value": format_isk(drill_value),
        "drill_hourly_value": hourly,
        "display_drill_hourly_value": format_isk(hourly),
    }


def moon_data_for_structures(structures: Any) -> dict[int, dict[str, Any]]:
    """Match drills and refineries to aa-moonmining moons by system and planet/moon."""
    if not moonmining_available():
        return {}
    structures = list(structures)
    moons = _moons_in_systems(structures)
    if not moons:
        return {}
    by_structure_id = {s.id: s for s in structures}
    return {
        structure_id: moon_match_data(by_structure_id[structure_id], moon)
        for structure_id, moon in match_structure_moons(moons, structures).items()
    }


def moon_options_for_structures(structures: Any) -> dict[str, list[Any]]:
    """Return selectable scanned moons grouped by solar-system name."""
    if not moonmining_available():
        return {}
    # Third Party
    from moonmining.models import Moon

    systems = {s.solar_system.name.lower() for s in structures if s.solar_system}
    if not systems:
        return {}
    system_filter = Q()
    for system in systems:
        system_filter |= Q(eve_moon__eve_planet__eve_solar_system__name__iexact=system)
    moons = Moon.objects.select_related(
        "eve_moon__eve_planet__eve_solar_system"
    ).filter(system_filter)
    result = {}
    for moon in moons:
        system = moon.eve_moon.eve_planet.eve_solar_system.name
        result.setdefault(system.lower(), []).append(moon)
    for values in result.values():
        values.sort(key=lambda m: m.eve_moon.name)
    return result


# ---------------------------------------------------------------------------
# Moon Data tab
# ---------------------------------------------------------------------------

UNCLAIMED_OWNER_KEY = "unclaimed"


def registry_owner_group(structure: Any) -> tuple[str, str, str]:
    """Return (key, name, ticker) used to group a structure under its owner.

    The key format is mirrored by ``registryGroupKey`` in structures.html so inline owner
    edits regroup rows client-side, and by the Moon Data tab's owner grouping.
    """
    alliance = getattr(structure, "alliance", None)
    corporation = getattr(structure, "corporation", None)
    ticker = getattr(structure, "owner_ticker", "") or ""
    if getattr(structure, "alliance_id", None) and alliance:
        return (
            f"a{structure.alliance_id}",
            structure.owner_name,
            alliance.ticker or ticker,
        )
    if getattr(structure, "corporation_id", None) and corporation:
        return (
            f"c{structure.corporation_id}",
            structure.owner_name,
            corporation.ticker or ticker,
        )
    if ticker:
        return f"t{ticker.lower()}", ticker, ticker
    return "unknown", "Unknown", ""


def _owner_url(structure: Any) -> str:
    # Django
    from django.urls import NoReverseMatch, reverse

    try:
        if getattr(structure, "alliance_id", None):
            return reverse("hostile:alliance_detail", args=[structure.alliance_id])
        if getattr(structure, "corporation_id", None):
            return reverse(
                "hostile:corporation_detail", args=[structure.corporation_id]
            )
    except NoReverseMatch:
        return ""
    return ""


def _is_friendly(structure: Any) -> bool:
    alliance = getattr(structure, "alliance", None)
    corporation = getattr(structure, "corporation", None)
    return bool(
        (alliance and getattr(alliance, "is_friendly", False))
        or (corporation and getattr(corporation, "is_friendly", False))
    )


def _structure_payload(structure: Any) -> dict[str, Any]:
    key, name, ticker = registry_owner_group(structure)
    structure_type = getattr(structure, "structure_type", None)
    return {
        "id": structure.id,
        "name": structure.name,
        "type": getattr(structure_type, "name", "") or "",
        "state": structure.state,
        "is_drill": is_drill(structure),
        "owner_key": key,
        "owner_name": name,
        "owner_ticker": ticker,
        "owner_url": _owner_url(structure),
        "is_friendly": _is_friendly(structure),
        "vuln_hour": getattr(structure, "vulnerability_hour", None),
    }


def _refinery_payload(moon: Any) -> dict[str, Any] | None:
    """moonmining's own refinery record; only exists for corps with ESI tokens in Auth."""
    refinery = getattr(moon, "refinery", None)
    if refinery is None:
        return None
    owner = getattr(refinery, "owner", None)
    corporation = getattr(owner, "corporation", None)
    alliance = getattr(corporation, "alliance", None)
    return {
        "name": getattr(refinery, "name", "") or "",
        "type": getattr(getattr(refinery, "eve_type", None), "name", "") or "",
        "corporation": getattr(corporation, "corporation_name", "") or "",
        "corporation_id": getattr(corporation, "corporation_id", None),
        "ticker": getattr(alliance, "alliance_ticker", "")
        or getattr(corporation, "corporation_ticker", "")
        or "",
        "alliance": getattr(alliance, "alliance_name", "") or "",
    }


def _ore_payload(ore_type: Any) -> dict[str, Any]:
    extras = getattr(ore_type, "extras", None)
    return {
        "name": ore_type.name,
        "rarity": ORE_GROUP_RARITY.get(getattr(ore_type, "eve_group_id", None), 0),
        "price": getattr(extras, "current_price", None),
        "volume": getattr(ore_type, "volume", None),
        "icon": f"https://images.evetech.net/types/{ore_type.id}/icon?size=32",
    }


def product_monthly_value(
    price: float | None,
    amount: float,
    ore_volume: float | None,
    volume_per_month: float,
) -> float:
    """Mirrors moonmining's Moon value: price * share * monthly volume / ore volume."""
    if not ore_volume:
        return 0.0
    return (
        float(price or 0.0)
        * float(amount)
        * float(volume_per_month)
        / float(ore_volume)
    )


def _moon_owner(
    structures: list[dict[str, Any]], refinery: dict[str, Any] | None
) -> dict[str, Any]:
    if structures:
        primary = structures[0]
        return {
            "owner_key": primary["owner_key"],
            "owner_name": primary["owner_name"],
            "owner_ticker": primary["owner_ticker"],
            "owner_url": primary["owner_url"],
            "status": "friendly" if primary["is_friendly"] else "hostile",
        }
    if refinery:
        return {
            "owner_key": f"mm{refinery['corporation_id'] or refinery['corporation']}",
            "owner_name": refinery["corporation"] or refinery["name"],
            "owner_ticker": refinery["ticker"],
            "owner_url": "",
            "status": "friendly",
        }
    return {
        "owner_key": UNCLAIMED_OWNER_KEY,
        "owner_name": "Unclaimed",
        "owner_ticker": "",
        "owner_url": "",
        "status": "unclaimed",
    }


def _moon_products(
    moon: Any, ores: dict[int, dict[str, Any]], volume_per_month: float
) -> list[list[Any]]:
    """[[ore_type_id, share, isk_per_month], ...] by value; fills the shared ``ores`` index."""
    products = []
    manager = getattr(moon, "products", None)
    for product in manager.all() if manager is not None else []:
        ore_type = product.ore_type
        if ore_type.id not in ores:
            ores[ore_type.id] = _ore_payload(ore_type)
        ore = ores[ore_type.id]
        isk = product_monthly_value(
            ore["price"], product.amount, ore["volume"], volume_per_month
        )
        products.append([ore_type.id, round(float(product.amount), 4), round(isk, 2)])
    products.sort(key=lambda p: (-p[2], -p[1]))
    return products


def _label_bootstrap_style(label: Any) -> str:
    """Bootstrap 5 class suffix for a moonmining Label (style lives on ``Label.Style``)."""
    try:
        return str(type(label).Style(label.style).bootstrap_style)
    except (AttributeError, ValueError, TypeError):
        style = getattr(label, "style", "") or ""
        return style if style and style != "default" else LABEL_STYLE_FALLBACK


def build_moon_data_payload(
    moons: Any,
    structures: Any,
    volume_per_month: float,
    only_moon_ids: Any = None,
    metenox_values: dict[int, float] | None = None,
    drill_costs: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Join moonmining moons with registry drills/refineries into the Moon Data payload.

    Pure function (no queries of its own). ``moons`` need ``eve_moon`` -> planet -> system
    -> constellation -> region, ``products`` (with ``ore_type`` / ``extras``), ``label``
    and the optional ``refinery``; ``structures`` are HostileStructure-like objects.

    Drill value: aa-metenox's own monthly value (``metenox_values``) when it has the moon,
    otherwise ``DRILL_SHARE`` of the moonmining value. ``drill_net`` subtracts the monthly
    running cost (``drill_costs["total"]``) when known.
    """
    moons = list(moons)
    metenox_values = metenox_values or {}
    running_cost = drill_costs["total"] if drill_costs else None
    structures = [s for s in structures if getattr(s, "state", "") != "DESTROYED"]
    # Match against every moon given, even ones not returned as rows (only_moon_ids).
    by_moon = match_structures_to_moons(moons, structures)
    wanted = {int(i) for i in only_moon_ids} if only_moon_ids is not None else None
    ores: dict[int, dict[str, Any]] = {}
    rows = []
    for moon in moons:
        if wanted is not None and moon.eve_moon_id not in wanted:
            continue
        system = _moon_system(moon)
        constellation = getattr(system, "eve_constellation", None)
        region = getattr(constellation, "eve_region", None)
        placed = sorted(
            by_moon.get(moon.eve_moon_id, []),
            key=lambda s: (not is_drill(s), -(getattr(s, "id", 0) or 0)),
        )
        structure_rows = [_structure_payload(s) for s in placed]
        refinery = _refinery_payload(moon)
        label = getattr(moon, "label", None)
        updated_at = getattr(moon, "products_updated_at", None)
        location = _moon_location(moon)
        value = moon.value
        products = _moon_products(moon, ores, volume_per_month)
        drill_value, drill_source = _drill_value(
            value, metenox_values.get(moon.eve_moon_id)
        )
        row = {
            "id": moon.eve_moon_id,
            "name": moon.eve_moon.name,
            "location": f"P{location[0]}M{location[1]}" if location else "",
            "system": getattr(system, "name", "") or "",
            "system_id": getattr(system, "id", None),
            "security": getattr(system, "security_status", None),
            "constellation": getattr(constellation, "name", "") or "",
            "region": getattr(region, "name", "") or "",
            "rarity": getattr(moon, "rarity_class", 0) or 0,
            "value": value,
            "drill_value": drill_value,
            "drill_source": drill_source,
            "drill_net": (
                drill_value - running_cost
                if drill_value is not None and running_cost is not None
                else None
            ),
            "label": (
                {"name": label.name, "style": _label_bootstrap_style(label)}
                if label
                else None
            ),
            "products": products,
            "scanned": bool(products),
            "updated_at": updated_at.isoformat() if updated_at else None,
            "structures": structure_rows,
            "refinery": refinery,
        }
        row.update(_moon_owner(structure_rows, refinery))
        rows.append(row)
    return {"available": True, "ores": ores, "rows": rows, "drill_costs": drill_costs}


def _drill_value(
    moon_value: float | None, metenox_value: float | None
) -> tuple[float | None, str | None]:
    """(monthly drill value, source): aa-metenox's value wins over the 40% estimate."""
    if metenox_value:
        return float(metenox_value), "metenox"
    if moon_value is not None:
        return moon_value * DRILL_SHARE, "estimate"
    return None, None


def _moon_queryset():
    # Third Party
    from moonmining.models import Moon, MoonProduct

    # Django
    from django.db.models import Prefetch

    return Moon.objects.selected_related_defaults().prefetch_related(
        Prefetch(
            "products",
            queryset=MoonProduct.objects.select_related(
                "ore_type", "ore_type__eve_group", "ore_type__extras"
            ),
        )
    )


def moon_structures_queryset(system_ids: Any = None) -> list[Any]:
    """Live (not destroyed) drills and refineries, optionally limited to some systems.

    Filters on structure type in the database so the full Moon Data list doesn't need a
    (potentially huge) ``solar_system_id IN (...)`` clause.
    """
    # AA Hostile Intel
    from hostile.models import HostileStructure

    structures = (
        HostileStructure.objects.exclude(state="DESTROYED")
        .filter(
            Q(structure_type__group_id__in=[REFINERY_GROUP_ID, MOON_DRILL_GROUP_ID])
            | Q(structure_type__name__iregex=MOON_STRUCTURE_NAME_REGEX)
        )
        .select_related(
            "structure_type", "solar_system__constellation", "alliance", "corporation"
        )
    )
    if system_ids is not None:
        structures = structures.filter(solar_system_id__in=list(system_ids))
    return [s for s in structures if is_moon_structure(s)]


def load_moon_data_payload(moon_ids: Any = None) -> dict[str, Any] | None:
    """Build the Moon Data payload from the database; ``None`` without aa-moonmining.

    With ``moon_ids`` only those rows are returned, but matching still sees every moon
    in their systems, so a structure linked to a sibling moon isn't misplaced onto them.
    """
    if not moonmining_available():
        return None
    # Third Party
    from moonmining.app_settings import MOONMINING_VOLUME_PER_MONTH
    from moonmining.models import Moon

    # AA Hostile Intel
    from hostile.services.metenox_integration import load_drill_inputs

    moons = _moon_queryset()
    if moon_ids is None:
        moons = list(moons)
        structures = moon_structures_queryset()
        region_ids = set(
            Moon.objects.values_list(
                "eve_moon__eve_planet__eve_solar_system__eve_constellation__eve_region_id",
                flat=True,
            )
        )
        region_ids |= {_structure_region_id(s) for s in structures}
        region_ids.discard(None)
        unscanned = sde_moons_in_regions(
            region_ids, exclude_ids={m.eve_moon_id for m in moons}
        )
        metenox_values, drill_costs = load_drill_inputs()
        return build_moon_data_payload(
            moons + unscanned,
            structures,
            MOONMINING_VOLUME_PER_MONTH,
            metenox_values=metenox_values,
            drill_costs=drill_costs,
        )

    # Details: the moon may be scanned (moonmining) or only known from the SDE.
    moon_ids = [int(i) for i in moon_ids]
    system_ids = set(
        Moon.objects.filter(pk__in=moon_ids).values_list(
            "eve_moon__eve_planet__eve_solar_system_id", flat=True
        )
    ) | _sde_system_ids(moon_ids)
    system_ids.discard(None)
    if not system_ids:
        return build_moon_data_payload([], [], MOONMINING_VOLUME_PER_MONTH)
    moons = list(moons.filter(eve_moon__eve_planet__eve_solar_system_id__in=system_ids))
    unscanned = sde_moons_in_systems(
        system_ids, exclude_ids={m.eve_moon_id for m in moons}
    )
    metenox_values, drill_costs = load_drill_inputs(moon_ids)
    return build_moon_data_payload(
        moons + unscanned,
        moon_structures_queryset(system_ids),
        MOONMINING_VOLUME_PER_MONTH,
        only_moon_ids=moon_ids,
        metenox_values=metenox_values,
        drill_costs=drill_costs,
    )


# ---------------------------------------------------------------------------
# Unscanned moons from the SDE
# ---------------------------------------------------------------------------

_SDE_MOON_FIELDS = (
    "id",
    "name",
    "solar_system__id",
    "solar_system__name",
    "solar_system__security_status",
    "solar_system__constellation__id",
    "solar_system__constellation__name",
    "solar_system__constellation__region__id",
    "solar_system__constellation__region__name",
)


def _structure_region_id(structure: Any) -> int | None:
    constellation = getattr(
        getattr(structure, "solar_system", None), "constellation", None
    )
    return getattr(constellation, "region_id", None)


def _sde_moon_adapter(sde_moon: Any) -> Any:
    """Wrap an ``eve_sde`` Moon so it reads like an (unsurveyed) moonmining Moon.

    Gives the same ``eve_moon`` -> planet -> system -> constellation -> region chain used by
    matching and ``build_moon_data_payload``, with no products, value or rarity.
    """
    system = getattr(sde_moon, "solar_system", None)
    constellation = getattr(system, "constellation", None)
    region = getattr(constellation, "region", None)
    name = sde_moon.name or ""
    planet_name = name.split(" - ", 1)[0] if " - " in name else ""
    eve_system = SimpleNamespace(
        id=getattr(system, "id", None),
        name=getattr(system, "name", "") or "",
        security_status=getattr(system, "security_status", None),
        eve_constellation=SimpleNamespace(
            name=getattr(constellation, "name", "") or "",
            eve_region=SimpleNamespace(name=getattr(region, "name", "") or ""),
        ),
    )
    return SimpleNamespace(
        eve_moon_id=sde_moon.id,
        eve_moon=SimpleNamespace(
            id=sde_moon.id,
            name=name,
            eve_planet=SimpleNamespace(name=planet_name, eve_solar_system=eve_system),
        ),
        value=None,
        rarity_class=0,
        products=_EmptyProducts(),
        label=None,
        products_updated_at=None,
        refinery=None,
    )


class _EmptyProducts(list):
    def all(self):
        return self


def _sde_moon_from_values(values: dict[str, Any]) -> Any:
    """Rebuild the moon -> system -> constellation -> region chain from a values() row."""
    region = SimpleNamespace(
        id=values["solar_system__constellation__region__id"],
        name=values["solar_system__constellation__region__name"],
    )
    constellation = SimpleNamespace(
        id=values["solar_system__constellation__id"],
        name=values["solar_system__constellation__name"],
        region=region,
    )
    system = SimpleNamespace(
        id=values["solar_system__id"],
        name=values["solar_system__name"],
        security_status=values["solar_system__security_status"],
        constellation=constellation,
    )
    return SimpleNamespace(id=values["id"], name=values["name"], solar_system=system)


def _sde_moons(exclude_ids: Any, **filters: Any) -> list[Any]:
    # Third Party
    from eve_sde.models import Moon as SdeMoon

    # values() keeps tens of thousands of rows cheap and sidesteps the SDE manager's
    # default select_related.
    exclude = set(exclude_ids)
    rows = SdeMoon.objects.filter(**filters).values(*_SDE_MOON_FIELDS)
    return [
        _sde_moon_adapter(_sde_moon_from_values(row))
        for row in rows
        if row["id"] not in exclude
    ]


def sde_moons_in_regions(region_ids: Any, exclude_ids: Any = ()) -> list[Any]:
    """Adapters for every SDE moon in the given regions, minus ``exclude_ids`` (scanned)."""
    region_ids = [r for r in region_ids if r is not None]
    if not region_ids:
        return []
    return _sde_moons(
        exclude_ids, solar_system__constellation__region_id__in=region_ids
    )


def sde_moons_in_systems(system_ids: Any, exclude_ids: Any = ()) -> list[Any]:
    """Adapters for every SDE moon in the given systems, minus ``exclude_ids``."""
    system_ids = [s for s in system_ids if s is not None]
    if not system_ids:
        return []
    return _sde_moons(exclude_ids, solar_system_id__in=system_ids)


def _sde_system_ids(moon_ids: Any) -> set[int]:
    # Third Party
    from eve_sde.models import Moon as SdeMoon

    return set(
        SdeMoon.objects.filter(pk__in=list(moon_ids)).values_list(
            "solar_system_id", flat=True
        )
    )
