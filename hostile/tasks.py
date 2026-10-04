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
