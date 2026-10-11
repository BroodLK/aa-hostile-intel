"""
Signal receivers for Hostile Intel models, aa-structuretimers, and Timerboard synchronization
"""

# Standard Library
import logging

# Django
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

# AA Hostile Intel
from hostile.models import StructureTimer
from hostile.services.timer_sync import (
    TimerSyncService,
    is_sync_in_progress,
)

logger = logging.getLogger(__name__)


@receiver(post_save, sender=StructureTimer)
def structure_timer_post_save_handler(sender, instance, created, **kwargs):
    """
    Synchronizes StructureTimer changes to aa-structuretimers and Timerboard.
    """
    if kwargs.get("raw", False) or is_sync_in_progress():
        return
    # Only sync non-sov structure timers
    if instance.is_sov_campaign or instance.timer_type in ["SOV_IHUB", "SOV_TCU"]:
        return
    try:
        TimerSyncService.sync_structure_timer_to_all(instance)
    except Exception as exc:
        logger.warning(
            "Failed to sync StructureTimer %s to external timerboards: %s",
            instance.id,
            exc,
        )


@receiver(post_delete, sender=StructureTimer)
def structure_timer_post_delete_handler(sender, instance, **kwargs):
    """
    Deletes corresponding external timers when a StructureTimer is deleted.
    """
    if is_sync_in_progress():
        return
    try:
        TimerSyncService.delete_external_timers_for_structure_timer(instance)
    except Exception as exc:
        logger.warning(
            "Failed to delete external timers for StructureTimer %s: %s",
            instance.id,
            exc,
        )


def connect_timerboard_signals():
    """
    Connects post_save and post_delete signals to allianceauth.timerboard.models.Timer
    when timerboard is available in the installation.
    """
    timer_model = TimerSyncService.get_timerboard_timer_model()
    if not timer_model:
        return

    def timerboard_timer_post_save(sender, instance, created, **kwargs):
        if kwargs.get("raw", False) or is_sync_in_progress():
            return
        try:
            TimerSyncService.sync_timerboard_to_structure_timer(instance)
        except Exception as exc:
            logger.warning(
                "Failed to sync Timerboard timer %s to Hostile Intel: %s",
                instance.id,
                exc,
            )

    def timerboard_timer_post_delete(sender, instance, **kwargs):
        if is_sync_in_progress():
            return
        try:
            TimerSyncService.delete_structure_timer_for_timerboard(instance)
        except Exception as exc:
            logger.warning(
                "Failed to delete StructureTimer for Timerboard timer %s: %s",
                instance.id,
                exc,
            )

    post_save.connect(
        timerboard_timer_post_save,
        sender=timer_model,
        dispatch_uid="hostile_timerboard_timer_post_save",
        weak=False,
    )
    post_delete.connect(
        timerboard_timer_post_delete,
        sender=timer_model,
        dispatch_uid="hostile_timerboard_timer_post_delete",
        weak=False,
    )
    logger.debug("Successfully connected Timerboard bidirectional signals.")


def connect_structuretimers_signals():
    """
    Connects post_save and post_delete signals to aa-structuretimers Timer
    when structuretimers is available in the installation.
    """
    st_model = TimerSyncService.get_structuretimers_timer_model()
    if not st_model:
        return

    def structuretimers_timer_post_save(sender, instance, created, **kwargs):
        if kwargs.get("raw", False) or is_sync_in_progress():
            return
        try:
            TimerSyncService.sync_structuretimers_to_structure_timer(instance)
        except Exception as exc:
            logger.warning(
                "Failed to sync aa-structuretimers timer %s to Hostile Intel: %s",
                instance.id,
                exc,
            )

    def structuretimers_timer_post_delete(sender, instance, **kwargs):
        if is_sync_in_progress():
            return
        try:
            TimerSyncService.delete_structure_timer_for_structuretimers(instance)
        except Exception as exc:
            logger.warning(
                "Failed to delete StructureTimer for aa-structuretimers timer %s: %s",
                instance.id,
                exc,
            )

    post_save.connect(
        structuretimers_timer_post_save,
        sender=st_model,
        dispatch_uid="hostile_structuretimers_timer_post_save",
        weak=False,
    )
    post_delete.connect(
        structuretimers_timer_post_delete,
        sender=st_model,
        dispatch_uid="hostile_structuretimers_timer_post_delete",
        weak=False,
    )
    logger.debug("Successfully connected aa-structuretimers bidirectional signals.")


def connect_all_timer_signals():
    """Connects all available external timerboard signals"""
    connect_timerboard_signals()
    connect_structuretimers_signals()


try:
    connect_all_timer_signals()
except Exception:
    pass
