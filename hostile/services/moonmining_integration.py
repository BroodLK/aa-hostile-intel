"""Optional aa-moonmining integration for the hostile structure registry."""

# Standard Library
import re
from typing import Any

# Django
from django.db.models import Q

_LOCATION_RE = re.compile(
    r"(?:\bP\s*(\d+)\s*M\s*(\d+)\b"
    r"|\bPlanet\s*(\d+)\s*-?\s*Moon\s*(\d+)\b"
    r"|\b(X|IX|VIII|VII|VI|V|IV|III|II|I)\s*-\s*Moon\s*(\d+)\b)",
    re.IGNORECASE,
)
_ROMAN = {
    "I": 1,
    "II": 2,
    "III": 3,
    "IV": 4,
    "V": 5,
    "VI": 6,
    "VII": 7,
    "VIII": 8,
    "IX": 9,
    "X": 10,
}


def _location(value: str | None) -> tuple[int, int] | None:
    match = _LOCATION_RE.search(value or "")
    if not match:
        return None
    if match.group(5):
        # EVE celestial name, e.g. "BNX-AS VI - Moon 2"
        return _ROMAN[match.group(5).upper()], int(match.group(6))
    return (
        int(match.group(1) or match.group(3)),
        int(match.group(2) or match.group(4)),
    )


def _moon_location(moon: Any) -> tuple[int, int] | None:
    planet_name = getattr(getattr(moon.eve_moon, "eve_planet", None), "name", "")
    moon_name = getattr(moon.eve_moon, "name", "")
    planet_match = re.search(
        r"\b(I|II|III|IV|V|VI|VII|VIII|IX|X)\b", planet_name.upper()
    )
    moon_match = re.search(r"(?:-|\s)(\d+)\s*$", moon_name)
    if not planet_match or not moon_match:
        return None
    return _ROMAN[planet_match.group(1)], int(moon_match.group(1))


def is_drill(structure: Any) -> bool:
    type_name = getattr(getattr(structure, "structure_type", None), "name", "")
    return "metenox" in type_name.lower() or "moon drill" in type_name.lower()


def format_isk(value: float | int | None) -> str:
    """Format ISK compactly for registry display without changing the value."""
    if value is None:
        return "—"
    value = float(value)
    if abs(value) >= 1_000_000_000:
        return f"{value / 1_000_000_000:.1f}b".rstrip("0").rstrip(".")
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.1f}m".rstrip("0").rstrip(".")
    if abs(value) >= 1_000:
        return f"{value / 1_000:.1f}k".rstrip("0").rstrip(".")
    return f"{value:.0f}"


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


def moon_data_for_structures(structures: Any) -> dict[int, dict[str, Any]]:
    """Match drills to aa-moonmining moons by system and planet/moon coordinates."""
    try:
        # Django
        from django.apps import apps

        if not apps.is_installed("moonmining"):
            return {}
        # Third Party
        from moonmining.models import Moon
    except (ImportError, LookupError):
        return {}

    systems = {s.solar_system.name for s in structures if s.solar_system}
    if not systems:
        return {}
    system_filter = Q()
    for system in systems:
        system_filter |= Q(eve_moon__eve_planet__eve_solar_system__name__iexact=system)
    moons = Moon.objects.select_related(
        "eve_moon__eve_planet__eve_solar_system"
    ).filter(system_filter)
    by_id = {m.eve_moon_id: m for m in moons}
    by_key = {
        (m.eve_moon.eve_planet.eve_solar_system.name.lower(), _moon_location(m)): m
        for m in moons
        if _moon_location(m)
    }
    result = {}
    for structure in structures:
        if not is_drill(structure):
            continue
        location = structure_location(structure)
        system = getattr(getattr(structure, "solar_system", None), "name", "")
        moon = by_id.get(structure.moonmining_moon_id)
        if moon and (
            not system
            or moon.eve_moon.eve_planet.eve_solar_system.name.lower() != system.lower()
        ):
            # Do not display a stale/manual correlation from another system.
            moon = None
        if not moon:
            moon = (
                by_key.get((system.lower(), location)) if location and system else None
            )
        if moon:
            value = moon.value
            result[structure.id] = {
                "moon": moon,
                "name": structure_display_name(structure),
                "value": value,
                "drill_value": value * 0.4 if value is not None else None,
                "display_value": format_isk(value),
                "display_drill_value": (
                    format_isk(value * 0.4) if value is not None else "—"
                ),
            }
    return result


def moon_options_for_structures(structures: Any) -> dict[str, list[Any]]:
    """Return selectable scanned moons grouped by solar-system name."""
    try:
        # Django
        from django.apps import apps

        if not apps.is_installed("moonmining"):
            return {}
        # Third Party
        from moonmining.models import Moon
    except (ImportError, LookupError):
        return {}
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
