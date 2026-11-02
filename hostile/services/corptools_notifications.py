"""
Optional allianceauth-corptools integration: creates friendly StructureTimers from the
EVE character notifications corptools stores (structure reinforced, anchoring, etc.)
and keeps the Hostile Intel structure registry up to date.

corptools writes notifications with ``bulk_create`` so no model signals fire; this module
is driven by the ``hostile.tasks.import_corptools_notification_timers`` periodic task.

Created timers are posted to aa-structuretimers by the regular StructureTimer post_save
sync. They are friendly, so they are never copied to the Alliance Auth Timerboard (that
board is for fleet operations). Double-posting is prevented at several layers:

1. notification already processed (``source_notification_id``)
2. equivalent timer already in Hostile Intel (same structure / system, type, time)
3. equivalent timer already on aa-structuretimers (e.g. from aa-structures) -> linked
4. the structuretimers push itself adopts an equivalent timer instead of creating one
"""

# Standard Library
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from typing import Any

# Third Party
import yaml

# Django
from django.apps import apps
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)

# Windows FILETIME epoch offset (100ns intervals between 1601-01-01 and 1970-01-01)
_FILETIME_EPOCH_OFFSET = 116444736000000000


@dataclass
class NotificationTimer:
    """Timer details parsed from a single structure notification."""

    notification_type: str
    timer_type: str
    timer_datetime: datetime
    solar_system_id: int | None
    structure_state: str = ""
    structure_type_id: int | None = None
    structure_id: int | None = None
    planet_id: int | None = None
    label: str = ""
    aggressor: str = ""


# -------------------------------------------------------------------------
# Low level helpers
# -------------------------------------------------------------------------


def filetime_to_datetime(value: Any) -> datetime | None:
    """Converts an EVE / Windows FILETIME integer to an aware UTC datetime."""
    try:
        ticks = int(value)
    except (TypeError, ValueError):
        return None
    if ticks <= _FILETIME_EPOCH_OFFSET:
        return None
    return datetime.fromtimestamp(
        (ticks - _FILETIME_EPOCH_OFFSET) / 10_000_000, tz=dt_timezone.utc
    )


def ticks_to_timedelta(value: Any) -> timedelta | None:
    """Converts a duration in 100ns ticks (e.g. ``timeLeft``) to a timedelta."""
    try:
        ticks = int(value)
    except (TypeError, ValueError):
        return None
    if ticks < 0:
        return None
    return timedelta(microseconds=ticks / 10)


def parse_notification_text(text: str | None) -> dict:
    """Parses the YAML body of an ESI notification. Returns {} when unparsable."""
    if not text:
        return {}
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        # Some notifications contain non-standard YAML tags; retry with the base loader
        try:
            data = yaml.load(text, Loader=yaml.BaseLoader)
        except yaml.YAMLError:
            return {}
    return data if isinstance(data, dict) else {}


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _system_id(data: dict) -> int | None:
    return _int_or_none(data.get("solarsystemID") or data.get("solarSystemID"))


def _relative_time(data: dict, sent_at: datetime) -> datetime | None:
    """Timer = notification timestamp + ``timeLeft``."""
    delta = ticks_to_timedelta(data.get("timeLeft"))
    if delta is None:
        return None
    return sent_at + delta


def _show_info_type_id(data: dict, key: str) -> int | None:
    info = data.get(key)
    if isinstance(info, list | tuple) and len(info) >= 2:
        return _int_or_none(info[1])
    return None


def _aggressor(data: dict) -> str:
    """'Corp / Alliance' from ``<a href="showinfo:...">Name</a>`` style fields."""
    parts = []
    for key in ("aggressorCorporationName", "aggressorAllianceName"):
        name = strip_tags(str(data.get(key) or "")).strip()
        if name and name not in parts:
            parts.append(name)
    return " / ".join(parts)


# -------------------------------------------------------------------------
# Per notification type parsers
# -------------------------------------------------------------------------


def _parse_upwell(timer_type: str, structure_state: str, label: str) -> Callable:
    def parser(
        notification_type: str, data: dict, sent_at: datetime
    ) -> NotificationTimer | None:
        when = _relative_time(data, sent_at)
        if not when:
            return None
        return NotificationTimer(
            notification_type=notification_type,
            timer_type=timer_type,
            timer_datetime=when,
            solar_system_id=_system_id(data),
            structure_state=structure_state,
            structure_type_id=_int_or_none(data.get("structureTypeID"))
            or _show_info_type_id(data, "structureShowInfoData"),
            structure_id=_int_or_none(data.get("structureID")),
            label=label,
        )

    return parser


def _parse_orbital_reinforced(
    notification_type: str, data: dict, sent_at: datetime
) -> NotificationTimer | None:
    when = filetime_to_datetime(data.get("reinforceExitTime"))
    if not when:
        return None
    return NotificationTimer(
        notification_type=notification_type,
        timer_type="HULL",
        timer_datetime=when,
        solar_system_id=_system_id(data),
        structure_state="REINFORCED",
        structure_type_id=_int_or_none(data.get("typeID")),
        planet_id=_int_or_none(data.get("planetID")),
        label="Customs Office reinforced (final timer)",
    )


def _parse_skyhook_lost_shields(
    notification_type: str, data: dict, sent_at: datetime
) -> NotificationTimer | None:
    when = _relative_time(data, sent_at)
    if not when:
        return None
    return NotificationTimer(
        notification_type=notification_type,
        timer_type="HULL",
        timer_datetime=when,
        solar_system_id=_system_id(data),
        structure_state="REINFORCED",
        structure_type_id=_int_or_none(data.get("typeID"))
        or _show_info_type_id(data, "skyhookShowInfoData"),
        structure_id=_int_or_none(data.get("itemID")),
        planet_id=_int_or_none(data.get("planetID")),
        label="Skyhook lost shields (final timer)",
    )


def _parse_mercenary_den_reinforced(
    notification_type: str, data: dict, sent_at: datetime
) -> NotificationTimer | None:
    when = filetime_to_datetime(data.get("timestampExited"))
    if not when:
        return None
    return NotificationTimer(
        notification_type=notification_type,
        timer_type="HULL",
        timer_datetime=when,
        solar_system_id=_system_id(data),
        structure_state="REINFORCED",
        structure_type_id=_int_or_none(data.get("typeID"))
        or _show_info_type_id(data, "mercenaryDenShowInfoData"),
        structure_id=_int_or_none(data.get("itemID")),
        planet_id=_int_or_none(data.get("planetID")),
        label="Mercenary Den reinforced (final timer)",
        aggressor=_aggressor(data),
    )


NOTIFICATION_HANDLERS: dict[str, Callable] = {
    "StructureLostShields": _parse_upwell("ARMOR", "ARMOR", "Structure lost shields"),
    "StructureLostArmor": _parse_upwell("HULL", "HULL", "Structure lost armor"),
    "StructureAnchoring": _parse_upwell(
        "ANCHORING", "ANCHORING", "Structure anchoring"
    ),
    "StructureUnanchoring": _parse_upwell(
        "UNANCHORING", "UNANCHORING", "Structure unanchoring"
    ),
    "OrbitalReinforced": _parse_orbital_reinforced,
    "SkyhookLostShields": _parse_skyhook_lost_shields,
    "MercenaryDenReinforced": _parse_mercenary_den_reinforced,
}


def parse_notification(
    notification_type: str, text: str | None, sent_at: datetime
) -> NotificationTimer | None:
    """Parses a stored notification into timer details, or None if not a timer."""
    handler = NOTIFICATION_HANDLERS.get(notification_type)
    if not handler:
        return None
    data = parse_notification_text(text)
    if not data:
        return None
    try:
        return handler(notification_type, data, sent_at)
    except Exception as exc:  # defensive: malformed notification bodies
        logger.debug("Failed to parse %s notification: %s", notification_type, exc)
        return None


# -------------------------------------------------------------------------
# Import service
# -------------------------------------------------------------------------


class CorptoolsNotificationTimerService:
    """Creates friendly StructureTimers from corptools-stored structure notifications."""

    @classmethod
    def is_corptools_installed(cls) -> bool:
        return apps.is_installed("corptools")

    @classmethod
    def is_enabled(cls) -> bool:
        # AA Hostile Intel
        from hostile import app_settings

        enabled = getattr(
            settings,
            "HOSTILE_CORPTOOLS_NOTIFICATIONS_ENABLED",
            app_settings.HOSTILE_CORPTOOLS_NOTIFICATIONS_ENABLED,
        )
        return bool(enabled) and cls.is_corptools_installed()

    @classmethod
    def _lookback(cls) -> timedelta:
        # AA Hostile Intel
        from hostile import app_settings

        hours = getattr(
            settings,
            "HOSTILE_CORPTOOLS_NOTIFICATION_LOOKBACK_HOURS",
            app_settings.HOSTILE_CORPTOOLS_NOTIFICATION_LOOKBACK_HOURS,
        )
        return timedelta(hours=int(hours))

    @classmethod
    def _tolerance(cls) -> timedelta:
        # AA Hostile Intel
        from hostile import app_settings

        minutes = getattr(
            settings,
            "HOSTILE_NOTIFICATION_TIMER_TOLERANCE_MINUTES",
            app_settings.HOSTILE_NOTIFICATION_TIMER_TOLERANCE_MINUTES,
        )
        return timedelta(minutes=int(minutes))

    # ---------------------------------------------------------------------

    @classmethod
    def _pending_notifications(cls, notification_model: Any, now: datetime) -> Any:
        return (
            notification_model.objects.filter(
                notification_type__in=list(NOTIFICATION_HANDLERS),
                timestamp__gte=now - cls._lookback(),
            )
            .select_related("notification_text", "character__character")
            .order_by("timestamp", "notification_id")
        )

    @classmethod
    def import_notification_timers(cls) -> dict[str, int]:
        stats = {"processed": 0, "created": 0, "linked": 0, "skipped": 0}
        if not cls.is_enabled():
            return stats

        # Third Party
        from corptools.models import Notification

        now = timezone.now()
        for notification in cls._pending_notifications(Notification, now):
            stats["processed"] += 1
            try:
                with transaction.atomic():
                    result = cls.process_notification(notification, now=now)
            except Exception as exc:
                logger.warning(
                    "Failed to create timer from notification %s: %s",
                    notification.notification_id,
                    exc,
                )
                result = "skipped"
            stats[result] += 1

        return stats

    @classmethod
    def process_notification(
        cls, notification: Any, now: datetime | None = None
    ) -> str:
        """Returns 'created', 'linked' or 'skipped'."""
        return cls.handle_notification(notification, now=now)[0]

    @classmethod
    def handle_notification(
        cls, notification: Any, now: datetime | None = None
    ) -> tuple[str, str]:
        """Processes one notification. Returns (result, human readable reason)."""
        # AA Hostile Intel
        from hostile.models import StructureTimer

        now = now or timezone.now()

        # Layer 1: this exact notification was already turned into / matched to a timer
        done = StructureTimer.objects.filter(
            source_notification_id=notification.notification_id
        ).first()
        if done:
            return "skipped", f"already handled (timer #{done.pk})"

        text = (
            notification.notification_text.notification_text
            if notification.notification_text_id and notification.notification_text
            else None
        )
        parsed = parse_notification(
            notification.notification_type, text, notification.timestamp
        )
        if not parsed:
            return "skipped", "could not read a timer from the notification text"
        if parsed.timer_datetime <= now:
            return (
                "skipped",
                f"timer already passed ({parsed.timer_datetime:%Y-%m-%d %H:%M} UTC)",
            )

        solar_system, item_type = cls._resolve_universe(parsed)
        if not solar_system:
            return "skipped", f"unknown solar system {parsed.solar_system_id}"

        owner = cls._owner_info(notification)

        # Keep the structure registry current, even if the timer already exists
        structure = cls._update_registry(
            parsed, solar_system, item_type, owner, notification.timestamp
        )
        when = f"{parsed.timer_type} {parsed.timer_datetime:%Y-%m-%d %H:%M} UTC"

        # Layer 2: already known in Hostile Intel (another director's copy, manual entry)
        existing = cls._find_existing_timer(parsed, structure, solar_system, item_type)
        if existing:
            cls._tag(existing, notification.notification_id)
            return (
                "skipped",
                f"{when} on {structure.name}: reuses existing timer #{existing.pk}",
            )

        # Layer 3: already on aa-structuretimers (e.g. posted by aa-structures)
        linked = cls._link_structuretimers_timer(
            parsed, structure, solar_system, item_type, owner, notification
        )
        if linked:
            return linked

        # Create it; post_save posts it to aa-structuretimers (layer 4 guards that)
        StructureTimer.objects.create(
            structure=structure,
            solar_system=solar_system,
            timer_type=parsed.timer_type,
            timer_datetime=parsed.timer_datetime,
            is_verified=True,
            source=StructureTimer.SOURCE_NOTIFICATION,
            source_notification_id=notification.notification_id,
            notes=cls._notes(parsed),
            created_by=owner.get("user"),
            **cls._defender_fields(owner),
        )
        return "created", f"{when} on {structure.name}: new timer"

    @classmethod
    def dry_run(cls) -> list[dict[str, Any]]:
        """
        Reports what an import would do, without keeping any change. Everything runs
        in one transaction that is rolled back, so later notifications see the
        effects of earlier ones exactly as in a real run.
        """
        rows: list[dict[str, Any]] = []
        if not cls.is_enabled():
            return rows

        # Third Party
        from corptools.models import Notification

        now = timezone.now()
        with transaction.atomic():
            for notification in cls._pending_notifications(Notification, now):
                try:
                    with transaction.atomic():
                        result, reason = cls.handle_notification(notification, now=now)
                except Exception as exc:
                    result, reason = "skipped", f"error: {exc}"
                rows.append(
                    {
                        "notification_id": notification.notification_id,
                        "type": notification.notification_type,
                        "character": getattr(
                            getattr(notification.character, "character", None),
                            "character_name",
                            "",
                        ),
                        "result": result,
                        "reason": reason,
                    }
                )
            transaction.set_rollback(True)
        return rows

    # ---------------------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------------------

    @staticmethod
    def _notes(parsed: NotificationTimer) -> str:
        notes = f"Auto: {parsed.label} ({parsed.notification_type})"
        if parsed.aggressor:
            notes += f"\nAggressor: {parsed.aggressor}"
        return notes

    @staticmethod
    def _defender_fields(owner: dict[str, Any]) -> dict[str, Any]:
        return {
            key: owner[key]
            for key in ("defender_id", "defender_name", "defender_ticker")
            if key in owner
        }

    @staticmethod
    def _tag(structure_timer: Any, notification_id: int) -> None:
        # AA Hostile Intel
        from hostile.models import StructureTimer

        if not structure_timer.source_notification_id:
            # queryset update: no signals, nothing re-posted
            StructureTimer.objects.filter(pk=structure_timer.pk).update(
                source_notification_id=notification_id
            )

    @classmethod
    def _resolve_universe(cls, parsed: NotificationTimer) -> tuple[Any, Any]:
        # Third Party
        from eve_sde.models import ItemType, SolarSystem

        solar_system = (
            SolarSystem.objects.filter(id=parsed.solar_system_id).first()
            if parsed.solar_system_id
            else None
        )
        item_type = (
            ItemType.objects.filter(id=parsed.structure_type_id).first()
            if parsed.structure_type_id
            else None
        )
        return solar_system, item_type

    @classmethod
    def _planet_name(cls, parsed: NotificationTimer) -> str:
        if not parsed.planet_id:
            return ""
        try:
            # Third Party
            from eve_sde.models import Planet

            planet = Planet.objects.filter(id=parsed.planet_id).first()
            return planet.name if planet and planet.name else ""
        except Exception:
            return ""

    @classmethod
    def _corptools_structure_name(cls, parsed: NotificationTimer) -> str:
        if not parsed.structure_id:
            return ""
        try:
            # Third Party
            from corptools.models import Structure

            ct_structure = Structure.objects.filter(
                structure_id=parsed.structure_id
            ).first()
            return ct_structure.name if ct_structure and ct_structure.name else ""
        except Exception:
            return ""

    @classmethod
    def _update_registry(
        cls,
        parsed: NotificationTimer,
        solar_system: Any,
        item_type: Any,
        owner: dict[str, Any],
        sent_at: datetime,
    ) -> Any:
        """Creates or refreshes the HostileStructure the notification is about."""
        # AA Hostile Intel
        from hostile.models import HostileAlliance, HostileCorporation, HostileStructure
        from hostile.services.intel_manager import IntelManager
        from hostile.services.timer_sync import TimerSyncService

        location = cls._planet_name(parsed)
        real_name = cls._corptools_structure_name(parsed)
        type_name = item_type.name if item_type else "Structure"
        name = real_name or f"{type_name} ({location or solar_system.name})"
        ticker = (owner.get("ticker") or "")[:32]

        defaults = {
            key: value
            for key, value in (
                ("location_details", location),
                ("owner_ticker", ticker),
            )
            if value
        }
        structure = (
            HostileStructure.objects.filter(structure_id=parsed.structure_id).first()
            if parsed.structure_id
            else None
        )
        if not structure:
            structure, _ = IntelManager.get_or_create_structure(
                name=name,
                solar_system=solar_system,
                structure_type=item_type,
                structure_id=parsed.structure_id,
                defaults=defaults,
            )
            if (
                parsed.structure_id
                and structure.structure_id
                and structure.structure_id != parsed.structure_id
            ):
                # Name-matched a different, positively identified structure
                structure = HostileStructure.objects.create(
                    name=name,
                    solar_system=solar_system,
                    structure_type=item_type,
                    structure_id=parsed.structure_id,
                    **defaults,
                )

        updated = set()
        if parsed.structure_id and structure.structure_id != parsed.structure_id:
            structure.structure_id = parsed.structure_id
            updated.add("structure_id")
        if item_type and structure.structure_type_id != item_type.id:
            structure.structure_type = item_type
            updated.add("structure_type")
        if structure.solar_system_id != solar_system.id:
            structure.solar_system = solar_system
            updated.add("solar_system")
        if (
            real_name
            and structure.name != real_name
            and TimerSyncService._is_generic_name(
                structure.name, type_name, solar_system.name
            )
        ):
            structure.name = real_name
            updated.add("name")
        if location and not structure.location_details:
            structure.location_details = location[:254]
            updated.add("location_details")

        # Owner: we received the notification, so we own it. Link existing registry
        # entities only - never create or re-flag alliances / corporations here.
        if ticker and structure.owner_ticker != ticker:
            structure.owner_ticker = ticker
            updated.add("owner_ticker")
        alliance_id = owner.get("alliance_id")
        corporation_id = owner.get("corporation_id")
        alliance = (
            HostileAlliance.objects.filter(alliance_id=alliance_id).first()
            if alliance_id
            else None
        )
        corporation = (
            HostileCorporation.objects.filter(corporation_id=corporation_id).first()
            if corporation_id
            else None
        )
        current_alliance = structure.alliance
        if alliance and structure.alliance_id != alliance.pk:
            structure.alliance = alliance
            updated.add("alliance")
        elif (
            not alliance
            and current_alliance
            and current_alliance.alliance_id != alliance_id
        ):
            structure.alliance = None
            updated.add("alliance")
        current_corp = structure.corporation
        if corporation and structure.corporation_id != corporation.pk:
            structure.corporation = corporation
            updated.add("corporation")
        elif (
            not corporation
            and current_corp
            and current_corp.corporation_id != corporation_id
        ):
            structure.corporation = None
            updated.add("corporation")

        # State: never let an older notification roll a newer state back
        if parsed.structure_state and (
            not structure.last_notification_at
            or sent_at > structure.last_notification_at
        ):
            if structure.state != parsed.structure_state:
                structure.state = parsed.structure_state
                updated.add("state")
            structure.last_notification_at = sent_at
            updated.add("last_notification_at")

        if updated:
            structure.save(update_fields=list(updated) + ["updated_at"])
        return structure

    @classmethod
    def _find_existing_timer(
        cls,
        parsed: NotificationTimer,
        structure: Any,
        solar_system: Any,
        item_type: Any,
    ) -> Any | None:
        """
        An existing Hostile Intel timer for the same structure, type and time.

        Ambiguous candidates count as duplicates; a candidate is only ruled out
        when it is positively a different structure (other structure ID, other
        type, other real name) or positively hostile-owned.
        """
        # Django
        from django.db.models import Q

        # AA Hostile Intel
        from hostile.models import StructureTimer
        from hostile.services.timer_sync import TimerSyncService

        tolerance = cls._tolerance()
        candidates = (
            StructureTimer.objects.filter(
                timer_type=parsed.timer_type,
                timer_datetime__gte=parsed.timer_datetime - tolerance,
                timer_datetime__lte=parsed.timer_datetime + tolerance,
                is_sov_campaign=False,
            )
            .filter(
                Q(solar_system=solar_system) | Q(structure__solar_system=solar_system)
            )
            .select_related("structure", "structure__structure_type")
            .order_by("timer_datetime")
        )

        type_name = item_type.name if item_type else ""
        for candidate in candidates:
            other = candidate.structure
            # Same registry entry or same in-game structure: certainly the same timer
            if other and (
                other.pk == structure.pk
                or (parsed.structure_id and other.structure_id == parsed.structure_id)
            ):
                return candidate
            if other:
                if (
                    parsed.structure_id
                    and other.structure_id
                    and other.structure_id != parsed.structure_id
                ):
                    continue
                if (
                    item_type
                    and other.structure_type_id
                    and other.structure_type_id != item_type.id
                ):
                    continue
                other_type = (
                    other.structure_type.name if other.structure_type else type_name
                )
                if not TimerSyncService._is_generic_name(
                    other.name, other_type, solar_system.name
                ) and not TimerSyncService._is_generic_name(
                    structure.name, type_name, solar_system.name
                ):
                    if TimerSyncService._core_name(
                        other.name, solar_system.name
                    ) != TimerSyncService._core_name(structure.name, solar_system.name):
                        continue
            if cls._is_positively_hostile(candidate):
                continue
            return candidate
        return None

    @staticmethod
    def _is_positively_hostile(structure_timer: Any) -> bool:
        """True when the timer is known to belong to a non-friendly owner."""
        # AA Hostile Intel
        from hostile.services.timer_sync import TimerSyncService

        if structure_timer.source == "NOTIFICATION":
            return False
        if TimerSyncService.is_structure_timer_friendly(structure_timer):
            return False
        other = structure_timer.structure
        has_owner = bool(
            structure_timer.defender_id
            or structure_timer.defender_name
            or (
                other
                and (other.alliance_id or other.corporation_id or other.owner_ticker)
            )
        )
        return has_owner

    @classmethod
    def _link_structuretimers_timer(
        cls,
        parsed: NotificationTimer,
        structure: Any,
        solar_system: Any,
        item_type: Any,
        owner: dict[str, Any],
        notification: Any,
    ) -> tuple[str, str] | None:
        """
        Links to an equivalent timer already on aa-structuretimers instead of posting
        another one. Returns (result, reason), or None when nothing matched.
        """
        # AA Hostile Intel
        from hostile.models import StructureTimer
        from hostile.services.timer_sync import TimerSyncService, sync_operation

        if not TimerSyncService.is_structuretimers_sync_enabled():
            return None

        match = TimerSyncService.find_matching_structuretimers_timer(
            solar_system_id=solar_system.id,
            timer_type=parsed.timer_type,
            when=parsed.timer_datetime,
            structure_name=structure.name,
            structure_type_id=item_type.id if item_type else None,
            tolerance=cls._tolerance(),
            objective="FR",
        )
        if not match:
            return None

        already = StructureTimer.objects.filter(
            structuretimers_timer_id=match.id
        ).first()
        if already:
            cls._tag(already, notification.notification_id)
            return (
                "skipped",
                f"already on aa-structuretimers (timer {match.id}, "
                f"linked to #{already.pk})",
            )

        # Create the Hostile side ourselves, without triggering any outbound sync:
        # the external timer is left untouched and nothing is posted anywhere.
        with sync_operation("structuretimers_timer", match.id) as acquired:
            if not acquired:
                return "skipped", "aa-structuretimers timer is being synced right now"
            StructureTimer.objects.create(
                structure=structure,
                solar_system=solar_system,
                timer_type=parsed.timer_type,
                timer_datetime=match.date,
                is_verified=True,
                source=StructureTimer.SOURCE_NOTIFICATION,
                source_notification_id=notification.notification_id,
                notes=match.details_notes or cls._notes(parsed),
                created_by=owner.get("user"),
                structuretimers_timer_id=match.id,
                structuretimers_adopted=True,
                last_sync_hash=TimerSyncService.compute_structuretimers_timer_hash(
                    match
                ),
                **cls._defender_fields(owner),
            )
        return (
            "linked",
            f"already on aa-structuretimers (timer {match.id}); linked, not reposted",
        )

    @classmethod
    def _owner_info(cls, notification: Any) -> dict[str, Any]:
        """Owner / defender details from the receiving character."""
        info: dict[str, Any] = {}
        audit = getattr(notification, "character", None)
        character = getattr(audit, "character", None)
        if not character:
            return info

        info["corporation_id"] = getattr(character, "corporation_id", None)
        info["alliance_id"] = getattr(character, "alliance_id", None)

        if info["alliance_id"]:
            info["defender_id"] = character.alliance_id
            info["defender_name"] = (character.alliance_name or "")[:254]
            info["defender_ticker"] = (character.alliance_ticker or "")[:10]
        elif info["corporation_id"]:
            info["defender_id"] = character.corporation_id
            info["defender_name"] = (character.corporation_name or "")[:254]
            info["defender_ticker"] = (character.corporation_ticker or "")[:10]
        info["ticker"] = info.get("defender_ticker", "")

        user = None
        try:
            user = character.character_ownership.user
        except Exception:
            pass
        if user is None:
            try:
                # Alliance Auth
                from allianceauth.authentication.models import UserProfile

                profile = (
                    UserProfile.objects.filter(main_character=character)
                    .select_related("user")
                    .first()
                )
                user = profile.user if profile else None
            except Exception:
                pass
        if user is not None:
            info["user"] = user
        return info
