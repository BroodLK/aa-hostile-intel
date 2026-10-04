"""App Configuration"""

# Django
from django.apps import AppConfig

# AA Hostile Intel
from hostile import __version__


class ExampleConfig(AppConfig):
    """App Config"""

    name = "hostile"
    label = "hostile"
    verbose_name = f"Hostile Intel v{__version__}"
