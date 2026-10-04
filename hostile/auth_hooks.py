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
            navactive=["hostile:"],
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


@hooks.register("menu_item_hook")
def register_menu():
    """Register the menu item in Alliance Auth"""
    return HostileIntelMenuItem()


@hooks.register("url_hook")
def register_urls():
    """Register hostile intel app URLs in Alliance Auth"""
    return UrlHook(urls, "hostile", r"^hostile/")
