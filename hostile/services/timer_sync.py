"""
Two-way synchronization service between Hostile Intel Structure Timers,
aa-structuretimers (3.2.0+), and Alliance Auth built-in Structure Timers / Timerboard.
"""

# Standard Library
import hashlib
import logging
import threading
from contextlib import contextmanager
from typing import Any

# Django
from django.apps import apps
from django.contrib.auth.models import User
from django.utils import timezone

logger = logging.getLogger(__name__)

# Thread-local storage to prevent infinite signal loops between StructureTimer and external timers
_thread_locals = threading.local()


def _get_sync_lock() -> set:
    if not hasattr(_thread_locals, "sync_lock"):
        _thread_locals.sync_lock = set()
    return _thread_locals.sync_lock


def is_sync_in_progress() -> bool:
    return getattr(_thread_locals, "in_sync", False)


@contextmanager
def sync_operation(source_type: str, item_id: Any):
    sync_lock = _get_sync_lock()
    key = f"{source_type}_{item_id}"
    if key in sync_lock or getattr(_thread_locals, "in_sync", False):
        yield False
        return
    sync_lock.add(key)
    _thread_locals.in_sync = True
    try:
        yield True
    finally:
        sync_lock.discard(key)
        _thread_locals.in_sync = False


def compute_timer_hash(
    solar_system_id: int | None,
    structure_name: str,
    structure_type_id: int | None,
    timer_type: str,
    timer_datetime: Any,
    owner_name: str = "",
    notes: str = "",
    location_details: str = "",
) -> str:
    """
    Computes a deterministic hash marker representing the timer state.
    Used for instant change detection and identifying untracked external modifications.
    """
    if isinstance(timer_datetime, str):
        # Truncate string datetime up to minute: YYYY-MM-DD HH:MM
        dt_str = timer_datetime[:16].replace("T", " ")
    elif timer_datetime:
        dt_str = timer_datetime.strftime("%Y-%m-%d %H:%M")
    else:
        dt_str = ""

    norm_owner = (owner_name or "").strip().lower()
    if norm_owner in ["unknown", "none", ""]:
        norm_owner = ""

    raw = (
        f"{solar_system_id or ''}|"
        f"{(structure_name or '').strip().lower()}|"
        f"{structure_type_id or ''}|"
        f"{(timer_type or '').strip().upper()}|"
        f"{dt_str}|"
        f"{norm_owner}|"
        f"{(location_details or '').strip().lower()}|"
        f"{(notes or '').strip()}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class TimerSyncService:
    """
    Handles bidirectional synchronization between Hostile Intel StructureTimer,
    aa-structuretimers (structuretimers.models.Timer), and Alliance Auth Timerboard (timerboard.models.Timer).
    """

    # -------------------------------------------------------------------------
    # App Detection & Model Access
    # -------------------------------------------------------------------------

    @classmethod
    def is_timerboard_installed(cls) -> bool:
        """Returns True if allianceauth.timerboard is installed in INSTALLED_APPS"""
        return apps.is_installed("timerboard") or apps.is_installed(
            "allianceauth.timerboard"
        )

    @classmethod
    def is_structuretimers_installed(cls) -> bool:
        """Returns True if aa-structuretimers (structuretimers) is installed in INSTALLED_APPS"""
        return apps.is_installed("structuretimers")

    @classmethod
    def get_timerboard_timer_model(cls) -> Any | None:
        """Returns the Timer model class from allianceauth.timerboard if installed"""
        if not cls.is_timerboard_installed():
            return None
        try:
            return apps.get_model("timerboard", "Timer")
        except (LookupError, ValueError):
            try:
                return apps.get_model("allianceauth", "Timer")
            except (LookupError, ValueError):
                return None

    @classmethod
    def get_structuretimers_timer_model(cls) -> Any | None:
        """Returns the Timer model class from structuretimers if installed"""
        if not cls.is_structuretimers_installed():
            return None
        try:
            return apps.get_model("structuretimers", "Timer")
        except (LookupError, ValueError):
            return None

    # Backward compatibility alias
    @classmethod
    def get_timer_model(cls) -> Any | None:
        return cls.get_timerboard_timer_model()

    @classmethod
    def is_timerboard_sync_enabled(cls) -> bool:
        """Returns True if synchronization with Timerboard is globally enabled"""
        # AA Hostile Intel
        from hostile.app_settings import HOSTILE_TIMERBOARD_SYNC_ENABLED

        return HOSTILE_TIMERBOARD_SYNC_ENABLED and cls.is_timerboard_installed()

    @classmethod
    def is_structuretimers_sync_enabled(cls) -> bool:
        """Returns True if synchronization with aa-structuretimers is globally enabled"""
        # AA Hostile Intel
        from hostile.app_settings import HOSTILE_STRUCTURETIMERS_SYNC_ENABLED

        return (
            HOSTILE_STRUCTURETIMERS_SYNC_ENABLED and cls.is_structuretimers_installed()
        )

    @classmethod
    def is_sync_enabled(cls) -> bool:
        """Returns True if synchronization with any external timerboard is enabled"""
        return cls.is_timerboard_sync_enabled() or cls.is_structuretimers_sync_enabled()

    @classmethod
    def get_timerboard_timer_by_id(cls, timer_id: int) -> Any | None:
        """Fetches a Timerboard Timer instance by ID"""
        timer_model = cls.get_timerboard_timer_model()
        if not timer_model or not timer_id:
            return None
        try:
            return timer_model.objects.filter(id=timer_id).first()
        except Exception as exc:
            logger.debug("Failed to retrieve Timerboard Timer %s: %s", timer_id, exc)
            return None

    @classmethod
    def get_structuretimers_timer_by_id(cls, timer_id: int) -> Any | None:
        """Fetches an aa-structuretimers Timer instance by ID"""
        timer_model = cls.get_structuretimers_timer_model()
        if not timer_model or not timer_id:
            return None
        try:
            return timer_model.objects.filter(id=timer_id).first()
        except Exception as exc:
            logger.debug(
                "Failed to retrieve structuretimers Timer %s: %s", timer_id, exc
            )
            return None

    # -------------------------------------------------------------------------
    # Universe / SDE Model Helpers
    # -------------------------------------------------------------------------

    @classmethod
    def get_or_create_eveuniverse_solar_system(cls, solar_system: Any) -> Any | None:
        """Resolves or provisions an EveSolarSystem model instance in eveuniverse"""
        if not solar_system:
            return None
        try:
            # Third Party
            from eveuniverse.models import (
                EveConstellation,
                EveRegion,
                EveSolarSystem,
            )
        except ImportError:
            return None

        eve_system = EveSolarSystem.objects.filter(id=solar_system.id).first()
        if eve_system:
            return eve_system

        # Attempt to use ESI get_or_create if network is available
        try:
            return EveSolarSystem.objects.get_or_create_esi(id=solar_system.id)[0]
        except Exception:
            pass

        # Local database creation fallback
        try:
            region_id = getattr(solar_system, "region_id", 10000001) or 10000001
            region_name = "Universe Region"
            if hasattr(solar_system, "constellation") and solar_system.constellation:
                if (
                    hasattr(solar_system.constellation, "region")
                    and solar_system.constellation.region
                ):
                    region_id = solar_system.constellation.region.id
                    region_name = solar_system.constellation.region.name

            region, _ = EveRegion.objects.get_or_create(
                id=region_id,
                defaults={"name": region_name},
            )

            constellation_id = (
                getattr(solar_system, "constellation_id", 20000001) or 20000001
            )
            constellation_name = "Universe Constellation"
            if hasattr(solar_system, "constellation") and solar_system.constellation:
                constellation_id = solar_system.constellation.id
                constellation_name = solar_system.constellation.name

            constellation, _ = EveConstellation.objects.get_or_create(
                id=constellation_id,
                defaults={
                    "name": constellation_name,
                    "eve_region": region,
                },
            )

            system, _ = EveSolarSystem.objects.get_or_create(
                id=solar_system.id,
                defaults={
                    "name": solar_system.name,
                    "eve_constellation": constellation,
                    "security_status": getattr(solar_system, "security_status", 0.0)
                    or 0.0,
                },
            )
            return system
        except Exception as exc:
            logger.debug(
                "Fallback creation of EveSolarSystem %s failed: %s",
                solar_system.id,
                exc,
            )
            return None

    @classmethod
    def get_or_create_eveuniverse_type(cls, item_type: Any) -> Any | None:
        """Resolves or provisions an EveType model instance in eveuniverse"""
        if not item_type:
            return None
        try:
            # Third Party
            from eveuniverse.models import EveCategory, EveGroup, EveType
        except ImportError:
            return None

        eve_type = EveType.objects.filter(id=item_type.id).first()
        if eve_type:
            return eve_type

        try:
            return EveType.objects.get_or_create_esi(id=item_type.id)[0]
        except Exception:
            pass

        try:
            category_id = getattr(item_type, "category_id", 65) or 65
            category_name = "Structure"
            if hasattr(item_type, "group") and item_type.group:
                if hasattr(item_type.group, "category") and item_type.group.category:
                    category_id = item_type.group.category.id
                    category_name = item_type.group.category.name

            category, _ = EveCategory.objects.get_or_create(
                id=category_id,
                defaults={"name": category_name},
            )

            group_id = getattr(item_type, "group_id", 1657) or 1657
            group_name = "Citadel"
            if hasattr(item_type, "group") and item_type.group:
                group_id = item_type.group.id
                group_name = item_type.group.name

            group, _ = EveGroup.objects.get_or_create(
                id=group_id,
                defaults={"name": group_name, "eve_category": category},
            )

            type_obj, _ = EveType.objects.get_or_create(
                id=item_type.id,
                defaults={
                    "name": item_type.name,
                    "eve_group": group,
                    "published": True,
                },
            )
            return type_obj
        except Exception as exc:
            logger.debug(
                "Fallback creation of EveType %s failed: %s", item_type.id, exc
            )
            return None

    @classmethod
    def get_or_create_eve_sde_solar_system(cls, eve_solar_system: Any) -> Any | None:
        """Resolves or provisions an eve_sde SolarSystem from an EveSolarSystem"""
        if not eve_solar_system:
            return None
        try:
            # Third Party
            from eve_sde.models import SolarSystem
        except ImportError:
            return None

        system = SolarSystem.objects.filter(id=eve_solar_system.id).first()
        if system:
            return system

        system = SolarSystem.objects.filter(
            name__iexact=getattr(eve_solar_system, "name", "")
        ).first()
        if system:
            return system

        try:
            return SolarSystem.objects.create(
                id=eve_solar_system.id,
                name=eve_solar_system.name,
                security_status=getattr(eve_solar_system, "security_status", 0.0),
            )
        except Exception as exc:
            logger.debug(
                "Failed to create SolarSystem from eveuniverse system %s: %s",
                eve_solar_system.id,
                exc,
            )
            return None

    @classmethod
    def get_or_create_eve_sde_item_type(cls, eve_type: Any) -> Any | None:
        """Resolves or provisions an eve_sde ItemType from an EveType"""
        if not eve_type:
            return None
        try:
            # Third Party
            from eve_sde.models import ItemType
        except ImportError:
            return None

        type_obj = ItemType.objects.filter(id=eve_type.id).first()
        if type_obj:
            return type_obj

        type_obj = ItemType.objects.filter(
            name__iexact=getattr(eve_type, "name", "")
        ).first()
        if type_obj:
            return type_obj

        try:
            return ItemType.objects.create(
                id=eve_type.id,
                name=eve_type.name,
                published=getattr(eve_type, "published", True),
            )
        except Exception as exc:
            logger.debug(
                "Failed to create ItemType from eveuniverse type %s: %s",
                eve_type.id,
                exc,
            )
            return None

    # -------------------------------------------------------------------------
    # Value & Enum Mapping
    # -------------------------------------------------------------------------

    @classmethod
    def map_structure_timer_type_to_structuretimers(cls, timer_type: str) -> str:
        """
        Maps Hostile StructureTimer timer_type to aa-structuretimers Timer.Type choice codes.
        """
        mapping = {
            "ARMOR": "AR",
            "HULL": "HL",
            "ANCHORING": "AN",
            "UNANCHORING": "UA",
            "OTHER": "NO",
        }
        return mapping.get((timer_type or "").upper(), "NO")

    @classmethod
    def map_structuretimers_type_to_structure_timer(cls, timer_type_code: str) -> str:
        """
        Maps aa-structuretimers Timer.Type choice codes to Hostile StructureTimer timer_type.
        """
        mapping = {
            "AR": "ARMOR",
            "HL": "HULL",
            "FI": "HULL",  # Final timer represents Hull in EVE Upwell mechanics
            "AN": "ANCHORING",
            "UA": "UNANCHORING",
            "MM": "OTHER",
            "TF": "OTHER",
            "PL": "OTHER",
            "NO": "OTHER",
        }
        return mapping.get((timer_type_code or "").upper(), "OTHER")

    @classmethod
    def map_structure_timer_type_to_timerboard(cls, timer_type: str) -> str:
        """
        Maps Hostile StructureTimer timer_type to Timerboard Timer.TimerType choice.
        """
        mapping = {
            "ARMOR": "ARMOR",
            "HULL": "HULL",
            "ANCHORING": "ANCHORING",
            "UNANCHORING": "UNANCHORING",
            "OTHER": "UNSPECIFIED",
        }
        return mapping.get((timer_type or "").upper(), "UNSPECIFIED")

    @classmethod
    def map_timerboard_type_to_structure_timer(cls, tb_timer_type: str) -> str:
        """
        Maps Timerboard Timer.TimerType choice to Hostile StructureTimer timer_type.
        """
        mapping = {
            "ARMOR": "ARMOR",
            "HULL": "HULL",
            "FINAL": "HULL",
            "ANCHORING": "ANCHORING",
            "UNANCHORING": "UNANCHORING",
            "SHIELD": "OTHER",
            "UNSPECIFIED": "OTHER",
            "ABANDONED": "OTHER",
            "THEFT": "OTHER",
            # Fallback legacy casing
            "Armor": "ARMOR",
            "Hull": "HULL",
            "Anchoring": "ANCHORING",
            "Unanchoring": "UNANCHORING",
            "Final": "HULL",
            "Other": "OTHER",
        }
        return mapping.get(str(tb_timer_type), "OTHER")

    @classmethod
    def map_structure_to_timerboard_choice(
        cls, structure: Any | None, fallback_name: str = ""
    ) -> str:
        """
        Maps a HostileStructure instance or string name to allianceauth.timerboard Timer.Structure choices.
        """
        timer_model = cls.get_timerboard_timer_model()
        if not timer_model:
            return "Other"

        StructureChoices = getattr(timer_model, "Structure", None)
        if not StructureChoices:
            return "Other"

        type_name = ""
        struct_name = ""
        if structure:
            if hasattr(structure, "structure_type") and structure.structure_type:
                type_name = structure.structure_type.name or ""
            struct_name = getattr(structure, "name", "") or ""
        elif fallback_name:
            struct_name = fallback_name

        combined = f"{type_name} {struct_name}".lower()

        exact_map = {
            "astrahus": "Astrahus",
            "fortizar": "Fortizar",
            "keepstar": "Keepstar",
            "raitaru": "Raitaru",
            "azbel": "Azbel",
            "sotiyo": "Sotiyo",
            "athanor": "Athanor",
            "tatara": "Tatara",
            "ansiblex": "Ansiblex Jump Gate",
            "pharolux": "Pharolux Cyno Beacon",
            "tenebrex": "Tenebrex Cyno Jammer",
            "metenox": "Metenox Moon Drill",
            "mercenary den": "Mercenary Den",
            "orbital skyhook": "Orbital Skyhook",
            "poco": "POCO",
            "customs office": "POCO",
            "player owned customs office": "POCO",
            "ihub": "I-Hub",
            "infrastructure hub": "I-Hub",
            "tcu": "TCU",
            "territorial claim unit": "TCU",
            "pos": "POS",
            "control tower": "POS",
        }

        for key, val in exact_map.items():
            if key in combined:
                for choice in StructureChoices.choices:
                    if choice[0] == val or choice[1] == val:
                        return choice[0]

        for choice in StructureChoices.choices:
            if choice[1].lower() in combined or choice[0].lower() in combined:
                return choice[0]

        return "Other"

    # -------------------------------------------------------------------------
    # Hash State Calculation
    # -------------------------------------------------------------------------

    @classmethod
    def compute_structure_timer_hash(cls, structure_timer: Any) -> str:
        """Computes current state hash for a Hostile StructureTimer instance"""
        system_id = (
            structure_timer.solar_system_id
            if structure_timer.solar_system_id
            else (
                structure_timer.structure.solar_system_id
                if structure_timer.structure
                else None
            )
        )
        struct_name = (
            structure_timer.structure.name
            if structure_timer.structure
            else (
                structure_timer.solar_system.name
                if structure_timer.solar_system
                else ""
            )
        )
        struct_type_id = (
            structure_timer.structure.structure_type_id
            if structure_timer.structure and structure_timer.structure.structure_type_id
            else None
        )
        owner_name = structure_timer.defender_display
        location_details = (
            structure_timer.structure.location_details
            if structure_timer.structure
            else ""
        )

        return compute_timer_hash(
            solar_system_id=system_id,
            structure_name=struct_name,
            structure_type_id=struct_type_id,
            timer_type=structure_timer.timer_type,
            timer_datetime=structure_timer.timer_datetime,
            owner_name=owner_name,
            notes=structure_timer.notes,
            location_details=location_details,
        )

    @classmethod
    def compute_structuretimers_timer_hash(cls, st_timer: Any) -> str:
        """Computes normalized state hash for an aa-structuretimers Timer instance"""
        mapped_timer_type = cls.map_structuretimers_type_to_structure_timer(
            st_timer.timer_type
        )
        return compute_timer_hash(
            solar_system_id=st_timer.eve_solar_system_id,
            structure_name=st_timer.structure_name or "",
            structure_type_id=st_timer.structure_type_id,
            timer_type=mapped_timer_type,
            timer_datetime=st_timer.date,
            owner_name=st_timer.owner_name or "",
            notes=st_timer.details_notes or "",
            location_details=st_timer.location_details or "",
        )

    @classmethod
    def compute_timerboard_timer_hash(cls, tb_timer: Any) -> str:
        """Computes normalized state hash for an Alliance Auth Timerboard Timer instance"""
        mapped_timer_type = cls.map_timerboard_type_to_structure_timer(
            tb_timer.timer_type
        )
        owner_name = getattr(tb_timer, "owner_name", "") or ""
        objective = getattr(tb_timer, "objective", "")
        if (
            not owner_name
            and str(objective).lower() == "friendly"
            and hasattr(tb_timer, "eve_corp")
            and tb_timer.eve_corp
        ):
            owner_name = tb_timer.eve_corp.corporation_name or ""

        location_details = (
            getattr(tb_timer, "planet_moon", "")
            or getattr(tb_timer, "location_details", "")
            or ""
        )
        notes = getattr(tb_timer, "details", "") or getattr(tb_timer, "notes", "") or ""

        return compute_timer_hash(
            solar_system_id=None,  # Timerboard stores system as CharField
            structure_name=tb_timer.structure or "",
            structure_type_id=None,
            timer_type=mapped_timer_type,
            timer_datetime=tb_timer.eve_time,
            owner_name=owner_name,
            notes=notes,
            location_details=location_details,
        )

    @classmethod
    def is_entity_friendly(
        cls,
        alliance_id: int | None = None,
        alliance_name: str | None = None,
        corp_id: int | None = None,
        corp_name: str | None = None,
        ticker: str | None = None,
    ) -> bool:
        """
        Determines whether an alliance or corporation entity is friendly based on
        settings and database friendly flags.
        """
        # Django
        from django.conf import settings

        # AA Hostile Intel
        from hostile.models import HostileAlliance, HostileCorporation

        main_alliance_id = getattr(
            settings, "HOSTILE_MAIN_ALLIANCE_ID", getattr(settings, "ALLIANCE_ID", None)
        )
        main_alliance_name = getattr(
            settings,
            "HOSTILE_MAIN_ALLIANCE_NAME",
            getattr(settings, "ALLIANCE_NAME", None),
        )

        if (
            alliance_id
            and main_alliance_id
            and int(alliance_id) == int(main_alliance_id)
        ):
            return True
        if (
            alliance_name
            and main_alliance_name
            and alliance_name.strip().lower() == main_alliance_name.strip().lower()
        ):
            return True

        ignored_alliance_ids = [
            int(i)
            for i in getattr(settings, "HOSTILE_IGNORED_ALLIANCE_IDS", [])
            if str(i).isdigit()
        ]
        if alliance_id and int(alliance_id) in ignored_alliance_ids:
            return True

        ignored_corp_ids = [
            int(i)
            for i in getattr(settings, "HOSTILE_IGNORED_CORP_IDS", [])
            if str(i).isdigit()
        ]
        if corp_id and int(corp_id) in ignored_corp_ids:
            return True

        ignored_names = [
            str(n).strip().lower()
            for n in getattr(settings, "HOSTILE_IGNORED_ALLIANCE_NAMES", [])
            if n
        ]
        if alliance_name and alliance_name.strip().lower() in ignored_names:
            return True
        if corp_name and corp_name.strip().lower() in ignored_names:
            return True

        ignored_tickers = [
            str(t).strip().lower()
            for t in getattr(settings, "HOSTILE_IGNORED_ALLIANCE_TICKERS", [])
            if t
        ]
        if ticker and ticker.strip().lower() in ignored_tickers:
            return True

        # Check database is_friendly flags
        if (
            alliance_id
            and HostileAlliance.objects.filter(
                alliance_id=alliance_id, is_friendly=True
            ).exists()
        ):
            return True
        if (
            alliance_name
            and HostileAlliance.objects.filter(
                alliance_name__iexact=alliance_name.strip(), is_friendly=True
            ).exists()
        ):
            return True
        if (
            corp_id
            and HostileCorporation.objects.filter(
                corporation_id=corp_id, is_friendly=True
            ).exists()
        ):
            return True
        if (
            corp_name
            and HostileCorporation.objects.filter(
                corporation_name__iexact=corp_name.strip(), is_friendly=True
            ).exists()
        ):
            return True
        if ticker and (
            HostileAlliance.objects.filter(
                ticker__iexact=ticker.strip(), is_friendly=True
            ).exists()
            or HostileCorporation.objects.filter(
                ticker__iexact=ticker.strip(), is_friendly=True
            ).exists()
        ):
            return True

        return False

    @classmethod
    def is_structure_timer_friendly(cls, structure_timer: Any) -> bool:
        """Determines if a Hostile StructureTimer represents a friendly objective/structure."""
        if structure_timer.structure:
            if structure_timer.structure.alliance:
                if cls.is_entity_friendly(
                    alliance_id=structure_timer.structure.alliance.alliance_id,
                    alliance_name=structure_timer.structure.alliance.alliance_name,
                    ticker=structure_timer.structure.alliance.ticker,
                ) or getattr(structure_timer.structure.alliance, "is_friendly", False):
                    return True
            if structure_timer.structure.corporation:
                if cls.is_entity_friendly(
                    corp_id=structure_timer.structure.corporation.corporation_id,
                    corp_name=structure_timer.structure.corporation.corporation_name,
                    ticker=structure_timer.structure.corporation.ticker,
                ) or getattr(
                    structure_timer.structure.corporation, "is_friendly", False
                ):
                    return True
            if structure_timer.structure.owner_ticker and cls.is_entity_friendly(
                ticker=structure_timer.structure.owner_ticker
            ):
                return True

        if structure_timer.defender_id and cls.is_entity_friendly(
            alliance_id=structure_timer.defender_id, corp_id=structure_timer.defender_id
        ):
            return True
        if structure_timer.defender_name and cls.is_entity_friendly(
            alliance_name=structure_timer.defender_name,
            corp_name=structure_timer.defender_name,
        ):
            return True
        if structure_timer.defender_ticker and cls.is_entity_friendly(
            ticker=structure_timer.defender_ticker
        ):
            return True

        return False

    # -------------------------------------------------------------------------
    # Bidirectional Sync: Hostile -> External Boards
    # -------------------------------------------------------------------------

    @classmethod
    def sync_structure_timer_to_all(cls, structure_timer: Any) -> dict[str, Any]:
        """
        Synchronizes a Hostile Intel StructureTimer to both aa-structuretimers
        and Alliance Auth Timerboard seamlessly.
        """
        if structure_timer.is_sov_campaign or structure_timer.timer_type in [
            "SOV_IHUB",
            "SOV_TCU",
        ]:
            return {}

        results = {}
        with sync_operation("hostile_structure_timer", structure_timer.id) as acquired:
            if not acquired:
                return {}

            state_hash = cls.compute_structure_timer_hash(structure_timer)

            # Sync to aa-structuretimers
            if cls.is_structuretimers_sync_enabled():
                try:
                    st_obj = cls._sync_to_structuretimers_model(structure_timer)
                    if st_obj:
                        results["structuretimers"] = st_obj.id
                except Exception as exc:
                    logger.warning(
                        "Failed to sync StructureTimer %s to aa-structuretimers: %s",
                        structure_timer.id,
                        exc,
                    )

            # Sync to Alliance Auth Timerboard
            if cls.is_timerboard_sync_enabled():
                try:
                    tb_obj = cls._sync_to_timerboard_model(structure_timer)
                    if tb_obj:
                        results["timerboard"] = tb_obj.id
                except Exception as exc:
                    logger.warning(
                        "Failed to sync StructureTimer %s to Timerboard: %s",
                        structure_timer.id,
                        exc,
                    )

            # Update marker and IDs on StructureTimer
            update_fields = []
            if (
                "structuretimers" in results
                and structure_timer.structuretimers_timer_id
                != results["structuretimers"]
            ):
                structure_timer.structuretimers_timer_id = results["structuretimers"]
                update_fields.append("structuretimers_timer_id")

            if (
                "timerboard" in results
                and structure_timer.timerboard_timer_id != results["timerboard"]
            ):
                structure_timer.timerboard_timer_id = results["timerboard"]
                update_fields.append("timerboard_timer_id")

            if structure_timer.last_sync_hash != state_hash:
                structure_timer.last_sync_hash = state_hash
                update_fields.append("last_sync_hash")

            if update_fields:
                structure_timer.save(update_fields=update_fields)

        return results

    @classmethod
    def _sync_to_structuretimers_model(cls, structure_timer: Any) -> Any | None:
        """Internal helper to create or update an aa-structuretimers Timer model"""
        timer_model = cls.get_structuretimers_timer_model()
        if not timer_model:
            return None

        # Resolve universe models
        solar_system = structure_timer.solar_system
        if not solar_system and structure_timer.structure:
            solar_system = structure_timer.structure.solar_system

        eve_system = cls.get_or_create_eveuniverse_solar_system(solar_system)
        if not eve_system:
            logger.debug(
                "Cannot sync StructureTimer %s to structuretimers: solar system could not be resolved",
                structure_timer.id,
            )
            return None

        item_type = None
        if structure_timer.structure and structure_timer.structure.structure_type:
            item_type = structure_timer.structure.structure_type
        else:
            try:
                # Third Party
                from eve_sde.models import ItemType

                item_type = (
                    ItemType.objects.filter(name__iexact="Astrahus").first()
                    or ItemType.objects.first()
                )
            except Exception:
                pass

        eve_type = cls.get_or_create_eveuniverse_type(item_type)
        if not eve_type:
            logger.debug(
                "Cannot sync StructureTimer %s to structuretimers: structure type could not be resolved",
                structure_timer.id,
            )
            return None

        target_timer = None
        if structure_timer.structuretimers_timer_id:
            target_timer = timer_model.objects.filter(
                id=structure_timer.structuretimers_timer_id
            ).first()

        structure_name = (
            structure_timer.structure.name
            if structure_timer.structure
            else (solar_system.name if solar_system else "Structure")
        )
        location_details = (
            structure_timer.structure.location_details
            if structure_timer.structure
            else ""
        )
        owner_name = structure_timer.defender_display
        st_timer_type = cls.map_structure_timer_type_to_structuretimers(
            structure_timer.timer_type
        )

        objective = (
            getattr(timer_model.Objective, "FRIENDLY", "FR")
            if cls.is_structure_timer_friendly(structure_timer)
            else getattr(timer_model.Objective, "HOSTILE", "HO")
        )

        user = (
            structure_timer.created_by or User.objects.filter(is_superuser=True).first()
        )

        eve_character = None
        eve_corporation = None
        eve_alliance = None
        if user and hasattr(user, "profile"):
            try:
                # Alliance Auth
                from allianceauth.eveonline.models import (
                    EveAllianceInfo,
                    EveCorporationInfo,
                )

                main_char = getattr(user.profile, "main_character", None)
                if main_char:
                    eve_character = main_char
                    if getattr(main_char, "corporation_id", None):
                        eve_corporation = EveCorporationInfo.objects.filter(
                            corporation_id=main_char.corporation_id
                        ).first()
                    if getattr(main_char, "alliance_id", None):
                        eve_alliance = EveAllianceInfo.objects.filter(
                            alliance_id=main_char.alliance_id
                        ).first()
            except Exception:
                pass

        defaults = {
            "eve_solar_system": eve_system,
            "structure_type": eve_type,
            "structure_name": structure_name[:254],
            "location_details": location_details[:254],
            "owner_name": (owner_name or "Unknown")[:254],
            "timer_type": st_timer_type,
            "objective": objective,
            "visibility": getattr(timer_model.Visibility, "UNRESTRICTED", "UN"),
            "date": structure_timer.timer_datetime,
            "details_notes": structure_timer.notes or "",
            "user": user,
            "is_important": False,
            "is_opsec": False,
        }
        if eve_character and hasattr(timer_model, "eve_character"):
            defaults["eve_character"] = eve_character
        if eve_corporation and hasattr(timer_model, "eve_corporation"):
            defaults["eve_corporation"] = eve_corporation
        if eve_alliance and hasattr(timer_model, "eve_alliance"):
            defaults["eve_alliance"] = eve_alliance

        if target_timer:
            for field, val in defaults.items():
                setattr(target_timer, field, val)
            target_timer.save()
            return target_timer
        else:
            new_timer = timer_model.objects.create(**defaults)
            return new_timer

    @classmethod
    def _sync_to_timerboard_model(cls, structure_timer: Any) -> Any | None:
        """Internal helper to create or update an Alliance Auth Timerboard Timer model"""
        timer_model = cls.get_timerboard_timer_model()
        if not timer_model:
            return None

        solar_system_name = (
            structure_timer.solar_system.name
            if structure_timer.solar_system
            else (
                structure_timer.structure.solar_system.name
                if structure_timer.structure and structure_timer.structure.solar_system
                else "Unknown"
            )
        )

        target_timer = None
        if structure_timer.timerboard_timer_id:
            target_timer = timer_model.objects.filter(
                id=structure_timer.timerboard_timer_id
            ).first()

        struct_choice = cls.map_structure_to_timerboard_choice(
            structure_timer.structure,
            fallback_name=structure_timer.target_name,
        )
        tb_timer_type = cls.map_structure_timer_type_to_timerboard(
            structure_timer.timer_type
        )
        location_details = (
            structure_timer.structure.location_details
            if structure_timer.structure
            else ""
        )
        owner_name = structure_timer.defender_display
        user = (
            structure_timer.created_by or User.objects.filter(is_superuser=True).first()
        )

        # Resolve corporation for Timerboard if eve_corp field exists
        eve_corp = None
        if hasattr(timer_model, "eve_corp") or any(
            f.name == "eve_corp" for f in timer_model._meta.fields
        ):
            try:
                # Alliance Auth
                from allianceauth.eveonline.models import EveCorporationInfo

                if user and hasattr(user, "profile"):
                    main_char = getattr(user.profile, "main_character", None)
                    if main_char and main_char.corporation_id:
                        eve_corp = EveCorporationInfo.objects.filter(
                            corporation_id=main_char.corporation_id
                        ).first()
                if (
                    not eve_corp
                    and structure_timer.structure
                    and structure_timer.structure.corporation
                ):
                    eve_corp = EveCorporationInfo.objects.filter(
                        corporation_id=structure_timer.structure.corporation.corporation_id
                    ).first()
                if not eve_corp:
                    eve_corp = EveCorporationInfo.objects.first()
                if not eve_corp:
                    eve_corp, _ = EveCorporationInfo.objects.get_or_create(
                        corporation_id=1000001,
                        defaults={
                            "corporation_name": "Hostile Recon Corp",
                            "corporation_ticker": "HREC",
                            "member_count": 1,
                        },
                    )
            except Exception as c_exc:
                logger.debug(
                    "Failed resolving EveCorporationInfo for timerboard: %s", c_exc
                )

        objective = (
            "Friendly"
            if cls.is_structure_timer_friendly(structure_timer)
            else "Hostile"
        )

        defaults = {
            "system": solar_system_name,
            "structure": struct_choice,
            "planet_moon": location_details[:254] if location_details else "",
            "location_details": location_details[:254] if location_details else "",
            "details": structure_timer.notes or "",
            "notes": structure_timer.notes or "",
            "owner_name": owner_name or "Unknown",
            "timer_type": tb_timer_type,
            "objective": objective,
            "eve_time": structure_timer.timer_datetime,
            "user": user,
            "eve_corp": eve_corp,
            "corp_timer": False,
            "visibility": (
                getattr(timer_model.Visibility, "UNRESTRICTED", "UN")
                if hasattr(timer_model, "Visibility")
                else "UN"
            ),
            "important": False,
            "restricted_to": "",
        }

        if target_timer:
            for field, val in defaults.items():
                if hasattr(target_timer, field):
                    setattr(target_timer, field, val)
            target_timer.save()
            return target_timer
        else:
            create_kwargs = {
                k: v
                for k, v in defaults.items()
                if hasattr(timer_model, k)
                or any(f.name == k for f in timer_model._meta.fields)
            }
            new_timer = timer_model.objects.create(**create_kwargs)
            return new_timer

    # -------------------------------------------------------------------------
    # Bidirectional Sync: External Boards -> Hostile Intel
    # -------------------------------------------------------------------------

    @classmethod
    def sync_structuretimers_to_structure_timer(cls, st_timer: Any) -> Any | None:
        """
        Synchronizes a change from aa-structuretimers to Hostile Intel StructureTimer.
        Uses marker hashing to distinguish backend-initiated changes from external untracked changes.
        """
        # AA Hostile Intel
        from hostile.models import (
            HostileAlliance,
            HostileCorporation,
            StructureTimer,
        )

        with sync_operation("structuretimers_timer", st_timer.id) as acquired:
            if not acquired:
                return None

            incoming_hash = cls.compute_structuretimers_timer_hash(st_timer)

            # Check if linked StructureTimer already exists
            structure_timer = StructureTimer.objects.filter(
                structuretimers_timer_id=st_timer.id
            ).first()

            if structure_timer and structure_timer.last_sync_hash == incoming_hash:
                # Backend already in perfect parity (or change originated here)
                return structure_timer

            # Resolve solar system
            solar_system = cls.get_or_create_eve_sde_solar_system(
                st_timer.eve_solar_system
            )

            # Resolve item type
            item_type = cls.get_or_create_eve_sde_item_type(st_timer.structure_type)

            # Resolve or create HostileStructure
            structure_name = st_timer.structure_name or (
                f"{st_timer.structure_type.name} ({solar_system.name})"
                if st_timer.structure_type and solar_system
                else "Hostile Structure"
            )

            st_objective = getattr(st_timer, "objective", None)
            is_friendly = st_objective in [
                "FR",
                getattr(getattr(st_timer, "Objective", None), "FRIENDLY", "FR"),
                "Friendly",
                "friendly",
            ]

            owner_raw = (st_timer.owner_name or "").strip()
            alliance = None
            corp = None
            owner_ticker = owner_raw

            # AA Hostile Intel
            from hostile.services.entity_resolver import EntityResolver
            from hostile.services.intel_manager import IntelManager

            if owner_raw and owner_raw.lower() not in ["unknown", "none", ""]:
                resolved_alliance = EntityResolver.get_or_create_alliance(owner_raw)
                if resolved_alliance:
                    alliance = resolved_alliance
                    if is_friendly and not alliance.is_friendly:
                        alliance.is_friendly = True
                        alliance.save(update_fields=["is_friendly"])
                    owner_ticker = (
                        resolved_alliance.ticker or resolved_alliance.alliance_name
                    )
                else:
                    resolved_corp = EntityResolver.get_or_create_corporation(owner_raw)
                    if resolved_corp:
                        corp = resolved_corp
                        if resolved_corp.alliance:
                            alliance = resolved_corp.alliance
                        if is_friendly:
                            if not corp.is_friendly:
                                corp.is_friendly = True
                                corp.save(update_fields=["is_friendly"])
                            if alliance and not alliance.is_friendly:
                                alliance.is_friendly = True
                                alliance.save(update_fields=["is_friendly"])
                        owner_ticker = (
                            resolved_corp.ticker or resolved_corp.corporation_name
                        )
            elif is_friendly:
                if getattr(st_timer, "eve_alliance", None):
                    alliance, _ = HostileAlliance.objects.get_or_create(
                        alliance_id=st_timer.eve_alliance.alliance_id,
                        defaults={
                            "alliance_name": st_timer.eve_alliance.alliance_name,
                            "ticker": getattr(
                                st_timer.eve_alliance, "alliance_ticker", "ALLY"
                            ),
                            "is_friendly": True,
                        },
                    )
                    if not alliance.is_friendly:
                        alliance.is_friendly = True
                        alliance.save(update_fields=["is_friendly"])
                    owner_ticker = (
                        getattr(st_timer.eve_alliance, "alliance_ticker", "")
                        or st_timer.eve_alliance.alliance_name
                    )
                if getattr(st_timer, "eve_corporation", None):
                    corp, _ = HostileCorporation.objects.get_or_create(
                        corporation_id=st_timer.eve_corporation.corporation_id,
                        defaults={
                            "corporation_name": st_timer.eve_corporation.corporation_name,
                            "ticker": getattr(
                                st_timer.eve_corporation, "corporation_ticker", "CORP"
                            ),
                            "is_friendly": True,
                        },
                    )
                    if not corp.is_friendly:
                        corp.is_friendly = True
                        corp.save(update_fields=["is_friendly"])
                    if not owner_ticker:
                        owner_ticker = (
                            getattr(st_timer.eve_corporation, "corporation_ticker", "")
                            or st_timer.eve_corporation.corporation_name
                        )

            hostile_structure, _ = IntelManager.get_or_create_structure(
                name=structure_name,
                solar_system=solar_system,
                structure_type=item_type,
                defaults={
                    "alliance": alliance,
                    "corporation": corp,
                    "owner_ticker": (owner_ticker or "")[:32],
                    "location_details": st_timer.location_details or "",
                },
                user=st_timer.user,
            )

            if not is_friendly:
                updated_struct = False
                if hostile_structure.alliance and cls.is_entity_friendly(
                    alliance_id=hostile_structure.alliance.alliance_id
                ):
                    hostile_structure.alliance = None
                    updated_struct = True
                if hostile_structure.corporation and cls.is_entity_friendly(
                    corp_id=hostile_structure.corporation.corporation_id
                ):
                    hostile_structure.corporation = None
                    updated_struct = True
                if updated_struct:
                    hostile_structure.save()

            mapped_timer_type = cls.map_structuretimers_type_to_structure_timer(
                st_timer.timer_type
            )
            timer_datetime = st_timer.date or timezone.now()
            if isinstance(timer_datetime, str):
                # Django
                from django.utils.dateparse import parse_datetime

                parsed = parse_datetime(timer_datetime)
                if parsed:
                    timer_datetime = parsed

            if not structure_timer:
                # Match by structure and datetime close match if not linked by ID
                structure_timer = StructureTimer.objects.filter(
                    structure=hostile_structure,
                    timer_type=mapped_timer_type,
                ).first()

            if not structure_timer:
                structure_timer = StructureTimer.objects.create(
                    structure=hostile_structure,
                    solar_system=solar_system,
                    timer_type=mapped_timer_type,
                    timer_datetime=timer_datetime,
                    notes=st_timer.details_notes or "",
                    created_by=st_timer.user,
                    structuretimers_timer_id=st_timer.id,
                    last_sync_hash=incoming_hash,
                )
            else:
                structure_timer.structure = hostile_structure
                structure_timer.solar_system = solar_system
                structure_timer.timer_type = mapped_timer_type
                structure_timer.timer_datetime = timer_datetime
                structure_timer.notes = st_timer.details_notes or ""
                structure_timer.structuretimers_timer_id = st_timer.id
                structure_timer.last_sync_hash = incoming_hash
                structure_timer.save()

            # Ensure Timerboard is also synced for full cross-app parity
            if cls.is_timerboard_sync_enabled():
                try:
                    tb_obj = cls._sync_to_timerboard_model(structure_timer)
                    if tb_obj and structure_timer.timerboard_timer_id != tb_obj.id:
                        structure_timer.timerboard_timer_id = tb_obj.id
                        structure_timer.save(update_fields=["timerboard_timer_id"])
                except Exception as exc:
                    logger.debug("Failed secondary sync to timerboard: %s", exc)

            return structure_timer

    @classmethod
    def sync_timerboard_to_structure_timer(cls, tb_timer: Any) -> Any | None:
        """
        Synchronizes a change from Alliance Auth Timerboard to Hostile Intel StructureTimer.
        Uses marker hashing to detect untracked external modifications.
        """
        # AA Hostile Intel
        from hostile.models import (
            HostileCorporation,
            StructureTimer,
        )

        with sync_operation("timerboard_timer", tb_timer.id) as acquired:
            if not acquired:
                return None

            incoming_hash = cls.compute_timerboard_timer_hash(tb_timer)

            structure_timer = StructureTimer.objects.filter(
                timerboard_timer_id=tb_timer.id
            ).first()

            if structure_timer and structure_timer.last_sync_hash == incoming_hash:
                return structure_timer

            # Resolve solar system by name
            # Third Party
            from eve_sde.models import ItemType, SolarSystem

            solar_system = SolarSystem.objects.filter(
                name__iexact=tb_timer.system
            ).first()

            # Resolve structure type from choice string
            item_type = ItemType.objects.filter(
                name__icontains=tb_timer.structure
            ).first()

            tb_objective = getattr(tb_timer, "objective", "")
            is_friendly = str(tb_objective).lower() == "friendly"

            owner_raw = getattr(tb_timer, "owner_name", "") or ""
            alliance = None
            corp = None
            owner_ticker = owner_raw

            # AA Hostile Intel
            from hostile.services.entity_resolver import EntityResolver

            if owner_raw and owner_raw.lower() not in ["unknown", "none", ""]:
                resolved_alliance = EntityResolver.get_or_create_alliance(owner_raw)
                if resolved_alliance:
                    alliance = resolved_alliance
                    if is_friendly and not alliance.is_friendly:
                        alliance.is_friendly = True
                        alliance.save(update_fields=["is_friendly"])
                    owner_ticker = (
                        resolved_alliance.ticker or resolved_alliance.alliance_name
                    )
                else:
                    resolved_corp = EntityResolver.get_or_create_corporation(owner_raw)
                    if resolved_corp:
                        corp = resolved_corp
                        if resolved_corp.alliance:
                            alliance = resolved_corp.alliance
                        if is_friendly:
                            if not corp.is_friendly:
                                corp.is_friendly = True
                                corp.save(update_fields=["is_friendly"])
                            if alliance and not alliance.is_friendly:
                                alliance.is_friendly = True
                                alliance.save(update_fields=["is_friendly"])
                        owner_ticker = (
                            resolved_corp.ticker or resolved_corp.corporation_name
                        )
            elif is_friendly and getattr(tb_timer, "eve_corp", None):
                corp, _ = HostileCorporation.objects.get_or_create(
                    corporation_id=tb_timer.eve_corp.corporation_id,
                    defaults={
                        "corporation_name": tb_timer.eve_corp.corporation_name,
                        "ticker": getattr(
                            tb_timer.eve_corp, "corporation_ticker", "CORP"
                        ),
                        "is_friendly": True,
                    },
                )
                if not corp.is_friendly:
                    corp.is_friendly = True
                    corp.save(update_fields=["is_friendly"])
                owner_ticker = (
                    getattr(tb_timer.eve_corp, "corporation_ticker", "")
                    or tb_timer.eve_corp.corporation_name
                )

            location_details = (
                getattr(tb_timer, "planet_moon", "")
                or getattr(tb_timer, "location_details", "")
                or ""
            )
            notes = (
                getattr(tb_timer, "details", "") or getattr(tb_timer, "notes", "") or ""
            )

            struct_name = f"{tb_timer.system} {tb_timer.structure}"
            # AA Hostile Intel
            from hostile.services.intel_manager import IntelManager

            hostile_structure, _ = IntelManager.get_or_create_structure(
                name=struct_name,
                solar_system=solar_system,
                structure_type=item_type,
                defaults={
                    "alliance": alliance,
                    "corporation": corp,
                    "owner_ticker": (owner_ticker or "")[:32],
                    "location_details": location_details,
                },
                user=tb_timer.user,
            )

            if not is_friendly:
                updated_struct = False
                if hostile_structure.alliance and cls.is_entity_friendly(
                    alliance_id=hostile_structure.alliance.alliance_id
                ):
                    hostile_structure.alliance = None
                    updated_struct = True
                if hostile_structure.corporation and cls.is_entity_friendly(
                    corp_id=hostile_structure.corporation.corporation_id
                ):
                    hostile_structure.corporation = None
                    updated_struct = True
                if updated_struct:
                    hostile_structure.save()

            mapped_timer_type = cls.map_timerboard_type_to_structure_timer(
                tb_timer.timer_type
            )
            timer_datetime = tb_timer.eve_time or timezone.now()
            if isinstance(timer_datetime, str):
                # Django
                from django.utils.dateparse import parse_datetime

                parsed = parse_datetime(timer_datetime)
                if parsed:
                    timer_datetime = parsed

            if not structure_timer:
                structure_timer = StructureTimer.objects.filter(
                    structure=hostile_structure,
                    timer_type=mapped_timer_type,
                ).first()

            if not structure_timer:
                structure_timer = StructureTimer.objects.create(
                    structure=hostile_structure,
                    solar_system=solar_system,
                    timer_type=mapped_timer_type,
                    timer_datetime=tb_timer.eve_time,
                    notes=notes,
                    created_by=tb_timer.user,
                    timerboard_timer_id=tb_timer.id,
                    last_sync_hash=incoming_hash,
                )
            else:
                structure_timer.structure = hostile_structure
                structure_timer.solar_system = solar_system
                structure_timer.timer_type = mapped_timer_type
                structure_timer.timer_datetime = tb_timer.eve_time
                structure_timer.notes = notes
                structure_timer.timerboard_timer_id = tb_timer.id
                structure_timer.last_sync_hash = incoming_hash
                structure_timer.save()

            # Also push to aa-structuretimers if available
            if cls.is_structuretimers_sync_enabled():
                try:
                    st_obj = cls._sync_to_structuretimers_model(structure_timer)
                    if st_obj and structure_timer.structuretimers_timer_id != st_obj.id:
                        structure_timer.structuretimers_timer_id = st_obj.id
                        structure_timer.save(update_fields=["structuretimers_timer_id"])
                except Exception as exc:
                    logger.debug("Failed secondary sync to structuretimers: %s", exc)

            return structure_timer

    # -------------------------------------------------------------------------
    # Deletion Synchronization
    # -------------------------------------------------------------------------

    @classmethod
    def delete_external_timers_for_structure_timer(cls, structure_timer: Any) -> None:
        """Deletes linked external timers across all timerboards when StructureTimer is deleted"""
        if structure_timer.structuretimers_timer_id:
            st_model = cls.get_structuretimers_timer_model()
            if st_model:
                with sync_operation(
                    "structuretimers_timer", structure_timer.structuretimers_timer_id
                ):
                    st_model.objects.filter(
                        id=structure_timer.structuretimers_timer_id
                    ).delete()

        if structure_timer.timerboard_timer_id:
            tb_model = cls.get_timerboard_timer_model()
            if tb_model:
                with sync_operation(
                    "timerboard_timer", structure_timer.timerboard_timer_id
                ):
                    tb_model.objects.filter(
                        id=structure_timer.timerboard_timer_id
                    ).delete()

    @classmethod
    def delete_structure_timer_for_structuretimers(cls, st_timer: Any) -> None:
        """Deletes or unlinks StructureTimer when an aa-structuretimers Timer is deleted"""
        # AA Hostile Intel
        from hostile.models import StructureTimer

        linked_timers = StructureTimer.objects.filter(
            structuretimers_timer_id=st_timer.id
        )
        for timer in linked_timers:
            with sync_operation("hostile_structure_timer", timer.id):
                # Also delete timerboard counterpart if linked
                if timer.timerboard_timer_id:
                    tb_model = cls.get_timerboard_timer_model()
                    if tb_model:
                        with sync_operation(
                            "timerboard_timer", timer.timerboard_timer_id
                        ):
                            tb_model.objects.filter(
                                id=timer.timerboard_timer_id
                            ).delete()
                timer.delete()

    @classmethod
    def delete_structure_timer_for_timerboard(cls, tb_timer: Any) -> None:
        """Deletes or unlinks StructureTimer when a Timerboard Timer is deleted"""
        # AA Hostile Intel
        from hostile.models import StructureTimer

        linked_timers = StructureTimer.objects.filter(timerboard_timer_id=tb_timer.id)
        for timer in linked_timers:
            with sync_operation("hostile_structure_timer", timer.id):
                if timer.structuretimers_timer_id:
                    st_model = cls.get_structuretimers_timer_model()
                    if st_model:
                        with sync_operation(
                            "structuretimers_timer", timer.structuretimers_timer_id
                        ):
                            st_model.objects.filter(
                                id=timer.structuretimers_timer_id
                            ).delete()
                timer.delete()

    # -------------------------------------------------------------------------
    # Bulk Reconciliation / Self-Correcting Database Check
    # -------------------------------------------------------------------------

    @classmethod
    def sync_all_timers(cls) -> dict[str, int]:
        """
        Executes a rapid, lightweight database scan across Hostile Intel, aa-structuretimers,
        and Timerboard to self-correct any drift or missed signals.
        """
        # AA Hostile Intel
        from hostile.models import StructureTimer

        stats = {
            "synced_to_structuretimers": 0,
            "synced_from_structuretimers": 0,
            "synced_to_timerboard": 0,
            "synced_from_timerboard": 0,
            "corrected": 0,
        }

        if not cls.is_sync_enabled():
            return stats

        now = timezone.now()
        active_window = now - timezone.timedelta(hours=24)

        # 1. Check aa-structuretimers for external creations or drift first
        if cls.is_structuretimers_sync_enabled():
            st_model = cls.get_structuretimers_timer_model()
            if st_model:
                st_active = st_model.objects.filter(date__gte=active_window)
                for st_timer in st_active:
                    st_hash = cls.compute_structuretimers_timer_hash(st_timer)
                    linked = StructureTimer.objects.filter(
                        structuretimers_timer_id=st_timer.id
                    ).first()

                    if not linked or linked.last_sync_hash != st_hash:
                        cls.sync_structuretimers_to_structure_timer(st_timer)
                        stats["synced_from_structuretimers"] += 1
                        stats["corrected"] += 1

        # 2. Check Timerboard for external creations or drift
        if cls.is_timerboard_sync_enabled():
            tb_model = cls.get_timerboard_timer_model()
            if tb_model:
                tb_active = tb_model.objects.filter(eve_time__gte=active_window)
                for tb_timer in tb_active:
                    tb_hash = cls.compute_timerboard_timer_hash(tb_timer)
                    linked = StructureTimer.objects.filter(
                        timerboard_timer_id=tb_timer.id
                    ).first()

                    if not linked or linked.last_sync_hash != tb_hash:
                        cls.sync_timerboard_to_structure_timer(tb_timer)
                        stats["synced_from_timerboard"] += 1
                        stats["corrected"] += 1

        # 3. Check all upcoming Hostile Intel StructureTimers and ensure external parity
        hostile_timers = StructureTimer.objects.filter(
            is_sov_campaign=False,
            timer_datetime__gte=active_window,
        ).exclude(timer_type__in=["SOV_IHUB", "SOV_TCU"])

        for h_timer in hostile_timers:
            current_hash = cls.compute_structure_timer_hash(h_timer)
            needs_sync = (
                h_timer.last_sync_hash != current_hash
                or (
                    cls.is_structuretimers_sync_enabled()
                    and not h_timer.structuretimers_timer_id
                )
                or (
                    cls.is_timerboard_sync_enabled() and not h_timer.timerboard_timer_id
                )
            )

            if needs_sync:
                res = cls.sync_structure_timer_to_all(h_timer)
                if "structuretimers" in res:
                    stats["synced_to_structuretimers"] += 1
                if "timerboard" in res:
                    stats["synced_to_timerboard"] += 1
                stats["corrected"] += 1

        return stats


# Backward compatibility alias
TimerboardSyncService = TimerSyncService
