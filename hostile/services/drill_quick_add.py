"""Moon Data quick-add: turn a drill paste into Metenox structures on the right moons."""

# Standard Library
from typing import Any

# Third Party
from eve_sde.models import ItemType

# AA Hostile Intel
from hostile.parsers.drill_paste import drill_location, parse_drill_paste
from hostile.parsers.dscan import DScanParser
from hostile.services.moonmining_integration import MOON_DRILL_GROUP_ID

METENOX_TYPE_NAME = "Metenox Moon Drill"
ROW_FIELDS = (
    "name",
    "owner_ticker",
    "alliance",
    "corporation",
    "untracked_alliance",
    "untracked_corporation",
    "core_status",
    "state",
    "vulnerability_window",
    "vulnerability_hour",
    "notes",
)


def metenox_item_type() -> ItemType | None:
    """The Metenox Moon Drill type, by name, falling back to the moon drill group."""
    return (
        ItemType.objects.filter(name__iexact=METENOX_TYPE_NAME).first()
        or ItemType.objects.filter(group_id=MOON_DRILL_GROUP_ID).order_by("id").first()
    )


def _location_text(planet: int | None, moon: int | None) -> str:
    return f"P{planet}M{moon}" if planet and moon else ""


def system_moons(system_id: int) -> list[dict[str, Any]]:
    """SDE moons in a system as ``[{id, name, planet, moon, location, scanned}]``."""
    # Third Party
    from eve_sde.models import Moon as SdeMoon

    # AA Hostile Intel
    from hostile.services.moonmining_integration import moonmining_available

    rows = list(
        SdeMoon.objects.filter(solar_system_id=system_id).values(
            "id", "name", "celestial_index", "orbit_index"
        )
    )
    scanned: set[int] = set()
    if rows and moonmining_available():
        # Third Party
        from moonmining.models import Moon

        scanned = set(
            Moon.objects.filter(
                pk__in=[r["id"] for r in rows], products__isnull=False
            ).values_list("pk", flat=True)
        )
    moons = [
        {
            "id": r["id"],
            "name": r["name"],
            "planet": r["celestial_index"],
            "moon": r["orbit_index"],
            "location": _location_text(r["celestial_index"], r["orbit_index"]),
            "scanned": r["id"] in scanned,
        }
        for r in rows
    ]
    moons.sort(key=lambda m: (m["planet"] or 0, m["moon"] or 0, m["name"]))
    return moons


def _existing_payload(structure: Any) -> dict[str, Any] | None:
    if structure is None:
        return None
    return {
        "id": structure.id,
        "name": structure.name,
        "owner_ticker": structure.owner_ticker or "",
        "owner_name": structure.owner_name,
        "state": structure.state,
        "moon_id": structure.moonmining_moon_id,
        "location_details": structure.location_details or "",
    }


def preview_drills(text: str | None) -> dict[str, Any]:
    """Parse a paste and suggest system, moon and any existing registry match per drill."""
    # AA Hostile Intel
    from hostile.models import HostileStructure

    metenox = metenox_item_type()
    parsed = parse_drill_paste(text)
    if not parsed:
        return {
            "success": False,
            "error": "No Metenox drills found. Paste overview lines "
            "('F9-FUV - Bucket 10-10') or D-Scan lines.",
        }
    moons_by_system: dict[int, list[dict[str, Any]]] = {}
    drills = []
    for entry in parsed:
        system = DScanParser.infer_solar_system_from_name(entry["name"])
        location = drill_location(entry["name"])
        moons: list[dict[str, Any]] = []
        moon_id = None
        if system:
            if system.id not in moons_by_system:
                moons_by_system[system.id] = system_moons(system.id)
            moons = moons_by_system[system.id]
            if location:
                moon_id = next(
                    (
                        m["id"]
                        for m in moons
                        if (m["planet"], m["moon"]) == tuple(location)
                    ),
                    None,
                )
        existing = None
        if system:
            existing = HostileStructure.objects.find_matching(
                solar_system=system,
                name=entry["name"],
                structure_type=metenox,
                location_details=_location_text(*location) if location else "",
            )
        if existing is not None and moon_id is None and existing.moonmining_moon_id:
            moon_id = existing.moonmining_moon_id
        drills.append(
            {
                "name": entry["name"],
                "distance": entry["distance"],
                "system": {"id": system.id, "name": system.name} if system else None,
                "location": _location_text(*location) if location else "",
                # The name gives a planet-moon that the system doesn't have.
                "location_missing": bool(
                    system
                    and location
                    and moons
                    and not any(
                        (m["planet"], m["moon"]) == tuple(location) for m in moons
                    )
                ),
                "moons": moons,
                "moon_id": moon_id,
                "existing": _existing_payload(existing),
            }
        )
    return {
        "success": True,
        "metenox_type": {"id": metenox.id, "name": metenox.name} if metenox else None,
        "drills": drills,
    }


def _moon_for(system_id: Any, moon_id: Any) -> tuple[dict[str, Any] | None, str]:
    """Validate an SDE moon id against the system; returns (moon, error)."""
    # Third Party
    from eve_sde.models import Moon as SdeMoon

    if moon_id in (None, "", "none"):
        return None, ""
    try:
        moon_id = int(moon_id)
    except (TypeError, ValueError):
        return None, "Invalid moon."
    moon = (
        SdeMoon.objects.filter(pk=moon_id)
        .values("id", "solar_system_id", "celestial_index", "orbit_index")
        .first()
    )
    if not moon:
        return None, "Moon not found."
    if str(moon["solar_system_id"]) != str(system_id):
        return None, "Moon must be in the drill's solar system."
    return moon, ""


def _can_edit(user: Any, structure: Any) -> bool:
    return (
        user.has_perm("hostile.manage_intel")
        or getattr(structure, "created_by_id", None) == user.id
    )


def _moon_from_name(system_id: Any, name: str | None) -> dict[str, Any] | None:
    """The SDE moon matching the planet-moon in a drill name, if it exists in the system."""
    # Third Party
    from eve_sde.models import Moon as SdeMoon

    location = drill_location(name)
    if not location or not system_id:
        return None
    try:
        system_id = int(system_id)
    except (TypeError, ValueError):
        return None
    return (
        SdeMoon.objects.filter(
            solar_system_id=system_id,
            celestial_index=location[0],
            orbit_index=location[1],
        )
        .values("id", "solar_system_id", "celestial_index", "orbit_index")
        .first()
    )


def _find_existing(
    system_id: Any, name: str | None, metenox: Any, location: str
) -> Any:
    """The registry drill this row would update, using the same matching as the save."""
    # Third Party
    from eve_sde.models import SolarSystem

    # AA Hostile Intel
    from hostile.models import HostileStructure

    if not name or not system_id:
        return None
    try:
        system = SolarSystem.objects.filter(pk=int(system_id)).first()
    except (TypeError, ValueError):
        return None
    if system is None:
        return None
    return HostileStructure.objects.find_matching(
        solar_system=system,
        name=name,
        structure_type=metenox,
        location_details=location,
    )


def save_drill(row: dict[str, Any], user: Any) -> dict[str, Any]:
    """Create or update one Metenox from a quick-add row; never raises for bad input."""
    # AA Hostile Intel
    from hostile.forms import HostileStructureForm
    from hostile.services.intel_manager import IntelManager

    metenox = metenox_item_type()
    if metenox is None:
        return {
            "ok": False,
            "errors": ["Metenox Moon Drill type is missing from the SDE."],
        }

    system_id = row.get("solar_system")
    moon, moon_error = _moon_for(system_id, row.get("moon_id"))
    if moon_error:
        return {"ok": False, "errors": [moon_error]}

    if moon is None:
        # No moon picked: use the name's planet-moon, but only if it is a real moon here.
        moon = _moon_from_name(system_id, row.get("name"))
    location = (
        _location_text(moon["celestial_index"], moon["orbit_index"]) if moon else ""
    )

    # Someone else's drill already linked to a different moon keeps its moon (and we must
    # not let get_or_create back-fill this moon's location onto it either).
    warnings = []
    existing = _find_existing(system_id, row.get("name"), metenox, location)
    if (
        moon is not None
        and existing is not None
        and existing.moonmining_moon_id
        and existing.moonmining_moon_id != moon["id"]
        and not _can_edit(user, existing)
    ):
        warnings.append("Already linked to another moon; not changed.")
        moon, location = None, ""

    data = {field: row.get(field) or "" for field in ROW_FIELDS}
    data.update(
        {
            "structure_type": metenox.id,
            "solar_system": system_id or "",
            "location_details": location,
            "state": data["state"] or "ONLINE",
            "core_status": data["core_status"] or "UNKNOWN",
        }
    )
    form = HostileStructureForm(data)
    if not form.is_valid():
        errors = [
            f"{field}: {message}" if field != "__all__" else str(message)
            for field, messages in form.errors.items()
            for message in messages
        ]
        return {"ok": False, "errors": errors}

    struct, created = IntelManager.save_structure_form(
        form, user, source="Moon Data quick add"
    )
    # Same rule as inline edits: only fill blanks on other people's structures.
    can_edit = created or _can_edit(user, struct)
    update_fields = []
    # Moon id and location always move together so the drill can't point at two moons.
    moon_applies = moon is not None and (
        can_edit
        or not struct.moonmining_moon_id
        or struct.moonmining_moon_id == moon["id"]
    )
    if moon is not None and not moon_applies:
        warnings.append("Already linked to another moon; not changed.")
    if moon_applies:
        if struct.moonmining_moon_id != moon["id"]:
            struct.moonmining_moon_id = moon["id"]
            update_fields.append("moonmining_moon_id")
        if struct.location_details != location and (
            can_edit
            or not struct.location_details
            or "moonmining_moon_id" in update_fields
        ):
            struct.location_details = location
            update_fields.append("location_details")
    if update_fields:
        struct.save(update_fields=[*update_fields, "updated_at"])
        if not created:
            IntelManager.record_audit(
                user,
                "EDIT",
                struct,
                f"Moon set to {location or struct.moonmining_moon_id} via Moon Data quick add.",
            )
    return {
        "ok": True,
        "created": created,
        "structure_id": struct.id,
        "moon_id": struct.moonmining_moon_id,
        "name": struct.name,
        "warnings": warnings,
    }
