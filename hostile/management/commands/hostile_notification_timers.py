"""
Creates friendly structure timers from corptools notifications, or (with --dry-run)
reports what would happen to each notification without changing anything.
"""

# Django
from django.core.management.base import BaseCommand

# AA Hostile Intel
from hostile.services.corptools_notifications import (
    CorptoolsNotificationTimerService,
)


class Command(BaseCommand):
    help = (
        "Import friendly structure timers from structure notifications stored by "
        "allianceauth-corptools. Use --dry-run to see what would be created, linked "
        "or skipped (and why) without writing anything."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would happen to each notification; change nothing.",
        )

    def handle(self, *args, **options):
        service = CorptoolsNotificationTimerService
        if not service.is_corptools_installed():
            self.stdout.write(self.style.WARNING("corptools is not installed."))
            return
        if not service.is_enabled():
            self.stdout.write(
                self.style.WARNING(
                    "Disabled: HOSTILE_CORPTOOLS_NOTIFICATIONS_ENABLED is False."
                )
            )
            return

        if not options.get("dry_run"):
            stats = service.import_notification_timers()
            self.stdout.write(
                self.style.SUCCESS(
                    f"processed={stats['processed']} created={stats['created']} "
                    f"linked={stats['linked']} skipped={stats['skipped']}"
                )
            )
            return

        rows = service.dry_run()
        if not rows:
            self.stdout.write(
                "No structure notifications in the lookback window "
                f"({service._lookback()})."
            )
            return

        labels = {"created": "would create", "linked": "would link"}
        counts: dict[str, int] = {}
        for row in rows:
            label = labels.get(row["result"], "would skip")
            counts[label] = counts.get(label, 0) + 1
            self.stdout.write(
                f"[{label}] #{row['notification_id']} {row['type']} "
                f"({row['character']}): {row['reason']}"
            )
        summary = ", ".join(f"{key}: {value}" for key, value in counts.items())
        self.stdout.write(self.style.SUCCESS(f"Dry run, nothing changed. {summary}"))
