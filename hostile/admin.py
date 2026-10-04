"""
Django Admin integration for Hostile Intelligence models
"""

# Django
from django.contrib import admin

# AA Hostile Intel
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
    StructureFitting,
    StructureModule,
    StructureTimer,
    SystemObservation,
    SystemTag,
)


class StructureModuleInline(admin.TabularInline):
    model = StructureModule
    extra = 0
    raw_id_fields = ("module_type",)


class DoctrineFitInline(admin.StackedInline):
    model = DoctrineFit
    extra = 0
    raw_id_fields = ("ship_type",)


class StructureFittingInline(admin.StackedInline):
    model = StructureFitting
    extra = 0
    fields = ("name", "created_by", "created_at", "updated_at")
    readonly_fields = ("created_at", "updated_at")


class StructureTimerInline(admin.TabularInline):
    model = StructureTimer
    extra = 0
    raw_id_fields = ("solar_system",)


@admin.register(HostileAlliance)
class HostileAllianceAdmin(admin.ModelAdmin):
    list_display = (
        "alliance_name",
        "ticker",
        "alliance_id",
        "sov_capital",
        "systems_held_count",
        "member_count",
        "member_corps_count",
        "primary_timezone",
        "max_subcap_form",
        "max_cap_form",
        "max_super_form",
        "active_pilots_count",
        "weekly_kills_count",
        "weekly_losses_count",
        "updated_at",
    )
    search_fields = ("alliance_name", "ticker", "alliance_id")
    list_filter = ("primary_timezone",)
    raw_id_fields = ("sov_capital", "main_alliance")
    readonly_fields = ("created_at", "updated_at", "zkill_stats_updated_at")


@admin.register(HostileCorporation)
class HostileCorporationAdmin(admin.ModelAdmin):
    list_display = (
        "corporation_name",
        "ticker",
        "corporation_id",
        "alliance",
        "member_count",
        "max_subcap_form",
        "max_cap_form",
        "active_pilots_count",
        "weekly_kills_count",
        "weekly_losses_count",
        "updated_at",
    )
    search_fields = ("corporation_name", "ticker", "corporation_id")
    list_filter = ("alliance",)
    raw_id_fields = ("alliance",)
    readonly_fields = ("created_at", "updated_at", "zkill_stats_updated_at")


@admin.register(SystemTag)
class SystemTagAdmin(admin.ModelAdmin):
    list_display = ("name", "color_class", "description")
    search_fields = ("name", "description")
    list_filter = ("color_class",)


@admin.register(HostileStructure)
class HostileStructureAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "structure_type",
        "solar_system",
        "owner_ticker",
        "alliance",
        "core_status",
        "state",
        "is_verified",
        "updated_at",
    )
    list_filter = ("core_status", "state", "is_verified", "alliance")
    search_fields = (
        "name",
        "owner_ticker",
        "structure_type__name",
        "solar_system__name",
    )
    raw_id_fields = (
        "structure_type",
        "solar_system",
        "corporation",
        "alliance",
        "created_by",
    )
    readonly_fields = ("created_at", "updated_at")
    inlines = [StructureFittingInline, StructureTimerInline]


@admin.register(StructureFitting)
class StructureFittingAdmin(admin.ModelAdmin):
    list_display = ("structure", "name", "created_by", "updated_at")
    search_fields = ("structure__name", "name")
    raw_id_fields = ("structure", "created_by")
    readonly_fields = ("created_at", "updated_at")
    inlines = [StructureModuleInline]


@admin.register(StructureModule)
class StructureModuleAdmin(admin.ModelAdmin):
    list_display = ("fitting", "module_type", "slot_type", "slot_number", "is_online")
    list_filter = ("slot_type", "is_online")
    search_fields = ("module_type__name", "fitting__structure__name")
    raw_id_fields = ("fitting", "module_type")


@admin.register(StructureTimer)
class StructureTimerAdmin(admin.ModelAdmin):
    list_display = (
        "structure",
        "solar_system",
        "timer_type",
        "timer_datetime",
        "is_verified",
        "created_by",
    )
    list_filter = ("timer_type", "is_verified")
    search_fields = ("structure__name", "solar_system__name", "notes")
    raw_id_fields = ("structure", "solar_system", "created_by")
    readonly_fields = ("created_at", "updated_at")


@admin.register(SystemObservation)
class SystemObservationAdmin(admin.ModelAdmin):
    list_display = (
        "solar_system",
        "threat_level",
        "active_hours",
        "is_pinned",
        "is_verified",
        "created_by",
        "created_at",
    )
    list_filter = ("threat_level", "is_pinned", "is_verified", "tags")
    search_fields = ("solar_system__name", "observation_text", "active_hours")
    raw_id_fields = ("solar_system", "created_by")
    filter_horizontal = ("tags",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(HostilePilotDossier)
class HostilePilotDossierAdmin(admin.ModelAdmin):
    list_display = (
        "character_name",
        "corporation_name",
        "alliance_name",
        "is_alt",
        "main_character",
        "alt_confidence",
        "associated_alliance_name",
        "is_cyno_alt",
        "is_blops_pilot",
        "is_dread_pilot",
        "is_fax_pilot",
        "is_capital_pilot",
        "is_super_pilot",
        "is_titan_pilot",
        "is_fc",
        "is_awox",
        "is_alliance_awox",
        "is_faction_awox",
        "is_bait",
        "is_ganker",
        "is_logi_pilot",
        "is_rookie",
        "is_out_of_corp",
        "is_verified",
    )
    list_filter = (
        "is_alt",
        "alt_confidence",
        "is_cyno_alt",
        "is_blops_pilot",
        "is_dread_pilot",
        "is_fax_pilot",
        "is_capital_pilot",
        "is_super_pilot",
        "is_titan_pilot",
        "is_fc",
        "fc_level",
        "is_awox",
        "is_alliance_awox",
        "is_faction_awox",
        "is_bait",
        "bait_level",
        "is_ganker",
        "is_logi_pilot",
        "is_rookie",
        "is_out_of_corp",
        "is_verified",
    )
    search_fields = (
        "character_name",
        "corporation_name",
        "alliance_name",
        "associated_alliance_name",
        "alt_inference_reason",
        "notes",
    )
    raw_id_fields = ("last_seen_system", "created_by", "main_character")
    readonly_fields = ("created_at", "updated_at")


@admin.register(IntelAuditLog)
class IntelAuditLogAdmin(admin.ModelAdmin):
    list_display = (
        "timestamp",
        "user",
        "action",
        "target_model",
        "target_id",
        "target_repr",
    )
    list_filter = ("action", "target_model")
    search_fields = ("target_repr", "details", "user__username")
    raw_id_fields = ("user",)
    readonly_fields = ("timestamp",)


@admin.register(HostileStagingSystem)
class HostileStagingSystemAdmin(admin.ModelAdmin):
    list_display = (
        "solar_system",
        "alliance",
        "corporation",
        "staging_type",
        "is_primary",
        "max_subcap_form",
        "max_cap_form",
        "max_super_form",
        "is_verified",
        "created_by",
        "updated_at",
    )
    list_filter = ("staging_type", "is_primary", "is_verified", "alliance")
    search_fields = (
        "solar_system__name",
        "alliance__alliance_name",
        "corporation__corporation_name",
        "notes",
    )
    raw_id_fields = (
        "solar_system",
        "alliance",
        "corporation",
        "structure",
        "created_by",
    )
    readonly_fields = ("created_at", "updated_at")


@admin.register(HostileDoctrine)
class HostileDoctrineAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "alliance",
        "corporation",
        "role_type",
        "is_general_doctrine",
        "is_verified",
        "created_by",
        "updated_at",
    )
    list_filter = ("role_type", "is_general_doctrine", "is_verified", "alliance")
    search_fields = (
        "name",
        "primary_ship_types",
        "description",
        "alliance__alliance_name",
    )
    raw_id_fields = ("alliance", "corporation", "created_by")
    filter_horizontal = ("stagings",)
    readonly_fields = ("created_at", "updated_at")
    inlines = [DoctrineFitInline]


@admin.register(DoctrineFit)
class DoctrineFitAdmin(admin.ModelAdmin):
    list_display = ("doctrine", "name", "ship_type", "role", "zkill_link", "updated_at")
    list_filter = ("doctrine__alliance", "role")
    search_fields = ("name", "doctrine__name", "ship_type__name", "notes")
    raw_id_fields = ("doctrine", "ship_type")
    readonly_fields = ("created_at", "updated_at")


@admin.register(LocalThreatScan)
class LocalThreatScanAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "solar_system",
        "threat_level",
        "blops_drop_chance",
        "cap_drop_chance",
        "pilot_count",
        "hostile_count",
        "ignored_count",
        "created_by",
        "created_at",
    )
    list_filter = ("threat_level", "created_at")
    search_fields = ("solar_system__name", "raw_local_text", "created_by__username")
    raw_id_fields = ("solar_system", "created_by")
    readonly_fields = ("id", "created_at")
