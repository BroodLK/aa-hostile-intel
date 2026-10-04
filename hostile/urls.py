"""
Hostile Intelligence URL Configuration
"""

# Django
from django.urls import path

# AA Hostile Intel
from hostile import views

app_name: str = "hostile"

urlpatterns = [
    path("", views.index, name="index"),
    path("smart-paste/", views.smart_paste, name="smart_paste"),
    path("structures/", views.structures_list, name="structures"),
    path("structures/add/", views.structure_add, name="structure_add"),
    path(
        "structures/<int:structure_id>/edit/",
        views.structure_edit,
        name="structure_edit",
    ),
    path(
        "structures/<int:structure_id>/delete/",
        views.structure_delete,
        name="structure_delete",
    ),
    path(
        "structures/<int:structure_id>/verify/",
        views.structure_verify,
        name="structure_verify",
    ),
    path(
        "structures/<int:structure_id>/state/",
        views.structure_set_state,
        name="structure_set_state",
    ),
    path(
        "structures/<int:structure_id>/fitting-modal/",
        views.structure_fitting_modal,
        name="structure_fitting_modal",
    ),
    path(
        "structures/<int:structure_id>/fitting-save/",
        views.structure_fitting_save,
        name="structure_fitting_save",
    ),
    path("timers/", views.timers_list, name="timers"),
    path("sov-timers/", views.sov_timers_list, name="sov_timers"),
    path("api/sov-timers/", views.api_sov_timers, name="api_sov_timers"),
    path("timers/add/", views.timer_add, name="timer_add"),
    path(
        "timers/<int:timer_id>/edit/",
        views.timer_edit,
        name="timer_edit",
    ),
    path(
        "timers/<int:timer_id>/delete/",
        views.timer_delete,
        name="timer_delete",
    ),
    path("timers/<int:timer_id>/verify/", views.timer_verify, name="timer_verify"),
    path("calculator/", views.timer_calculator, name="calculator"),
    path("api/calculate-timer/", views.api_timer_calculate, name="api_timer_calculate"),
    path("api/routes/", views.api_routes_batch, name="api_routes_batch"),
    path(
        "api/solar-systems/",
        views.api_solar_systems_search,
        name="api_solar_systems_search",
    ),
    path("api/infer-system/", views.api_infer_system, name="api_infer_system"),
    path("api/characters/", views.api_character_search, name="api_character_search"),
    path(
        "api/character-resolve/",
        views.api_character_resolve,
        name="api_character_resolve",
    ),
    path("api/alliances/", views.api_alliance_search, name="api_alliance_search"),
    path(
        "api/alliance-resolve/", views.api_alliance_resolve, name="api_alliance_resolve"
    ),
    path(
        "api/corporations/", views.api_corporation_search, name="api_corporation_search"
    ),
    path(
        "api/corporation-resolve/",
        views.api_corporation_resolve,
        name="api_corporation_resolve",
    ),
    path("systems/", views.systems_list, name="systems"),
    path("systems/add/", views.system_observation_add, name="system_observation_add"),
    path(
        "systems/<int:obs_id>/edit/",
        views.system_observation_edit,
        name="system_observation_edit",
    ),
    path(
        "systems/<int:obs_id>/delete/",
        views.system_observation_delete,
        name="system_observation_delete",
    ),
    path(
        "systems/<int:obs_id>/verify/",
        views.system_observation_verify,
        name="system_observation_verify",
    ),
    path(
        "systems/<int:obs_id>/pin/",
        views.system_observation_pin,
        name="system_observation_pin",
    ),
    path("pilots/", views.pilots_list, name="pilots"),
    path("pilots/add/", views.pilot_add, name="pilot_add"),
    path("pilots/detect-alts/", views.pilot_detect_alts, name="pilot_detect_alts"),
    path(
        "pilots/<int:pilot_id>/",
        views.pilot_detail,
        name="pilot_detail",
    ),
    path(
        "pilots/<int:pilot_id>/sync-zkill/",
        views.pilot_sync_zkill,
        name="pilot_sync_zkill",
    ),
    path(
        "pilots/<int:pilot_id>/edit/",
        views.pilot_edit,
        name="pilot_edit",
    ),
    path(
        "pilots/<int:pilot_id>/delete/",
        views.pilot_delete,
        name="pilot_delete",
    ),
    path("pilots/<int:pilot_id>/verify/", views.pilot_verify, name="pilot_verify"),
    path("threat-scans/", views.threat_scan_list, name="threat_scans"),
    path(
        "threat-scans/<uuid:scan_id>/",
        views.threat_scan_detail,
        name="threat_scan_detail",
    ),
    path(
        "api/threat-scans/<uuid:scan_id>/pilot-intel/",
        views.api_threat_scan_pilot_intelligence,
        name="api_threat_scan_pilot_intel",
    ),
    path(
        "threat-scans/<uuid:scan_id>/delete/",
        views.threat_scan_delete,
        name="threat_scan_delete",
    ),
    path("alliances/", views.alliances_list, name="alliances"),
    path("alliances/add/", views.alliance_add, name="alliance_add"),
    path(
        "alliances/<int:alliance_id>/",
        views.alliance_detail,
        name="alliance_detail",
    ),
    path(
        "alliances/<int:alliance_id>/edit/",
        views.alliance_edit,
        name="alliance_edit",
    ),
    path(
        "alliances/<int:alliance_id>/delete/",
        views.alliance_delete,
        name="alliance_delete",
    ),
    path(
        "alliances/<int:alliance_id>/sync-zkill/",
        views.alliance_sync_zkill,
        name="alliance_sync_zkill",
    ),
    path(
        "coalitions/manage/",
        views.coalition_manage,
        name="coalition_manage",
    ),
    path("corporations/add/", views.corporation_add, name="corporation_add"),
    path(
        "corporations/<int:corp_id>/",
        views.corporation_detail,
        name="corporation_detail",
    ),
    path(
        "corporations/<int:corp_id>/edit/",
        views.corporation_edit,
        name="corporation_edit",
    ),
    path(
        "corporations/<int:corp_id>/delete/",
        views.corporation_delete,
        name="corporation_delete",
    ),
    path("doctrines/", views.doctrines_list, name="doctrines"),
    path("doctrines/add/", views.doctrine_add, name="doctrine_add"),
    path(
        "doctrines/<int:doctrine_id>/edit/",
        views.doctrine_edit,
        name="doctrine_edit",
    ),
    path(
        "doctrines/<int:doctrine_id>/delete/",
        views.doctrine_delete,
        name="doctrine_delete",
    ),
    path(
        "doctrines/<int:doctrine_id>/verify/",
        views.doctrine_verify,
        name="doctrine_verify",
    ),
    path(
        "doctrines/<int:doctrine_id>/add-fit/",
        views.doctrine_fit_add,
        name="doctrine_fit_add",
    ),
    path(
        "doctrines/fits/<int:fit_id>/edit/",
        views.doctrine_fit_edit,
        name="doctrine_fit_edit",
    ),
    path(
        "doctrines/fits/<int:fit_id>/delete/",
        views.doctrine_fit_delete,
        name="doctrine_fit_delete",
    ),
    path(
        "doctrines/fits/<int:fit_id>/fitting-modal/",
        views.doctrine_fit_modal,
        name="doctrine_fit_modal",
    ),
    path("stagings/add/", views.staging_add, name="staging_add"),
    path(
        "stagings/<int:staging_id>/edit/",
        views.staging_edit,
        name="staging_edit",
    ),
    path(
        "stagings/<int:staging_id>/delete/",
        views.staging_delete,
        name="staging_delete",
    ),
    path(
        "stagings/<int:staging_id>/verify/", views.staging_verify, name="staging_verify"
    ),
    path("audit-logs/", views.audit_logs_list, name="audit_logs"),
]
