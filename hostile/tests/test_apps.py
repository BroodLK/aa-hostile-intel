"""
Tests for AppConfig and package metadata
"""

# Django
from django.apps import apps
from django.test import TestCase

# AA Hostile Intel
from hostile import __appname__, __title__, __url__, __version__
from hostile.apps import HostileConfig


class TestHostileConfig(TestCase):
    def test_version_and_metadata(self):
        self.assertEqual(__version__, "0.0.20")
        self.assertEqual(__title__, "Hostile Intel")
        self.assertEqual(__appname__, "aa-hostile-intel")
        self.assertEqual(__url__, "https://github.com/BroodLK/aa-hostile-intel")

    def test_app_config(self):
        self.assertEqual(HostileConfig.name, "hostile")
        self.assertEqual(HostileConfig.label, "hostile")
        self.assertEqual(HostileConfig.verbose_name, "Hostile Intel v0.0.20")
        app_config = apps.get_app_config("hostile")
        self.assertIsInstance(app_config, HostileConfig)
        self.assertEqual(app_config.verbose_name, f"Hostile Intel v{__version__}")
