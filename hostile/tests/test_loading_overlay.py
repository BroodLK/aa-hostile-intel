"""
Tests for Hostile Intel page loading overlay template, assets, and trigger attributes.
"""

# Standard Library
import os

# Django
from django.conf import settings
from django.test import Client, TestCase
from django.urls import reverse

# Alliance Auth
from allianceauth.tests.auth_utils import AuthUtils


class TestLoadingOverlay(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = AuthUtils.create_user(username="intel_agent")
        AuthUtils.add_main_character(cls.user, "Intel Agent Char", 21900001)
        AuthUtils.add_permission_to_user_by_name("hostile.basic_access", cls.user)
        cls.client = Client()

    def setUp(self):
        self.user.refresh_from_db()
        self.client.force_login(self.user)

    def test_static_files_exist(self):
        css_path = os.path.join(settings.BASE_DIR, "hostile", "static", "hostile", "css", "hostile_loading.css")
        js_path = os.path.join(settings.BASE_DIR, "hostile", "static", "hostile", "js", "hostile_loading.js")
        self.assertTrue(os.path.isfile(css_path), f"Missing CSS file: {css_path}")
        self.assertTrue(os.path.isfile(js_path), f"Missing JS file: {js_path}")

    def test_loading_overlay_rendered_in_index(self):
        response = self.client.get(reverse("hostile:index"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("hostilePageLoadingOverlay", content)
        self.assertIn("hostile_loading.css", content)
        self.assertIn("hostile_loading.js", content)
        self.assertIn("data-hostile-loading", content)

    def test_navigation_tabs_have_loading_attributes(self):
        response = self.client.get(reverse("hostile:index"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")

        for expected_title in [
            "Loading Dashboard",
            "Loading Structures",
            "Loading Stagings & Doctrines",
            "Loading Timer Board",
            "Loading Timer Calculator",
            "Loading System Intel",
            "Loading Pilot Dossiers",
            "Loading Threat Scanner",
            "Loading Alliances & Sov",
        ]:
            self.assertIn(expected_title, content, f"Expected loading title '{expected_title}' in nav tabs")

    def test_calculator_form_has_loading_attributes(self):
        response = self.client.get(reverse("hostile:calculator"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("Calculating Timers", content)

    def test_threat_scans_form_has_loading_attributes(self):
        response = self.client.get(reverse("hostile:threat_scans"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("Analyzing Threat Scan", content)
