"""
Hostile Intelligence views, endpoints, and modals
"""

# Standard Library
from datetime import timedelta
import hashlib
import json
from typing import Any, Dict, List, Optional

# Alliance Auth
from allianceauth.services.hooks import get_extension_logger

# Django
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.cache import cache
from django.core.handlers.wsgi import WSGIRequest
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

# AA Hostile Intel
from hostile.forms import (
    CoalitionAlliancesForm,
    DoctrineFitForm,
    HostileAllianceForm,
    HostileCorporationForm,
    HostileDoctrineForm,
    HostilePilotDossierForm,
    HostileStagingSystemForm,
    HostileStructureForm,
    LocalThreatScanForm,
    ReinforcementCalculatorForm,
    SmartPasteForm,
    StructureTimerForm,
    SystemObservationForm,
)
from hostile.models import (
    DoctrineFit,
    HostileAlliance,
    HostileCorporation,
    HostileDoctrine,
    HostilePilotDossier,
    HostileStagingSystem,
    HostileStructure,
    IntelAuditLog,
    LocalThreatScan,
    SolarSystem,
    StructureFitting,
    StructureModule,
    StructureTimer,
    SystemObservation,
    SystemTag,
)
from hostile.parsers.classifier import IntelFormat, parse_intel_paste
from hostile.parsers.dscan import DScanParser
from hostile.parsers.eft import EFTParser, StructureFittingParser
from hostile.parsers.local import LocalThreatParser
from hostile.services.esi_client import esi_client
from hostile.services.intel_manager import IntelManager
from hostile.services.map_routing import MapRoutingService
from hostile.services.reinforcement_calculator import (
    LocationSecurity,
    PowerState,
    ReinforcementCalculator,
    StructureCategory,
    TimerStage,
)
from hostile.services.sov_engine import SovAnalysisEngine
from hostile.services.zkill_client import zkill_client

logger = get_extension_logger(__name__)


def user_can_edit_object(user, obj) -> bool:
    """Returns True if user is a manager or the original author of the object."""
    if not user.is_authenticated:
        return False
    if user.has_perm("hostile.manage_intel"):
        return True
    return getattr(obj, "created_by_id", None) == user.id


@login_required
@permission_required("hostile.basic_access")
def index(request: WSGIRequest) -> HttpResponse:
    """Main intelligence dashboard and tactical command center with live triage, urgency KPI deck, staging proximity, and operational threat feeds"""
    now = timezone.now()
    now_plus_6h = now + timedelta(hours=6)
    now_plus_24h = now + timedelta(hours=24)
    past_7d = now - timedelta(days=7)
    past_48h = now - timedelta(hours=48)
    past_24h = now - timedelta(hours=24)

    # 1. Resolve Home Staging / Origin Solar System
    origin_param = (
        request.GET.get("origin")
        or request.GET.get("staging")
        or ""
    ).strip()

    origin_system = None
    if origin_param:
        try:
            origin_id = int(origin_param)
            origin_system = SolarSystem.objects.filter(id=origin_id).first()
            if origin_system:
                request.session["hostile_home_staging"] = origin_id
        except (ValueError, TypeError):
            pass

    if not origin_system and "hostile_home_staging" in request.session:
        saved_id = request.session.get("hostile_home_staging")
        if saved_id:
            origin_system = SolarSystem.objects.filter(id=saved_id).first()

    # Fallback to primary hostile staging, or first staging, or trade hub
    if not origin_system:
        primary_staging = (
            HostileStagingSystem.objects.filter(is_primary=True)
            .select_related("solar_system")
            .first()
        )
        if primary_staging and primary_staging.solar_system:
            origin_system = primary_staging.solar_system
        else:
            first_staging = (
                HostileStagingSystem.objects.select_related("solar_system").first()
            )
            if first_staging and first_staging.solar_system:
                origin_system = first_staging.solar_system

    # Reference solar systems for origin staging dropdown
    ref_filter = (
        Q(id__in=HostileStagingSystem.objects.values_list("solar_system_id", flat=True))
        | Q(
            id__in=HostileStructure.objects.active().values_list(
                "solar_system_id", flat=True
            )
        )
        | Q(
            id__in=StructureTimer.objects.upcoming().values_list(
                "solar_system_id", flat=True
            )
        )
        | Q(hub=True)
    )
    if origin_system:
        ref_filter |= Q(id=origin_system.id)

    reference_systems = (
        SolarSystem.objects.filter(ref_filter)
        .distinct()
        .select_related("constellation__region")
        .order_by("name")
    )

    # 2. Urgency KPI Metrics (Temporal & Actionable)
    stats = {
        "timers_6h_count": StructureTimer.objects.upcoming()
        .filter(timer_datetime__lte=now_plus_6h)
        .count(),
        "timers_24h_count": StructureTimer.objects.upcoming()
        .filter(timer_datetime__lte=now_plus_24h)
        .count(),
        "active_camps_count": SystemObservation.objects.active_recent(hours=24)
        .filter(Q(is_pinned=True) | Q(threat_level__in=["HIGH", "EXTREME"]))
        .count(),
        "active_sov_contests_count": StructureTimer.objects.sov_campaigns()
        .filter(is_concluded=False, timer_datetime__lte=now, timer_datetime__gte=past_24h)
        .count(),
        "hostile_hotspots_count": SystemObservation.objects.filter(
            created_at__gte=past_7d
        )
        .values("solar_system_id")
        .distinct()
        .count(),
        "cynos_count": HostilePilotDossier.objects.cynos().count(),
        "supers_count": HostilePilotDossier.objects.supers_and_titans().count(),
        "structures_count": HostileStructure.objects.active().count(),
        "stagings_count": HostileStagingSystem.objects.count(),
        "doctrines_count": HostileDoctrine.objects.count(),
        "timers_count": StructureTimer.objects.upcoming().count(),
        "observations_count": SystemObservation.objects.active_recent(hours=48).count(),
    }

    # 3. Operational Threats (Left Column)
    upcoming_timers = list(
        StructureTimer.objects.upcoming()
        .hostile_only()
        .select_related(
            "structure__structure_type",
            "structure__alliance",
            "structure__corporation",
            "solar_system__constellation__region",
        )[:8]
    )

    pinned_observations = list(
        SystemObservation.objects.pinned()
        .select_related("solar_system__constellation__region", "created_by", "local_scan")
        .prefetch_related("tags")
    )
    recent_observations = list(
        SystemObservation.objects.active_recent(hours=48)
        .exclude(is_pinned=True)
        .select_related("solar_system__constellation__region", "created_by", "local_scan")
        .prefetch_related("tags")[:6]
    )

    recent_threat_scans = list(
        LocalThreatScan.objects.all()
        .select_related("solar_system__constellation__region", "created_by")
        .order_by("-created_at")[:4]
    )

    # 4. Strategic Landscape (Right Column)
    # Hostile Hotspots: aggregate activity in the past 7 days
    hotspot_systems_dict = {}

    for struct in HostileStructure.objects.active().select_related(
        "solar_system__constellation__region", "alliance"
    ):
        sys = struct.solar_system
        if not sys:
            continue
        if sys.id not in hotspot_systems_dict:
            hotspot_systems_dict[sys.id] = {
                "system": sys,
                "structures_count": 0,
                "camps_count": 0,
                "is_staging": False,
                "alliances": set(),
                "score": 0,
            }
        hotspot_systems_dict[sys.id]["structures_count"] += 1
        hotspot_systems_dict[sys.id]["score"] += 3
        if struct.alliance and struct.alliance.ticker:
            hotspot_systems_dict[sys.id]["alliances"].add(struct.alliance.ticker)

    for obs in SystemObservation.objects.filter(
        created_at__gte=past_7d
    ).select_related("solar_system__constellation__region"):
        sys = obs.solar_system
        if not sys:
            continue
        if sys.id not in hotspot_systems_dict:
            hotspot_systems_dict[sys.id] = {
                "system": sys,
                "structures_count": 0,
                "camps_count": 0,
                "is_staging": False,
                "alliances": set(),
                "score": 0,
            }
        hotspot_systems_dict[sys.id]["camps_count"] += 1
        hotspot_systems_dict[sys.id]["score"] += (
            2 if obs.threat_level in ["HIGH", "EXTREME"] else 1
        )

    for staging in HostileStagingSystem.objects.all().select_related(
        "solar_system__constellation__region", "alliance"
    ):
        sys = staging.solar_system
        if not sys:
            continue
        if sys.id not in hotspot_systems_dict:
            hotspot_systems_dict[sys.id] = {
                "system": sys,
                "structures_count": 0,
                "camps_count": 0,
                "is_staging": True,
                "alliances": set(),
                "score": 5,
            }
        else:
            hotspot_systems_dict[sys.id]["is_staging"] = True
            hotspot_systems_dict[sys.id]["score"] += 5
        if staging.alliance and staging.alliance.ticker:
            hotspot_systems_dict[sys.id]["alliances"].add(staging.alliance.ticker)

    sorted_hotspots = sorted(
        hotspot_systems_dict.values(), key=lambda h: h["score"], reverse=True
    )[:5]

    active_sov_campaigns = list(
        StructureTimer.objects.sov_campaigns()
        .filter(is_concluded=False, timer_datetime__lte=now, timer_datetime__gte=past_24h)
        .select_related("solar_system__constellation__region", "structure__alliance")
        .order_by("-timer_datetime")[:4]
    )

    recent_stagings = list(
        HostileStagingSystem.objects.all()
        .select_related(
            "solar_system__constellation__region", "alliance", "corporation"
        )
        .prefetch_related("doctrines")
        .order_by("-is_primary", "-updated_at")[:4]
    )

    recent_doctrines = list(
        HostileDoctrine.objects.all()
        .select_related("alliance", "corporation")
        .prefetch_related("example_fits", "stagings__solar_system")
        .order_by("-updated_at")[:4]
    )

    # 5. Distance & Jump Calculations from Origin Staging
    if origin_system:
        all_dest_ids = set()
        for t in upcoming_timers:
            if t.solar_system_id:
                all_dest_ids.add(t.solar_system_id)
        for obs in pinned_observations + recent_observations:
            if obs.solar_system_id:
                all_dest_ids.add(obs.solar_system_id)
        for scan in recent_threat_scans:
            if scan.solar_system_id:
                all_dest_ids.add(scan.solar_system_id)
        for h in sorted_hotspots:
            all_dest_ids.add(h["system"].id)
        for sov in active_sov_campaigns:
            if sov.solar_system_id:
                all_dest_ids.add(sov.solar_system_id)
        for stg in recent_stagings:
            if stg.solar_system_id:
                all_dest_ids.add(stg.solar_system_id)

        routes_summary = MapRoutingService.get_batch_routes_summary(
            origin_system.id, list(all_dest_ids), flag="shortest"
        )

        for t in upcoming_timers:
            t.distance_data = routes_summary.get(t.solar_system_id)
        for obs in pinned_observations + recent_observations:
            obs.distance_data = routes_summary.get(obs.solar_system_id)
        for scan in recent_threat_scans:
            scan.distance_data = routes_summary.get(scan.solar_system_id)
        for h in sorted_hotspots:
            h["distance_data"] = routes_summary.get(h["system"].id)
        for sov in active_sov_campaigns:
            sov.distance_data = routes_summary.get(sov.solar_system_id)
        for stg in recent_stagings:
            stg.distance_data = routes_summary.get(stg.solar_system_id)

    # 6. Time Decay for Observations
    for obs in pinned_observations + recent_observations:
        ref_time = obs.display_time_seen
        age_seconds = (now - ref_time).total_seconds()
        if age_seconds < 3600:
            obs.decay_badge = "FRESH (<1h)"
            obs.decay_badge_class = "badge-subtle-teal"
        elif age_seconds < 14400:
            obs.decay_badge = "ACTIVE (<4h)"
            obs.decay_badge_class = "badge-subtle-warning"
        else:
            obs.decay_badge = "LOGGED"
            obs.decay_badge_class = "badge-subtle-secondary"

    # 7. Unified Real-Time Intel Stream Feed
    intel_feed = []
    for obs in (
        SystemObservation.objects.select_related("solar_system", "created_by").order_by(
            "-created_at"
        )[:10]
    ):
        intel_feed.append(
            {
                "type": "OBSERVATION",
                "icon": "fa-crosshairs text-warning",
                "title": f"Tactical Sitrep: {obs.solar_system.name if obs.solar_system else 'Unknown'}",
                "system": obs.solar_system.name if obs.solar_system else "Unknown",
                "badge": obs.threat_level,
                "badge_class": (
                    "badge-subtle-danger"
                    if obs.threat_level in ["HIGH", "EXTREME"]
                    else "badge-subtle-warning"
                ),
                "text": obs.observation_text,
                "user": obs.created_by.username if obs.created_by else "Intel Scout",
                "timestamp": obs.created_at,
                "link": reverse("hostile:systems"),
            }
        )

    for struct in (
        HostileStructure.objects.select_related(
            "solar_system", "structure_type", "created_by"
        ).order_by("-updated_at")[:8]
    ):
        intel_feed.append(
            {
                "type": "STRUCTURE",
                "icon": "fa-monument text-info",
                "title": f"Structure {struct.state.title()}: {struct.name}",
                "system": struct.solar_system.name if struct.solar_system else "Unknown",
                "badge": (
                    struct.structure_type.name
                    if struct.structure_type
                    else "Structure"
                ),
                "badge_class": "badge-subtle-info",
                "text": f"Owner: {struct.owner_name} | Core: {struct.core_status}",
                "user": struct.created_by.username if struct.created_by else "Scout",
                "timestamp": struct.updated_at,
                "link": reverse("hostile:structures"),
            }
        )

    for scan in (
        LocalThreatScan.objects.select_related("solar_system", "created_by").order_by(
            "-created_at"
        )[:6]
    ):
        intel_feed.append(
            {
                "type": "LOCAL_SCAN",
                "icon": "fa-user-secret text-danger",
                "title": f"Local Threat Scan: {scan.solar_system.name if scan.solar_system else 'Unknown'}",
                "system": scan.solar_system.name if scan.solar_system else "Unknown",
                "badge": f"{scan.threat_level} ({scan.pilot_count} pilots)",
                "badge_class": (
                    "badge-subtle-danger"
                    if scan.threat_level in ["HIGH", "CRITICAL"]
                    else "badge-subtle-warning"
                ),
                "text": f"Blops Drop: {scan.blops_drop_chance}% | Cap Drop: {scan.cap_drop_chance}%",
                "user": scan.created_by.username if scan.created_by else "Scout",
                "timestamp": scan.created_at,
                "link": reverse("hostile:threat_scan_detail", kwargs={"scan_id": scan.id}),
            }
        )

    intel_feed.sort(key=lambda x: x["timestamp"], reverse=True)
    intel_feed = intel_feed[:10]

    recent_structures = (
        HostileStructure.objects.active()
        .select_related("structure_type", "solar_system", "alliance")
        .order_by("-updated_at")[:6]
    )

    paste_form = SmartPasteForm()

    context = {
        "origin_system": origin_system,
        "selected_origin_id": origin_system.id if origin_system else None,
        "selected_origin_name": origin_system.name if origin_system else None,
        "reference_systems": reference_systems,
        "stats": stats,
        "upcoming_timers": upcoming_timers,
        "pinned_observations": pinned_observations,
        "recent_observations": recent_observations,
        "recent_threat_scans": recent_threat_scans,
        "sorted_hotspots": sorted_hotspots,
        "active_sov_campaigns": active_sov_campaigns,
        "recent_stagings": recent_stagings,
        "recent_doctrines": recent_doctrines,
        "recent_structures": recent_structures,
        "intel_feed": intel_feed,
        "paste_form": paste_form,
    }
    return render(request, "hostile/index.html", context)


@login_required
@permission_required("hostile.basic_access")
def structures_list(request: WSGIRequest) -> HttpResponse:
    """Structure registry view with filtering and fitting preview"""
    structures = (
        HostileStructure.objects.all()
        .select_related(
            "structure_type", "solar_system", "alliance", "corporation", "created_by"
        )
        .prefetch_related("fittings")
    )
    paste_form = SmartPasteForm()
    structure_form = HostileStructureForm()

    context = {
        "structures": structures,
        "paste_form": paste_form,
        "structure_form": structure_form,
    }
    return render(request, "hostile/structures.html", context)


@login_required
@permission_required("hostile.basic_access")
def structure_fitting_modal(request: WSGIRequest, structure_id: int) -> HttpResponse:
    """Corptools-inspired interactive fitting modal with high/med/low/rig/service slots and CCP type icons"""
    structure = get_object_or_404(
        HostileStructure.objects.select_related(
            "structure_type", "solar_system", "alliance", "corporation"
        ),
        id=structure_id,
    )
    latest_fitting = structure.latest_fitting

    grouped_modules = {
        "HIGH": [],
        "MED": [],
        "LOW": [],
        "RIG": [],
        "SERVICE": [],
        "CHARGE": [],
    }

    eft_export = ""
    if latest_fitting:
        modules = latest_fitting.modules.select_related("module_type").order_by(
            "slot_type", "slot_number"
        )
        for mod in modules:
            if mod.slot_type in grouped_modules:
                grouped_modules[mod.slot_type].append(mod)
        eft_export = latest_fitting.eft_format or EFTParser.export_eft(latest_fitting)

    context = {
        "structure": structure,
        "fitting": latest_fitting,
        "grouped_modules": grouped_modules,
        "eft_export": eft_export,
    }
    return render(request, "hostile/fitting_modal.html", context)


@login_required
@permission_required("hostile.basic_access")
def structure_fitting_save(request: WSGIRequest, structure_id: int) -> HttpResponse:
    """Updates or adds a structure fitting from ship scanner or EFT paste for a specific structure"""
    structure = get_object_or_404(HostileStructure, id=structure_id)
    if request.method == "POST":
        fitting_text = request.POST.get("fitting_text", "").strip()
        fit_name = (
            request.POST.get("fit_name", "").strip()
            or f"Scan {timezone.now().strftime('%Y-%m-%d %H:%M')}"
        )
        if fitting_text:
            parsed = StructureFittingParser.parse(fitting_text)
            fitting = StructureFitting.objects.create(
                structure=structure,
                name=fit_name,
                eft_format=fitting_text,
                created_by=request.user,
            )
            created_mods = 0
            for slot_name, mods in parsed["slots"].items():
                for idx, mod in enumerate(mods, 1):
                    if mod.get("item_type"):
                        StructureModule.objects.create(
                            fitting=fitting,
                            module_type=mod["item_type"],
                            slot_type=slot_name,
                            slot_number=idx,
                            is_online=True,
                        )
                        created_mods += 1

            if parsed.get("hull_item_type") and not structure.structure_type:
                structure.structure_type = parsed["hull_item_type"]
                structure.save(update_fields=["structure_type"])

            IntelManager.record_audit(
                request.user,
                "UPDATE",
                structure,
                f"Updated fitting for {structure.name} ({created_mods} modules)",
            )
            messages.success(
                request,
                f"Fitting updated for '{structure.name}' ({created_mods} modules loaded).",
            )
        else:
            messages.warning(request, "No fitting text provided.")
    return redirect("hostile:structures")


@login_required
@permission_required("hostile.basic_access")
def structure_add(request: WSGIRequest) -> HttpResponse:
    """Manually adds a new hostile structure and creates associated structure timers if anchoring/reinforced"""
    if request.method == "POST":
        form = HostileStructureForm(request.POST)
        if form.is_valid():
            struct = form.save(commit=False)
            struct.created_by = request.user
            if request.user.has_perm("hostile.manage_intel"):
                struct.is_verified = True
            struct.save()
            if struct.alliance:
                SovAnalysisEngine.aggregate_alliance_vulnerability_windows(
                    struct.alliance
                )
            IntelManager.record_audit(
                request.user, "CREATE", struct, "Manual structure creation."
            )

            # Check if a timer should be created (Anchoring, Armor, Hull, Reinforced)
            timer_datetime = form.cleaned_data.get("timer_datetime")
            timer_stage = form.cleaned_data.get("timer_stage")
            if not timer_stage:
                if struct.state == "ANCHORING":
                    timer_stage = "ANCHORING"
                elif struct.state in ["ARMOR", "HULL"]:
                    timer_stage = struct.state
                elif struct.state == "REINFORCED":
                    timer_stage = "ARMOR"

            if timer_datetime:
                timer = StructureTimer.objects.create(
                    structure=struct,
                    solar_system=struct.solar_system,
                    timer_type=timer_stage or "ANCHORING",
                    timer_datetime=timer_datetime,
                    notes=f"Auto-created timer on structure submission ({struct.state}).",
                    created_by=request.user,
                    is_verified=request.user.has_perm("hostile.manage_intel"),
                )
                IntelManager.record_audit(
                    request.user,
                    "CREATE",
                    timer,
                    f"Structure timer {timer.timer_type} created with structure.",
                )
                messages.success(
                    request,
                    f"Structure '{struct.name}' and its {timer.timer_type} timer were created successfully.",
                )
            else:
                messages.success(
                    request, f"Structure '{struct.name}' added successfully."
                )
            return redirect("hostile:structures")
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field.capitalize()}: {error}")
    return redirect("hostile:structures")


@login_required
@permission_required("hostile.basic_access")
def structure_edit(request: WSGIRequest, structure_id: int) -> HttpResponse:
    """Edits an existing hostile structure's properties, owner, and vulnerability profile"""
    structure = get_object_or_404(
        HostileStructure.objects.select_related(
            "solar_system", "alliance", "corporation", "structure_type"
        ),
        id=structure_id,
    )
    if not user_can_edit_object(request.user, structure):
        messages.error(request, "You do not have permission to edit this structure.")
        return redirect("hostile:structures")
    if request.method == "POST":
        old_alliance = structure.alliance
        form = HostileStructureForm(request.POST, instance=structure)
        if form.is_valid():
            struct = form.save()
            if struct.alliance:
                SovAnalysisEngine.aggregate_alliance_vulnerability_windows(
                    struct.alliance
                )
            if old_alliance and old_alliance != struct.alliance:
                SovAnalysisEngine.aggregate_alliance_vulnerability_windows(old_alliance)
            IntelManager.record_audit(
                request.user, "EDIT", struct, f"Structure '{struct.name}' edited."
            )

            timer_datetime = form.cleaned_data.get("timer_datetime")
            timer_stage = form.cleaned_data.get("timer_stage")
            if not timer_stage:
                if struct.state == "ANCHORING":
                    timer_stage = "ANCHORING"
                elif struct.state in ["ARMOR", "HULL"]:
                    timer_stage = struct.state
                elif struct.state == "REINFORCED":
                    timer_stage = "ARMOR"

            if timer_datetime:
                timer = StructureTimer.objects.create(
                    structure=struct,
                    solar_system=struct.solar_system,
                    timer_type=timer_stage or "ANCHORING",
                    timer_datetime=timer_datetime,
                    notes=f"Auto-created timer on structure edit ({struct.state}).",
                    created_by=request.user,
                    is_verified=request.user.has_perm("hostile.manage_intel"),
                )
                IntelManager.record_audit(
                    request.user,
                    "CREATE",
                    timer,
                    f"Structure timer {timer.timer_type} created with structure edit.",
                )
                messages.success(
                    request,
                    f"Structure '{struct.name}' updated and its {timer.timer_type} timer was created.",
                )
            else:
                messages.success(
                    request, f"Structure '{struct.name}' updated successfully."
                )

            return redirect("hostile:structures")
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field.capitalize()}: {error}")
    else:
        form = HostileStructureForm(instance=structure)

    context = {
        "form": form,
        "structure": structure,
    }
    return render(request, "hostile/structure_form.html", context)


@login_required
@permission_required("hostile.manage_intel")
def structure_delete(request: WSGIRequest, structure_id: int) -> HttpResponse:
    """Deletes a hostile structure and updates associated alliance vulnerability statistics"""
    structure = get_object_or_404(
        HostileStructure.objects.select_related("alliance", "solar_system"),
        id=structure_id,
    )
    if request.method == "POST":
        struct_name = structure.name
        struct_alliance = structure.alliance
        IntelManager.record_audit(
            request.user,
            "DELETE",
            structure,
            f"Deleted hostile structure '{struct_name}'.",
        )
        structure.delete()
        if struct_alliance:
            SovAnalysisEngine.aggregate_alliance_vulnerability_windows(struct_alliance)
        messages.success(request, f"Structure '{struct_name}' deleted successfully.")
        return redirect("hostile:structures")

    return render(
        request, "hostile/structure_confirm_delete.html", {"structure": structure}
    )


@login_required
@permission_required("hostile.manage_intel")
def structure_verify(request: WSGIRequest, structure_id: int) -> HttpResponse:
    """Officers verify a structure submission"""
    structure = get_object_or_404(HostileStructure, id=structure_id)
    IntelManager.verify_structure(structure, request.user)
    messages.success(request, f"Structure '{structure.name}' verified.")
    return redirect(request.headers.get("referer", "hostile:structures"))


@login_required
@permission_required("hostile.basic_access")
def structure_set_state(request: WSGIRequest, structure_id: int) -> HttpResponse:
    """Manually changes a hostile structure state (e.g. ONLINE, LOW_POWER, OFFLINE, ABANDONED, DESTROYED, etc.)"""
    structure = get_object_or_404(HostileStructure, id=structure_id)
    if request.method == "POST":
        new_state = (request.POST.get("state") or "").strip().upper()
        # Normalization
        if new_state == "LOWPOWER":
            new_state = "LOW_POWER"
        valid_states = [s[0] for s in HostileStructure.STATE_CHOICES]
        if new_state in valid_states:
            old_state_display = structure.get_state_display()
            structure.state = new_state
            if new_state == "ANCHORING":
                structure.core_status = "UNFITTED"
            structure.save(update_fields=["state", "core_status", "updated_at"])
            IntelManager.record_audit(
                request.user,
                "UPDATE",
                structure,
                f"Structure state manually updated from {old_state_display} to {structure.get_state_display()}",
            )
            messages.success(
                request,
                f"Structure '{structure.name}' state set to {structure.get_state_display()}.",
            )
        else:
            messages.error(request, f"Invalid structure state: '{new_state}'")
    return redirect(request.headers.get("referer", "hostile:structures"))


@login_required
@permission_required("hostile.basic_access")
def timers_list(request: WSGIRequest) -> HttpResponse:
    """Timer board with live countdowns and timezone conversion"""
    upcoming_timers = (
        StructureTimer.objects.structures_only()
        .upcoming()
        .select_related("structure", "solar_system", "created_by")
    )
    expired_timers = (
        StructureTimer.objects.structures_only()
        .expired()
        .select_related("structure", "solar_system", "created_by")[:20]
    )
    timer_form = StructureTimerForm()
    paste_form = SmartPasteForm()

    context = {
        "upcoming_timers": upcoming_timers,
        "expired_timers": expired_timers,
        "timer_form": timer_form,
        "paste_form": paste_form,
    }
    return render(request, "hostile/timers.html", context)


@login_required
@permission_required("hostile.basic_access")
def sov_timers_list(request: WSGIRequest) -> HttpResponse:
    """Dedicated Sovereignty Timers & Contest Campaigns Board with Jump Distance Routing and Multi-factor Sorting"""
    now = timezone.now()
    cutoff_24h = now - timedelta(hours=24)

    upcoming_timers_qs = (
        StructureTimer.objects.sov_campaigns()
        .filter(is_concluded=False, timer_datetime__gte=cutoff_24h)
        .select_related("solar_system__constellation__region", "structure")
    )
    expired_timers_qs = (
        StructureTimer.objects.sov_campaigns()
        .filter(Q(is_concluded=True) | Q(is_sov_campaign=False, timer_datetime__lt=now))
        .filter(timer_datetime__gte=cutoff_24h)
        .select_related("solar_system__constellation__region", "structure")
    )
    upcoming_timers = list(upcoming_timers_qs)
    expired_timers = list(expired_timers_qs)
    paste_form = SmartPasteForm()

    selected_origin_id = None
    selected_origin_name = ""
    origin_raw = request.GET.get("origin", "").strip()
    origin_system = None
    if origin_raw:
        if origin_raw.isdigit():
            origin_system = SolarSystem.objects.filter(id=int(origin_raw)).first()
        else:
            origin_system = SolarSystem.objects.filter(name__iexact=origin_raw).first()

    # Reference solar systems for origin distance calculation
    ref_filter = (
        Q(id__in=HostileStagingSystem.objects.values_list("solar_system_id", flat=True))
        | Q(id__in=HostileStructure.objects.values_list("solar_system_id", flat=True))
        | Q(
            id__in=StructureTimer.objects.sov_campaigns().values_list(
                "solar_system_id", flat=True
            )
        )
        | Q(hub=True)
    )
    if origin_system:
        ref_filter |= Q(id=origin_system.id)

    reference_systems = (
        SolarSystem.objects.filter(ref_filter)
        .distinct()
        .select_related("constellation__region")
        .order_by("name")
    )

    if origin_system:
        selected_origin_id = origin_system.id
        selected_origin_name = origin_system.name
        # AA Hostile Intel
        from hostile.services.map_routing import MapRoutingService

        for timer in upcoming_timers:
            timer.distance_data = MapRoutingService.get_system_distance_summary(
                origin_system, timer.solar_system, flag="shortest", include_jumps=True
            )
        for timer in expired_timers:
            timer.distance_data = MapRoutingService.get_system_distance_summary(
                origin_system, timer.solar_system, flag="shortest", include_jumps=True
            )

    sort_param = request.GET.get("sort", "").strip().lower()
    order_param = request.GET.get("order", "asc").strip().lower()
    is_desc = order_param == "desc"

    def get_sort_key(timer: StructureTimer, key: str) -> Any:
        if key == "region":
            return (timer.region_name or "").lower()
        elif key == "system":
            return (timer.solar_system.name if timer.solar_system else "").lower()
        elif key == "ly":
            if (
                hasattr(timer, "distance_data")
                and timer.distance_data
                and timer.distance_data.get("ly") is not None
            ):
                return timer.distance_data.get("ly")
            return 999999.0
        elif key == "jumps":
            if (
                hasattr(timer, "distance_data")
                and timer.distance_data
                and timer.distance_data.get("jumps") is not None
            ):
                return timer.distance_data.get("jumps")
            return 999999
        elif key == "structure":
            return (timer.structure_event_name or "").lower()
        elif key == "defender":
            return (timer.defender_display or "").lower()
        elif key == "score":
            return timer.defender_percent if timer.defender_percent is not None else 0
        elif key == "time":
            return timer.timer_datetime
        return timer.timer_datetime

    if sort_param:
        upcoming_timers.sort(key=lambda t: get_sort_key(t, sort_param), reverse=is_desc)
        expired_timers.sort(key=lambda t: get_sort_key(t, sort_param), reverse=is_desc)
    else:
        upcoming_timers.sort(key=lambda t: t.timer_datetime)
        expired_timers.sort(key=lambda t: t.timer_datetime, reverse=True)

    context = {
        "upcoming_timers": upcoming_timers,
        "expired_timers": expired_timers,
        "paste_form": paste_form,
        "reference_systems": reference_systems,
        "selected_origin_id": selected_origin_id,
        "selected_origin_name": selected_origin_name,
        "current_sort": sort_param,
        "current_order": order_param,
    }
    return render(request, "hostile/sov_timers.html", context)


@login_required
@permission_required("hostile.basic_access")
def timer_add(request: WSGIRequest) -> HttpResponse:
    """Creates a new structure or sovereignty timer, registering new structure to known structures if specified"""
    if request.method == "POST":
        form = StructureTimerForm(request.POST)
        if form.is_valid():
            timer = form.save(commit=False)
            timer.created_by = request.user
            if request.user.has_perm("hostile.manage_intel"):
                timer.is_verified = True

            structure = form.cleaned_data.get("structure")
            name = (form.cleaned_data.get("name") or "").strip()
            solar_system = form.cleaned_data.get("solar_system")

            if not structure and name:
                # Create the new structure and register it in known structures
                alliance = form.cleaned_data.get("alliance")
                corporation = form.cleaned_data.get("corporation")
                owner_ticker = form.cleaned_data.get("owner_ticker", "")
                if not owner_ticker:
                    if alliance and alliance.ticker:
                        owner_ticker = alliance.ticker
                    elif corporation and corporation.ticker:
                        owner_ticker = corporation.ticker

                struct = HostileStructure.objects.create(
                    name=name,
                    structure_type=form.cleaned_data.get("structure_type"),
                    solar_system=solar_system,
                    owner_ticker=owner_ticker,
                    alliance=alliance,
                    corporation=corporation,
                    core_status=form.cleaned_data.get("core_status") or "UNKNOWN",
                    state=form.cleaned_data.get("state") or "ONLINE",
                    vulnerability_hour=form.cleaned_data.get("vulnerability_hour"),
                    vulnerability_window=form.cleaned_data.get("vulnerability_window")
                    or "",
                    location_details=form.cleaned_data.get("location_details") or "",
                    notes=form.cleaned_data.get("notes") or "",
                    created_by=request.user,
                    is_verified=request.user.has_perm("hostile.manage_intel"),
                )
                if struct.alliance:
                    SovAnalysisEngine.aggregate_alliance_vulnerability_windows(
                        struct.alliance
                    )
                IntelManager.record_audit(
                    request.user,
                    "CREATE",
                    struct,
                    f"Structure '{struct.name}' registered via Timer Board.",
                )
                timer.structure = struct
                timer.solar_system = struct.solar_system
            elif structure:
                timer.structure = structure
                timer.solar_system = structure.solar_system or solar_system
            else:
                timer.solar_system = solar_system

            timer.save()
            IntelManager.record_audit(
                request.user, "CREATE", timer, f"Timer {timer.timer_type} created."
            )
            if not structure and name:
                messages.success(
                    request,
                    f"Structure '{name}' registered to known structures and {timer.timer_type} timer created.",
                )
            else:
                messages.success(
                    request, f"Timer {timer.timer_type} created successfully."
                )
            return redirect("hostile:timers")
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
    return redirect("hostile:timers")


@login_required
@permission_required("hostile.manage_intel")
def timer_verify(request: WSGIRequest, timer_id: int) -> HttpResponse:
    """Officers verify a structure timer"""
    timer = get_object_or_404(StructureTimer, id=timer_id)
    IntelManager.verify_timer(timer, request.user)
    messages.success(request, "Timer verified.")
    return redirect(request.headers.get("referer", "hostile:timers"))


@login_required
@permission_required("hostile.basic_access")
def timer_edit(request: WSGIRequest, timer_id: int) -> HttpResponse:
    """Edits an existing structure or sovereignty timer"""
    timer = get_object_or_404(
        StructureTimer.objects.select_related("structure", "solar_system"),
        id=timer_id,
    )
    if not user_can_edit_object(request.user, timer):
        messages.error(request, "You do not have permission to edit this timer.")
        return redirect("hostile:timers")
    if request.method == "POST":
        form = StructureTimerForm(request.POST, instance=timer)
        if form.is_valid():
            saved_timer = form.save(commit=False)
            structure = form.cleaned_data.get("structure")
            solar_system = form.cleaned_data.get("solar_system")
            if structure:
                saved_timer.structure = structure
                saved_timer.solar_system = structure.solar_system or solar_system
            elif solar_system:
                saved_timer.solar_system = solar_system
            saved_timer.save()
            IntelManager.record_audit(
                request.user,
                "EDIT",
                saved_timer,
                f"Timer {saved_timer.timer_type} updated.",
            )
            messages.success(
                request, f"Timer {saved_timer.timer_type} updated successfully."
            )
            return redirect("hostile:timers")
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
    else:
        form = StructureTimerForm(instance=timer)

    return render(request, "hostile/timer_form.html", {"form": form, "timer": timer})


@login_required
@permission_required("hostile.manage_intel")
def timer_delete(request: WSGIRequest, timer_id: int) -> HttpResponse:
    """Deletes an active or expired timer record"""
    timer = get_object_or_404(
        StructureTimer.objects.select_related("structure", "solar_system"),
        id=timer_id,
    )
    if request.method == "POST":
        timer_type = timer.timer_type
        target = timer.target_name
        IntelManager.record_audit(
            request.user, "DELETE", timer, f"Deleted {timer_type} timer for {target}."
        )
        timer.delete()
        messages.success(request, f"{timer_type} timer deleted successfully.")
        return redirect("hostile:timers")

    return render(request, "hostile/timer_confirm_delete.html", {"timer": timer})


@login_required
@permission_required("hostile.basic_access")
def systems_list(request: WSGIRequest) -> HttpResponse:
    """Solar system crowd-sourced threat map and sitrep feed"""
    observations = (
        SystemObservation.objects.all()
        .select_related("solar_system", "created_by", "local_scan")
        .prefetch_related("tags")
        .order_by("-is_pinned", "-time_seen", "-created_at")
    )
    obs_form = SystemObservationForm()
    paste_form = SmartPasteForm()
    available_tags = SystemTag.objects.all().order_by("name")

    context = {
        "observations": observations,
        "obs_form": obs_form,
        "paste_form": paste_form,
        "available_tags": available_tags,
    }
    return render(request, "hostile/systems.html", context)


@login_required
@permission_required("hostile.basic_access")
def system_observation_add(request: WSGIRequest) -> HttpResponse:
    """Submits a crowd-sourced system threat report or camp note"""
    if request.method == "POST":
        form = SystemObservationForm(request.POST)
        if form.is_valid():
            obs = form.save(commit=False, user=request.user)
            obs.created_by = request.user
            if request.user.has_perm("hostile.manage_intel"):
                obs.is_verified = True
            obs = form.save(commit=True, user=request.user)

            IntelManager.record_audit(
                request.user,
                "CREATE",
                obs,
                f"Observation in {obs.solar_system.name} added.",
            )
            messages.success(request, "Observation submitted successfully.")
            return redirect("hostile:systems")
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
    else:
        form = SystemObservationForm()

    available_tags = SystemTag.objects.all().order_by("name")
    return render(
        request,
        "hostile/system_observation_form.html",
        {"form": form, "available_tags": available_tags},
    )


@login_required
@permission_required("hostile.manage_intel")
def system_observation_verify(request: WSGIRequest, obs_id: int) -> HttpResponse:
    """Officers verify a system observation"""
    obs = get_object_or_404(SystemObservation, id=obs_id)
    IntelManager.verify_observation(obs, request.user)
    messages.success(request, "Observation verified.")
    return redirect(request.headers.get("referer", "hostile:systems"))


@login_required
@permission_required("hostile.manage_intel")
def system_observation_pin(request: WSGIRequest, obs_id: int) -> HttpResponse:
    """Officers pin/unpin a high-threat sitrep"""
    obs = get_object_or_404(SystemObservation, id=obs_id)
    IntelManager.toggle_pin_observation(obs, request.user)
    action = "pinned to dashboard" if obs.is_pinned else "unpinned"
    messages.info(request, f"Observation {action}.")
    return redirect(request.headers.get("referer", "hostile:systems"))


@login_required
@permission_required("hostile.basic_access")
def system_observation_edit(request: WSGIRequest, obs_id: int) -> HttpResponse:
    """Edits an existing system observation / threat report"""
    obs = get_object_or_404(
        SystemObservation.objects.select_related("solar_system", "local_scan").prefetch_related(
            "tags"
        ),
        id=obs_id,
    )
    if not user_can_edit_object(request.user, obs):
        messages.error(request, "You do not have permission to edit this observation.")
        return redirect("hostile:systems")
    if request.method == "POST":
        form = SystemObservationForm(request.POST, instance=obs)
        if form.is_valid():
            saved_obs = form.save(commit=True, user=request.user)
            IntelManager.record_audit(
                request.user,
                "EDIT",
                saved_obs,
                f"Observation in {saved_obs.solar_system.name} updated.",
            )
            messages.success(request, "Observation updated successfully.")
            return redirect("hostile:systems")
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
    else:
        form = SystemObservationForm(instance=obs)

    available_tags = SystemTag.objects.all().order_by("name")
    return render(
        request,
        "hostile/system_observation_form.html",
        {"form": form, "observation": obs, "available_tags": available_tags},
    )


@login_required
@permission_required("hostile.manage_intel")
def system_observation_delete(request: WSGIRequest, obs_id: int) -> HttpResponse:
    """Deletes a system observation / threat report"""
    obs = get_object_or_404(
        SystemObservation.objects.select_related("solar_system"), id=obs_id
    )
    if request.method == "POST":
        sys_name = obs.solar_system.name if obs.solar_system else "Unknown"
        IntelManager.record_audit(
            request.user, "DELETE", obs, f"Deleted observation in {sys_name}."
        )
        obs.delete()
        messages.success(request, f"Observation in {sys_name} deleted successfully.")
        return redirect("hostile:systems")

    return render(
        request, "hostile/system_observation_confirm_delete.html", {"observation": obs}
    )


@login_required
@permission_required("hostile.basic_access")
def pilots_list(request: WSGIRequest) -> HttpResponse:
    """Hostile pilot dossiers, cyno alts, and capital commanders with full-text search and tag filtering"""
    pilots = (
        HostilePilotDossier.objects.all()
        .select_related("last_seen_system", "created_by")
        .prefetch_related("alts", "main_character")
        .order_by("character_name")
    )
    total_pilots_count = pilots.count()

    search_query = (
        request.GET.get("q")
        or request.GET.get("search")
        or ""
    ).strip()
    tag_filter = request.GET.get("tag", "").lower().strip()
    role_filter = request.GET.get("role", "").lower().strip()

    # 1. Text Search (by name, affiliations, system, notes, or matched behavioral tags)
    if search_query:
        sq_lower = search_query.lower()
        name_q = (
            Q(character_name__icontains=search_query)
            | Q(corporation_name__icontains=search_query)
            | Q(alliance_name__icontains=search_query)
            | Q(associated_alliance_name__icontains=search_query)
            | Q(notes__icontains=search_query)
            | Q(alt_inference_reason__icontains=search_query)
            | Q(last_seen_system__name__icontains=search_query)
        )

        tag_q = Q()
        if "cyno" in sq_lower:
            tag_q |= Q(is_cyno_alt=True)
        if "blop" in sq_lower or "black ops" in sq_lower or "covert" in sq_lower:
            tag_q |= Q(is_blops_pilot=True)
        if "fc" in sq_lower or "commander" in sq_lower or "fleet" in sq_lower:
            tag_q |= Q(is_fc=True)
        if "titan" in sq_lower:
            tag_q |= Q(is_titan_pilot=True)
        if "super" in sq_lower:
            tag_q |= Q(is_super_pilot=True)
        if "dread" in sq_lower:
            tag_q |= Q(is_dread_pilot=True)
        if "fax" in sq_lower or "auxiliary" in sq_lower:
            tag_q |= Q(is_fax_pilot=True)
        if "cap" in sq_lower or "carrier" in sq_lower:
            tag_q |= (
                Q(is_capital_pilot=True)
                | Q(is_dread_pilot=True)
                | Q(is_fax_pilot=True)
                | Q(is_super_pilot=True)
                | Q(is_titan_pilot=True)
            )
        if "awox" in sq_lower:
            tag_q |= (
                Q(is_awox=True)
                | Q(is_alliance_awox=True)
                | Q(is_faction_awox=True)
            )
        if "bait" in sq_lower:
            tag_q |= Q(is_bait=True)
        if "gank" in sq_lower:
            tag_q |= Q(is_ganker=True)
        if "logi" in sq_lower:
            tag_q |= Q(is_logi_pilot=True)
        if "rookie" in sq_lower:
            tag_q |= Q(is_rookie=True)
        if "alt" in sq_lower:
            tag_q |= Q(is_alt=True)
        if "main" in sq_lower:
            tag_q |= Q(is_alt=False)
        if "out of corp" in sq_lower or "ooc" in sq_lower or "undercover" in sq_lower:
            tag_q |= Q(is_out_of_corp=True)
        if "verif" in sq_lower:
            tag_q |= Q(is_verified=True)

        zkill_q = Q(zkill_labels__icontains=search_query)

        pilots = pilots.filter(name_q | tag_q | zkill_q).distinct()

    # 2. Tag Filter
    if tag_filter:
        if tag_filter in ["cyno", "cynos", "cyno_alt"]:
            pilots = pilots.filter(is_cyno_alt=True)
        elif tag_filter in ["blops", "black_ops", "covert"]:
            pilots = pilots.filter(is_blops_pilot=True)
        elif tag_filter in ["fc", "fleet_commander"]:
            pilots = pilots.filter(is_fc=True)
        elif tag_filter in ["titan", "titans"]:
            pilots = pilots.filter(is_titan_pilot=True)
        elif tag_filter in ["super", "supers", "supercarrier"]:
            pilots = pilots.filter(is_super_pilot=True)
        elif tag_filter in ["dread", "dreads", "dreadnought"]:
            pilots = pilots.filter(is_dread_pilot=True)
        elif tag_filter in ["fax", "force_auxiliary"]:
            pilots = pilots.filter(is_fax_pilot=True)
        elif tag_filter in ["capital", "capitals"]:
            pilots = pilots.filter(
                Q(is_capital_pilot=True)
                | Q(is_dread_pilot=True)
                | Q(is_fax_pilot=True)
                | Q(is_super_pilot=True)
                | Q(is_titan_pilot=True)
            )
        elif tag_filter in ["awox", "corp_awox"]:
            pilots = pilots.filter(is_awox=True)
        elif tag_filter in ["alliance_awox"]:
            pilots = pilots.filter(is_alliance_awox=True)
        elif tag_filter in ["faction_awox"]:
            pilots = pilots.filter(is_faction_awox=True)
        elif tag_filter in ["all_awox", "awoxs"]:
            pilots = pilots.filter(
                Q(is_awox=True)
                | Q(is_alliance_awox=True)
                | Q(is_faction_awox=True)
            )
        elif tag_filter in ["bait"]:
            pilots = pilots.filter(is_bait=True)
        elif tag_filter in ["ganker", "gankers"]:
            pilots = pilots.filter(is_ganker=True)
        elif tag_filter in ["logi", "logistics"]:
            pilots = pilots.filter(is_logi_pilot=True)
        elif tag_filter in ["rookie", "rookies"]:
            pilots = pilots.filter(is_rookie=True)
        elif tag_filter in ["alt", "alts"]:
            pilots = pilots.filter(is_alt=True)
        elif tag_filter in ["main", "mains"]:
            pilots = pilots.filter(is_alt=False)
        elif tag_filter in ["out_of_corp", "ooc"]:
            pilots = pilots.filter(is_out_of_corp=True)
        elif tag_filter in ["verified"]:
            pilots = pilots.filter(is_verified=True)
        elif tag_filter in ["unverified"]:
            pilots = pilots.filter(is_verified=False)
        else:
            pilots = pilots.filter(
                Q(zkill_labels__icontains=tag_filter)
                | Q(alt_inference_reason__icontains=tag_filter)
            ).distinct()

    # 3. Role Filter (Tabs)
    if role_filter == "fc":
        pilots = pilots.filter(is_fc=True)
    elif role_filter in ["capital", "capitals"]:
        pilots = pilots.filter(
            Q(is_capital_pilot=True)
            | Q(is_dread_pilot=True)
            | Q(is_fax_pilot=True)
            | Q(is_super_pilot=True)
            | Q(is_titan_pilot=True)
        )
    elif role_filter in ["cyno", "cynos"]:
        pilots = pilots.filter(is_cyno_alt=True)
    elif role_filter in ["alt", "alts"]:
        pilots = pilots.filter(is_alt=True)
    elif role_filter in ["main", "mains"]:
        pilots = pilots.filter(is_alt=False)
    elif role_filter == "blops":
        pilots = pilots.filter(is_blops_pilot=True)
    elif role_filter == "awox":
        pilots = pilots.filter(
            Q(is_awox=True) | Q(is_alliance_awox=True) | Q(is_faction_awox=True)
        )
    elif role_filter == "bait":
        pilots = pilots.filter(is_bait=True)
    elif role_filter in ["ganker", "gankers"]:
        pilots = pilots.filter(is_ganker=True)
    elif role_filter == "logi":
        pilots = pilots.filter(is_logi_pilot=True)
    elif role_filter in ["rookie", "rookies"]:
        pilots = pilots.filter(is_rookie=True)

    # Heal any pilot records with numeric or missing corporation names
    unresolved_pilots = [
        p
        for p in pilots
        if not p.corporation_name
        or str(p.corporation_name).isdigit()
        or p.corporation_name == "Unknown Corp"
    ]
    if unresolved_pilots:
        try:
            # Alliance Auth
            from allianceauth.eveonline.models import EveCorporationInfo

            # AA Hostile Intel
            from hostile.services.entity_resolver import _get_val
            from hostile.services.esi_client import esi_client

            cids = [p.character_id for p in unresolved_pilots]
            affiliations = esi_client.post_characters_affiliation(cids)
            if affiliations:
                corp_ids = {
                    int(_get_val(a, "character_id")): int(_get_val(a, "corporation_id"))
                    for a in affiliations
                    if _get_val(a, "character_id") and _get_val(a, "corporation_id")
                }
                needed_corps = set(corp_ids.values())
                known_corps = {
                    int(c.corporation_id): c.corporation_name
                    for c in HostileCorporation.objects.filter(
                        corporation_id__in=needed_corps
                    )
                }
                for ec in EveCorporationInfo.objects.filter(
                    corporation_id__in=needed_corps
                ):
                    if int(ec.corporation_id) not in known_corps:
                        known_corps[int(ec.corporation_id)] = ec.corporation_name

                missing_cids = [c for c in needed_corps if c not in known_corps]
                if missing_cids:
                    names_res = esi_client.post_universe_names(missing_cids)
                    for n in names_res or []:
                        if _get_val(n, "id") and _get_val(n, "name"):
                            known_corps[int(_get_val(n, "id"))] = str(
                                _get_val(n, "name")
                            )

                for p in unresolved_pilots:
                    c_id = corp_ids.get(p.character_id)
                    if c_id and c_id in known_corps:
                        p.corporation_name = known_corps[c_id]
                        p.save(update_fields=["corporation_name"])
        except Exception:
            pass

    pilot_form = HostilePilotDossierForm()
    paste_form = SmartPasteForm()

    context = {
        "pilots": pilots,
        "total_pilots_count": total_pilots_count,
        "pilot_form": pilot_form,
        "paste_form": paste_form,
        "active_role": role_filter,
        "active_search": search_query,
        "active_tag": tag_filter,
    }
    return render(request, "hostile/pilots.html", context)


@login_required
@permission_required("hostile.basic_access")
def pilot_add(request: WSGIRequest) -> HttpResponse:
    """Creates or updates a hostile pilot dossier with automatic keyword role tagging"""
    if request.method == "POST":
        form = HostilePilotDossierForm(request.POST)
        if form.is_valid():
            pilot = form.save(commit=False)
            pilot.created_by = request.user
            if request.user.has_perm("hostile.manage_intel"):
                pilot.is_verified = True
            IntelManager.auto_tag_pilot(pilot)
            pilot.save()
            try:
                # AA Hostile Intel
                from hostile.services.zkill_client import zkill_client

                zkill_client.sync_pilot_dossier_zkill(pilot)
            except Exception:
                pass
            IntelManager.run_alt_detection(pilots=[pilot])
            IntelManager.record_audit(
                request.user,
                "CREATE",
                pilot,
                f"Pilot dossier {pilot.character_name} added.",
            )
            messages.success(request, f"Pilot dossier '{pilot.character_name}' saved.")
            return redirect("hostile:pilots")
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(
                        request,
                        (
                            f"{field.replace('_', ' ').capitalize()}: {error}"
                            if field != "__all__"
                            else error
                        ),
                    )
    return redirect("hostile:pilots")


@login_required
@permission_required("hostile.manage_intel")
def pilot_verify(request: WSGIRequest, pilot_id: int) -> HttpResponse:
    """Officers verify a pilot dossier"""
    pilot = get_object_or_404(HostilePilotDossier, id=pilot_id)
    IntelManager.verify_pilot(pilot, request.user)
    messages.success(request, f"Pilot '{pilot.character_name}' verified.")
    return redirect(request.headers.get("referer", "hostile:pilots"))


@login_required
@permission_required("hostile.basic_access")
def pilot_edit(request: WSGIRequest, pilot_id: int) -> HttpResponse:
    """Edits an existing hostile pilot dossier"""
    pilot = get_object_or_404(
        HostilePilotDossier.objects.select_related("last_seen_system"),
        id=pilot_id,
    )
    if not user_can_edit_object(request.user, pilot):
        messages.error(request, "You do not have permission to edit this pilot dossier.")
        return redirect("hostile:pilots")
    if request.method == "POST":
        form = HostilePilotDossierForm(request.POST, instance=pilot)
        if form.is_valid():
            saved_pilot = form.save(commit=False)
            IntelManager.auto_tag_pilot(saved_pilot)
            saved_pilot.save()
            try:
                # AA Hostile Intel
                from hostile.services.zkill_client import zkill_client

                zkill_client.sync_pilot_dossier_zkill(saved_pilot)
            except Exception:
                pass
            IntelManager.run_alt_detection(pilots=[saved_pilot])
            IntelManager.record_audit(
                request.user,
                "EDIT",
                saved_pilot,
                f"Pilot dossier '{saved_pilot.character_name}' updated.",
            )
            messages.success(
                request, f"Pilot dossier '{saved_pilot.character_name}' updated."
            )
            return redirect("hostile:pilots")
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
    else:
        form = HostilePilotDossierForm(instance=pilot)

    return render(request, "hostile/pilot_form.html", {"form": form, "pilot": pilot})


@login_required
@permission_required("hostile.basic_access")
def pilot_detail(request: WSGIRequest, pilot_id: int) -> HttpResponse:
    """Comprehensive pilot intelligence dossier view with alt network, threat sightings, and combat stats"""
    pilot = get_object_or_404(
        HostilePilotDossier.objects.select_related(
            "last_seen_system", "main_character", "created_by"
        ).prefetch_related("alts"),
        id=pilot_id,
    )

    # 1. Alts network
    known_alts = list(pilot.alts.all())
    main_pilot = pilot.main_character
    sibling_alts = []
    if main_pilot and main_pilot.id != pilot.id:
        sibling_alts = list(main_pilot.alts.exclude(id=pilot.id))

    # 2. Local Threat Scan sightings
    scan_sightings = list(
        LocalThreatScan.objects.filter(
            raw_local_text__icontains=pilot.character_name
        ).select_related("solar_system").order_by("-created_at")[:15]
    )

    # 3. System observations / sitreps mentioning this character
    obs_q = Q(observation_text__icontains=pilot.character_name)
    if scan_sightings:
        obs_q |= Q(local_scan__in=scan_sightings)
    observations = list(
        SystemObservation.objects.filter(obs_q)
        .select_related("solar_system", "created_by")
        .distinct()
        .order_by("-time_seen", "-created_at")[:15]
    )

    # 4. Audit trail
    audit_trail = list(
        IntelAuditLog.objects.filter(
            target_model=pilot.__class__.__name__, target_id=str(pilot.id)
        )
        .select_related("user")
        .order_by("-timestamp")[:15]
    )

    # 5. Staging systems or associated entities
    associated_alliance = None
    if pilot.associated_alliance_name:
        associated_alliance = HostileAlliance.objects.filter(
            alliance_name__iexact=pilot.associated_alliance_name
        ).first()

    context = {
        "pilot": pilot,
        "known_alts": known_alts,
        "main_pilot": main_pilot,
        "sibling_alts": sibling_alts,
        "scan_sightings": scan_sightings,
        "observations": observations,
        "audit_trail": audit_trail,
        "associated_alliance": associated_alliance,
        "can_edit": user_can_edit_object(request.user, pilot),
    }
    return render(request, "hostile/pilot_detail.html", context)


@login_required
@permission_required("hostile.manage_intel")
def pilot_delete(request: WSGIRequest, pilot_id: int) -> HttpResponse:
    """Deletes a hostile pilot dossier"""
    pilot = get_object_or_404(HostilePilotDossier, id=pilot_id)
    if request.method == "POST":
        char_name = pilot.character_name
        IntelManager.record_audit(
            request.user, "DELETE", pilot, f"Deleted pilot dossier for '{char_name}'."
        )
        pilot.delete()
        messages.success(request, f"Pilot dossier '{char_name}' deleted successfully.")
        return redirect("hostile:pilots")

    return render(request, "hostile/pilot_confirm_delete.html", {"pilot": pilot})


@login_required
@permission_required("hostile.basic_access")
def pilot_sync_zkill(request: WSGIRequest, pilot_id: int) -> HttpResponse:
    """Syncs a specific pilot dossier with zKillboard combat intelligence and behavioral tags"""
    pilot = get_object_or_404(HostilePilotDossier, id=pilot_id)
    try:
        from hostile.services.zkill_client import zkill_client

        updated = zkill_client.sync_pilot_dossier_zkill(pilot)
        IntelManager.record_audit(
            request.user,
            "SYNC",
            pilot,
            f"Synced zKillboard stats & behavioral tags for '{pilot.character_name}'.",
        )
        if updated:
            messages.success(
                request,
                f"Combat intelligence & behavioral tags updated for '{pilot.character_name}'.",
            )
        else:
            messages.info(
                request,
                f"Intelligence for '{pilot.character_name}' is already up-to-date.",
            )
    except Exception as e:
        messages.error(
            request,
            f"Failed to sync zKillboard stats for '{pilot.character_name}': {e}",
        )
    return redirect(request.headers.get("referer", "hostile:pilots"))


@login_required
@permission_required("hostile.basic_access")
def pilot_detect_alts(request: WSGIRequest) -> HttpResponse:
    """Executes automated alt detection scan across all pilot dossiers and refreshes combat intel"""
    from hostile.services.zkill_client import zkill_client

    all_pilots = list(HostilePilotDossier.objects.all())
    for p in all_pilots:
        try:
            zkill_client.sync_pilot_dossier_zkill(p)
        except Exception:
            pass

    updated_count = IntelManager.run_alt_detection(pilots=all_pilots)
    IntelManager.record_audit(
        request.user,
        "SCAN",
        None,
        f"Alt detection scan executed. {updated_count} alt linkages updated.",
    )
    if updated_count > 0:
        messages.success(
            request,
            f"Alt detection & intelligence refresh completed. {updated_count} alt character linkage(s) identified or updated.",
        )
    else:
        messages.info(
            request,
            "Alt detection & intelligence refresh completed. Pilot combat stats and behavioral tags synchronized.",
        )
    return redirect("hostile:pilots")


@login_required
@permission_required("hostile.basic_access")
def alliances_list(request: WSGIRequest) -> HttpResponse:
    """Hostile alliances, coalitions, corporations, alt corps, and active stagings"""
    alliances = (
        HostileAlliance.objects.all()
        .select_related("main_alliance", "sov_capital")
        .prefetch_related(
            "structures",
            "corporations",
            "staging_systems__solar_system",
            "doctrines",
            "alt_alliances",
            "affiliated_alt_corps",
        )
        .order_by("alliance_name")
    )
    corporations = (
        HostileCorporation.objects.all()
        .select_related("alliance", "main_alliance", "main_corporation")
        .prefetch_related("structures", "alt_corps")
        .order_by("corporation_name")
    )

    # Build structured coalition breakdown
    coalitions_dict = {}
    for a in alliances:
        if a.coalition:
            c_name = a.coalition.strip()
            if c_name not in coalitions_dict:
                coalitions_dict[c_name] = {
                    "name": c_name,
                    "leader": None,
                    "alliances": [],
                    "total_active_pilots": 0,
                    "total_kills": 0,
                    "total_structures": 0,
                }
            coalitions_dict[c_name]["alliances"].append(a)
            coalitions_dict[c_name]["total_active_pilots"] += a.active_pilots_count or 0
            coalitions_dict[c_name]["total_kills"] += a.weekly_kills_count or 0
            coalitions_dict[c_name]["total_structures"] += a.structures.count()
            if a.is_coalition_leader:
                coalitions_dict[c_name]["leader"] = a

    coalitions = sorted(coalitions_dict.values(), key=lambda c: c["name"].lower())

    alliance_form = HostileAllianceForm()
    corp_form = HostileCorporationForm()
    coalition_form = CoalitionAlliancesForm()
    paste_form = SmartPasteForm()
    is_zkill_syncing = bool(cache.get("hostile-intel-update-all-zkill-stats-lock"))

    context = {
        "alliances": alliances,
        "corporations": corporations,
        "coalitions": coalitions,
        "alliance_form": alliance_form,
        "corp_form": corp_form,
        "coalition_form": coalition_form,
        "paste_form": paste_form,
        "is_zkill_syncing": is_zkill_syncing,
    }
    return render(request, "hostile/alliances.html", context)


@login_required
@permission_required("hostile.manage_intel")
def coalition_manage(request: WSGIRequest) -> HttpResponse:
    """Batch updates coalition member alliances and designates the coalition leader"""
    if request.method == "POST":
        form = CoalitionAlliancesForm(request.POST)
        if form.is_valid():
            coalition_name = form.cleaned_data["coalition_name"].strip()
            selected_alliances = list(form.cleaned_data["alliances"])
            leader_alliance = form.cleaned_data.get("leader_alliance")

            # Update selected alliances to belong to this coalition and set leader flag
            for ally in selected_alliances:
                ally.coalition = coalition_name
                ally.is_coalition_leader = (
                    leader_alliance is not None and ally.id == leader_alliance.id
                )
                ally.save()

            # For any alliances that previously had this coalition but were deselected, remove coalition
            other_allies = HostileAlliance.objects.filter(
                coalition__iexact=coalition_name
            ).exclude(id__in=[a.id for a in selected_alliances])
            for ally in other_allies:
                ally.coalition = ""
                ally.is_coalition_leader = False
                ally.save()

            IntelManager.record_audit(
                request.user,
                "EDIT",
                selected_alliances[0] if selected_alliances else None,
                f"Updated coalition '{coalition_name}' with {len(selected_alliances)} alliances (Leader: {leader_alliance.alliance_name if leader_alliance else 'None'}).",
            )
            messages.success(
                request,
                f"Coalition '{coalition_name}' saved with {len(selected_alliances)} member alliances.",
            )
            return redirect("hostile:alliances")
        else:
            messages.error(
                request,
                "Could not save coalition alliances. Please check the form fields.",
            )
    return redirect("hostile:alliances")


@login_required
@permission_required("hostile.basic_access")
def alliance_add(request: WSGIRequest) -> HttpResponse:
    """Manual addition or registration of a hostile alliance"""
    if request.method == "POST":
        form = HostileAllianceForm(request.POST)
        if form.is_valid():
            alliance = form.save(commit=False)
            alliance.created_by = request.user
            alliance.save()
            try:
                # AA Hostile Intel
                from hostile.services.sov_engine import SovAnalysisEngine

                SovAnalysisEngine.sync_alliance_members_and_corps(alliance)
                SovAnalysisEngine.sync_alliance_sovereignty_held_single(alliance)
            except Exception:
                pass
            zkill_client.update_alliance_stats(alliance)
            try:
                # AA Hostile Intel
                from hostile.tasks import sync_alliance_zkill_stats

                sync_alliance_zkill_stats.delay(alliance.alliance_id)
            except Exception:
                pass
            IntelManager.record_audit(
                request.user,
                "CREATE",
                alliance,
                f"Alliance '{alliance.alliance_name}' added.",
            )
            messages.success(
                request,
                f"Alliance '{alliance.alliance_name}' added and synchronized.",
            )
            return redirect("hostile:alliance_detail", alliance_id=alliance.id)
    else:
        initial = {}
        if request.GET.get("main_alliance_id"):
            initial["main_alliance"] = request.GET.get("main_alliance_id")
            initial["is_alt_alliance"] = True
        if request.GET.get("coalition"):
            initial["coalition"] = request.GET.get("coalition")
        if request.GET.get("is_coalition_leader"):
            initial["is_coalition_leader"] = True
        form = HostileAllianceForm(initial=initial)
    return render(request, "hostile/alliance_form.html", {"form": form})


@login_required
@permission_required("hostile.basic_access")
def alliance_edit(request: WSGIRequest, alliance_id: int) -> HttpResponse:
    """Updates alliance form numbers and operational timezone"""
    alliance = get_object_or_404(HostileAlliance, id=alliance_id)
    if not user_can_edit_object(request.user, alliance):
        messages.error(request, "You do not have permission to edit this alliance.")
        return redirect("hostile:alliance_detail", alliance_id=alliance.id)
    if request.method == "POST":
        form = HostileAllianceForm(request.POST, instance=alliance)
        if form.is_valid():
            form.save()
            IntelManager.record_audit(
                request.user,
                "EDIT",
                alliance,
                f"Updated intel parameters for '{alliance.alliance_name}'.",
            )
            messages.success(request, f"Alliance '{alliance.alliance_name}' updated.")
            return redirect("hostile:alliance_detail", alliance_id=alliance.id)
    else:
        form = HostileAllianceForm(instance=alliance)
    return render(
        request, "hostile/alliance_form.html", {"form": form, "alliance": alliance}
    )


@login_required
@permission_required("hostile.manage_intel")
def alliance_delete(request: WSGIRequest, alliance_id: int) -> HttpResponse:
    """Deletes a hostile alliance record and all associated member corporations"""
    alliance = get_object_or_404(HostileAlliance, id=alliance_id)
    if request.method == "POST":
        all_name = alliance.alliance_name
        member_corps_count = alliance.corporations.count()
        IntelManager.record_audit(
            request.user,
            "DELETE",
            alliance,
            f"Deleted hostile alliance '{all_name}' and {member_corps_count} member corporation(s).",
        )
        alliance.delete()
        if member_corps_count > 0:
            messages.success(
                request,
                f"Alliance '{all_name}' and {member_corps_count} member corporation(s) deleted successfully.",
            )
        else:
            messages.success(request, f"Alliance '{all_name}' deleted successfully.")
        return redirect("hostile:alliances")

    member_corps = alliance.corporations.all()
    return render(
        request,
        "hostile/alliance_confirm_delete.html",
        {"alliance": alliance, "member_corps": member_corps},
    )


def _format_heatmap_data(
    heatmap_dict: dict[str, Any] | None,
    entity: Any | None = None,
) -> dict[str, Any]:
    """Helper to convert stored 7x24 heatmap matrix into formatted template view model"""
    # AA Hostile Intel
    from hostile.services.sov_engine import SovAnalysisEngine

    if (
        not heatmap_dict
        or not isinstance(heatmap_dict, dict)
        or not heatmap_dict.get("matrix")
    ):
        return {}

    matrix = heatmap_dict.get("matrix", [])
    total_matrix_kills = sum(sum(r) for r in matrix) if matrix else 0
    if total_matrix_kills == 0:
        return {}

    days = heatmap_dict.get(
        "days",
        ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
    )
    max_val = max(int(heatmap_dict.get("max_val", 1) or 1), 1)

    rows = []
    hour_totals = [0] * 24
    grand_total = 0

    for day_idx, day_name in enumerate(days):
        day_row = matrix[day_idx] if day_idx < len(matrix) else [0] * 24
        day_total = sum(day_row)
        grand_total += day_total

        cells = []
        for hour_idx in range(24):
            count = day_row[hour_idx] if hour_idx < len(day_row) else 0
            hour_totals[hour_idx] += count
            ratio = round(count / max_val, 2) if max_val > 0 else 0

            if count == 0:
                bg_color = "rgba(255, 255, 255, 0.02)"
                text_color = "rgba(255, 255, 255, 0.2)"
                border_color = "rgba(255, 255, 255, 0.04)"
                cell_class = "cell-zero"
            elif ratio < 0.25:
                bg_color = "rgba(13, 110, 253, 0.18)"
                text_color = "#38bdf8"
                border_color = "rgba(56, 189, 248, 0.35)"
                cell_class = "cell-low"
            elif ratio < 0.50:
                bg_color = "rgba(20, 184, 166, 0.20)"
                text_color = "#2dd4bf"
                border_color = "rgba(45, 212, 191, 0.40)"
                cell_class = "cell-med"
            elif ratio < 0.75:
                bg_color = "rgba(255, 193, 7, 0.22)"
                text_color = "#facc15"
                border_color = "rgba(250, 204, 21, 0.45)"
                cell_class = "cell-high"
            else:
                bg_color = "rgba(220, 53, 69, 0.28)"
                text_color = "#f87171"
                border_color = "rgba(248, 113, 113, 0.55)"
                cell_class = "cell-peak"

            cells.append(
                {
                    "hour": f"{hour_idx:02d}",
                    "count": count,
                    "ratio": ratio,
                    "bg_color": bg_color,
                    "text_color": text_color,
                    "border_color": border_color,
                    "cell_class": cell_class,
                }
            )

        rows.append(
            {
                "day_name": day_name,
                "cells": cells,
                "total": day_total,
            }
        )

    peak_hour_num = int(heatmap_dict.get("peak_hour", 0) or 0)
    peak_tz = SovAnalysisEngine.get_hour_timezone(peak_hour_num)

    return {
        "rows": rows,
        "hours": [f"{h:02d}" for h in range(24)],
        "hour_totals": hour_totals,
        "grand_total": grand_total,
        "peak_day": heatmap_dict.get("peak_day", "Unknown"),
        "peak_hour": f"{peak_hour_num:02d}:00 UTC",
        "peak_tz": peak_tz,
        "max_val": heatmap_dict.get("max_val", 0),
        "total_kills_3m": heatmap_dict.get("total_kills_3m", grand_total),
    }


@login_required
@permission_required("hostile.basic_access")
def alliance_detail(request: WSGIRequest, alliance_id: int) -> HttpResponse:
    """Detailed intelligence dossier and complete profile for a hostile alliance"""
    alliance = get_object_or_404(
        HostileAlliance.objects.select_related(
            "main_alliance", "sov_capital"
        ).prefetch_related(
            "corporations",
            "alt_alliances",
            "affiliated_alt_corps",
            "staging_systems__solar_system__constellation__region",
            "doctrines__example_fits__ship_type",
            "structures__solar_system",
            "structures__structure_type",
        ),
        id=alliance_id,
    )

    # Active and upcoming structure timers for this alliance
    timers = (
        StructureTimer.objects.filter(structure__alliance=alliance)
        .select_related("structure", "solar_system")
        .order_by("timer_datetime")
    )

    # Pilot dossiers belonging to or associated with this alliance or its member corporations
    member_corp_names = list(
        alliance.corporations.values_list("corporation_name", flat=True)
    )
    pilots_query = Q(associated_alliance_name__iexact=alliance.alliance_name) | Q(
        alliance_name__iexact=alliance.alliance_name
    )
    if member_corp_names:
        pilots_query |= Q(corporation_name__in=member_corp_names)

    pilots = (
        HostilePilotDossier.objects.filter(pilots_query)
        .select_related("last_seen_system")
        .order_by(
            "-is_fc",
            "-is_titan_pilot",
            "-is_super_pilot",
            "-is_cyno_alt",
            "character_name",
        )
    )

    # Other member alliances in the same coalition
    coalition_alliances = (
        HostileAlliance.objects.filter(coalition__iexact=alliance.coalition)
        .exclude(id=alliance.id)
        .order_by("alliance_name")
        if alliance.coalition
        else HostileAlliance.objects.none()
    )

    heatmap_data = _format_heatmap_data(alliance.activity_heatmap, entity=alliance)

    sov_intel = SovAnalysisEngine.get_alliance_sovereignty_intel(alliance)
    hourly_schedule = SovAnalysisEngine.get_alliance_hourly_schedule(alliance)

    member_corps = list(alliance.corporations.all())
    for corp in member_corps:
        if corp.max_subcap_form == 0 and corp.member_count > 0:
            corp.max_subcap_form = max(1, int(corp.member_count * 0.15))
            if corp.member_count >= 50 and corp.max_cap_form == 0:
                corp.max_cap_form = max(1, int(corp.member_count * 0.03))

    context = {
        "alliance": alliance,
        "timers": timers,
        "pilots": pilots,
        "coalition_alliances": coalition_alliances,
        "member_corporations": member_corps,
        "alt_alliances": alliance.alt_alliances.all(),
        "affiliated_alt_corps": alliance.affiliated_alt_corps.all(),
        "staging_systems": alliance.staging_systems.all(),
        "doctrines": alliance.doctrines.all(),
        "structures": alliance.structures.all(),
        "heatmap_data": heatmap_data,
        "sov_intel": sov_intel,
        "hourly_schedule": hourly_schedule,
    }
    return render(request, "hostile/alliance_detail.html", context)


@login_required
@permission_required("hostile.basic_access")
def corporation_add(request: WSGIRequest) -> HttpResponse:
    """Manual addition or registration of a hostile corporation or alt corp"""
    if request.method == "POST":
        form = HostileCorporationForm(request.POST)
        if form.is_valid():
            corp = form.save(commit=False)
            corp.created_by = request.user
            corp.save()
            try:
                # AA Hostile Intel
                from hostile.services.entity_resolver import _get_val
                from hostile.services.esi_client import esi_client

                corp_info = esi_client.get_corporation_info(corp.corporation_id)
                if corp_info:
                    m_count_raw = _get_val(corp_info, "member_count")
                    if m_count_raw is not None:
                        corp.member_count = int(m_count_raw)
                        corp.prev_member_count = corp.member_count
                    if not corp.ticker and _get_val(corp_info, "ticker"):
                        corp.ticker = _get_val(corp_info, "ticker")
                    if not corp.corporation_name and _get_val(corp_info, "name"):
                        corp.corporation_name = _get_val(corp_info, "name")
                    all_id = _get_val(corp_info, "alliance_id")
                    if all_id and not corp.alliance:
                        corp.alliance = HostileAlliance.objects.filter(
                            alliance_id=int(all_id)
                        ).first()
                    subcap_est = (
                        max(1, int(corp.member_count * 0.15))
                        if corp.member_count > 0
                        else 0
                    )
                    cap_est = (
                        max(1, int(corp.member_count * 0.03))
                        if corp.member_count >= 50
                        else 0
                    )
                    if corp.max_subcap_form == 0 and subcap_est > 0:
                        corp.max_subcap_form = subcap_est
                    if corp.max_cap_form == 0 and cap_est > 0:
                        corp.max_cap_form = cap_est
                    corp.save()
            except Exception:
                pass

            zkill_client.update_corporation_stats(corp)
            IntelManager.record_audit(
                request.user,
                "CREATE",
                corp,
                f"Corporation '{corp.corporation_name}' added.",
            )
            messages.success(
                request,
                f"Corporation '{corp.corporation_name}' added successfully.",
            )
            return redirect("hostile:corporation_detail", corp_id=corp.id)
    else:
        initial = {}
        if request.GET.get("alliance_id"):
            initial["alliance"] = request.GET.get("alliance_id")
        if request.GET.get("main_alliance_id"):
            initial["main_alliance"] = request.GET.get("main_alliance_id")
            initial["is_alt_corp"] = True
        if request.GET.get("main_corporation_id"):
            initial["main_corporation"] = request.GET.get("main_corporation_id")
            initial["is_alt_corp"] = True
        if request.GET.get("coalition"):
            initial["coalition"] = request.GET.get("coalition")
        if request.GET.get("is_alt_corp"):
            initial["is_alt_corp"] = True
        form = HostileCorporationForm(initial=initial)
    return render(request, "hostile/corporation_form.html", {"form": form})


@login_required
@permission_required("hostile.basic_access")
def corporation_edit(request: WSGIRequest, corp_id: int) -> HttpResponse:
    """Updates corporation form numbers, coalition, alliance, and alt corp affiliation"""
    corp = get_object_or_404(HostileCorporation, id=corp_id)
    if not user_can_edit_object(request.user, corp):
        messages.error(request, "You do not have permission to edit this corporation.")
        return redirect("hostile:corporation_detail", corp_id=corp.id)
    if request.method == "POST":
        form = HostileCorporationForm(request.POST, instance=corp)
        if form.is_valid():
            form.save()
            IntelManager.record_audit(
                request.user,
                "EDIT",
                corp,
                f"Updated intel parameters for '{corp.corporation_name}'.",
            )
            messages.success(request, f"Corporation '{corp.corporation_name}' updated.")
            return redirect("hostile:corporation_detail", corp_id=corp.id)
    else:
        form = HostileCorporationForm(instance=corp)
    return render(
        request, "hostile/corporation_form.html", {"form": form, "corporation": corp}
    )


@login_required
@permission_required("hostile.manage_intel")
def corporation_delete(request: WSGIRequest, corp_id: int) -> HttpResponse:
    """Deletes a hostile corporation record"""
    corp = get_object_or_404(HostileCorporation, id=corp_id)
    if request.method == "POST":
        corp_name = corp.corporation_name
        IntelManager.record_audit(
            request.user, "DELETE", corp, f"Deleted hostile corporation '{corp_name}'."
        )
        corp.delete()
        messages.success(request, f"Corporation '{corp_name}' deleted successfully.")
        return redirect("hostile:alliances")

    return render(
        request, "hostile/corporation_confirm_delete.html", {"corporation": corp}
    )


@login_required
@permission_required("hostile.basic_access")
def corporation_detail(request: WSGIRequest, corp_id: int) -> HttpResponse:
    """Detailed intelligence dossier and complete profile for a hostile corporation"""
    corporation = get_object_or_404(
        HostileCorporation.objects.select_related(
            "alliance", "main_alliance", "main_corporation"
        ).prefetch_related(
            "alt_corps",
            "structures__solar_system",
            "structures__structure_type",
            "staging_systems__solar_system",
        ),
        id=corp_id,
    )

    timer_filter = Q(structure__corporation=corporation)
    if corporation.alliance:
        timer_filter |= Q(structure__alliance=corporation.alliance)
    timers = (
        StructureTimer.objects.filter(timer_filter)
        .select_related("structure", "solar_system")
        .order_by("timer_datetime")
    )

    struct_filter = Q(corporation=corporation)
    if corporation.alliance:
        struct_filter |= Q(alliance=corporation.alliance)
    structures = (
        HostileStructure.objects.filter(struct_filter)
        .select_related("solar_system", "structure_type", "alliance", "corporation")
        .order_by("name")
    )

    staging_filter = Q(corporation=corporation)
    if corporation.alliance:
        staging_filter |= Q(alliance=corporation.alliance)
    staging_systems = (
        HostileStagingSystem.objects.filter(staging_filter)
        .select_related("solar_system", "alliance", "corporation", "structure")
        .order_by("-is_primary", "solar_system__name")
    )

    doctrine_filter = Q(corporation=corporation)
    if corporation.alliance:
        doctrine_filter |= Q(alliance=corporation.alliance)
    doctrines = (
        HostileDoctrine.objects.filter(doctrine_filter)
        .select_related("alliance", "corporation", "created_by")
        .prefetch_related("example_fits__ship_type")
        .order_by("name")
    )

    pilots_filter = Q(corporation_name__iexact=corporation.corporation_name)
    if corporation.ticker:
        pilots_filter |= Q(corporation_name__icontains=f"[{corporation.ticker}]")
    pilots = (
        HostilePilotDossier.objects.filter(pilots_filter)
        .select_related("last_seen_system")
        .order_by(
            "-is_fc",
            "-is_titan_pilot",
            "-is_super_pilot",
            "-is_dread_pilot",
            "-is_fax_pilot",
            "-is_capital_pilot",
            "-is_cyno_alt",
            "character_name",
        )
    )

    heatmap_data = _format_heatmap_data(
        corporation.activity_heatmap, entity=corporation
    )

    context = {
        "corporation": corporation,
        "timers": timers,
        "pilots": pilots,
        "alt_corps": corporation.alt_corps.all(),
        "staging_systems": staging_systems,
        "structures": structures,
        "doctrines": doctrines,
        "heatmap_data": heatmap_data,
    }
    return render(request, "hostile/corporation_detail.html", context)


@login_required
@permission_required("hostile.manage_intel")
def alliance_sync_zkill(request: WSGIRequest, alliance_id: int) -> HttpResponse:
    """Refreshes zKillboard statistics (active pilots, weekly kills/losses, supers) for an alliance"""
    alliance = get_object_or_404(HostileAlliance, id=alliance_id)
    success = zkill_client.update_alliance_stats(alliance)
    if success:
        messages.success(
            request,
            f"zKillboard stats refreshed for '{alliance.alliance_name}': {alliance.active_pilots_count} active pilots, {alliance.weekly_kills_count} kills in last 7 days.",
        )
    else:
        messages.warning(
            request,
            f"Could not retrieve zKillboard stats for '{alliance.alliance_name}' (ID: {alliance.alliance_id}).",
        )
    return redirect(request.headers.get("referer", "hostile:alliances"))


@login_required
@permission_required("hostile.basic_access")
def doctrines_list(request: WSGIRequest) -> HttpResponse:
    """Staging systems and hostile fleet doctrines directory with example fits and zKill links"""
    doctrines = (
        HostileDoctrine.objects.all()
        .select_related("alliance", "corporation", "created_by")
        .prefetch_related("stagings__solar_system", "example_fits__ship_type")
        .order_by("name")
    )
    stagings = (
        HostileStagingSystem.objects.all()
        .select_related(
            "solar_system", "alliance", "corporation", "structure", "created_by"
        )
        .prefetch_related("doctrines")
        .order_by("-is_primary", "solar_system__name")
    )
    doctrine_form = HostileDoctrineForm()
    staging_form = HostileStagingSystemForm()
    fit_form = DoctrineFitForm()
    paste_form = SmartPasteForm()

    context = {
        "doctrines": doctrines,
        "stagings": stagings,
        "doctrine_form": doctrine_form,
        "staging_form": staging_form,
        "fit_form": fit_form,
        "paste_form": paste_form,
    }
    return render(request, "hostile/doctrines.html", context)


@login_required
@permission_required("hostile.basic_access")
def doctrine_add(request: WSGIRequest) -> HttpResponse:
    """Creates a new hostile doctrine definition"""
    if request.method == "POST":
        form = HostileDoctrineForm(request.POST)
        if form.is_valid():
            doctrine = form.save(commit=False)
            doctrine.created_by = request.user
            if request.user.has_perm("hostile.manage_intel"):
                doctrine.is_verified = True
            doctrine.save()
            form.save_m2m()
            IntelManager.record_audit(
                request.user, "CREATE", doctrine, f"Doctrine '{doctrine.name}' added."
            )
            messages.success(request, f"Doctrine '{doctrine.name}' saved successfully.")
            return redirect("hostile:doctrines")
    return redirect("hostile:doctrines")


@login_required
@permission_required("hostile.manage_intel")
def doctrine_verify(request: WSGIRequest, doctrine_id: int) -> HttpResponse:
    """Officers verify a hostile doctrine"""
    doctrine = get_object_or_404(HostileDoctrine, id=doctrine_id)
    IntelManager.verify_doctrine(doctrine, request.user)
    messages.success(request, f"Doctrine '{doctrine.name}' verified.")
    return redirect(request.headers.get("referer", "hostile:doctrines"))


@login_required
@permission_required("hostile.basic_access")
def doctrine_edit(request: WSGIRequest, doctrine_id: int) -> HttpResponse:
    """Edits an existing hostile doctrine"""
    doctrine = get_object_or_404(
        HostileDoctrine.objects.select_related(
            "alliance", "corporation"
        ).prefetch_related("stagings"),
        id=doctrine_id,
    )
    if not user_can_edit_object(request.user, doctrine):
        messages.error(request, "You do not have permission to edit this doctrine.")
        return redirect("hostile:doctrines")
    if request.method == "POST":
        form = HostileDoctrineForm(request.POST, instance=doctrine)
        if form.is_valid():
            saved_doctrine = form.save()
            IntelManager.record_audit(
                request.user,
                "EDIT",
                saved_doctrine,
                f"Doctrine '{saved_doctrine.name}' updated.",
            )
            messages.success(request, f"Doctrine '{saved_doctrine.name}' updated.")
            return redirect("hostile:doctrines")
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
    else:
        form = HostileDoctrineForm(instance=doctrine)

    return render(
        request, "hostile/doctrine_form.html", {"form": form, "doctrine": doctrine}
    )


@login_required
@permission_required("hostile.manage_intel")
def doctrine_delete(request: WSGIRequest, doctrine_id: int) -> HttpResponse:
    """Deletes a hostile doctrine and its fits"""
    doctrine = get_object_or_404(HostileDoctrine, id=doctrine_id)
    if request.method == "POST":
        doc_name = doctrine.name
        IntelManager.record_audit(
            request.user, "DELETE", doctrine, f"Deleted doctrine '{doc_name}'."
        )
        doctrine.delete()
        messages.success(request, f"Doctrine '{doc_name}' deleted successfully.")
        return redirect("hostile:doctrines")

    return render(
        request, "hostile/doctrine_confirm_delete.html", {"doctrine": doctrine}
    )


@login_required
@permission_required("hostile.basic_access")
def doctrine_fit_add(request: WSGIRequest, doctrine_id: int) -> HttpResponse:
    """Adds an example fit to a doctrine with optional zKillboard link or EFT text"""
    doctrine = get_object_or_404(HostileDoctrine, id=doctrine_id)
    if request.method == "POST":
        form = DoctrineFitForm(request.POST)
        if form.is_valid():
            fit = form.save(commit=False)
            fit.doctrine = doctrine
            fit.created_by = request.user
            if not fit.ship_type and fit.eft_format:
                parsed = EFTParser.parse_eft(fit.eft_format)
                if parsed.get("hull_item_type"):
                    fit.ship_type = parsed["hull_item_type"]
            fit.save()
            IntelManager.record_audit(
                request.user,
                "CREATE",
                fit,
                f"Example fit '{fit.name}' added to {doctrine.name}",
            )
            messages.success(request, f"Example fit '{fit.name}' added to doctrine.")
            return redirect("hostile:doctrines")
    return redirect("hostile:doctrines")


@login_required
@permission_required("hostile.basic_access")
def doctrine_fit_edit(request: WSGIRequest, fit_id: int) -> HttpResponse:
    """Edits an example fit for a doctrine"""
    fit = get_object_or_404(
        DoctrineFit.objects.select_related("doctrine", "ship_type"),
        id=fit_id,
    )
    if not user_can_edit_object(request.user, fit):
        messages.error(request, "You do not have permission to edit this example fit.")
        return redirect("hostile:doctrines")
    if request.method == "POST":
        form = DoctrineFitForm(request.POST, instance=fit)
        if form.is_valid():
            saved_fit = form.save(commit=False)
            if not saved_fit.ship_type and saved_fit.eft_format:
                parsed = EFTParser.parse_eft(saved_fit.eft_format)
                if parsed.get("hull_item_type"):
                    saved_fit.ship_type = parsed["hull_item_type"]
            saved_fit.save()
            IntelManager.record_audit(
                request.user,
                "EDIT",
                saved_fit,
                f"Example fit '{saved_fit.name}' updated.",
            )
            messages.success(request, f"Example fit '{saved_fit.name}' updated.")
            return redirect("hostile:doctrines")
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
    else:
        form = DoctrineFitForm(instance=fit)

    return render(
        request, "hostile/doctrine_fit_form.html", {"form": form, "doctrine_fit": fit}
    )


@login_required
@permission_required("hostile.manage_intel")
def doctrine_fit_delete(request: WSGIRequest, fit_id: int) -> HttpResponse:
    """Deletes an example fit from a doctrine"""
    fit = get_object_or_404(DoctrineFit.objects.select_related("doctrine"), id=fit_id)
    if request.method == "POST":
        fit_name = fit.name
        doc_name = fit.doctrine.name if fit.doctrine else ""
        IntelManager.record_audit(
            request.user,
            "DELETE",
            fit,
            f"Deleted fit '{fit_name}' from doctrine '{doc_name}'.",
        )
        fit.delete()
        messages.success(request, f"Example fit '{fit_name}' deleted.")
        return redirect("hostile:doctrines")

    return render(
        request, "hostile/doctrine_fit_confirm_delete.html", {"doctrine_fit": fit}
    )


@login_required
@permission_required("hostile.basic_access")
def doctrine_fit_modal(request: WSGIRequest, fit_id: int) -> HttpResponse:
    """Corptools-inspired interactive fitting modal for a doctrine example fit"""
    fit = get_object_or_404(
        DoctrineFit.objects.select_related(
            "doctrine", "doctrine__alliance", "doctrine__corporation", "ship_type"
        ),
        id=fit_id,
    )
    grouped_modules = {
        "HIGH": [],
        "MED": [],
        "LOW": [],
        "RIG": [],
        "SERVICE": [],
        "CHARGE": [],
    }

    if fit.eft_format:
        parsed = EFTParser.parse_eft(fit.eft_format)
        for slot_name, mods in parsed["slots"].items():
            for idx, mod in enumerate(mods, 1):
                if mod.get("item_type"):
                    grouped_modules[slot_name].append(
                        {
                            "module_type": mod["item_type"],
                            "slot_type": slot_name,
                            "slot_number": idx,
                            "is_online": True,
                        }
                    )

    context = {
        "doctrine_fit": fit,
        "grouped_modules": grouped_modules,
        "eft_export": fit.eft_format,
    }
    return render(request, "hostile/doctrine_fit_modal.html", context)


@login_required
@permission_required("hostile.basic_access")
def staging_add(request: WSGIRequest) -> HttpResponse:
    """Adds a hostile staging system report"""
    if request.method == "POST":
        form = HostileStagingSystemForm(request.POST)
        if form.is_valid():
            staging = form.save(commit=False)
            staging.created_by = request.user
            if request.user.has_perm("hostile.manage_intel"):
                staging.is_verified = True
            staging.save()
            IntelManager.record_audit(
                request.user,
                "CREATE",
                staging,
                f"Staging system {staging.solar_system.name} added.",
            )
            messages.success(
                request,
                f"Staging system '{staging.solar_system.name}' saved successfully.",
            )
            return redirect("hostile:doctrines")
    return redirect("hostile:doctrines")


@login_required
@permission_required("hostile.manage_intel")
def staging_verify(request: WSGIRequest, staging_id: int) -> HttpResponse:
    """Officers verify a staging system"""
    staging = get_object_or_404(HostileStagingSystem, id=staging_id)
    IntelManager.verify_staging(staging, request.user)
    messages.success(request, f"Staging system '{staging.solar_system.name}' verified.")
    return redirect(request.headers.get("referer", "hostile:doctrines"))


@login_required
@permission_required("hostile.basic_access")
def staging_edit(request: WSGIRequest, staging_id: int) -> HttpResponse:
    """Edits an existing hostile staging system report"""
    staging = get_object_or_404(
        HostileStagingSystem.objects.select_related(
            "solar_system", "alliance", "corporation", "structure"
        ),
        id=staging_id,
    )
    if not user_can_edit_object(request.user, staging):
        messages.error(request, "You do not have permission to edit this staging hub.")
        return redirect("hostile:doctrines")
    if request.method == "POST":
        form = HostileStagingSystemForm(request.POST, instance=staging)
        if form.is_valid():
            saved_staging = form.save()
            IntelManager.record_audit(
                request.user,
                "EDIT",
                saved_staging,
                f"Staging system {saved_staging.solar_system.name} updated.",
            )
            messages.success(
                request, f"Staging system '{saved_staging.solar_system.name}' updated."
            )
            return redirect("hostile:doctrines")
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
    else:
        form = HostileStagingSystemForm(instance=staging)

    return render(
        request, "hostile/staging_form.html", {"form": form, "staging": staging}
    )


@login_required
@permission_required("hostile.manage_intel")
def staging_delete(request: WSGIRequest, staging_id: int) -> HttpResponse:
    """Deletes a hostile staging system report"""
    staging = get_object_or_404(
        HostileStagingSystem.objects.select_related("solar_system"),
        id=staging_id,
    )
    if request.method == "POST":
        sys_name = staging.solar_system.name if staging.solar_system else "Unknown"
        IntelManager.record_audit(
            request.user, "DELETE", staging, f"Deleted staging system in {sys_name}."
        )
        staging.delete()
        messages.success(request, f"Staging system in {sys_name} deleted successfully.")
        return redirect("hostile:doctrines")

    return render(request, "hostile/staging_confirm_delete.html", {"staging": staging})


@login_required
@permission_required("hostile.basic_access")
def smart_paste(request: WSGIRequest) -> HttpResponse:
    """Universal paste ingestion endpoint that routes clipboard text to local scans, structures, fittings, or sitreps"""
    if request.method == "POST":
        form = SmartPasteForm(request.POST)
        if form.is_valid():
            intel_text = form.cleaned_data["intel_text"]
            dscan_text = form.cleaned_data.get("dscan_text") or ""
            override_system = form.cleaned_data.get("solar_system")

            parsed = parse_intel_paste(
                intel_text,
                dscan_text=dscan_text,
                default_system=override_system,
                fetch_zkill=False,
            )
            fmt = parsed["format"]
            data = parsed["data"]

            if fmt == IntelFormat.LOCAL:
                scan = LocalThreatScan.objects.create(
                    solar_system=data.get("solar_system") or override_system,
                    raw_local_text=intel_text,
                    raw_dscan_text=dscan_text,
                    pilot_count=data.get("pilot_count", 0),
                    hostile_count=data.get("hostile_count", 0),
                    ignored_count=data.get("ignored_count", 0),
                    blops_drop_chance=data.get("blops_drop_chance", 0),
                    cap_drop_chance=data.get("cap_drop_chance", 0),
                    threat_level=data.get("threat_level", "LOW"),
                    threat_factors=data.get("threat_factors", []),
                    activity_profile=data.get("activity_profile", {}),
                    pilots_data=data.get("pilots", []),
                    ship_profile=data.get("ship_profile", {}),
                    created_by=request.user,
                )
                IntelManager.record_audit(
                    request.user,
                    "CREATE",
                    scan,
                    f"Local Threat scan generated ({scan.pilot_count} pilots, {scan.threat_level})",
                )
                messages.success(
                    request,
                    f"Local Threat scan analyzed: {scan.pilot_count} pilot(s), Blops drop {scan.blops_drop_chance}%, Cap drop {scan.cap_drop_chance}% ({scan.threat_level} Threat).",
                )
                return redirect("hostile:threat_scan_detail", scan_id=scan.id)

            elif fmt == IntelFormat.DSCAN:
                created_count = 0
                for s in data["structures"]:
                    struct, created = HostileStructure.objects.get_or_create(
                        name=s["name"],
                        defaults={
                            "structure_type": s["structure_type"],
                            "solar_system": s["solar_system"] or override_system,
                            "owner_ticker": s["owner_ticker"],
                            "location_details": s["location_details"],
                            "created_by": request.user,
                            "is_verified": request.user.has_perm(
                                "hostile.manage_intel"
                            ),
                        },
                    )
                    if created:
                        created_count += 1
                        IntelManager.record_audit(
                            request.user,
                            "CREATE",
                            struct,
                            f"Ingested from D-Scan in {struct.solar_system}",
                        )
                messages.success(
                    request,
                    f"D-Scan parsed: Found {len(data['structures'])} structure(s) ({created_count} new) and {len(data['ship_counts'])} ship type(s).",
                )
                return redirect("hostile:structures")

            elif fmt == IntelFormat.SHOWINFO:
                struct_system = data["solar_system"] or override_system
                alliance_obj = None
                if data.get("alliance_name"):
                    alliance_obj = HostileAlliance.objects.filter(
                        alliance_name__iexact=data["alliance_name"]
                    ).first()

                struct, created = HostileStructure.objects.get_or_create(
                    name=data["name"],
                    defaults={
                        "structure_type": data["structure_type"],
                        "solar_system": struct_system,
                        "alliance": alliance_obj,
                        "owner_ticker": data["owner_ticker"],
                        "core_status": data["core_status"],
                        "state": data["state"],
                        "vulnerability_window": data.get("vulnerability_window", ""),
                        "vulnerability_hour": data.get("vulnerability_hour"),
                        "location_details": data["location_details"],
                        "notes": data["notes"],
                        "created_by": request.user,
                        "is_verified": request.user.has_perm("hostile.manage_intel"),
                    },
                )
                if not created:
                    struct.core_status = data["core_status"]
                    struct.state = data["state"]
                    if data.get("vulnerability_window"):
                        struct.vulnerability_window = data["vulnerability_window"]
                    if data.get("vulnerability_hour") is not None:
                        struct.vulnerability_hour = data["vulnerability_hour"]
                    if alliance_obj and not struct.alliance:
                        struct.alliance = alliance_obj
                    if struct_system and not struct.solar_system:
                        struct.solar_system = struct_system
                    struct.save()

                if struct.alliance:
                    SovAnalysisEngine.aggregate_alliance_vulnerability_windows(
                        struct.alliance
                    )

                if data.get("fitting_data"):
                    fit_res = data["fitting_data"]
                    fitting = StructureFitting.objects.create(
                        structure=struct,
                        name=f"Inspect Fit {timezone.now().strftime('%Y-%m-%d')}",
                        eft_format="\n".join(data.get("fitting_lines", [])),
                        created_by=request.user,
                    )
                    for slot_name, mods in fit_res["slots"].items():
                        for idx, mod in enumerate(mods, 1):
                            if mod.get("item_type"):
                                StructureModule.objects.create(
                                    fitting=fitting,
                                    module_type=mod["item_type"],
                                    slot_type=slot_name,
                                    slot_number=idx,
                                    is_online=True,
                                )

                IntelManager.record_audit(
                    request.user,
                    "INGEST",
                    struct,
                    f"Ingested structure inspect profile for {struct.name}",
                )
                messages.success(
                    request,
                    f"Structure profile for '{struct.name}' cataloged successfully.",
                )
                return redirect("hostile:structures")

            elif fmt == IntelFormat.STRUCTURE_HACK:
                struct_system = data["solar_system"] or override_system
                struct_name = data["name"]
                vuln_hour = data.get("vulnerability_hour")
                vuln_window = data.get("vulnerability_window", "")

                # Look up existing structure by name and system, or by exact name
                struct = None
                if struct_system:
                    struct = HostileStructure.objects.filter(
                        solar_system=struct_system,
                        name__iexact=struct_name,
                    ).first()
                if not struct:
                    struct = HostileStructure.objects.filter(
                        name__iexact=struct_name
                    ).first()

                if struct:
                    if vuln_hour is not None:
                        struct.vulnerability_hour = vuln_hour
                    if vuln_window:
                        struct.vulnerability_window = vuln_window
                    if struct_system and not struct.solar_system:
                        struct.solar_system = struct_system
                    if data.get("owner_ticker") and not struct.owner_ticker:
                        struct.owner_ticker = data["owner_ticker"]
                    if data.get("structure_type") and not struct.structure_type:
                        struct.structure_type = data["structure_type"]
                    struct.save()
                else:
                    struct = HostileStructure.objects.create(
                        name=struct_name,
                        structure_type=data.get("structure_type"),
                        solar_system=struct_system,
                        owner_ticker=data.get("owner_ticker", ""),
                        vulnerability_hour=vuln_hour,
                        vulnerability_window=vuln_window,
                        notes=data.get("notes", ""),
                        created_by=request.user,
                        is_verified=request.user.has_perm("hostile.manage_intel"),
                    )

                if struct.alliance:
                    SovAnalysisEngine.aggregate_alliance_vulnerability_windows(
                        struct.alliance
                    )

                hour_display = (
                    f"{vuln_hour:02d}:00 UTC (±3h)"
                    if vuln_hour is not None
                    else "Unknown"
                )
                IntelManager.record_audit(
                    request.user,
                    "HACK",
                    struct,
                    f"Data Analyzer timer hack parsed for {struct.name} (Vulnerability Hour: {hour_display})",
                )
                messages.success(
                    request,
                    f"Data Analyzer timer hack parsed for '{struct.name}': Scanned defense vulnerability hour set to {hour_display}.",
                )
                return redirect(
                    f"{reverse('hostile:calculator')}?structure_id={struct.id}"
                )

            elif fmt in [IntelFormat.EFT, IntelFormat.STRUCTURE_FIT]:
                fit_res = data
                hull_type = fit_res.get("hull_item_type")
                fit_name = fit_res.get("fitting_name")
                if not fit_name:
                    hull_display = hull_type.name if hull_type else "Structure"
                    fit_name = f"Scanned {hull_display} Fit ({timezone.now().strftime('%Y-%m-%d %H:%M')})"

                struct = None
                if override_system:
                    # Check if structure with fitting_name exists in this system
                    if fit_res.get("fitting_name"):
                        struct = HostileStructure.objects.filter(
                            solar_system=override_system,
                            name__iexact=fit_res["fitting_name"],
                        ).first()
                    if not struct:
                        # Or match existing structure in system with same hull type
                        if hull_type:
                            struct = HostileStructure.objects.filter(
                                solar_system=override_system,
                                structure_type=hull_type,
                            ).first()
                elif fit_res.get("fitting_name"):
                    struct = HostileStructure.objects.filter(
                        name__iexact=fit_res["fitting_name"]
                    ).first()

                if not struct:
                    struct = HostileStructure.objects.create(
                        name=fit_name,
                        structure_type=hull_type,
                        solar_system=override_system,
                        created_by=request.user,
                        is_verified=request.user.has_perm("hostile.manage_intel"),
                    )
                elif hull_type and not struct.structure_type:
                    struct.structure_type = hull_type
                    struct.save(update_fields=["structure_type"])

                fitting = StructureFitting.objects.create(
                    structure=struct,
                    name=fit_name,
                    eft_format=intel_text,
                    created_by=request.user,
                )
                created_mod_count = 0
                for slot_name, mods in fit_res["slots"].items():
                    for idx, mod in enumerate(mods, 1):
                        if mod.get("item_type"):
                            StructureModule.objects.create(
                                fitting=fitting,
                                module_type=mod["item_type"],
                                slot_type=slot_name,
                                slot_number=idx,
                                is_online=True,
                            )
                            created_mod_count += 1
                IntelManager.record_audit(
                    request.user,
                    "INGEST",
                    struct,
                    f"Ingested structure fitting for {struct.name} ({created_mod_count} modules)",
                )
                messages.success(
                    request,
                    f"Structure fitting parsed ({created_mod_count} modules) and attached to '{struct.name}'.",
                )
                return redirect("hostile:structures")

            else:
                # Raw text / Sitrep observation
                if override_system:
                    obs = SystemObservation.objects.create(
                        solar_system=override_system,
                        threat_level="MEDIUM",
                        observation_text=intel_text,
                        created_by=request.user,
                        is_verified=request.user.has_perm("hostile.manage_intel"),
                    )
                    IntelManager.record_audit(
                        request.user,
                        "CREATE",
                        obs,
                        f"Raw paste observation created in {override_system.name}",
                    )
                    messages.success(
                        request,
                        f"System observation created in {override_system.name}.",
                    )
                    return redirect("hostile:systems")
                else:
                    messages.warning(
                        request,
                        "Could not auto-detect a structure or solar system from paste. Please select a solar system override.",
                    )
                    return redirect("hostile:systems")

    return redirect("hostile:index")


@login_required
@permission_required("hostile.basic_access")
def threat_scan_list(request: WSGIRequest) -> HttpResponse:
    """Local Threat Intel scanner directory and new scan submission view"""
    if request.method == "POST":
        form = LocalThreatScanForm(request.POST)
        if form.is_valid():
            local_text = form.cleaned_data["local_text"]
            dscan_text = form.cleaned_data.get("dscan_text") or ""
            override_system = form.cleaned_data.get("solar_system")

            data = LocalThreatParser.parse(
                local_text,
                dscan_text=dscan_text,
                default_system=override_system,
                fetch_zkill=False,
            )
            scan = LocalThreatScan.objects.create(
                solar_system=data.get("solar_system") or override_system,
                raw_local_text=local_text,
                raw_dscan_text=dscan_text,
                pilot_count=data.get("pilot_count", 0),
                hostile_count=data.get("hostile_count", 0),
                ignored_count=data.get("ignored_count", 0),
                blops_drop_chance=data.get("blops_drop_chance", 0),
                cap_drop_chance=data.get("cap_drop_chance", 0),
                threat_level=data.get("threat_level", "LOW"),
                threat_factors=data.get("threat_factors", []),
                activity_profile=data.get("activity_profile", {}),
                pilots_data=data.get("pilots", []),
                ship_profile=data.get("ship_profile", {}),
                created_by=request.user,
            )
            IntelManager.record_audit(
                request.user,
                "CREATE",
                scan,
                f"Local Threat scan created in {scan.solar_system}",
            )
            messages.success(
                request,
                f"Local Threat scan created: {scan.pilot_count} pilots, Blops drop {scan.blops_drop_chance}%, Cap drop {scan.cap_drop_chance}% ({scan.threat_level} Threat).",
            )
            return redirect("hostile:threat_scan_detail", scan_id=scan.id)
    else:
        form = LocalThreatScanForm()

    recent_scans = (
        LocalThreatScan.objects.all()
        .select_related("solar_system", "created_by")
        .order_by("-created_at")[:25]
    )
    all_systems = SolarSystem.objects.all().order_by("name")
    paste_form = SmartPasteForm()

    context = {
        "form": form,
        "recent_scans": recent_scans,
        "paste_form": paste_form,
        "all_systems": all_systems,
    }
    return render(request, "hostile/threat_scans.html", context)


@login_required
@permission_required("hostile.basic_access")
def threat_scan_detail(request: WSGIRequest, scan_id) -> HttpResponse:
    """localthreat.xyz-style detailed threat report with drop probabilities, activity profile, and ship pairing"""
    scan = get_object_or_404(
        LocalThreatScan.objects.select_related("solar_system", "created_by"),
        id=scan_id,
    )
    paste_form = SmartPasteForm()

    context = {
        "scan": scan,
        "pilots": scan.pilots_data,
        "activity_profile": scan.activity_profile,
        "ship_profile": scan.ship_profile,
        "threat_factors": scan.threat_factors,
        "paste_form": paste_form,
    }
    return render(request, "hostile/threat_scan_detail.html", context)


@login_required
@permission_required("hostile.basic_access")
def api_threat_scan_pilot_intelligence(
    request: WSGIRequest, scan_id
) -> JsonResponse:
    """
    Asynchronously streams enriched pilot combat intelligence (corporations, alliances,
    top ships flown, cyno deaths, zKill tags, alt detection, and drop chances)
    to prevent HTTP 504 timeouts on large rosters.
    """
    scan = get_object_or_404(
        LocalThreatScan.objects.select_related("solar_system"), id=scan_id
    )

    batch_size = 5
    char_names = None

    if request.method == "POST":
        try:
            import json

            body_data = (
                json.loads(request.body.decode("utf-8")) if request.body else {}
            )
            if "batch_size" in body_data:
                batch_size = int(body_data["batch_size"])
            if "character_names" in body_data and isinstance(
                body_data["character_names"], list
            ):
                char_names = body_data["character_names"]
        except Exception:
            pass
    else:
        if "batch_size" in request.GET:
            try:
                batch_size = int(request.GET["batch_size"])
            except ValueError:
                pass
        if "character_names" in request.GET:
            char_names = [
                n.strip()
                for n in request.GET["character_names"].split(",")
                if n.strip()
            ]

    batch_size = max(1, min(batch_size, 20))

    result = LocalThreatParser.enrich_scan(
        scan,
        character_names=char_names,
        batch_size=batch_size,
    )

    return JsonResponse(result)


@login_required
@permission_required("hostile.manage_intel")
def threat_scan_delete(request: WSGIRequest, scan_id) -> HttpResponse:
    """Deletes a local threat scan record"""
    scan = get_object_or_404(
        LocalThreatScan.objects.select_related("solar_system"), id=scan_id
    )
    if request.method == "POST":
        sys_name = scan.solar_system.name if scan.solar_system else "Unknown"
        IntelManager.record_audit(
            request.user,
            "DELETE",
            scan,
            f"Deleted threat scan {scan.id} in {sys_name}.",
        )
        scan.delete()
        messages.success(request, f"Threat scan in {sys_name} deleted successfully.")
        return redirect("hostile:threat_scans")

    return render(request, "hostile/threat_scan_confirm_delete.html", {"scan": scan})


@login_required
@permission_required("hostile.manage_intel")
def audit_logs_list(request: WSGIRequest) -> HttpResponse:
    """Intelligence audit log view for officers"""
    logs = IntelAuditLog.objects.select_related("user").order_by("-timestamp")[:100]
    context = {"logs": logs}
    return render(request, "hostile/audit_logs.html", context)


@login_required
@permission_required("hostile.basic_access")
def timer_calculator(request: WSGIRequest) -> HttpResponse:
    """
    Upwell Structure and POCO reinforce exit timer calculator.
    Calculates estimated exit windows based on CCP vulnerability rules, space type, and scanned vulnerability hours.
    """
    initial_data = {}
    structure_obj = None

    # Handle pre-fill query parameters (e.g. ?structure_id=123)
    structure_id = request.GET.get("structure_id")
    if structure_id:
        try:
            structure_obj = HostileStructure.objects.select_related(
                "structure_type", "solar_system", "alliance"
            ).get(id=structure_id)
            initial_data["structure"] = structure_obj
            initial_data["solar_system"] = structure_obj.solar_system
            initial_data["structure_size"] = (
                ReinforcementCalculator.infer_structure_size(
                    structure_obj.structure_type or structure_obj.name
                )
            )
            initial_data["location_type"] = ReinforcementCalculator.infer_location_type(
                structure_obj.solar_system
            )
            if structure_obj.vulnerability_hour is not None:
                initial_data["vulnerability_hour"] = structure_obj.vulnerability_hour
            if structure_obj.vulnerability_window:
                initial_data["vulnerability_window"] = (
                    structure_obj.vulnerability_window
                )
            if structure_obj.state == "LOW_POWER":
                initial_data["is_low_power"] = True
                initial_data["power_state"] = PowerState.LOW_POWER
            elif structure_obj.state == "ABANDONED":
                initial_data["power_state"] = PowerState.ABANDONED

            # Set valid stage for initial structure
            size = initial_data.get("structure_size")
            if size == StructureCategory.FLEX:
                initial_data["timer_stage"] = TimerStage.ARMOR_REINFORCE
            elif size == StructureCategory.LARGE_XL and initial_data.get(
                "is_low_power"
            ):
                initial_data["timer_stage"] = TimerStage.ARMOR_REINFORCE
            elif size in (StructureCategory.MEDIUM, StructureCategory.POCO):
                initial_data["timer_stage"] = TimerStage.SHIELD_REINFORCE
        except (HostileStructure.DoesNotExist, ValueError):
            pass

    if "stage" in request.GET:
        initial_data["timer_stage"] = request.GET.get("stage")
    if "hour" in request.GET:
        try:
            initial_data["vulnerability_hour"] = int(request.GET.get("hour"))
        except ValueError:
            pass

    calc_result = None

    if request.method == "POST":
        form = ReinforcementCalculatorForm(request.POST)
        if form.is_valid():
            structure = form.cleaned_data.get("structure")
            solar_system = form.cleaned_data.get("solar_system")
            structure_size = form.cleaned_data.get("structure_size")
            location_type = form.cleaned_data.get("location_type")
            timer_stage = form.cleaned_data.get("timer_stage")
            power_state = form.cleaned_data.get("power_state")
            reinforced_at = form.cleaned_data.get("reinforced_at") or timezone.now()
            vulnerability_hour = form.cleaned_data.get("vulnerability_hour")
            vulnerability_window = form.cleaned_data.get("vulnerability_window") or ""
            save_to_timers = form.cleaned_data.get("save_to_timers")
            notes = form.cleaned_data.get("notes") or ""

            # If a structure was selected and system / size not explicitly overridden
            if structure:
                if not solar_system and structure.solar_system:
                    solar_system = structure.solar_system
                if (
                    vulnerability_hour is None
                    and structure.vulnerability_hour is not None
                ):
                    vulnerability_hour = structure.vulnerability_hour
                if not vulnerability_window and structure.vulnerability_window:
                    vulnerability_window = structure.vulnerability_window

            calc_result = ReinforcementCalculator.calculate_exit_timer(
                reinforced_at=reinforced_at,
                structure_size=structure_size,
                location_type=location_type,
                timer_stage=timer_stage,
                vulnerability_hour=vulnerability_hour,
                vulnerability_window=vulnerability_window,
                power_state=power_state,
            )

            if (
                save_to_timers
                and calc_result.is_applicable
                and calc_result.nominal_exit
            ):
                timer = StructureTimer.objects.create(
                    structure=structure,
                    solar_system=solar_system
                    or (structure.solar_system if structure else None),
                    timer_type=timer_stage,
                    timer_datetime=calc_result.nominal_exit,
                    notes=f"{notes} [Calculated Exit Window: {calc_result.exit_window_str}]".strip(),
                    created_by=request.user,
                    is_verified=request.user.has_perm("hostile.manage_intel"),
                )
                IntelManager.record_audit(
                    request.user,
                    "CREATE",
                    timer,
                    f"Timer {timer.timer_type} created via Reinforcement Calculator.",
                )
                messages.success(
                    request,
                    f"Timer for {timer.structure.name if timer.structure else (timer.solar_system.name if timer.solar_system else 'Target')} ({timer.timer_type}) saved to Active Timer Board successfully!",
                )
                return redirect("hostile:timers")
    else:
        form = ReinforcementCalculatorForm(initial=initial_data)
        # If pre-filled via GET parameters (e.g. structure clicked), calculate initial default preview
        if initial_data:
            calc_result = ReinforcementCalculator.calculate_exit_timer(
                reinforced_at=timezone.now(),
                structure_size=initial_data.get(
                    "structure_size", StructureCategory.MEDIUM
                ),
                location_type=initial_data.get(
                    "location_type", LocationSecurity.NULLSEC_LOWSEC
                ),
                timer_stage=initial_data.get(
                    "timer_stage", TimerStage.SHIELD_REINFORCE
                ),
                vulnerability_hour=initial_data.get("vulnerability_hour"),
                vulnerability_window=initial_data.get("vulnerability_window", ""),
                power_state=initial_data.get("power_state", PowerState.FULL_POWER),
            )

    paste_form = SmartPasteForm()

    context = {
        "form": form,
        "calc_result": calc_result,
        "paste_form": paste_form,
        "structure_obj": structure_obj,
    }
    return render(request, "hostile/calculator.html", context)


@login_required
@permission_required("hostile.basic_access")
def api_timer_calculate(request: WSGIRequest) -> JsonResponse:
    """JSON API endpoint for dynamic asynchronous timer calculation"""
    if request.method in ["POST", "GET"]:
        data = request.POST if request.method == "POST" else request.GET

        structure_id = data.get("structure_id")
        structure_obj = None
        if structure_id:
            try:
                structure_obj = HostileStructure.objects.select_related(
                    "structure_type", "solar_system"
                ).get(id=structure_id)
            except (HostileStructure.DoesNotExist, ValueError):
                pass

        structure_size = data.get("structure_size")
        if not structure_size and structure_obj:
            structure_size = ReinforcementCalculator.infer_structure_size(
                structure_obj.structure_type or structure_obj.name
            )
        structure_size = structure_size or StructureCategory.MEDIUM

        location_type = data.get("location_type")
        if not location_type and structure_obj:
            location_type = ReinforcementCalculator.infer_location_type(
                structure_obj.solar_system
            )
        location_type = location_type or LocationSecurity.NULLSEC_LOWSEC

        timer_stage = data.get("timer_stage") or TimerStage.SHIELD_REINFORCE
        is_low_power = data.get("is_low_power") in [True, "true", "True", "1", "on"]
        power_state = data.get("power_state") or (
            PowerState.LOW_POWER if is_low_power else PowerState.FULL_POWER
        )
        if is_low_power and power_state != PowerState.ABANDONED:
            power_state = PowerState.LOW_POWER

        reinforced_at_str = data.get("reinforced_at")
        reinforced_at = None
        if reinforced_at_str:
            reinforced_at = parse_datetime(reinforced_at_str)
        if not reinforced_at:
            reinforced_at = timezone.now()

        vulnerability_hour_val = data.get("vulnerability_hour")
        vulnerability_hour = None
        if vulnerability_hour_val not in [None, ""]:
            try:
                vulnerability_hour = int(vulnerability_hour_val)
            except ValueError:
                pass
        elif structure_obj and structure_obj.vulnerability_hour is not None:
            vulnerability_hour = structure_obj.vulnerability_hour

        vulnerability_window = data.get("vulnerability_window") or (
            structure_obj.vulnerability_window if structure_obj else ""
        )

        result = ReinforcementCalculator.calculate_exit_timer(
            reinforced_at=reinforced_at,
            structure_size=structure_size,
            location_type=location_type,
            timer_stage=timer_stage,
            vulnerability_hour=vulnerability_hour,
            vulnerability_window=vulnerability_window,
            power_state=power_state,
            is_low_power=is_low_power,
        )

        valid_stages = [
            {"value": s[0], "label": s[1]}
            for s in ReinforcementCalculator.get_valid_stages(
                structure_size, is_low_power=is_low_power
            )
        ]

        return JsonResponse(
            {
                "success": True,
                "is_applicable": result.is_applicable,
                "status_message": result.status_message,
                "exit_window_str": result.exit_window_str,
                "nominal_exit": (
                    result.nominal_exit.isoformat() if result.nominal_exit else None
                ),
                "earliest_exit": (
                    result.earliest_exit.isoformat() if result.earliest_exit else None
                ),
                "latest_exit": (
                    result.latest_exit.isoformat() if result.latest_exit else None
                ),
                "base_delay_hours": result.base_delay_hours,
                "jitter_hours": result.jitter_hours,
                "duration_min_hours": result.duration_min_hours,
                "duration_max_hours": result.duration_max_hours,
                "time_remaining_str": result.time_remaining_str,
                "notes": result.notes,
                "valid_stages": valid_stages,
            }
        )

    return JsonResponse(
        {"success": False, "error": "Invalid request method"}, status=400
    )


@login_required
@permission_required("hostile.basic_access")
def api_solar_systems_search(request: WSGIRequest) -> JsonResponse:
    """JSON API endpoint for searching solar systems by name substring with limit"""
    q = request.GET.get("q", "").strip()
    try:
        limit = min(int(request.GET.get("limit", 25)), 100)
    except ValueError:
        limit = 25
    qs = SolarSystem.objects.all().order_by("name")
    if q:
        qs = qs.filter(name__icontains=q)
    results = [{"id": s.id, "name": s.name} for s in qs[:limit]]
    return JsonResponse({"results": results})


@login_required
@permission_required("hostile.basic_access")
def api_infer_system(request: WSGIRequest) -> JsonResponse:
    """JSON API endpoint to infer solar system from pasted D-Scan or clipboard text"""
    text = (request.POST.get("text") or request.GET.get("text") or "").strip()
    detected = DScanParser.parse(text).get("solar_system") if text else None
    if detected:
        return JsonResponse(
            {
                "success": True,
                "solar_system_id": detected.id,
                "solar_system_name": detected.name,
            }
        )
    return JsonResponse(
        {
            "success": False,
            "solar_system_id": None,
            "solar_system_name": None,
        }
    )


@login_required
@permission_required("hostile.basic_access")
def api_character_search(request: WSGIRequest) -> JsonResponse:
    """JSON API endpoint for fast character autocomplete by name or ID"""
    q = request.GET.get("q", "").strip()
    try:
        limit = min(int(request.GET.get("limit", 15)), 50)
    except ValueError:
        limit = 15
    # AA Hostile Intel
    from hostile.services.entity_resolver import EntityResolver

    results = EntityResolver.search_characters(q, limit=limit)
    return JsonResponse({"results": results})


@login_required
@permission_required("hostile.basic_access")
def api_character_resolve(request: WSGIRequest) -> JsonResponse:
    """JSON API endpoint for resolving character by Name OR ID and returning auto-filled corp and alliance info"""
    query = (
        request.GET.get("query")
        or request.GET.get("q")
        or request.POST.get("query")
        or ""
    ).strip()
    if not query:
        return JsonResponse({"success": False, "error": "Query required"}, status=400)
    # AA Hostile Intel
    from hostile.services.entity_resolver import EntityResolver

    res = EntityResolver.resolve_character(query)
    if res:
        return JsonResponse({"success": True, "character": res})
    return JsonResponse(
        {"success": False, "error": f"Character '{query}' not found"}, status=404
    )


@login_required
@permission_required("hostile.basic_access")
def api_alliance_search(request: WSGIRequest) -> JsonResponse:
    """JSON API endpoint for fast alliance autocomplete by name, ticker, or ID"""
    q = request.GET.get("q", "").strip()
    try:
        limit = min(int(request.GET.get("limit", 15)), 50)
    except ValueError:
        limit = 15
    # AA Hostile Intel
    from hostile.services.entity_resolver import EntityResolver

    results = EntityResolver.search_alliances(q, limit=limit)
    return JsonResponse({"results": results})


@login_required
@permission_required("hostile.basic_access")
def api_alliance_resolve(request: WSGIRequest) -> JsonResponse:
    """JSON API endpoint for resolving alliance by Name OR ID and returning ticker and stats"""
    query = (
        request.GET.get("query")
        or request.GET.get("q")
        or request.POST.get("query")
        or ""
    ).strip()
    if not query:
        return JsonResponse({"success": False, "error": "Query required"}, status=400)
    # AA Hostile Intel
    from hostile.services.entity_resolver import EntityResolver

    res = EntityResolver.resolve_alliance(query)
    if res:
        return JsonResponse({"success": True, "alliance": res})
    return JsonResponse(
        {"success": False, "error": f"Alliance '{query}' not found"}, status=404
    )


@login_required
@permission_required("hostile.basic_access")
def api_corporation_search(request: WSGIRequest) -> JsonResponse:
    """JSON API endpoint for fast corporation autocomplete by name, ticker, or ID"""
    q = request.GET.get("q", "").strip()
    try:
        limit = min(int(request.GET.get("limit", 15)), 50)
    except ValueError:
        limit = 15
    # AA Hostile Intel
    from hostile.services.entity_resolver import EntityResolver

    results = EntityResolver.search_corporations(q, limit=limit)
    return JsonResponse({"results": results})


@login_required
@permission_required("hostile.basic_access")
def api_corporation_resolve(request: WSGIRequest) -> JsonResponse:
    """JSON API endpoint for resolving corporation by Name OR ID and returning ticker and stats"""
    query = (
        request.GET.get("query")
        or request.GET.get("q")
        or request.POST.get("query")
        or ""
    ).strip()
    if not query:
        return JsonResponse({"success": False, "error": "Query required"}, status=400)
    # AA Hostile Intel
    from hostile.services.entity_resolver import EntityResolver

    res = EntityResolver.resolve_corporation(query)
    if res:
        return JsonResponse({"success": True, "corporation": res})
    return JsonResponse(
        {"success": False, "error": f"Corporation '{query}' not found"}, status=404
    )


@login_required
@permission_required("hostile.basic_access")
def api_routes_batch(request: WSGIRequest) -> JsonResponse:
    """JSON API endpoint returning jump distances (LY) and shortest stargate jumps from origin to destination systems"""
    origin_raw = (
        request.GET.get("origin")
        or request.POST.get("origin")
        or request.GET.get("origin_id")
        or ""
    ).strip()
    if not origin_raw:
        return JsonResponse({"error": "Missing origin solar system ID"}, status=400)

    try:
        origin_id = int(origin_raw)
    except ValueError:
        sys = SolarSystem.objects.filter(name__iexact=origin_raw).first()
        if not sys:
            return JsonResponse(
                {"error": f"Solar system '{origin_raw}' not found"}, status=404
            )
        origin_id = sys.id

    dest_param = (
        request.GET.get("destinations")
        or request.POST.get("destinations")
        or request.GET.get("destination_ids")
        or ""
    ).strip()
    destination_ids: list[int] = []
    if dest_param:
        for part in dest_param.split(","):
            part = part.strip()
            if part.isdigit():
                destination_ids.append(int(part))

    flag = (request.GET.get("flag") or "shortest").strip()

    # AA Hostile Intel
    from hostile.services.map_routing import MapRoutingService

    routes_data = MapRoutingService.get_batch_routes_summary(
        origin_id=origin_id,
        destination_ids=destination_ids,
        flag=flag,
    )
    return JsonResponse({"origin_id": origin_id, "routes": routes_data})


@login_required
@permission_required("hostile.basic_access")
def api_sov_timers(request: WSGIRequest) -> JsonResponse:
    """
    JSON API endpoint returning active and concluded sovereignty campaign timers for live polling (e.g. every 60 seconds).
    Synchronizes new ESI contest campaigns periodically with throttled caching and calculates origin routes.
    """
    # Optional ESI sync check (throttled to at most once every 45s per cache key)
    do_sync = request.GET.get("sync") == "1" or not cache.get("hostile_sov_campaigns_synced_recent")
    if do_sync:
        try:
            campaigns = esi_client.get_sovereignty_campaigns()
            if campaigns:
                SovAnalysisEngine.process_sovereignty_campaigns(campaigns)
            cache.set("hostile_sov_campaigns_synced_recent", True, 45)
        except Exception as exc:
            logger.debug("Failed on-demand ESI sovereignty campaigns sync in API: %s", exc)

    now = timezone.now()
    cutoff_24h = now - timedelta(hours=24)

    upcoming_timers_qs = (
        StructureTimer.objects.sov_campaigns()
        .filter(is_concluded=False, timer_datetime__gte=cutoff_24h)
        .select_related("solar_system__constellation__region", "structure")
    )
    expired_timers_qs = (
        StructureTimer.objects.sov_campaigns()
        .filter(Q(is_concluded=True) | Q(is_sov_campaign=False, timer_datetime__lt=now))
        .filter(timer_datetime__gte=cutoff_24h)
        .select_related("solar_system__constellation__region", "structure")
    )
    upcoming_timers = list(upcoming_timers_qs)
    expired_timers = list(expired_timers_qs)

    selected_origin_id = None
    origin_raw = request.GET.get("origin", "").strip()
    origin_system = None
    if origin_raw:
        if origin_raw.isdigit():
            origin_system = SolarSystem.objects.filter(id=int(origin_raw)).first()
        else:
            origin_system = SolarSystem.objects.filter(name__iexact=origin_raw).first()

    if origin_system:
        selected_origin_id = origin_system.id
        for timer in upcoming_timers:
            timer.distance_data = MapRoutingService.get_system_distance_summary(
                origin_system, timer.solar_system, flag="shortest", include_jumps=True
            )
        for timer in expired_timers:
            timer.distance_data = MapRoutingService.get_system_distance_summary(
                origin_system, timer.solar_system, flag="shortest", include_jumps=True
            )

    sort_param = request.GET.get("sort", "").strip().lower()
    order_param = request.GET.get("order", "asc").strip().lower()
    is_desc = order_param == "desc"

    def get_sort_key(timer: StructureTimer, key: str) -> Any:
        if key == "region":
            return (timer.region_name or "").lower()
        elif key == "system":
            return (timer.solar_system.name if timer.solar_system else "").lower()
        elif key == "ly":
            if (
                hasattr(timer, "distance_data")
                and timer.distance_data
                and timer.distance_data.get("ly") is not None
            ):
                return timer.distance_data.get("ly")
            return 999999.0
        elif key == "jumps":
            if (
                hasattr(timer, "distance_data")
                and timer.distance_data
                and timer.distance_data.get("jumps") is not None
            ):
                return timer.distance_data.get("jumps")
            return 999999
        elif key == "structure":
            return (timer.structure_event_name or "").lower()
        elif key == "defender":
            return (timer.defender_display or "").lower()
        elif key == "score":
            return timer.defender_percent if timer.defender_percent is not None else 0
        elif key == "time":
            return timer.timer_datetime
        return timer.timer_datetime

    if sort_param:
        upcoming_timers.sort(key=lambda t: get_sort_key(t, sort_param), reverse=is_desc)
        expired_timers.sort(key=lambda t: get_sort_key(t, sort_param), reverse=is_desc)
    else:
        upcoming_timers.sort(key=lambda t: t.timer_datetime)
        expired_timers.sort(key=lambda t: t.timer_datetime, reverse=True)

    def serialize_timer(timer: StructureTimer) -> Dict[str, Any]:
        return {
            "id": timer.id,
            "campaign_id": timer.campaign_id,
            "timer_type": timer.timer_type,
            "structure_event_name": timer.structure_event_name,
            "is_tracked_hostile": timer.is_tracked_hostile,
            "solar_system_id": timer.solar_system.id if timer.solar_system else None,
            "solar_system_name": timer.solar_system.name if timer.solar_system else "Unknown",
            "region_name": timer.region_name,
            "solar_system_x": timer.solar_system.x if timer.solar_system else None,
            "solar_system_y": timer.solar_system.y if timer.solar_system else None,
            "solar_system_z": timer.solar_system.z if timer.solar_system else None,
            "defender_id": timer.defender_id,
            "defender_name": timer.defender_name,
            "defender_ticker": timer.defender_ticker,
            "defender_display": timer.defender_display,
            "defender_score": timer.defender_score or 0.0,
            "defender_percent": timer.defender_percent,
            "attacker_percent": timer.attacker_percent,
            "has_started": timer.has_started,
            "is_ongoing": timer.is_ongoing,
            "is_concluded": timer.is_concluded,
            "outcome_display": timer.outcome_display,
            "timer_datetime": timer.timer_datetime.isoformat(),
            "timer_datetime_formatted": timer.timer_datetime.strftime("%Y-%m-%d %H:%M:%S") + " UTC",
            "timestamp": int(timer.timer_datetime.timestamp()),
            "distance_data": getattr(timer, "distance_data", None),
            "notes": timer.notes or "",
        }

    serialized_upcoming = [serialize_timer(t) for t in upcoming_timers]
    serialized_expired = [serialize_timer(t) for t in expired_timers]

    # Fingerprint hash for quick change detection on client
    fingerprint_raw = json.dumps(
        [
            (t["id"], t["defender_percent"], t["has_started"], t["is_concluded"], t["timestamp"])
            for t in (serialized_upcoming + serialized_expired)
        ],
        sort_keys=True,
    )
    content_hash = hashlib.md5(fingerprint_raw.encode("utf-8")).hexdigest()

    return JsonResponse(
        {
            "success": True,
            "hash": content_hash,
            "server_time": now.isoformat(),
            "selected_origin_id": selected_origin_id,
            "active_count": len(serialized_upcoming),
            "concluded_count": len(serialized_expired),
            "upcoming_timers": serialized_upcoming,
            "expired_timers": serialized_expired,
        }
    )
