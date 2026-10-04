"""
Tests for Alliance Auth menu hooks and URL hooks
"""

# Django
from django.test import RequestFactory, TestCase

# Alliance Auth
from allianceauth.tests.auth_utils import AuthUtils

# AA Hostile Intel
from hostile.auth_hooks import HostileIntelMenuItem, register_menu, register_urls


class TestAuthHooks(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user_no_perms = AuthUtils.create_user(username="no_perm_user")
        AuthUtils.add_main_character(cls.user_no_perms, "No Perm Char", 21100001)

        cls.user_basic = AuthUtils.create_user(username="basic_user")
        AuthUtils.add_main_character(cls.user_basic, "Basic User Char", 21100002)
        AuthUtils.add_permission_to_user_by_name("hostile.basic_access", cls.user_basic)

    def test_menu_hook_rendering(self):
        menu_item = register_menu()
        self.assertIsInstance(menu_item, HostileIntelMenuItem)

        rf = RequestFactory()

        # No perm user
        request = rf.get("/")
        request.user = self.user_no_perms
        rendered = menu_item.render(request)
        self.assertEqual(rendered, "")

        # Permitted user
        request.user = self.user_basic
        rendered = menu_item.render(request)
        self.assertIn("Hostile Intel", rendered)

    def test_url_hook(self):
        url_hook = register_urls()
        self.assertIsInstance(url_hook, register_urls().__class__)
