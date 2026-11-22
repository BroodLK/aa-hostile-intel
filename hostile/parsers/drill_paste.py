"""Parse Metenox moon drill pastes for the Moon Data quick-add.

Two clipboard formats are accepted (several drills per paste):

* overview / probe results, a name line optionally followed by a distance line::

        F9-FUV - Bucket 10-10
        29.9 AU

* D-Scan lines (type id, name, type name, distance)::

        81826	F9-FUV - Bucket 10-10	Metenox Moon Drill	120 m
"""

# Standard Library
import re
from typing import Any

METENOX_TYPE_ID = 81826
_DISTANCE_RE = re.compile(r"^[\d.,\s]+\s*(m|km|au)$", re.IGNORECASE)
# Drill naming convention: "<System> - <Name> <planet>-<moon>", e.g. "Bucket 10-10"
_TRAILING_PLANET_MOON_RE = re.compile(r"(?<![\w-])(\d{1,2})\s*-\s*(\d{1,3})\s*$")


def _is_drill_type(type_id: str, type_name: str) -> bool:
    name = type_name.lower()
    return (
        type_id.strip() == str(METENOX_TYPE_ID)
        or "metenox" in name
        or "moon drill" in name
    )


def _dscan_parts(line: str) -> list[str] | None:
    parts = [p.strip() for p in line.split("\t")]
    if len(parts) >= 4 and parts[0].isdigit():
        return parts
    return None


def parse_drill_paste(text: str | None) -> list[dict[str, Any]]:
    """Return ``[{name, distance, type_name}]`` for every drill in the paste."""
    drills: list[dict[str, Any]] = []
    seen: dict[str, dict[str, Any]] = {}

    def add(
        name: str, distance: str = "", type_name: str = ""
    ) -> dict[str, Any] | None:
        name = " ".join(name.split())
        if not name:
            return None
        key = name.lower()
        if key in seen:
            entry = seen[key]
            entry["distance"] = entry["distance"] or distance
            return entry
        entry = {"name": name, "distance": distance, "type_name": type_name}
        seen[key] = entry
        drills.append(entry)
        return entry

    last: dict[str, Any] | None = None
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = _dscan_parts(raw)
        if parts:
            last = None
            if _is_drill_type(parts[0], parts[2]):
                add(parts[1], parts[3], parts[2])
            continue
        if "\t" in raw:
            # Some other tabular paste (local, cargo, ...): not a drill name.
            last = None
            continue
        if _DISTANCE_RE.match(line):
            if last is not None and not last["distance"]:
                last["distance"] = line
            last = None
            continue
        # Overview / probe names follow "<System> - <Name>"; other text isn't a drill.
        last = add(line) if " - " in line else None
    return drills


def drill_location(name: str | None) -> tuple[int, int] | None:
    """Planet/moon from a drill name: ``P10M10``, ``X - Moon 10`` or a trailing ``10-10``."""
    # AA Hostile Intel
    from hostile.services.moonmining_integration import _location

    name = name or ""
    location = _location(name)
    if location:
        return location
    # Only look after "<System> - " so the system name's own dash isn't read.
    custom = name.split(" - ", 1)[1] if " - " in name else name
    match = _TRAILING_PLANET_MOON_RE.search(custom)
    if not match:
        return None
    planet, moon = int(match.group(1)), int(match.group(2))
    if planet < 1 or moon < 1:
        return None
    return planet, moon
