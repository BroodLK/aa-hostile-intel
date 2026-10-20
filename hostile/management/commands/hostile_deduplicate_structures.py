"""
Management command to deduplicate Hostile Structure records
"""

# Django
from django.core.management.base import BaseCommand

# AA Hostile Intel
from hostile.services.intel_manager import IntelManager


class Command(BaseCommand):
    help = "Scans all systems for duplicate Hostile Structure records and merges them into primary structures."

    def add_arguments(self, parser):
        pass

    def handle(self, *args, **options):
        self.stdout.write("Running Hostile Structure automatic deduplication...")
        results = IntelManager.auto_deduplicate_all(user=None)
        total_merged = results["total_merged"]
        systems_affected = results["systems_affected"]

        if total_merged > 0:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Successfully deduplicated structures: {total_merged} duplicate structure(s) merged across {systems_affected} system(s)."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS("No duplicate structures found in the database.")
            )
