"""
Celery background tasks for ESI synchronization and intelligence maintenance
"""

# Third Party
from celery import shared_task

# Django
from django.core.cache import cache

# Alliance Auth
from allianceauth.services.hooks import get_extension_logger

# AA Hostile Intel
from hostile.models import HostileAlliance, HostileCorporation
from hostile.services.esi_client import esi_client
from hostile.services.sov_engine import SovAnalysisEngine
from hostile.services.zkill_client import zkill_client

logger = get_extension_logger(__name__)


@shared_task
def update_sovereignty_intelligence() -> str:
    """Polls public ESI sovereignty structures and sovereignty map to update alliance vulnerability timezone profiles and systems held"""
    logger.info(
        "Starting background synchronization of public ESI sovereignty structures and map..."
    )
    structures = esi_client.get_sovereignty_structures()
    msg_parts = []
    if structures:
        results = SovAnalysisEngine.process_sovereignty_structures(structures)
        msg_parts.append(
            f"Successfully updated sovereignty profiles for {len(results)} alliances"
        )
    else:
        logger.info("No sovereignty structures returned or ESI error encountered.")

    sov_map = esi_client.get_sovereignty_map()
    if sov_map:
        held_results = SovAnalysisEngine.sync_sovereignty_held(sov_map)
        msg_parts.append(f"Synchronized systems held for {len(held_results)} alliances")

    msg = ". ".join(msg_parts) if msg_parts else "No sovereignty intelligence updated."
    logger.info(msg)
    return msg


@shared_task
def update_sovereignty_campaigns() -> str:
    """Polls public ESI sovereignty campaigns and synchronizes upcoming contest timers"""
    logger.info(
        "Starting background synchronization of public ESI sovereignty campaigns..."
    )
    campaigns = esi_client.get_sovereignty_campaigns()
    if not campaigns:
        logger.info("No active sovereignty campaigns returned.")
        return "No campaigns updated."

    timers = SovAnalysisEngine.process_sovereignty_campaigns(campaigns)
    msg = f"Successfully synchronized {len(timers)} active sovereignty campaign timers."
    logger.info(msg)
    return msg


@shared_task(time_limit=7200)
def update_all_zkill_stats() -> str:
    """Synchronizes active pilot counts, weekly kills, and supers from zKillboard for all tracked entities"""
    lock_id = "hostile-intel-update-all-zkill-stats-lock"
    if not cache.add(lock_id, True, 7200):
        logger.warning(
            "zKillboard entity stats update task is already running. Skipping this run."
        )
        return "Task already running"

    try:
        logger.info(
            "Starting background synchronization of zKillboard statistics for hostile entities..."
        )
        alliances = HostileAlliance.objects.all()
        alliance_count = 0
        for alliance in alliances:
            if zkill_client.update_alliance_stats(alliance):
                alliance_count += 1
            try:
                SovAnalysisEngine.sync_alliance_members_and_corps(alliance)
                SovAnalysisEngine.sync_alliance_sovereignty_held_single(alliance)
            except Exception as exc:
                logger.warning(
                    "Error syncing member corps / sov held for alliance %s: %s",
                    alliance.alliance_name,
                    exc,
                )

        corps = HostileCorporation.objects.all()
        corp_count = 0
        for corp in corps:
            if zkill_client.update_corporation_stats(corp):
                corp_count += 1

        msg = f"zKillboard stats updated for {alliance_count} alliances and {corp_count} corporations."
        logger.info(msg)
        return msg
    finally:
        cache.delete(lock_id)


@shared_task
def sync_alliance_zkill_stats(alliance_id: int) -> bool:
    """Synchronizes zKillboard statistics, member corporations, character numbers, and sov for a specific alliance"""
    try:
        alliance = HostileAlliance.objects.get(alliance_id=alliance_id)
        res = zkill_client.update_alliance_stats(alliance)
        try:
            SovAnalysisEngine.sync_alliance_members_and_corps(alliance)
            SovAnalysisEngine.sync_alliance_sovereignty_held_single(alliance)
            for corp in alliance.corporations.all():
                try:
                    zkill_client.update_corporation_stats(corp)
                except Exception as c_exc:
                    logger.debug(
                        "Error syncing zkill stats for member corp %s: %s",
                        corp.corporation_id,
                        c_exc,
                    )
        except Exception as exc:
            logger.warning(
                "Error syncing member corps / sov held for alliance %s: %s",
                alliance.alliance_name,
                exc,
            )
        return res
    except HostileAlliance.DoesNotExist:
        logger.warning(
            "Alliance ID %s not found in hostile intelligence database",
            alliance_id,
        )
        return False


@shared_task
def sync_timerboard_timers() -> str:
    """
    Synchronizes Hostile Structure Timers with external timerboards (aa-structuretimers & Timerboard) in both directions.
    """
    logger.info(
        "Starting background bidirectional synchronization with external structure timerboards..."
    )
    # AA Hostile Intel
    from hostile.services.timer_sync import TimerSyncService

    if not TimerSyncService.is_sync_enabled():
        logger.info(
            "External timer sync is disabled or neither structuretimers nor timerboard is installed."
        )
        return "Timer sync skipped (disabled or uninstalled)."

    stats = TimerSyncService.sync_all_timers()
    msg = (
        f"Timer sync complete: "
        f"structuretimers (pushed={stats.get('synced_to_structuretimers', 0)}, pulled={stats.get('synced_from_structuretimers', 0)}), "
        f"timerboard (pushed={stats.get('synced_to_timerboard', 0)}, pulled={stats.get('synced_from_timerboard', 0)}), "
        f"reconciled={stats.get('corrected', 0)}."
    )
    logger.info(msg)
    return msg


@shared_task
def prune_old_threat_scans(days: int | None = None) -> str:
    """
    Celery task to automatically prune outdated Local Threat Scans.
    """
    # Standard Library
    from datetime import timedelta

    # Django
    from django.utils import timezone

    # AA Hostile Intel
    from hostile.app_settings import HOSTILE_THREAT_SCAN_RETENTION_DAYS
    from hostile.models import LocalThreatScan

    retention_days = days if days is not None else HOSTILE_THREAT_SCAN_RETENTION_DAYS
    cutoff = timezone.now() - timedelta(days=retention_days)

    logger.info(
        "Starting background pruning of threat scans older than %d days (cutoff: %s)...",
        retention_days,
        cutoff.isoformat(),
    )
    scans_qs = LocalThreatScan.objects.filter(created_at__lt=cutoff)
    count, _ = scans_qs.delete()
    msg = f"Pruned {count} threat scan(s) older than {retention_days} days."
    logger.info(msg)
    return msg
