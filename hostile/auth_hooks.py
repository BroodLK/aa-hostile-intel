"""
Hook into Alliance Auth menu and URL routing
"""

# Django
from django.utils.translation import gettext_lazy as _

# Alliance Auth
from allianceauth import hooks
from allianceauth.services.hooks import MenuItemHook, UrlHook

# AA Hostile Intel
from hostile import urls
from hostile.models import StructureTimer


class HostileIntelMenuItem(MenuItemHook):
    """Sidebar navigation menu item with active timer count badge and permission gating"""

    def __init__(self):
        super().__init__(
            _("Hostile Intel"),
            "fas fa-skull-crossbones fa-fw",
            "hostile:index",
            navactive=[
                "hostile:index",
                "hostile:smart_paste",
                "hostile:structures",
                "hostile:structure_add",
                "hostile:structure_edit",
                "hostile:structure_delete",
                "hostile:structure_verify",
                "hostile:structure_set_state",
                "hostile:structure_fitting_modal",
                "hostile:structure_fitting_save",
                "hostile:timers",
                "hostile:sov_timers",
                "hostile:timer_add",
                "hostile:timer_edit",
                "hostile:timer_delete",
                "hostile:timer_verify",
                "hostile:timerboard_sync",
                "hostile:calculator",
                "hostile:systems",
                "hostile:system_observation_add",
                "hostile:system_observation_edit",
                "hostile:system_observation_delete",
                "hostile:system_observation_verify",
                "hostile:system_observation_pin",
                "hostile:pilots",
                "hostile:pilot_add",
                "hostile:pilot_detect_alts",
                "hostile:pilot_detail",
                "hostile:pilot_sync_zkill",
                "hostile:pilot_edit",
                "hostile:pilot_delete",
                "hostile:pilot_verify",
                "hostile:alliances",
                "hostile:alliance_add",
                "hostile:alliance_detail",
                "hostile:alliance_edit",
                "hostile:alliance_delete",
                "hostile:alliance_sync_zkill",
                "hostile:coalition_manage",
                "hostile:corporation_add",
                "hostile:corporation_detail",
                "hostile:corporation_edit",
                "hostile:corporation_delete",
                "hostile:doctrines",
                "hostile:doctrine_add",
                "hostile:doctrine_edit",
                "hostile:doctrine_delete",
                "hostile:audit_logs",
            ],
        )

    def render(self, request):
        if not request.user.has_perm("hostile.basic_access"):
            return ""

        # Calculate count badge for upcoming timers or high-threat sitreps
        try:
            active_timers = StructureTimer.objects.upcoming().count()
            if active_timers > 0:
                self.count = active_timers
            else:
                self.count = None
        except Exception:
            self.count = None

        return super().render(request)


class IntelScannerMenuItem(MenuItemHook):
    """Sidebar navigation menu item for Threat Intel Scanner with permission gating"""

    def __init__(self):
        super().__init__(
            _("Intel Scanner"),
            "fas fa-radar fa-fw",
            "hostile:threat_scans",
            navactive=[
                "hostile:threat_scans",
                "hostile:threat_scan_detail",
                "hostile:threat_scan_delete",
            ],
        )

    def render(self, request):
        if not (
            request.user.has_perm("hostile.threat_scan_access")
            or request.user.has_perm("hostile.basic_access")
        ):
            return ""
        return super().render(request)


@hooks.register("menu_item_hook")
def register_menu():
    """Register the main hostile intel menu item in Alliance Auth"""
    return HostileIntelMenuItem()


@hooks.register("menu_item_hook")
def register_threat_scanner_menu():
    """Register the intel scanner menu item in Alliance Auth"""
    return IntelScannerMenuItem()


@hooks.register("url_hook")
def register_urls():
    """Register hostile intel app URLs in Alliance Auth"""
    return UrlHook(urls, "hostile", r"^hostile/")
