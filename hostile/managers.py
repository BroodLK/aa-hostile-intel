# Standard Library
from datetime import timedelta

# Django
from django.db import models
from django.utils import timezone


class AllianceQuerySet(models.QuerySet):
    def with_sov(self):
        return self.exclude(sov_timer_distribution={})

    def hostiles(self):
        return self.filter(is_friendly=False)

    def friendlies(self):
        return self.filter(is_friendly=True)


class StructureQuerySet(models.QuerySet):
    def verified(self):
        return self.filter(is_verified=True)

    def active(self):
        return self.exclude(state="DESTROYED")

    def in_system(self, system):
        return self.filter(solar_system=system)

    def in_region(self, region):
        return self.filter(solar_system__constellation__region=region)

    def by_alliance(self, alliance):
        return self.filter(alliance=alliance)

    def find_matching(
        self,
        solar_system=None,
        name=None,
        structure_type=None,
        structure_id=None,
        location_details="",
    ):
        """
        Smart deduplication search for an existing structure:
        - Exact structure_id match if provided
        - Exact normalized name match in solar_system (or globally if no system)
        - Ansiblex route matching (bidirectional endpoint matching e.g. 'A-YB15 » ZS-PNI' vs 'ZS-PNI » A-YB15')
        - Ticker-stripped core name match within solar_system (e.g. '[ALLI] Staging Citadel' vs 'Staging Citadel')
        - Generic vs System-specific fallback matching ('Mercenary Den' vs 'Mercenary Den (BNX-AS)')
        - Coordinate/celestial matching
        """
        # AA Hostile Intel
        from hostile.models import extract_core_structure_name, normalize_structure_name

        if structure_id:
            match = self.filter(structure_id=structure_id).first()
            if match:
                return match

        if not name and not structure_type and not location_details:
            return None

        norm_name = normalize_structure_name(name) if name else ""
        system_structures = list(
            self.filter(solar_system=solar_system) if solar_system else self.all()
        )

        # 1. Exact case-insensitive match on normalized name
        if norm_name:
            for s in system_structures:
                if s.name.lower() == norm_name.lower():
                    return s

        # 2. Ansiblex bidirectional route matching
        if norm_name and "»" in norm_name:
            parts = [
                p.strip().split()[0]
                for p in norm_name.split("»")
                if p.strip().split()
            ]
            if len(parts) >= 2:
                sys1, sys2 = parts[0].upper(), parts[1].upper()
                if sys1 and sys2:
                    for s in system_structures:
                        if "»" in s.name:
                            s_parts = [
                                p.strip().split()[0]
                                for p in s.name.split("»")
                                if p.strip().split()
                            ]
                            if len(s_parts) >= 2:
                                s_pair = {s_parts[0].upper(), s_parts[1].upper()}
                                if s_pair == {sys1, sys2}:
                                    return s
                        elif sys1 in s.name.upper() and sys2 in s.name.upper():
                            return s

        # 3. Ticker-stripped core name match (handles [TICKR] prefixes and (System) suffixes)
        if norm_name:
            core_name = extract_core_structure_name(
                norm_name, solar_system=solar_system
            ).lower()
            if core_name:
                for s in system_structures:
                    s_core = extract_core_structure_name(
                        s.name, solar_system=solar_system
                    ).lower()
                    if s_core == core_name:
                        return s

        # 4. Generic hull type fallback matching ONLY if name is strictly generic
        if solar_system and structure_type:
            sys_name = getattr(solar_system, "name", str(solar_system)).strip()
            type_name = getattr(structure_type, "name", "").strip()
            if type_name and norm_name.lower() in [
                type_name.lower(),
                f"{type_name.lower()} ({sys_name.lower()})",
            ]:
                type_matches = [
                    s
                    for s in system_structures
                    if s.name.lower()
                    in [
                        type_name.lower(),
                        f"{type_name.lower()} ({sys_name.lower()})",
                    ]
                ]
                if len(type_matches) == 1:
                    return type_matches[0]

        # 5. Global match if solar_system was not specified
        if norm_name and not solar_system:
            match = self.filter(name__iexact=norm_name).first()
            if match:
                return match

        return None


class TimerQuerySet(models.QuerySet):
    def upcoming(self):
        cutoff_24h = timezone.now() - timedelta(hours=24)
        return self.filter(
            models.Q(
                is_sov_campaign=True, is_concluded=False, timer_datetime__gte=cutoff_24h
            )
            | models.Q(is_sov_campaign=False, timer_datetime__gte=timezone.now())
        ).order_by("timer_datetime")

    def expired(self, hours: int = 24):
        cutoff = timezone.now() - timedelta(hours=hours)
        return self.filter(
            models.Q(
                is_sov_campaign=True, is_concluded=True, timer_datetime__gte=cutoff
            )
            | models.Q(
                is_sov_campaign=False,
                timer_datetime__lt=timezone.now(),
                timer_datetime__gte=cutoff,
            )
            | models.Q(is_sov_campaign=False, timer_datetime__lt=timezone.now())
        ).order_by("-timer_datetime")

    def verified(self):
        return self.filter(is_verified=True)

    def sov_campaigns(self):
        return self.filter(
            models.Q(is_sov_campaign=True)
            | models.Q(timer_type__in=["SOV_IHUB", "SOV_TCU"])
        )

    def ongoing_sov_campaigns(self):
        """Filters to sovereignty campaigns that have started and are currently being contested in space"""
        cutoff_24h = timezone.now() - timedelta(hours=24)
        return self.sov_campaigns().filter(
            is_concluded=False,
            timer_datetime__lte=timezone.now(),
            timer_datetime__gte=cutoff_24h,
        )

    def structures_only(self):
        return self.filter(is_sov_campaign=False).exclude(
            timer_type__in=["SOV_IHUB", "SOV_TCU"]
        )

    def hostile_only(self):
        """Filters to timers that belong to a known structure or a tracked hostile defender entity"""
        # AA Hostile Intel
        from hostile.models import HostileAlliance, HostileCorporation

        tracked_alliance_ids = list(
            HostileAlliance.objects.filter(is_friendly=False).values_list(
                "alliance_id", flat=True
            )
        )
        tracked_corp_ids = list(
            HostileCorporation.objects.filter(is_friendly=False).values_list(
                "corporation_id", flat=True
            )
        )
        tracked_alliance_names = list(
            HostileAlliance.objects.filter(is_friendly=False).values_list(
                "alliance_name", flat=True
            )
        )
        tracked_corp_names = list(
            HostileCorporation.objects.filter(is_friendly=False).values_list(
                "corporation_name", flat=True
            )
        )

        q_filter = models.Q(structure__isnull=False) | models.Q(is_sov_campaign=False)
        if (
            tracked_alliance_ids
            or tracked_corp_ids
            or tracked_alliance_names
            or tracked_corp_names
        ):
            match_q = models.Q()
            if tracked_alliance_ids or tracked_corp_ids:
                match_q |= models.Q(
                    defender_id__in=tracked_alliance_ids + tracked_corp_ids
                )
            if tracked_alliance_names or tracked_corp_names:
                match_q |= models.Q(
                    defender_name__in=tracked_alliance_names + tracked_corp_names
                )
            q_filter |= match_q
        return self.filter(q_filter)


class ObservationQuerySet(models.QuerySet):
    def pinned(self):
        return self.filter(is_pinned=True)

    def verified(self):
        return self.filter(is_verified=True)

    def high_threat(self):
        return self.filter(threat_level__in=["HIGH", "EXTREME"])

    def active_recent(self, hours: int = 48):
        cutoff = timezone.now() - timedelta(hours=hours)
        return self.filter(
            models.Q(is_pinned=True)
            | models.Q(time_seen__gte=cutoff)
            | models.Q(created_at__gte=cutoff)
        )


class PilotQuerySet(models.QuerySet):
    def cynos(self):
        return self.filter(is_cyno_alt=True)

    def blops(self):
        return self.filter(is_blops_pilot=True)

    def dreads(self):
        return self.filter(is_dread_pilot=True)

    def faxes(self):
        return self.filter(is_fax_pilot=True)

    def capitals(self):
        return self.filter(
            models.Q(is_capital_pilot=True)
            | models.Q(is_dread_pilot=True)
            | models.Q(is_fax_pilot=True)
            | models.Q(is_super_pilot=True)
            | models.Q(is_titan_pilot=True)
        )

    def supers_and_titans(self):
        return self.filter(
            models.Q(is_super_pilot=True) | models.Q(is_titan_pilot=True)
        )

    def fcs(self):
        return self.filter(is_fc=True)

    def awoxers(self):
        return self.filter(
            models.Q(is_awox=True)
            | models.Q(is_alliance_awox=True)
            | models.Q(is_faction_awox=True)
        )

    def baits(self):
        return self.filter(is_bait=True)

    def gankers(self):
        return self.filter(is_ganker=True)

    def logis(self):
        return self.filter(is_logi_pilot=True)

    def rookies(self):
        return self.filter(is_rookie=True)

    def alts(self):
        return self.filter(is_alt=True)

    def mains(self):
        return self.filter(is_alt=False)

    def verified(self):
        return self.filter(is_verified=True)


class StagingQuerySet(models.QuerySet):
    def verified(self):
        return self.filter(is_verified=True)

    def primary(self):
        return self.filter(is_primary=True)

    def by_alliance(self, alliance):
        return self.filter(alliance=alliance)


class DoctrineQuerySet(models.QuerySet):
    def verified(self):
        return self.filter(is_verified=True)

    def general(self):
        return self.filter(is_general_doctrine=True)

    def by_alliance(self, alliance):
        return self.filter(alliance=alliance)

    def by_role(self, role):
        return self.filter(role_type=role)
