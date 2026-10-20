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

    def ready(self) -> None:
        try:
            # Register signals and connect external timerboard bidirectional synchronization
            # AA Hostile Intel
            from hostile import signals

            signals.connect_all_timer_signals()
        except Exception:
            pass
