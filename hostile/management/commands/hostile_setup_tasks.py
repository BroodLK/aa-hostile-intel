"""
Management command to configure periodic Celery beat tasks for Hostile Intel
"""

# Third Party
from django_celery_beat.models import CrontabSchedule, IntervalSchedule, PeriodicTask

# Django
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Sets up periodic Celery tasks for hostile intelligence, sovereignty synchronization, member corporation census, and weekly zKillboard scans."

    def add_arguments(self, parser):
        parser.add_argument(
            "--disable",
            action="store_true",
            help="Disable the scheduled tasks instead of enabling them.",
        )

    def handle(self, *args, **options):
        disabled = options.get("disable", False)
        enabled = not disabled

        # 1. Weekly zKillboard Entity Statistics, Pilot Capabilities & Member Census (Every Sunday 04:00 UTC)
        zkill_cron, _ = CrontabSchedule.objects.get_or_create(
            minute="0",
            hour="4",
            day_of_week="0",  # Sunday
            day_of_month="*",
            month_of_year="*",
        )
        task_zkill, created = PeriodicTask.objects.update_or_create(
            task="hostile.tasks.update_all_zkill_stats",
            defaults={
                "name": "Hostile Intel: Weekly zKillboard Stats, Census & Formup Scan",
                "crontab": zkill_cron,
                "interval": None,
                "enabled": enabled,
                "description": "Weekly scan of zKillboard statistics, pilot dossiers, member corporation census, character counts, and 3-month rolling form capabilities for tracked hostile entities.",
            },
        )
        status_zkill = "Created" if created else "Updated"
        self.stdout.write(
            self.style.SUCCESS(
                f"[{status_zkill}] '{task_zkill.name}' (Every Sunday at 04:00 UTC, Enabled: {enabled})"
            )
        )

        # 2. Sovereignty Structures, Map & Vulnerability Profiles (Every 6 Hours)
        sov_cron, _ = CrontabSchedule.objects.get_or_create(
            minute="15",
            hour="*/6",
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
        )
        task_sov, created = PeriodicTask.objects.update_or_create(
            task="hostile.tasks.update_sovereignty_intelligence",
            defaults={
                "name": "Hostile Intel: Sovereignty Structures & Map Synchronization",
                "crontab": sov_cron,
                "interval": None,
                "enabled": enabled,
                "description": "Synchronizes public ESI sovereignty structures and sovereignty map to update alliance vulnerability profiles and systems held counts.",
            },
        )
        status_sov = "Created" if created else "Updated"
        self.stdout.write(
            self.style.SUCCESS(
                f"[{status_sov}] '{task_sov.name}' (Every 6 hours at :15, Enabled: {enabled})"
            )
        )

        # 3. Sovereignty Campaign Timers (Every 1 Minute)
        campaign_interval, _ = IntervalSchedule.objects.get_or_create(
            every=1,
            period=IntervalSchedule.MINUTES,
        )
        task_campaign, created = PeriodicTask.objects.update_or_create(
            task="hostile.tasks.update_sovereignty_campaigns",
            defaults={
                "name": "Hostile Intel: Sovereignty Contest Campaigns Sync",
                "interval": campaign_interval,
                "crontab": None,
                "enabled": enabled,
                "description": "Polls active sovereignty campaigns and contest timers from ESI every minute.",
            },
        )
        status_campaign = "Created" if created else "Updated"
        self.stdout.write(
            self.style.SUCCESS(
                f"[{status_campaign}] '{task_campaign.name}' (Every 1 minute, Enabled: {enabled})"
            )
        )

        # 4. Bidirectional sync with aa-structuretimers / Timerboard
        # AA Hostile Intel
        from hostile.app_settings import HOSTILE_TIMERBOARD_SYNC_MINUTES

        sync_minutes = max(1, int(HOSTILE_TIMERBOARD_SYNC_MINUTES))
        sync_interval, _ = IntervalSchedule.objects.get_or_create(
            every=sync_minutes,
            period=IntervalSchedule.MINUTES,
        )
        task_sync, created = PeriodicTask.objects.update_or_create(
            task="hostile.tasks.sync_timerboard_timers",
            defaults={
                "name": "Hostile Intel: Structure Timer Board Sync",
                "interval": sync_interval,
                "crontab": None,
                "enabled": enabled,
                "description": "Reconciles Hostile Intel structure timers with aa-structuretimers and the Alliance Auth Timerboard.",
            },
        )
        status_sync = "Created" if created else "Updated"
        self.stdout.write(
            self.style.SUCCESS(
                f"[{status_sync}] '{task_sync.name}' (Every {sync_minutes} minutes, Enabled: {enabled})"
            )
        )

        # 5. Friendly timers from corptools structure notifications (Every 5 Minutes)
        notif_interval, _ = IntervalSchedule.objects.get_or_create(
            every=5,
            period=IntervalSchedule.MINUTES,
        )
        task_notif, created = PeriodicTask.objects.update_or_create(
            task="hostile.tasks.import_corptools_notification_timers",
            defaults={
                "name": "Hostile Intel: Friendly Timers from Corptools Notifications",
                "interval": notif_interval,
                "crontab": None,
                "enabled": enabled,
                "description": "Creates friendly structure timers from structure notifications stored by corptools.",
            },
        )
        status_notif = "Created" if created else "Updated"
        self.stdout.write(
            self.style.SUCCESS(
                f"[{status_notif}] '{task_notif.name}' (Every 5 minutes, Enabled: {enabled})"
            )
        )

        self.stdout.write(
            self.style.SUCCESS(
                "Hostile Intel periodic Celery tasks setup completed successfully."
            )
        )
