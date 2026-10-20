"""
Management command to deduplicate Hostile Structure records with dry-run and targeted system filtering
"""

# Django
from django.core.management.base import BaseCommand

# AA Hostile Intel
from hostile.services.intel_manager import IntelManager


class Command(BaseCommand):
    help = "Scans systems for duplicate Hostile Structure records and merges them into primary structures."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simulates structure deduplication and displays planned merges without modifying the database.",
        )
        parser.add_argument(
            "--system",
            type=str,
            default=None,
            help="Target a specific solar system (name or ID) for deduplication.",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Outputs detailed information for each merged duplicate and reason.",
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)
        system_filter = options.get("system")
        verbose = options.get("verbose", False)

        prefix = "[DRY RUN] " if dry_run else ""
        target_str = (
            f" in system '{system_filter}'" if system_filter else " across all systems"
        )
        self.stdout.write(
            f"{prefix}Running Hostile Structure automatic deduplication{target_str}..."
        )

        results = IntelManager.auto_deduplicate_all(
            user=None,
            system=system_filter,
            dry_run=dry_run,
            verbose=verbose,
        )
        total_merged = results["total_merged"]
        systems_affected = results["systems_affected"]
        details = results.get("details", [])

        if verbose or dry_run:
            for item in details:
                dup_str = ", ".join(
                    f"'{name}' (ID: {did})"
                    for did, name in zip(item["duplicate_ids"], item["duplicate_names"])
                )
                self.stdout.write(
                    f"  - [{item['system_name']}] Primary: '{item['primary_name']}' (ID: {item['primary_id']}, Score: {item['primary_score']}) "
                    f"<- Duplicates: {dup_str} | Reason: {item['reason']}"
                )

        if total_merged > 0:
            action_word = "Would merge" if dry_run else "Successfully merged"
            self.stdout.write(
                self.style.SUCCESS(
                    f"{prefix}{action_word} {total_merged} duplicate structure(s) across {systems_affected} system(s)."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"{prefix}No duplicate structures found in the database."
                )
            )
