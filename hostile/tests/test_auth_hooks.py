"""
Tests for Alliance Auth menu hooks and URL hooks
"""

# Django
from django.test import RequestFactory, TestCase

# Alliance Auth
from allianceauth.tests.auth_utils import AuthUtils

# AA Hostile Intel
from hostile.auth_hooks import (
    HostileIntelMenuItem,
    IntelScannerMenuItem,
    register_menu,
    register_threat_scanner_menu,
    register_urls,
)


class TestAuthHooks(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user_no_perms = AuthUtils.create_user(username="no_perm_user")
        AuthUtils.add_main_character(cls.user_no_perms, "No Perm Char", 21100001)

        cls.user_threat_only = AuthUtils.create_user(username="threat_only_user")
        AuthUtils.add_main_character(cls.user_threat_only, "Threat Char", 21100003)
        AuthUtils.add_permission_to_user_by_name(
            "hostile.threat_scan_access", cls.user_threat_only
        )

        cls.user_basic = AuthUtils.create_user(username="basic_user")
        AuthUtils.add_main_character(cls.user_basic, "Basic User Char", 21100002)
        AuthUtils.add_permission_to_user_by_name("hostile.basic_access", cls.user_basic)

    def test_menu_hook_rendering(self):
        menu_item = register_menu()
        self.assertIsInstance(menu_item, HostileIntelMenuItem)

        scanner_item = register_threat_scanner_menu()
        self.assertIsInstance(scanner_item, IntelScannerMenuItem)

        rf = RequestFactory()

        # No perm user
        request = rf.get("/")
        request.user = self.user_no_perms
        self.assertEqual(menu_item.render(request), "")
        self.assertEqual(scanner_item.render(request), "")

        # Threat scan only user
        request.user = self.user_threat_only
        self.assertEqual(menu_item.render(request), "")
        rendered_scanner = scanner_item.render(request)
        self.assertIn("Intel Scanner", rendered_scanner)

        # Basic permitted user
        request.user = self.user_basic
        rendered_intel = menu_item.render(request)
        self.assertIn("Hostile Intel", rendered_intel)
        rendered_scanner_basic = scanner_item.render(request)
        self.assertIn("Intel Scanner", rendered_scanner_basic)

    def test_menu_hook_navactive_isolation(self):
        menu_item = register_menu()
        scanner_item = register_threat_scanner_menu()

        # Threat scan urls should only be in scanner_item.navactive
        self.assertIn("hostile:threat_scans", scanner_item.navactive)
        self.assertIn("hostile:threat_scan_detail", scanner_item.navactive)
        self.assertIn("hostile:threat_scan_delete", scanner_item.navactive)

        self.assertNotIn("hostile:threat_scans", menu_item.navactive)
        self.assertNotIn("hostile:threat_scan_detail", menu_item.navactive)
        self.assertNotIn("hostile:threat_scan_delete", menu_item.navactive)

        # Hostile intel index should only be in menu_item.navactive
        self.assertIn("hostile:index", menu_item.navactive)
        self.assertNotIn("hostile:index", scanner_item.navactive)

    def test_url_hook(self):
        url_hook = register_urls()
        self.assertIsInstance(url_hook, register_urls().__class__)
