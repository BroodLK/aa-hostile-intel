"""
Hostile Intelligence Models
"""

# Standard Library
import uuid
from typing import Any, Dict, List, Optional

# Third Party
from eve_sde.models import ItemType, SolarSystem

# Django
from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone

# AA Hostile Intel
from hostile.managers import (
    AllianceQuerySet,
    DoctrineQuerySet,
    ObservationQuerySet,
    PilotQuerySet,
    StagingQuerySet,
    StructureQuerySet,
    TimerQuerySet,
)


class General(models.Model):
    """Meta model for app permissions"""

    class Meta:
        managed = False
        default_permissions = ()
        permissions = (
            ("basic_access", "Can access this app"),
            ("manage_intel", "Can manage, verify, and moderate hostile intel"),
            ("officer_access", "Can perform senior intel officer actions"),
        )


class HostileAlliance(models.Model):
    """Hostile Alliance entity with sovereignty intelligence"""

    TIMEZONE_CHOICES = (
        ("USTZ", "USTZ (00:00 - 08:00 UTC)"),
        ("AUTZ", "AUTZ (08:00 - 14:00 UTC)"),
        ("EUTZ", "EUTZ (14:00 - 22:00 UTC)"),
        ("UNKNOWN", "Unknown"),
    )

    alliance_id = models.BigIntegerField(unique=True, help_text="Alliance ID")
    alliance_name = models.CharField(max_length=254)
    ticker = models.CharField(max_length=10, blank=True)
    primary_timezone = models.CharField(
        max_length=10, choices=TIMEZONE_CHOICES, default="UNKNOWN"
    )
    override_timezone = models.BooleanField(
        default=False,
        help_text="Manually override auto-detected timezone from zKillboard & structure timers",
    )
    sov_timer_distribution = models.JSONField(
        default=dict,
        blank=True,
        help_text="Hourly distribution of active defense vulnerability timers",
    )
    coalition = models.CharField(
        max_length=128,
        blank=True,
        help_text="Affiliated coalition (e.g. Imperium, PanFam, WinterCo, FI.RE)",
    )
    is_coalition_leader = models.BooleanField(
        default=False,
        help_text="Flagged as the leader / executor alliance of the coalition",
    )
    executor_corp_id = models.BigIntegerField(
        null=True,
        blank=True,
        help_text="Executor corporation ID",
    )
    is_friendly = models.BooleanField(
        default=False,
        help_text="Flagged as friendly / allied entity (ignored by threat scanner & hostile intel)",
    )
    is_alt_alliance = models.BooleanField(
        default=False,
        help_text="Flagged as an alt/holding alliance",
    )
    main_alliance = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="alt_alliances",
        help_text="Main alliance if this is an alt/holding entity",
    )
    sov_capital = models.ForeignKey(
        SolarSystem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="capital_alliances",
        help_text="Designated sovereignty capital solar system",
    )
    systems_held_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of sovereign solar systems held",
    )
    prev_systems_held_count = models.PositiveIntegerField(
        default=0,
        help_text="Previous number of sovereign solar systems held before last scan",
    )
    member_count = models.PositiveIntegerField(
        default=0,
        help_text="Total number of character members across all member corporations",
    )
    prev_member_count = models.PositiveIntegerField(
        default=0,
        help_text="Previous total character member count before last sync",
    )
    member_corps_count = models.PositiveIntegerField(
        default=0,
        help_text="Total count of member corporations in this alliance",
    )
    max_subcap_form = models.PositiveIntegerField(
        default=0,
        help_text="Estimated maximum subcap formup fleet numbers",
    )
    max_cap_form = models.PositiveIntegerField(
        default=0,
        help_text="Estimated maximum capital formup numbers (Dreadnoughts / FAX / Carriers)",
    )
    max_super_form = models.PositiveIntegerField(
        default=0,
        help_text="Estimated maximum supercapital formup numbers (Supercarriers / Titans)",
    )
    prev_max_subcap_form = models.PositiveIntegerField(
        default=0,
        help_text="Previous estimated maximum subcap formup fleet numbers before last zKill scan",
    )
    prev_max_cap_form = models.PositiveIntegerField(
        default=0,
        help_text="Previous estimated maximum capital formup numbers before last zKill scan",
    )
    prev_max_super_form = models.PositiveIntegerField(
        default=0,
        help_text="Previous estimated maximum supercapital formup numbers before last zKill scan",
    )
    zkill_deltas = models.JSONField(
        default=dict,
        blank=True,
        help_text="Percentage changes and capability deltas detected during zKillboard scans",
    )
    activity_heatmap = models.JSONField(
        default=dict,
        blank=True,
        help_text="7x24 activity heat map (day of week x hour) of 3-month rolling kills",
    )
    active_pilots_count = models.PositiveIntegerField(
        default=0,
        help_text="Active PvP pilots count (from zKillboard 7-day stats)",
    )
    weekly_kills_count = models.PositiveIntegerField(
        default=0,
        help_text="Total kills in the last 7 days (last week)",
    )
    weekly_losses_count = models.PositiveIntegerField(
        default=0,
        help_text="Total losses in the last 7 days (last week)",
    )
    zkill_stats_updated_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Last synchronized timestamp from zKillboard stats API",
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submitted_alliances",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = AllianceQuerySet.as_manager()

    @property
    def is_zkill_syncing(self) -> bool:
        """Returns True if zKill stats synchronization is currently running for this alliance or globally"""
        # Django
        from django.core.cache import cache

        return bool(
            cache.get(f"hostile-intel-zkill-syncing-{self.id}")
            or cache.get(f"hostile-intel-zkill-syncing-{self.alliance_id}")
            or cache.get("hostile-intel-update-all-zkill-stats-lock")
        )

    @property
    def member_count_delta_percent(self) -> float | None:
        """Calculates percentage change in character member count since previous sync"""
        if self.prev_member_count > 0 and self.member_count != self.prev_member_count:
            return round(
                ((self.member_count - self.prev_member_count) / self.prev_member_count)
                * 100,
                1,
            )
        return None

    @property
    def systems_held_delta_percent(self) -> float | None:
        """Calculates percentage change in sovereign systems held since previous scan"""
        if (
            self.prev_systems_held_count > 0
            and self.systems_held_count != self.prev_systems_held_count
        ):
            return round(
                (
                    (self.systems_held_count - self.prev_systems_held_count)
                    / self.prev_systems_held_count
                )
                * 100,
                1,
            )
        return None

    class Meta:
        default_permissions = ()
        verbose_name = "Hostile Alliance"
        verbose_name_plural = "Hostile Alliances"
        ordering = ["alliance_name"]

    def __str__(self) -> str:
        if self.ticker:
            return f"{self.alliance_name} [{self.ticker}]"
        return self.alliance_name

    def sync_sov_capital_staging(self, old_capital_id=None):
        """Ensures a primary capital staging system exists for this alliance's sovereignty capital and updates it if the capital moves."""
        if not self.sov_capital:
            return None

        # If capital changed from old_capital_id to a new capital
        if old_capital_id and old_capital_id != self.sov_capital_id:
            old_staging = self.staging_systems.filter(
                solar_system_id=old_capital_id
            ).first()
            if old_staging:
                existing_new = self.staging_systems.filter(
                    solar_system=self.sov_capital
                ).first()
                if existing_new:
                    if (
                        not existing_new.is_primary
                        or existing_new.staging_type != "CAPITAL"
                    ):
                        existing_new.is_primary = True
                        existing_new.staging_type = "CAPITAL"
                        existing_new.save(
                            update_fields=["is_primary", "staging_type", "updated_at"]
                        )
                    return existing_new
                else:
                    old_staging.solar_system = self.sov_capital
                    old_staging.staging_type = "CAPITAL"
                    old_staging.is_primary = True
                    old_staging.notes = "Designated Alliance Sovereignty Capital"
                    old_staging.save(
                        update_fields=[
                            "solar_system",
                            "staging_type",
                            "is_primary",
                            "notes",
                            "updated_at",
                        ]
                    )
                    return old_staging

        existing_staging = self.staging_systems.filter(
            solar_system=self.sov_capital
        ).first()
        if existing_staging:
            if (
                not existing_staging.is_primary
                or existing_staging.staging_type != "CAPITAL"
            ):
                existing_staging.is_primary = True
                existing_staging.staging_type = "CAPITAL"
                existing_staging.save(
                    update_fields=["is_primary", "staging_type", "updated_at"]
                )
            return existing_staging

        # AA Hostile Intel
        from hostile.models import HostileStagingSystem

        return HostileStagingSystem.objects.create(
            solar_system=self.sov_capital,
            alliance=self,
            staging_type="CAPITAL",
            is_primary=True,
            notes="Designated Alliance Sovereignty Capital",
            is_verified=True,
        )

    def save(self, *args, **kwargs):
        old_capital_id = None
        if self.pk:
            try:
                old_inst = (
                    HostileAlliance.objects.filter(pk=self.pk)
                    .values("sov_capital_id")
                    .first()
                )
                if old_inst:
                    old_capital_id = old_inst.get("sov_capital_id")
            except Exception:
                pass

        super().save(*args, **kwargs)

        if self.sov_capital_id:
            try:
                self.sync_sov_capital_staging(old_capital_id=old_capital_id)
            except Exception:
                pass


class HostileCorporation(models.Model):
    """Hostile Corporation entity"""

    corporation_id = models.BigIntegerField(unique=True, help_text="Corporation ID")
    corporation_name = models.CharField(max_length=254)
    ticker = models.CharField(max_length=10, blank=True)
    alliance = models.ForeignKey(
        HostileAlliance,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="corporations",
    )
    coalition = models.CharField(
        max_length=128,
        blank=True,
        help_text="Affiliated coalition (e.g. Imperium, PanFam, WinterCo)",
    )
    is_friendly = models.BooleanField(
        default=False,
        help_text="Flagged as friendly / allied entity (ignored by threat scanner & hostile intel)",
    )
    is_alt_corp = models.BooleanField(
        default=False,
        help_text="Flagged as an alt/holding/cyno corporation",
    )
    main_alliance = models.ForeignKey(
        HostileAlliance,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="affiliated_alt_corps",
        help_text="Associated main alliance if this is an alt/holding corp",
    )
    main_corporation = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="alt_corps",
        help_text="Parent or main corporation if this is an alt corp",
    )
    member_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of character members in this corporation",
    )
    prev_member_count = models.PositiveIntegerField(
        default=0,
        help_text="Previous character member count before last sync",
    )
    max_subcap_form = models.PositiveIntegerField(
        default=0,
        help_text="Estimated maximum subcap formup fleet numbers",
    )
    max_cap_form = models.PositiveIntegerField(
        default=0,
        help_text="Estimated maximum capital formup numbers",
    )
    max_super_form = models.PositiveIntegerField(
        default=0,
        help_text="Estimated maximum supercapital formup numbers",
    )
    prev_max_subcap_form = models.PositiveIntegerField(
        default=0,
        help_text="Previous estimated maximum subcap formup fleet numbers before last zKill scan",
    )
    prev_max_cap_form = models.PositiveIntegerField(
        default=0,
        help_text="Previous estimated maximum capital formup numbers before last zKill scan",
    )
    prev_max_super_form = models.PositiveIntegerField(
        default=0,
        help_text="Previous estimated maximum supercapital formup numbers before last zKill scan",
    )
    zkill_deltas = models.JSONField(
        default=dict,
        blank=True,
        help_text="Percentage changes and capability deltas detected during zKillboard scans",
    )
    activity_heatmap = models.JSONField(
        default=dict,
        blank=True,
        help_text="7x24 activity heat map (day of week x hour) of 3-month rolling kills",
    )
    active_pilots_count = models.PositiveIntegerField(
        default=0,
        help_text="Active PvP pilots count (from zKillboard stats)",
    )
    weekly_kills_count = models.PositiveIntegerField(
        default=0,
        help_text="Total kills in the last 7 days",
    )
    weekly_losses_count = models.PositiveIntegerField(
        default=0,
        help_text="Total losses in the last 7 days",
    )
    zkill_stats_updated_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Last synchronized timestamp from zKillboard stats API",
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submitted_corporations",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def is_zkill_syncing(self) -> bool:
        """Returns True if zKill stats synchronization is currently running for this corporation or globally"""
        # Django
        from django.core.cache import cache

        return bool(
            cache.get(f"hostile-intel-zkill-syncing-{self.id}")
            or cache.get(f"hostile-intel-zkill-syncing-corp-{self.corporation_id}")
            or cache.get("hostile-intel-update-all-zkill-stats-lock")
        )

    @property
    def member_count_delta_percent(self) -> float | None:
        """Calculates percentage change in character member count since previous sync"""
        if self.prev_member_count > 0 and self.member_count != self.prev_member_count:
            return round(
                ((self.member_count - self.prev_member_count) / self.prev_member_count)
                * 100,
                1,
            )
        return None

    @property
    def is_executor(self) -> bool:
        """Returns True if this corporation is the executor corporation of its parent alliance"""
        if self.alliance and self.alliance.executor_corp_id:
            return self.alliance.executor_corp_id == self.corporation_id
        return False

    class Meta:
        default_permissions = ()
        verbose_name = "Hostile Corporation"
        verbose_name_plural = "Hostile Corporations"
        ordering = ["corporation_name"]

    def __str__(self) -> str:
        if self.ticker:
            return f"{self.corporation_name} [{self.ticker}]"
        return self.corporation_name


class SystemTag(models.Model):
    """Categorization tags for solar system intelligence observations"""

    name = models.CharField(max_length=64, unique=True)
    description = models.CharField(max_length=254, blank=True)
    color_class = models.CharField(
        max_length=32,
        default="danger",
        choices=(
            ("danger", "Red (Danger)"),
            ("warning", "Yellow (Warning)"),
            ("primary", "Blue (Primary)"),
            ("info", "Cyan (Info)"),
            ("secondary", "Grey (Secondary)"),
            ("success", "Green (Success)"),
        ),
    )

    class Meta:
        default_permissions = ()
        verbose_name = "System Threat Tag"
        verbose_name_plural = "System Threat Tags"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class HostileStructure(models.Model):
    """Hostile Upwell structure or starbase deployment"""

    CORE_CHOICES = (
        ("FITTED", "Quantum Core Installed"),
        ("UNFITTED", "No Core Detected"),
        ("UNKNOWN", "Unknown"),
    )

    STATE_CHOICES = (
        ("ANCHORING", "Anchoring"),
        ("UNANCHORING", "Unanchoring"),
        ("ARMOR", "Armor Reinforced"),
        ("HULL", "Hull Reinforced"),
        ("ONLINE", "Online"),
        ("LOW_POWER", "Low Power"),
        ("OFFLINE", "Offline"),
        ("ABANDONED", "Abandoned"),
        ("REINFORCED", "Reinforced"),
        ("DESTROYED", "Destroyed"),
    )

    structure_id = models.BigIntegerField(
        null=True, blank=True, unique=True, help_text="Structure ID if known"
    )
    name = models.CharField(max_length=254)
    structure_type = models.ForeignKey(
        ItemType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="hostile_structures",
    )
    solar_system = models.ForeignKey(
        SolarSystem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="hostile_structures",
    )
    corporation = models.ForeignKey(
        HostileCorporation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="structures",
    )
    alliance = models.ForeignKey(
        HostileAlliance,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="structures",
    )
    owner_ticker = models.CharField(
        max_length=32, blank=True, help_text="Ticker shown in bracket/inspect"
    )
    core_status = models.CharField(
        max_length=16, choices=CORE_CHOICES, default="UNKNOWN"
    )
    state = models.CharField(max_length=16, choices=STATE_CHOICES, default="ONLINE")
    vulnerability_window = models.CharField(
        max_length=64,
        blank=True,
        help_text="Crowd-sourced defense vulnerability / reinforcement window (e.g. 18:00 - 22:00 UTC or 19:00 UTC)",
    )
    vulnerability_hour = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Crowd-sourced defense vulnerability hour in UTC (0-23)",
    )
    location_details = models.CharField(
        max_length=254, blank=True, help_text="Celestial / Planet / Moon location"
    )
    notes = models.TextField(blank=True)
    is_verified = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submitted_structures",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = StructureQuerySet.as_manager()

    class Meta:
        default_permissions = ()
        verbose_name = "Hostile Structure"
        verbose_name_plural = "Hostile Structures"
        ordering = ["solar_system__name", "name"]

    def __str__(self) -> str:
        system = self.solar_system.name if self.solar_system else "Unknown System"
        type_name = self.structure_type.name if self.structure_type else "Structure"
        return f"{self.name} ({type_name} - {system})"

    @property
    def owner_name(self) -> str:
        """Return owner alliance name, corporation name, ticker, or fallback"""
        if self.alliance:
            return self.alliance.alliance_name
        if self.corporation:
            return self.corporation.corporation_name
        return self.owner_ticker or "Unknown"

    def save(self, *args, **kwargs):
        if self.state == "ANCHORING":
            self.core_status = "UNFITTED"
        if self.vulnerability_hour is not None and not self.vulnerability_window:
            start_hour = (self.vulnerability_hour - 3) % 24
            end_hour = (self.vulnerability_hour + 3) % 24
            self.vulnerability_window = f"{start_hour:02d}:00 - {end_hour:02d}:00 UTC"
        super().save(*args, **kwargs)

    @property
    def latest_fitting(self):
        return self.fittings.order_by("-updated_at").first()


class StructureFitting(models.Model):
    """Module and service fitting snapshot for a hostile structure"""

    structure = models.ForeignKey(
        HostileStructure, on_delete=models.CASCADE, related_name="fittings"
    )
    name = models.CharField(max_length=254, blank=True)
    eft_format = models.TextField(
        blank=True, help_text="Standard EFT format representation"
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submitted_fittings",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()
        verbose_name = "Structure Fitting"
        verbose_name_plural = "Structure Fittings"
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return f"Fitting for {self.structure.name} ({self.created_at.strftime('%Y-%m-%d')})"


class StructureModule(models.Model):
    """Individual module installed on a structure fitting"""

    SLOT_CHOICES = (
        ("HIGH", "High Slot"),
        ("MED", "Medium Slot"),
        ("LOW", "Low Slot"),
        ("RIG", "Rig Slot"),
        ("SERVICE", "Service Slot"),
        ("CHARGE", "Charge / Fighter / Ammo"),
    )

    fitting = models.ForeignKey(
        StructureFitting, on_delete=models.CASCADE, related_name="modules"
    )
    module_type = models.ForeignKey(
        ItemType, on_delete=models.CASCADE, related_name="structure_module_instances"
    )
    slot_type = models.CharField(max_length=16, choices=SLOT_CHOICES)
    slot_number = models.PositiveSmallIntegerField(default=1)
    is_online = models.BooleanField(default=True)

    class Meta:
        default_permissions = ()
        verbose_name = "Structure Module"
        verbose_name_plural = "Structure Modules"
        ordering = ["slot_type", "slot_number"]

    def __str__(self) -> str:
        return f"{self.module_type.name} [{self.slot_type} {self.slot_number}]"


class StructureTimer(models.Model):
    """Timer record for hostile structure reinforcement, anchor, or sovereignty contest"""

    TIMER_TYPES = (
        ("ANCHORING", "Anchoring"),
        ("UNANCHORING", "Unanchoring"),
        ("ARMOR", "Armor"),
        ("HULL", "Hull"),
        ("SOV_IHUB", "Sov I-Hub"),
        ("SOV_TCU", "Sov TCU"),
        ("OTHER", "Other"),
    )

    structure = models.ForeignKey(
        HostileStructure,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="timers",
    )
    solar_system = models.ForeignKey(
        SolarSystem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="structure_timers",
    )
    timer_type = models.CharField(max_length=16, choices=TIMER_TYPES)
    timer_datetime = models.DateTimeField(
        help_text="Expected timer expiry datetime in UTC"
    )
    is_verified = models.BooleanField(default=False)
    defender_id = models.BigIntegerField(
        null=True, blank=True, help_text="Defender Alliance or Corporation ID"
    )
    defender_name = models.CharField(
        max_length=254, blank=True, help_text="Defender Alliance / Corp Name"
    )
    defender_ticker = models.CharField(
        max_length=10, blank=True, help_text="Defender Ticker"
    )
    defender_score = models.FloatField(
        default=0.0, help_text="Defender score (0.0 to 1.0)"
    )
    campaign_id = models.IntegerField(
        null=True, blank=True, help_text="ESI Sovereignty Campaign ID"
    )
    is_sov_campaign = models.BooleanField(
        default=False,
        help_text="True if this is an automated public sovereignty campaign timer",
    )
    is_concluded = models.BooleanField(
        default=False,
        help_text="True if this sovereignty campaign has concluded/ended",
    )
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submitted_timers",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TimerQuerySet.as_manager()

    class Meta:
        default_permissions = ()
        verbose_name = "Structure / Sov Timer"
        verbose_name_plural = "Structure / Sov Timers"
        ordering = ["timer_datetime"]

    def __str__(self) -> str:
        target = (
            self.structure.name
            if self.structure
            else (self.solar_system.name if self.solar_system else "Unknown")
        )
        return f"{target} - {self.timer_type} ({self.timer_datetime.strftime('%Y-%m-%d %H:%M UTC')})"

    @property
    def target_name(self) -> str:
        """Return structure name or solar system name with timer type fallback"""
        if self.structure:
            return self.structure.name
        if self.solar_system:
            return f"{self.solar_system.name} ({self.get_timer_type_display()})"
        return self.get_timer_type_display()

    @property
    def defender_display(self) -> str:
        """Return formatted defender name and ticker or owner fallback"""
        if self.structure and self.structure.owner_name:
            return self.structure.owner_name
        if self.defender_name:
            if self.defender_ticker:
                return f"{self.defender_name} [{self.defender_ticker}]"
            return self.defender_name
        return "Unknown Defender"

    @property
    def defender_percent(self) -> int:
        if self.is_sov_campaign and (
            self.defender_score is None
            or (self.defender_score == 0.0 and not self.is_concluded)
        ):
            return 60
        score = self.defender_score or 0.0
        if 0.0 <= score <= 1.0:
            return int(round(score * 100))
        return int(round(score))

    @property
    def attacker_percent(self) -> int:
        return max(0, 100 - self.defender_percent)

    @property
    def is_tracked_hostile(self) -> bool:
        """True if this timer or its defender is related to a tracked hostile entity"""
        if self.structure:
            return True
        if self.defender_id:
            if HostileAlliance.objects.filter(alliance_id=self.defender_id).exists():
                return True
            if HostileCorporation.objects.filter(
                corporation_id=self.defender_id
            ).exists():
                return True
        if self.defender_name:
            if HostileAlliance.objects.filter(
                alliance_name__iexact=self.defender_name
            ).exists():
                return True
            if HostileCorporation.objects.filter(
                corporation_name__iexact=self.defender_name
            ).exists():
                return True
        return False

    @property
    def region_name(self) -> str:
        if self.solar_system:
            try:
                if (
                    hasattr(self.solar_system, "constellation")
                    and self.solar_system.constellation
                ):
                    if (
                        hasattr(self.solar_system.constellation, "region")
                        and self.solar_system.constellation.region
                    ):
                        return self.solar_system.constellation.region.name
            except Exception:
                pass
        return "Unknown Region"

    @property
    def has_started(self) -> bool:
        """True if the timer or contest window has already reached its scheduled start time"""
        return timezone.now() >= self.timer_datetime

    @property
    def is_ongoing(self) -> bool:
        """True if this is a sovereignty campaign that has started and is currently ongoing"""
        if self.is_sov_campaign:
            return self.has_started and not self.is_concluded
        return False

    @property
    def is_expired(self) -> bool:
        """True if the timer datetime is in the past and concluded"""
        if self.is_sov_campaign:
            return self.is_concluded
        if self.timer_datetime:
            return self.timer_datetime < timezone.now()
        return False

    @property
    def outcome_display(self) -> str:
        """Summary of campaign outcome for concluded events"""
        if not self.is_concluded and not self.is_expired:
            return "Active / In Progress" if self.has_started else "Upcoming"
        if self.defender_percent >= 60 or self.defender_score >= 1.0:
            return f"Defended ({self.defender_percent}%)"
        elif self.defender_percent <= 40 or self.defender_score <= 0.0:
            return f"Captured by Attackers ({self.attacker_percent}%)"
        else:
            return f"Concluded (Def {self.defender_percent}% / Atk {self.attacker_percent}%)"

    @property
    def defender_score_percent(self) -> int:
        return self.defender_percent

    @property
    def attacker_score_percent(self) -> int:
        return self.attacker_percent

    @property
    def structure_event_name(self) -> str:
        """Descriptive name of what is currently reinforced"""
        if self.timer_type == "SOV_IHUB":
            return "Infrastructure Hub (I-Hub)"
        elif self.timer_type == "SOV_TCU":
            return "Territorial Claim Unit (TCU)"
        elif self.timer_type == "ANCHORING":
            return "Structure Anchoring"
        elif self.timer_type == "UNANCHORING":
            return "Structure Unanchoring"
        elif self.timer_type == "ARMOR":
            return "Armor Reinforcement"
        elif self.timer_type == "HULL":
            return "Hull Reinforcement"
        elif self.timer_type == "OTHER":
            if self.is_sov_campaign or "Station" in (self.notes or ""):
                return "Station Freeport"
            return "Structure Defense"
        return self.get_timer_type_display()


class SystemObservation(models.Model):
    """Crowd-sourced system report, gate camp warning, or staging intel"""

    THREAT_CHOICES = (
        ("LOW", "Low Threat"),
        ("MEDIUM", "Medium Threat"),
        ("HIGH", "High Threat"),
        ("EXTREME", "Extreme Threat"),
    )

    solar_system = models.ForeignKey(
        SolarSystem, on_delete=models.CASCADE, related_name="hostile_observations"
    )
    threat_level = models.CharField(
        max_length=16, choices=THREAT_CHOICES, default="LOW"
    )
    tags = models.ManyToManyField(SystemTag, blank=True, related_name="observations")
    time_seen = models.DateTimeField(
        default=timezone.now,
        help_text="Time when the hostile fleet, camp, or activity was observed (UTC)",
    )
    local_scan = models.ForeignKey(
        "LocalThreatScan",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="observations",
        help_text="Linked Local Threat Scan analysis",
    )
    dscan_summary = models.JSONField(
        default=dict,
        blank=True,
        help_text="Summary of ship types and deployables detected on D-Scan",
    )
    observation_text = models.TextField(
        help_text="Details regarding gate camps, bubbles, or hostile presence"
    )
    active_hours = models.CharField(
        max_length=64, blank=True, help_text="e.g. 18:00 - 22:00 UTC or USTZ / EUTZ"
    )
    is_pinned = models.BooleanField(
        default=False, help_text="Pinned sitreps are highlighted on the dashboard"
    )
    is_verified = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submitted_observations",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ObservationQuerySet.as_manager()

    class Meta:
        default_permissions = ()
        verbose_name = "System Observation"
        verbose_name_plural = "System Observations"
        ordering = ["-is_pinned", "-time_seen", "-created_at"]

    def __str__(self) -> str:
        seen = self.time_seen or self.created_at
        return f"{self.solar_system.name} [{self.threat_level}] ({seen.strftime('%Y-%m-%d %H:%M')})"

    @property
    def display_time_seen(self):
        return self.time_seen or self.created_at

    @property
    def is_fresh(self) -> bool:
        """True if observed within the last hour"""
        ref_time = self.time_seen or self.created_at
        return (timezone.now() - ref_time).total_seconds() < 3600

    @property
    def is_active_recent(self) -> bool:
        """True if observed within the last 4 hours"""
        ref_time = self.time_seen or self.created_at
        return (timezone.now() - ref_time).total_seconds() < 14400

    @property
    def freshness_status(self) -> str:
        ref_time = self.time_seen or self.created_at
        age_seconds = (timezone.now() - ref_time).total_seconds()
        if age_seconds < 3600:
            return "FRESH"
        elif age_seconds < 14400:
            return "ACTIVE"
        return "LOGGED"


class HostilePilotDossier(models.Model):
    """Intelligence dossier on key hostile pilots, cyno alts, and capital commanders"""

    character_id = models.BigIntegerField(unique=True, help_text="Character ID")
    character_name = models.CharField(max_length=254)
    corporation_name = models.CharField(max_length=254, blank=True)
    alliance_name = models.CharField(max_length=254, blank=True)
    associated_alliance_name = models.CharField(
        max_length=254,
        blank=True,
        help_text="Specific hostile alliance this out-of-corp pilot / alt is associated with (name only)",
    )
    main_character = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="alts",
        help_text="Main character if this pilot is an alt",
    )
    is_alt = models.BooleanField(
        default=False,
        help_text="Mark if this character is an alt of another pilot",
    )
    alt_inference_reason = models.CharField(
        max_length=254,
        blank=True,
        help_text="Reason for alt detection (e.g. Name Pattern, Cyno Association, Multibox Pattern)",
    )
    alt_confidence = models.CharField(
        max_length=16,
        blank=True,
        choices=[("HIGH", "High"), ("MEDIUM", "Medium"), ("LOW", "Low")],
        help_text="Confidence rating for alt detection",
    )
    is_out_of_corp = models.BooleanField(
        default=False,
        help_text="Mark if this pilot operates in NPC/holding corps as an undercover alt",
    )
    is_cyno_alt = models.BooleanField(
        default=False, help_text="Known cyno dropper / cyno alt"
    )
    is_blops_pilot = models.BooleanField(
        default=False, help_text="Known Black Ops / Covert Hunter pilot"
    )
    is_dread_pilot = models.BooleanField(default=False, help_text="Dreadnought pilot")
    is_fax_pilot = models.BooleanField(
        default=False, help_text="Force Auxiliary / FAX pilot"
    )
    is_capital_pilot = models.BooleanField(
        default=False, help_text="Carrier / Capital pilot"
    )
    is_super_pilot = models.BooleanField(default=False, help_text="Supercarrier pilot")
    is_titan_pilot = models.BooleanField(default=False, help_text="Titan pilot")
    is_fc = models.BooleanField(default=False, help_text="Fleet Commander")

    # Behavioral tags & ratings (zKillboard intelligence)
    is_awox = models.BooleanField(
        default=False,
        help_text="Corporate AWOX tag (10+ corp friendly fire final blows in past year)",
    )
    awox_count = models.IntegerField(
        default=0, blank=True, help_text="Corporate AWOX final blows count"
    )
    is_alliance_awox = models.BooleanField(
        default=False,
        help_text="Alliance AWOX tag (15+ alliance friendly fire final blows in past year)",
    )
    alliance_awox_count = models.IntegerField(
        default=0, blank=True, help_text="Alliance AWOX final blows count"
    )
    is_faction_awox = models.BooleanField(
        default=False,
        help_text="Faction AWOX tag (20+ faction friendly fire final blows in past year)",
    )
    faction_awox_count = models.IntegerField(
        default=0, blank=True, help_text="Faction AWOX final blows count"
    )
    is_bait = models.BooleanField(default=False, help_text="Bait pilot tag")
    bait_level = models.CharField(
        max_length=16,
        blank=True,
        choices=[("LOW", "Low"), ("MEDIUM", "Medium"), ("HIGH", "High")],
        help_text="Bait rating level (Low 4-6, Med 7-11, High 12+)",
    )
    bait_count = models.IntegerField(
        default=0, blank=True, help_text="Bait matches count"
    )
    is_ganker = models.BooleanField(
        default=False, help_text="Highsec Ganker tag (10+ highsec ganks in past year)"
    )
    ganker_count = models.IntegerField(
        default=0, blank=True, help_text="Highsec ganks count"
    )
    is_logi_pilot = models.BooleanField(
        default=False,
        help_text="Logistics pilot tag (12+ logistics appearances in past 90 days)",
    )
    logi_count = models.IntegerField(
        default=0, blank=True, help_text="Logistics appearances count"
    )
    fc_level = models.CharField(
        max_length=16,
        blank=True,
        choices=[
            ("LOW", "Low"),
            ("MEDIUM", "Medium"),
            ("HIGH", "High"),
            ("CANDIDATE", "Candidate"),
        ],
        help_text="Fleet Commander rating level (Low 35+, Med 60+, High 100+)",
    )
    fc_score = models.IntegerField(
        default=0, blank=True, help_text="Fleet Commander rating score"
    )
    cyno_count = models.IntegerField(
        default=0, blank=True, help_text="Fitted cyno losses count (past year)"
    )
    blops_count = models.IntegerField(
        default=0, blank=True, help_text="Black Ops appearances (past 90d)"
    )
    capital_count = models.IntegerField(
        default=0, blank=True, help_text="Capital appearances (past 90d)"
    )
    super_count = models.IntegerField(
        default=0, blank=True, help_text="Supercarrier appearances (past 90d)"
    )
    titan_count = models.IntegerField(
        default=0, blank=True, help_text="Titan appearances (past 90d)"
    )
    is_rookie = models.BooleanField(
        default=False,
        help_text="Rookie pilot (<180 days old, negative PvP ratio in past 90d)",
    )
    danger_ratio = models.IntegerField(
        default=0,
        blank=True,
        help_text="zKillboard Danger Ratio (0-100%)",
    )
    gang_ratio = models.IntegerField(
        default=0,
        blank=True,
        help_text="zKillboard Gang Ratio (0-100%)",
    )
    security_status = models.FloatField(
        null=True,
        blank=True,
        help_text="Character security status (-10.0 to 10.0)",
    )
    birthday = models.DateField(
        null=True,
        blank=True,
        help_text="Character creation date",
    )
    corporation_ticker = models.CharField(
        max_length=16,
        blank=True,
        help_text="Corporation ticker (e.g. STHCM)",
    )
    alliance_ticker = models.CharField(
        max_length=16,
        blank=True,
        help_text="Alliance ticker (e.g. FIGL)",
    )
    top_ships = models.JSONField(
        default=list,
        blank=True,
        help_text="Top flown ship hulls",
    )
    likely_ship = models.CharField(
        max_length=128,
        blank=True,
        help_text="Primary / most likely flown ship",
    )
    zkill_labels = models.JSONField(
        default=list,
        blank=True,
        help_text="Raw behavioral labels and tags from zKillboard",
    )
    last_seen_system = models.ForeignKey(
        SolarSystem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="seen_hostile_pilots",
    )
    last_seen_date = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    is_verified = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submitted_pilots",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = PilotQuerySet.as_manager()

    class Meta:
        default_permissions = ()
        verbose_name = "Hostile Pilot Dossier"
        verbose_name_plural = "Hostile Pilot Dossiers"
        ordering = ["character_name"]

    def __str__(self) -> str:
        return f"{self.character_name} ({self.display_corporation_name})"

    @property
    def fc_display(self) -> str:
        """Formatted Fleet Commander label"""
        if not self.is_fc and not self.fc_level:
            return ""
        level = (self.fc_level or "").capitalize()
        if level in ["Low", "Medium", "High"]:
            if self.fc_score > 0:
                return f"FC ({level} {self.fc_score})"
            return f"FC ({level})"
        if level == "Candidate":
            return "FC Candidate"
        return "Fleet Commander"

    @property
    def bait_display(self) -> str:
        """Formatted Bait label"""
        if not self.is_bait:
            return ""
        level = (self.bait_level or "").capitalize()
        if level and self.bait_count > 0:
            return f"Bait ({level} {self.bait_count})"
        if level:
            return f"Bait ({level})"
        if self.bait_count > 0:
            return f"Bait ({self.bait_count})"
        return "Bait Pilot"

    @property
    def awox_display(self) -> str:
        """Formatted Corp AWOX label"""
        if not self.is_awox:
            return ""
        if self.awox_count > 0:
            return f"AWOX ({self.awox_count})"
        return "AWOX"

    @property
    def alliance_awox_display(self) -> str:
        """Formatted Alliance AWOX label"""
        if not self.is_alliance_awox:
            return ""
        if self.alliance_awox_count > 0:
            return f"Alliance AWOX ({self.alliance_awox_count})"
        return "Alliance AWOX"

    @property
    def faction_awox_display(self) -> str:
        """Formatted Faction AWOX label"""
        if not self.is_faction_awox:
            return ""
        if self.faction_awox_count > 0:
            return f"Faction AWOX ({self.faction_awox_count})"
        return "Faction AWOX"

    @property
    def ganker_display(self) -> str:
        """Formatted Ganker label"""
        if not self.is_ganker:
            return ""
        if self.ganker_count > 0:
            return f"Ganker ({self.ganker_count})"
        return "Ganker"

    @property
    def logi_display(self) -> str:
        """Formatted Logistics label"""
        if not self.is_logi_pilot:
            return ""
        if self.logi_count > 0:
            return f"Logi ({self.logi_count})"
        return "Logi Pilot"

    @property
    def cyno_display(self) -> str:
        """Formatted Cyno label"""
        if not self.is_cyno_alt:
            return ""
        if self.cyno_count > 0:
            return f"Cyno ({self.cyno_count})"
        return "Cyno Alt"

    @property
    def blops_display(self) -> str:
        """Formatted Black Ops label"""
        if not self.is_blops_pilot:
            return ""
        if self.blops_count > 0:
            return f"Blops ({self.blops_count})"
        return "Black Ops"

    @property
    def capital_display(self) -> str:
        """Formatted Capital label"""
        if not self.is_capital_pilot:
            return ""
        if self.capital_count > 0:
            return f"Capital ({self.capital_count})"
        return "Capital Pilot"

    @property
    def super_display(self) -> str:
        """Formatted Supercarrier label"""
        if not self.is_super_pilot:
            return ""
        if self.super_count > 0:
            return f"Super ({self.super_count})"
        return "Super Pilot"

    @property
    def titan_display(self) -> str:
        """Formatted Titan label"""
        if not self.is_titan_pilot:
            return ""
        if self.titan_count > 0:
            return f"Titan ({self.titan_count})"
        return "Titan Pilot"

    @property
    def known_alts(self):
        """QuerySet of detected or assigned alts of this pilot"""
        return self.alts.all()

    @property
    def is_main(self) -> bool:
        """True if this character has known alts and is not itself marked as an alt"""
        return self.alts.exists() and not self.is_alt

    @property
    def alt_display(self) -> str:
        """Formatted summary of alt status and main character linkage"""
        if self.is_alt and self.main_character:
            conf_str = (
                f" ({self.alt_confidence.capitalize()})" if self.alt_confidence else ""
            )
            return f"Alt of {self.main_character.character_name}{conf_str}"
        elif self.is_alt:
            return "Alt Pilot"
        elif self.is_main:
            cnt = self.alts.count()
            return f"Main ({cnt} Alt{'s' if cnt != 1 else ''})"
        return ""

    @property
    def danger_boxes(self) -> list:
        """Returns 3-element list of booleans representing the zKillboard danger boxes"""
        ratio = self.danger_ratio or 0
        if ratio >= 80:
            return [True, True, True]
        elif ratio >= 45:
            return [True, True, False]
        elif ratio >= 15:
            return [True, False, False]
        return [False, False, False]

    @property
    def danger_level_class(self) -> str:
        """CSS class color for danger meter"""
        ratio = self.danger_ratio or 0
        if ratio >= 66:
            return "danger"
        elif ratio >= 33:
            return "warning"
        return "secondary"

    @property
    def age_years(self) -> Optional[int]:
        """Calculates character age in years from birthday"""
        if not self.birthday:
            return None
        from datetime import date
        today = date.today()
        return today.year - self.birthday.year - ((today.month, today.day) < (self.birthday.month, self.birthday.day))

    @property
    def formatted_sec_status(self) -> str:
        """Formatted security status string (e.g. -0.30 or +5.00)"""
        if self.security_status is None:
            return ""
        val = float(self.security_status)
        return f"{val:+.2f}"

    @property
    def display_corporation_name(self) -> str:
        """Returns resolved corporation name or fallback"""
        if (
            self.corporation_name
            and not str(self.corporation_name).isdigit()
            and self.corporation_name != "Unknown Corp"
        ):
            return self.corporation_name
        if self.corporation_name and str(self.corporation_name).isdigit():
            corp_id = int(self.corporation_name)
            hc = HostileCorporation.objects.filter(corporation_id=corp_id).first()
            if hc and hc.corporation_name:
                return hc.corporation_name
            try:
                # Alliance Auth
                from allianceauth.eveonline.models import EveCorporationInfo

                ec = EveCorporationInfo.objects.filter(corporation_id=corp_id).first()
                if ec and ec.corporation_name:
                    return ec.corporation_name
            except Exception:
                pass
        return self.corporation_name or "Unknown Corp"


class IntelAuditLog(models.Model):
    """Audit log for intel verification, edits, pins, and approvals"""

    action = models.CharField(max_length=64)
    target_model = models.CharField(max_length=64)
    target_id = models.CharField(max_length=254, null=True, blank=True)
    target_repr = models.CharField(max_length=254, blank=True)
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="intel_audit_logs",
    )
    details = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        verbose_name = "Intel Audit Log"
        verbose_name_plural = "Intel Audit Logs"
        ordering = ["-timestamp"]

    def __str__(self) -> str:
        user_name = self.user.username if self.user else "System"
        return f"[{self.timestamp.strftime('%Y-%m-%d %H:%M')}] {user_name} - {self.action} on {self.target_model}"


class HostileStagingSystem(models.Model):
    """Hostile staging system (can be inside or outside the target's sov)"""

    STAGING_TYPES = (
        ("PRIMARY", "Primary Staging"),
        ("FORWARD_DEPLOYED", "Forward Staging (FOB)"),
        ("CAPITAL", "Capital / Super Staging"),
        ("DEPLOYMENT", "Deployment Staging"),
        ("HUNTING", "Hunting / Black Ops Staging"),
        ("OTHER", "Other Staging"),
    )

    solar_system = models.ForeignKey(
        SolarSystem, on_delete=models.CASCADE, related_name="hostile_stagings"
    )
    alliance = models.ForeignKey(
        HostileAlliance,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="staging_systems",
    )
    corporation = models.ForeignKey(
        HostileCorporation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="staging_systems",
    )
    structure = models.ForeignKey(
        HostileStructure,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="staging_instances",
    )
    staging_type = models.CharField(
        max_length=32, choices=STAGING_TYPES, default="PRIMARY"
    )
    is_primary = models.BooleanField(
        default=False,
        help_text="Mark as the main active staging system for this entity",
    )
    notes = models.TextField(
        blank=True,
        help_text="Deployment details, jump bridge access, cyno beacons, etc.",
    )
    max_subcap_form = models.PositiveIntegerField(
        default=0,
        help_text="Estimated max subcaps staged/formed from this system",
    )
    max_cap_form = models.PositiveIntegerField(
        default=0,
        help_text="Estimated max capitals staged/formed from this system",
    )
    max_super_form = models.PositiveIntegerField(
        default=0,
        help_text="Estimated max supercapitals staged/formed from this system",
    )
    is_verified = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submitted_stagings",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = StagingQuerySet.as_manager()

    class Meta:
        default_permissions = ()
        verbose_name = "Hostile Staging System"
        verbose_name_plural = "Hostile Staging Systems"
        ordering = ["-is_primary", "solar_system__name"]

    def __str__(self) -> str:
        owner = (
            self.alliance.alliance_name
            if self.alliance
            else (self.corporation.corporation_name if self.corporation else "Hostiles")
        )
        return f"{self.solar_system.name} ({owner} - {self.get_staging_type_display()})"

    @property
    def owner_name(self) -> str:
        """Return owning alliance name, corporation name, or fallback"""
        if self.alliance:
            return self.alliance.alliance_name
        if self.corporation:
            return self.corporation.corporation_name
        return "Hostile Fleet"

    @property
    def estimated_subcap_form(self) -> int:
        if self.max_subcap_form > 0:
            return self.max_subcap_form
        if self.alliance and self.alliance.max_subcap_form > 0:
            return self.alliance.max_subcap_form
        if self.corporation and self.corporation.max_subcap_form > 0:
            return self.corporation.max_subcap_form
        return 0

    @property
    def estimated_cap_form(self) -> int:
        if self.max_cap_form > 0:
            return self.max_cap_form
        if self.alliance and self.alliance.max_cap_form > 0:
            return self.alliance.max_cap_form
        if self.corporation and self.corporation.max_cap_form > 0:
            return self.corporation.max_cap_form
        return 0

    @property
    def estimated_super_form(self) -> int:
        if self.max_super_form > 0:
            return self.max_super_form
        if self.alliance and self.alliance.max_super_form > 0:
            return self.alliance.max_super_form
        if self.corporation and self.corporation.max_super_form > 0:
            return self.corporation.max_super_form
        return 0

    @property
    def is_form_overridden(self) -> bool:
        return bool(self.max_subcap_form or self.max_cap_form or self.max_super_form)


class HostileDoctrine(models.Model):
    """Hostile fleet doctrine (general or staged)"""

    ROLE_TYPES = (
        ("MAIN_FLEET", "Mainline Fleet Doctrine"),
        ("SKIRMISH", "Skirmish / Fast Response"),
        ("CAPITAL_SUPER", "Capital / Supercapital Fleet"),
        ("HOME_DEFENSE", "Home Defense / Standing Fleet"),
        ("ROAMING", "Small Gang / Roaming"),
        ("GATECAMP", "Gatecamp / Lockdown"),
        ("SPECIALTY", "Specialty / Black Ops / T3C"),
    )

    name = models.CharField(
        max_length=254,
        help_text="e.g. Armor Battleships (TFI), RLML Cerberus, Heavy Nano",
    )
    alliance = models.ForeignKey(
        HostileAlliance,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="doctrines",
    )
    corporation = models.ForeignKey(
        HostileCorporation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="doctrines",
    )
    role_type = models.CharField(
        max_length=32, choices=ROLE_TYPES, default="MAIN_FLEET"
    )
    is_general_doctrine = models.BooleanField(
        default=True,
        help_text="Mark if used generally by this entity across multiple theaters",
    )
    stagings = models.ManyToManyField(
        HostileStagingSystem,
        blank=True,
        related_name="doctrines",
        help_text="Stagings where this doctrine is actively staged",
    )
    primary_ship_types = models.CharField(
        max_length=254,
        blank=True,
        help_text="Primary hulls e.g. Tempest Fleet Issue, Guardian, Damnation, Loki",
    )
    description = models.TextField(
        blank=True, help_text="Combat tactics, engagement profile, counterplay notes"
    )
    is_verified = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submitted_doctrines",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = DoctrineQuerySet.as_manager()

    class Meta:
        default_permissions = ()
        verbose_name = "Hostile Doctrine"
        verbose_name_plural = "Hostile Doctrines"
        ordering = ["name"]

    def __str__(self) -> str:
        owner = (
            self.alliance.alliance_name
            if self.alliance
            else (self.corporation.corporation_name if self.corporation else "Hostile")
        )
        return f"{self.name} [{owner}]"

    @property
    def owner_name(self) -> str:
        """Return owning alliance name, corporation name, or fallback"""
        if self.alliance:
            return self.alliance.alliance_name
        if self.corporation:
            return self.corporation.corporation_name
        return "Hostiles"


class DoctrineFit(models.Model):
    """Example fit for a hostile doctrine, linkable to zKillboard or EFT"""

    doctrine = models.ForeignKey(
        HostileDoctrine, on_delete=models.CASCADE, related_name="example_fits"
    )
    ship_type = models.ForeignKey(
        ItemType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="doctrine_fits",
    )
    name = models.CharField(
        max_length=254,
        help_text="Fit role/title e.g. Main DPS TFI, Armor Logi Guardian",
    )
    role = models.CharField(
        max_length=64, blank=True, help_text="e.g. DPS, Logi, Tackle, Boosts, Cyno"
    )
    zkill_link = models.URLField(
        max_length=500,
        blank=True,
        help_text="zKillboard killmail link demonstrating fit",
    )
    eft_format = models.TextField(
        blank=True, help_text="Standard EFT format representation"
    )
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submitted_doctrine_fits",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()
        verbose_name = "Doctrine Example Fit"
        verbose_name_plural = "Doctrine Example Fits"
        ordering = ["ship_type__name", "name"]

    def __str__(self) -> str:
        ship = self.ship_type.name if self.ship_type else "Ship"
        return f"{self.doctrine.name} - {self.name} ({ship})"


class LocalThreatScan(models.Model):
    """Local scan analysis record (localthreat-style) with Black Ops & Capital drop probability estimation"""

    THREAT_LEVELS = (
        ("LOW", "Low Threat"),
        ("MODERATE", "Moderate Threat"),
        ("HIGH", "High Threat"),
        ("CRITICAL", "Critical Threat"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    solar_system = models.ForeignKey(
        SolarSystem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="local_scans",
    )
    raw_local_text = models.TextField(help_text="Raw pasted local chat or member list")
    raw_dscan_text = models.TextField(
        blank=True, help_text="Coupled D-Scan text for ship profile cross-referencing"
    )

    pilot_count = models.PositiveIntegerField(default=0)
    hostile_count = models.PositiveIntegerField(default=0)
    ignored_count = models.PositiveIntegerField(
        default=0, help_text="Friendly / ignored alliance pilots excluded"
    )

    blops_drop_chance = models.PositiveSmallIntegerField(
        default=0, help_text="Calculated percentage chance of Black Ops drop (0-100%)"
    )
    cap_drop_chance = models.PositiveSmallIntegerField(
        default=0,
        help_text="Calculated percentage chance of Capital / Super drop (0-100%)",
    )
    threat_level = models.CharField(max_length=16, choices=THREAT_LEVELS, default="LOW")

    threat_factors = models.JSONField(
        default=list, blank=True, help_text="List of key risk indicators"
    )
    activity_profile = models.JSONField(
        default=dict, blank=True, help_text="Timeline & timezone activity breakdown"
    )
    pilots_data = models.JSONField(
        default=list,
        blank=True,
        help_text="Parsed pilots with dossiers, tags, and likely ships",
    )
    ship_profile = models.JSONField(
        default=dict, blank=True, help_text="Coupled D-Scan ship class breakdown"
    )

    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submitted_scans",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        verbose_name = "Local Threat Scan"
        verbose_name_plural = "Local Threat Scans"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        sys_name = self.solar_system.name if self.solar_system else "Unknown System"
        return f"Local Threat: {sys_name} [{self.threat_level}] ({self.created_at.strftime('%Y-%m-%d %H:%M')})"
