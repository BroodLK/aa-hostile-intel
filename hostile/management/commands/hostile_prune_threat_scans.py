"""
Management command to prune old Threat Scans based on retention policies
"""

# Standard Library
from datetime import timedelta

# Django
from django.core.management.base import BaseCommand
from django.utils import timezone

# AA Hostile Intel
from hostile.app_settings import HOSTILE_THREAT_SCAN_RETENTION_DAYS
from hostile.models import LocalThreatScan


class Command(BaseCommand):
    help = "Prunes outdated Local Threat Scans to optimize database storage and query performance."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=HOSTILE_THREAT_SCAN_RETENTION_DAYS,
            help=f"Retention period in days (default: {HOSTILE_THREAT_SCAN_RETENTION_DAYS}).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simulates scan pruning and displays matching records without deleting them.",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Outputs detailed information for each scan eligible for pruning.",
        )

    def handle(self, *args, **options):
        days = options.get("days", HOSTILE_THREAT_SCAN_RETENTION_DAYS)
        dry_run = options.get("dry_run", False)
        verbose = options.get("verbose", False)

        cutoff = timezone.now() - timedelta(days=days)
        prefix = "[DRY RUN] " if dry_run else ""
        self.stdout.write(
            f"{prefix}Scanning for threat scans older than {days} days (created before {cutoff.strftime('%Y-%m-%d %H:%M:%S')} UTC)..."
        )

        scans_qs = LocalThreatScan.objects.filter(created_at__lt=cutoff).select_related(
            "solar_system"
        )
        count = scans_qs.count()

        if verbose or dry_run:
            for s in scans_qs[:100]:
                sys_name = s.solar_system.name if s.solar_system else "Unknown System"
                self.stdout.write(
                    f"  - Scan ID: {s.id} | System: {sys_name} | Created: {s.created_at.strftime('%Y-%m-%d %H:%M')} | "
                    f"Pilots: {s.pilot_count} | Threat: {s.threat_level}"
                )
            if count > 100:
                self.stdout.write(f"  ... and {count - 100} more scan(s).")

        if count > 0:
            if not dry_run:
                deleted_total, _ = scans_qs.delete()
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Successfully pruned {deleted_total} threat scan(s) older than {days} days."
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"{prefix}Found {count} threat scan(s) eligible for pruning."
                    )
                )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"{prefix}No threat scans found older than {days} days."
                )
            )
