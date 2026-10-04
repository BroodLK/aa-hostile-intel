"""
Forms for intelligence submissions, smart paste ingestion, and entity moderation
"""

# Django
from django import forms
from django.db import models
from django.utils import timezone

# Third Party
from eve_sde.models import ItemType, SolarSystem

# AA Hostile Intel
from hostile.models import (
    DoctrineFit,
    HostileAlliance,
    HostileCorporation,
    HostileDoctrine,
    HostilePilotDossier,
    HostileStagingSystem,
    HostileStructure,
    LocalThreatScan,
    StructureFitting,
    StructureModule,
    StructureTimer,
    SystemObservation,
    SystemTag,
)
from hostile.services.reinforcement_calculator import (
    LocationSecurity,
    PowerState,
    StructureCategory,
    TimerStage,
)


class SolarSystemChoiceField(forms.ModelChoiceField):
    """ModelChoiceField for SolarSystem that renders system names cleanly without system IDs and accepts name strings or IDs"""

    def __init__(self, queryset=None, *args, **kwargs):
        if queryset is None:
            queryset = SolarSystem.objects.all().order_by("name")
        super().__init__(queryset=queryset, *args, **kwargs)

    def label_from_instance(self, obj: SolarSystem) -> str:
        return obj.name

    def to_python(self, value):
        if value in self.empty_values:
            return None
        if isinstance(value, SolarSystem):
            return value
        if isinstance(value, str) and not value.isdigit():
            sys_obj = SolarSystem.objects.filter(name__iexact=value.strip()).first()
            if sys_obj:
                return sys_obj
        return super().to_python(value)


class SmartPasteForm(forms.Form):
    """Smart paste form that accepts any raw EVE clipboard text (Local Chat, D-Scan, Show-Info, EFT, Notes)"""

    intel_text = forms.CharField(
        widget=forms.Textarea(
            attrs={
                "class": "form-control font-monospace",
                "rows": 7,
                "placeholder": "Paste Local chat member list / chat log, D-Scan, Structure Inspect, EFT fit, or sitrep notes here...",
            }
        ),
        label="Primary Intelligence Paste (Local / D-Scan / Fit / Structure)",
        help_text="The smart parser auto-classifies local scans, structure fittings, positions, and threat data.",
    )
    dscan_text = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control font-monospace",
                "rows": 4,
                "placeholder": "Optional: Paste D-Scan here to couple with Local and correlate pilot ship assignments & drop risks...",
            }
        ),
        label="Coupled D-Scan (Optional for Local Threat Pairing)",
        help_text="Provide a D-Scan alongside Local chat to calculate Blops/Capital drop chances and match ships on grid.",
    )
    solar_system = SolarSystemChoiceField(
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
        label="Solar System (Optional Override)",
        help_text="Leave blank to auto-detect from paste text.",
    )


class LocalThreatScanForm(forms.Form):
    """Dedicated form for localthreat-style local intelligence and drop probability analysis"""

    local_text = forms.CharField(
        widget=forms.Textarea(
            attrs={
                "class": "form-control font-monospace",
                "rows": 8,
                "placeholder": "Paste Local chat window (Ctrl+A / Ctrl+C) or timestamped chat log lines here...",
                "id": "id_local_text",
            }
        ),
        label="Local Window / Chat Log Paste",
        help_text="Accepts pilot names, channel member lists, or [ timestamp ] Pilot > chat logs.",
    )
    dscan_text = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control font-monospace",
                "rows": 5,
                "placeholder": "Optional: Paste D-Scan to couple with local and build ship profiles...",
                "id": "id_dscan_text",
            }
        ),
        label="Coupled D-Scan (Optional)",
        help_text="Analyzes ships on grid to calculate Black Ops & Capital drop chances.",
    )
    solar_system = SolarSystemChoiceField(
        required=False,
        widget=forms.Select(attrs={"class": "form-select", "id": "id_solar_system"}),
        label="Solar System (Optional)",
        help_text="System context for staging proximity and observation threat tags.",
    )

    def clean_solar_system(self):
        val = self.cleaned_data.get("solar_system")
        if val:
            return val
        raw_val = self.data.get("solar_system") or self.data.get("solar_system_name")
        if raw_val and isinstance(raw_val, str) and not raw_val.isdigit():
            return SolarSystem.objects.filter(name__iexact=raw_val.strip()).first()
        return val


class SystemObservationForm(forms.ModelForm):
    """Form for reporting gate camps, bubbles, and tactical system observations with integrated Local & D-Scan scanners"""

    solar_system = SolarSystemChoiceField(
        required=True,
        widget=forms.Select(attrs={"class": "form-select", "id": "id_solar_system"}),
    )
    time_seen = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(
            attrs={
                "class": "form-control text-light bg-black border-secondary font-mono",
                "type": "datetime-local",
                "id": "id_time_seen",
            },
            format="%Y-%m-%dT%H:%M",
        ),
        label="Time Seen (UTC)",
        help_text="Time when activity was observed. Leave blank or use 'Now' for current time.",
    )
    new_tag = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control text-light bg-black border-secondary",
                "placeholder": "Type tag name to create (e.g. Titan Undocked, T3C Gang)...",
                "id": "id_new_tag",
            }
        ),
        label="Add New Custom Tag",
    )
    raw_local_text = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control text-light bg-black border-secondary font-mono",
                "rows": 4,
                "placeholder": "Optional: Paste raw Local chat window (Ctrl+A / Ctrl+C) or pilot roster here...",
                "id": "id_raw_local_text",
            }
        ),
        label="Local Chat / Pilot Roster Paste",
        help_text="Paste Local chat to auto-generate threat analysis, calculate drop risks, and update pilot dossiers.",
    )
    raw_dscan_text = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control text-light bg-black border-secondary font-mono",
                "rows": 4,
                "placeholder": "Optional: Paste D-Scan items here (ships on grid, warp disruption bubbles, structures)...",
                "id": "id_raw_dscan_text",
            }
        ),
        label="Directional Scan (D-Scan) Paste",
        help_text="Paste D-Scan to extract ship hulls, bubble warnings, and anchor intelligence.",
    )

    class Meta:
        model = SystemObservation
        fields = [
            "solar_system",
            "threat_level",
            "time_seen",
            "tags",
            "active_hours",
            "observation_text",
        ]
        widgets = {
            "threat_level": forms.Select(
                attrs={"class": "form-select", "id": "id_threat_level"}
            ),
            "tags": forms.SelectMultiple(
                attrs={"class": "form-select", "id": "id_tags"}
            ),
            "active_hours": forms.TextInput(
                attrs={
                    "class": "form-control text-light bg-black border-secondary",
                    "placeholder": "Optional legacy timezone notes (e.g. USTZ / EUTZ)",
                    "id": "id_active_hours",
                }
            ),
            "observation_text": forms.Textarea(
                attrs={
                    "class": "form-control text-light bg-black border-secondary",
                    "rows": 4,
                    "placeholder": "Describe camps, smartbomb setups, warp bubbles, or standing hostiles...",
                    "id": "id_observation_text",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and self.instance.local_scan:
            if not self.initial.get("raw_local_text"):
                self.initial["raw_local_text"] = self.instance.local_scan.raw_local_text
            if not self.initial.get("raw_dscan_text"):
                self.initial["raw_dscan_text"] = self.instance.local_scan.raw_dscan_text

    def clean_solar_system(self):
        val = self.cleaned_data.get("solar_system")
        if val:
            return val
        raw_val = self.data.get("solar_system") or self.data.get("solar_system_name")
        if raw_val and isinstance(raw_val, str) and not raw_val.isdigit():
            return SolarSystem.objects.filter(name__iexact=raw_val.strip()).first()
        return val

    def clean_time_seen(self):
        val = self.cleaned_data.get("time_seen")
        if not val:
            return timezone.now()
        return val

    def save(self, commit=True, user=None):
        instance = super().save(commit=False)
        if not instance.time_seen:
            instance.time_seen = timezone.now()
        if user and not instance.created_by:
            instance.created_by = user

        raw_local = (self.cleaned_data.get("raw_local_text") or "").strip()
        raw_dscan = (self.cleaned_data.get("raw_dscan_text") or "").strip()

        # Process D-Scan text if provided
        dscan_parsed = None
        if raw_dscan:
            try:
                from hostile.parsers.dscan import DScanParser

                dscan_parsed = DScanParser.parse(
                    raw_dscan, default_system=instance.solar_system
                )
                ship_counts = dscan_parsed.get("ship_counts", {})
                structures = dscan_parsed.get("structures", [])
                total_ships = sum(ship_counts.values())

                bubble_count = sum(
                    count
                    for name, count in ship_counts.items()
                    if any(
                        b in name.lower()
                        for b in [
                            "bubble",
                            "disruptor",
                            "warp disrupt",
                            "mobile warp",
                            "sabre",
                            "flycatcher",
                            "eris",
                            "heretic",
                        ]
                    )
                )
                has_bubbles = bubble_count > 0 or any(
                    "bubble" in s["name"].lower() for s in structures
                )

                instance.dscan_summary = {
                    "ship_counts": ship_counts,
                    "total_ships": total_ships,
                    "total_structures": len(structures),
                    "has_bubbles": has_bubbles,
                    "bubble_count": bubble_count,
                }
            except Exception:
                pass

        # Process Local chat paste if provided
        if raw_local:
            try:
                from hostile.models import LocalThreatScan
                from hostile.parsers.local import LocalThreatParser

                parsed_local = LocalThreatParser.parse(
                    raw_local,
                    dscan_text=raw_dscan,
                    default_system=instance.solar_system,
                    fetch_zkill=False,
                )

                scan_user = user or instance.created_by
                if instance.local_scan:
                    scan = instance.local_scan
                    scan.solar_system = (
                        parsed_local.get("solar_system") or instance.solar_system
                    )
                    scan.raw_local_text = raw_local
                    scan.raw_dscan_text = raw_dscan
                    scan.pilot_count = parsed_local.get("pilot_count", 0)
                    scan.hostile_count = parsed_local.get("hostile_count", 0)
                    scan.ignored_count = parsed_local.get("ignored_count", 0)
                    scan.blops_drop_chance = parsed_local.get("blops_drop_chance", 0)
                    scan.cap_drop_chance = parsed_local.get("cap_drop_chance", 0)
                    scan.threat_level = parsed_local.get("threat_level", "LOW")
                    scan.threat_factors = parsed_local.get("threat_factors", [])
                    scan.activity_profile = parsed_local.get("activity_profile", {})
                    scan.pilots_data = parsed_local.get("pilots", [])
                    scan.ship_profile = parsed_local.get("ship_profile", {})
                    scan.save()
                else:
                    scan = LocalThreatScan.objects.create(
                        solar_system=parsed_local.get("solar_system")
                        or instance.solar_system,
                        raw_local_text=raw_local,
                        raw_dscan_text=raw_dscan,
                        pilot_count=parsed_local.get("pilot_count", 0),
                        hostile_count=parsed_local.get("hostile_count", 0),
                        ignored_count=parsed_local.get("ignored_count", 0),
                        blops_drop_chance=parsed_local.get("blops_drop_chance", 0),
                        cap_drop_chance=parsed_local.get("cap_drop_chance", 0),
                        threat_level=parsed_local.get("threat_level", "LOW"),
                        threat_factors=parsed_local.get("threat_factors", []),
                        activity_profile=parsed_local.get("activity_profile", {}),
                        pilots_data=parsed_local.get("pilots", []),
                        ship_profile=parsed_local.get("ship_profile", {}),
                        created_by=scan_user,
                    )
                    instance.local_scan = scan
            except Exception:
                pass

        if commit:
            instance.save()
            self.save_m2m()

            # Auto-apply tags from scan / dscan if relevant
            if instance.dscan_summary:
                if instance.dscan_summary.get("has_bubbles"):
                    tag_bubble, _ = SystemTag.objects.get_or_create(
                        name="Bubbles on Gate", defaults={"color_class": "danger"}
                    )
                    instance.tags.add(tag_bubble)

                if (
                    instance.dscan_summary.get("total_ships", 0) >= 3
                    and instance.threat_level in ["HIGH", "EXTREME"]
                ):
                    tag_camp, _ = SystemTag.objects.get_or_create(
                        name="Gate Camp", defaults={"color_class": "danger"}
                    )
                    instance.tags.add(tag_camp)

            if instance.local_scan:
                if instance.local_scan.blops_drop_chance >= 40:
                    tag_blops, _ = SystemTag.objects.get_or_create(
                        name="Blops Hunter", defaults={"color_class": "danger"}
                    )
                    instance.tags.add(tag_blops)
                if instance.local_scan.cap_drop_chance >= 40:
                    tag_cap, _ = SystemTag.objects.get_or_create(
                        name="Capital Fleet", defaults={"color_class": "danger"}
                    )
                    instance.tags.add(tag_cap)

            new_tags_str = self.cleaned_data.get("new_tag")
            if new_tags_str:
                for raw_name in new_tags_str.split(","):
                    clean_name = raw_name.strip()
                    if clean_name:
                        tag_obj, _ = SystemTag.objects.get_or_create(
                            name=clean_name,
                            defaults={"color_class": "warning"},
                        )
                        instance.tags.add(tag_obj)
        return instance


class StructureTypeChoiceField(forms.ModelChoiceField):
    """ModelChoiceField for structure ItemTypes that filters specifically for deployable citadels, complexes, refineries, FLEX, POCOs, and control towers"""

    def __init__(self, queryset=None, *args, **kwargs):
        if queryset is None:
            # SDE Group IDs:
            # 1657: Citadel
            # 1404: Engineering Complex
            # 1406: Refinery
            # 2016: Upwell Jump Gate
            # 2017: Structure Cynosural Jammer / Cynosural Jammer
            # 2014: Structure Cynosural Beacon / Cynosural Navigation Beacon
            # 511:  Customs Office / Planetary Custom Offices
            # 365:  Control Tower
            # 1003: Infrastructure Hub
            # 322:  Territorial Claim Unit
            # 4807: Moon Drill
            structure_group_ids = [1657, 1404, 1406, 2016, 2017, 2014, 511, 365, 1003, 322, 4807]
            structure_group_names = [
                "Citadel",
                "Engineering Complex",
                "Refinery",
                "Upwell Jump Gate",
                "Structure Cynosural Jammer",
                "Cynosural Jammer",
                "Structure Cynosural Beacon",
                "Cynosural Navigation Beacon",
                "Customs Office",
                "Planetary Custom Offices",
                "Control Tower",
                "Infrastructure Hub",
                "Territorial Claim Unit",
                "Moon Drill",
            ]
            known_structures = [
                # Citadels & Faction Citadels
                "Astrahus",
                "Fortizar",
                "Keepstar",
                "Upwell Palatine Keepstar",
                "Palatine Keepstar",
                "'Draccous' Fortizar",
                "'Horizon' Fortizar",
                "'Marginis' Fortizar",
                "'Moreau' Fortizar",
                "'Prometheus' Fortizar",
                # Engineering Complexes
                "Raitaru",
                "Azbel",
                "Sotiyo",
                # Refineries
                "Athanor",
                "Tatara",
                # FLEX & Upwell Deployables
                "Ansiblex Jump Gate",
                "Pharolux Cyno Beacon",
                "Tenebrex Cyno Jammer",
                "Metenox Moon Drill",
                # Orbitals & Sov
                "Customs Office",
                "Player Owned Customs Office",
                "Interbus Customs Office",
                "Infrastructure Hub",
                "Territorial Claim Unit",
            ]

            # Base query: restrict to valid structure group IDs, group names, known structure names, or Control Tower names
            qs = ItemType.objects.filter(
                models.Q(group_id__in=structure_group_ids)
                | models.Q(group__name__in=structure_group_names)
                | models.Q(name__in=known_structures)
                | (models.Q(name__endswith="Control Tower") & ~models.Q(name__icontains="Blueprint"))
            )

            # Exclude wrecks, blueprints, plates/modules, assembly arrays, test items, tutorial AIR items, and NPC diamond structures
            qs = qs.exclude(
                models.Q(name__icontains="Wreck")
                | models.Q(name__icontains="Blueprint")
                | models.Q(name__icontains="Steel Plates")
                | models.Q(name__icontains="Armor Plate")
                | models.Q(name__icontains="Assembly Array")
                | models.Q(name__icontains="Silo")
                | models.Q(name__icontains="Battery")
                | models.Q(name__icontains="Reactor")
                | models.Q(name__icontains="Laboratory")
                | models.Q(name__icontains="Hangar Array")
                | models.Q(name__icontains="Shield Hardener")
                | models.Q(name__icontains="Non-Interactable")
                | models.Q(name__icontains="(copy)")
                | models.Q(name__startswith="[AIR]")
                | models.Q(name__startswith="♦")
                | models.Q(name__iexact="Academy")
                | models.Q(name__iexact="Amarr Citadel")
                | models.Q(
                    group__name__in=[
                        "Structure Service Module",
                        "Structure Rig",
                        "Structure Module",
                        "Assembly Array",
                        "Starbase Silo",
                        "Starbase Array",
                        "Starbase Battery",
                        "Armor Reinforcement",
                    ]
                )
                | models.Q(group__category_id__in=[7, 8, 9, 16, 17, 18, 20, 22, 24])
            )

            queryset = qs.distinct().order_by("name")

        super().__init__(queryset=queryset, *args, **kwargs)

    def label_from_instance(self, obj: ItemType) -> str:
        return obj.name

    def to_python(self, value):
        if value in self.empty_values:
            return None
        if isinstance(value, ItemType):
            return value
        if isinstance(value, str) and not value.isdigit():
            obj = ItemType.objects.filter(name__iexact=value.strip()).first()
            if obj:
                return obj
        return super().to_python(value)


class HostileStructureForm(forms.ModelForm):
    """Form for manual entry and editing of hostile structures"""

    structure_type = StructureTypeChoiceField(
        required=False,
        widget=forms.Select(attrs={"class": "form-select", "id": "id_structure_type"}),
    )
    solar_system = SolarSystemChoiceField(
        required=True,
        widget=forms.Select(attrs={"class": "form-select", "id": "id_solar_system"}),
    )
    untracked_alliance = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Or enter new/untracked alliance name/ticker/ID",
                "id": "id_untracked_alliance",
            }
        ),
        label="Untracked Alliance",
        help_text="If the alliance is not in the list, enter it here to automatically resolve and track it.",
    )
    untracked_corporation = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Or enter new/untracked corp name/ticker/ID",
                "id": "id_untracked_corporation",
            }
        ),
        label="Untracked Corporation",
        help_text="If the corporation is not in the list, enter it here to automatically resolve and track it.",
    )
    timer_datetime = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(
            attrs={
                "class": "form-control",
                "type": "datetime-local",
                "id": "id_timer_datetime",
            }
        ),
        label="Structure Timer Target UTC (Optional)",
        help_text="If status is Anchoring or Reinforced, specify the exit datetime to create a timer automatically.",
    )
    timer_stage = forms.ChoiceField(
        choices=(
            ("ANCHORING", "Anchoring Timer"),
            ("ARMOR", "Armor Reinforce Timer"),
            ("HULL", "Hull Reinforce Timer"),
            ("REINFORCED", "Reinforced (General)"),
        ),
        required=False,
        initial="ANCHORING",
        widget=forms.Select(attrs={"class": "form-select", "id": "id_timer_stage"}),
        label="Timer Type",
    )
    core_status = forms.ChoiceField(
        choices=HostileStructure.CORE_CHOICES,
        required=False,
        initial="UNKNOWN",
        widget=forms.Select(attrs={"class": "form-select", "id": "id_core_status"}),
    )
    state = forms.ChoiceField(
        choices=HostileStructure.STATE_CHOICES,
        required=False,
        initial="ONLINE",
        widget=forms.Select(attrs={"class": "form-select", "id": "id_state"}),
    )

    class Meta:
        model = HostileStructure
        fields = [
            "name",
            "structure_type",
            "solar_system",
            "owner_ticker",
            "alliance",
            "corporation",
            "core_status",
            "state",
            "vulnerability_window",
            "vulnerability_hour",
            "location_details",
            "notes",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "id": "id_structure_name"}),
            "owner_ticker": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. CONDI", "id": "id_owner_ticker"}),
            "alliance": forms.Select(attrs={"class": "form-select", "id": "id_alliance"}),
            "corporation": forms.Select(attrs={"class": "form-select", "id": "id_corporation"}),
            "vulnerability_window": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "e.g. 15:00 - 21:00 UTC (Auto-filled +-3h)", "id": "id_vulnerability_window"}
            ),
            "vulnerability_hour": forms.NumberInput(
                attrs={"class": "form-control", "placeholder": "UTC Hour (0-23)", "min": 0, "max": 23, "id": "id_vulnerability_hour"}
            ),
            "location_details": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Planet / Moon / Gate", "id": "id_location_details"}
            ),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3, "id": "id_notes"}),
        }

    def clean(self):
        cleaned_data = super().clean()
        from hostile.services.entity_resolver import EntityResolver

        if not cleaned_data.get("core_status"):
            cleaned_data["core_status"] = "UNKNOWN"
        if not cleaned_data.get("state"):
            cleaned_data["state"] = "ONLINE"

        untracked_alliance = cleaned_data.get("untracked_alliance")
        if untracked_alliance and not cleaned_data.get("alliance"):
            alliance_obj = EntityResolver.get_or_create_alliance(untracked_alliance)
            if alliance_obj:
                cleaned_data["alliance"] = alliance_obj

        untracked_corp = cleaned_data.get("untracked_corporation")
        if untracked_corp and not cleaned_data.get("corporation"):
            corp_obj = EntityResolver.get_or_create_corporation(
                untracked_corp, alliance=cleaned_data.get("alliance")
            )
            if corp_obj:
                cleaned_data["corporation"] = corp_obj

        # Pre-fill owner_ticker from alliance or corporation if empty
        owner_ticker = cleaned_data.get("owner_ticker")
        if not owner_ticker:
            if cleaned_data.get("alliance") and cleaned_data["alliance"].ticker:
                cleaned_data["owner_ticker"] = cleaned_data["alliance"].ticker
            elif cleaned_data.get("corporation") and cleaned_data["corporation"].ticker:
                cleaned_data["owner_ticker"] = cleaned_data["corporation"].ticker

        # Vulnerability hour set with +- 3 hours auto filled in
        vulnerability_hour = cleaned_data.get("vulnerability_hour")
        if vulnerability_hour is not None:
            start_hour = (vulnerability_hour - 3) % 24
            end_hour = (vulnerability_hour + 3) % 24
            computed_window = f"{start_hour:02d}:00 - {end_hour:02d}:00 UTC"
            if not cleaned_data.get("vulnerability_window") or cleaned_data.get("vulnerability_window") == "":
                cleaned_data["vulnerability_window"] = computed_window

        # Anchoring status: core status will always be not in (UNFITTED)
        state = cleaned_data.get("state")
        if state == "ANCHORING":
            cleaned_data["core_status"] = "UNFITTED"

        # Validate timer stage according to structure category if timer is being configured
        structure_type = cleaned_data.get("structure_type")
        timer_datetime = cleaned_data.get("timer_datetime")
        timer_stage = cleaned_data.get("timer_stage")
        if timer_datetime and structure_type and timer_stage:
            from hostile.services.reinforcement_calculator import (
                ReinforcementCalculator,
                StructureCategory,
            )

            size = ReinforcementCalculator.infer_structure_size(structure_type)
            if size in (StructureCategory.MEDIUM, StructureCategory.POCO) and timer_stage == "HULL":
                self.add_error(
                    "timer_stage",
                    "Medium structures and POCOs do not have a hull reinforcement stage. Select 'Armor Reinforce Timer' or 'Anchoring Timer'.",
                )
            elif size == StructureCategory.FLEX and timer_stage == "ARMOR":
                self.add_error(
                    "timer_stage",
                    "FLEX structures do not have an armor reinforcement stage. Select 'Hull Reinforce Timer' or 'Anchoring Timer'.",
                )

        return cleaned_data


class HostilePilotDossierForm(forms.ModelForm):
    """Form for creating or updating hostile pilot dossiers and tags with Name OR ID lookup"""

    character_id = forms.IntegerField(
        required=False,
        widget=forms.NumberInput(
            attrs={
                "class": "form-control",
                "placeholder": "Character ID (e.g. 90000001)",
                "id": "id_character_id",
            }
        ),
        label="Character ID",
    )
    character_name = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Character Name (e.g. Pilot Name)",
                "id": "id_character_name",
            }
        ),
        label="Character Name",
    )
    corporation_name = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "In-Game Corporation (Auto-filled)",
                "id": "id_corporation_name",
            }
        ),
        label="In-Game Corporation",
    )
    alliance_name = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "In-Game Alliance (Auto-filled)",
                "id": "id_alliance_name",
            }
        ),
        label="In-Game Alliance",
    )
    associated_alliance_name = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Associated Hostile Alliance (For Out-of-Corp Alts)",
                "id": "id_associated_alliance_name",
            }
        ),
        label="Associated Alliance (Out-of-Corp Alt)",
        help_text="Specifically state the hostile alliance this out-of-corp pilot / alt is associated with (name only).",
    )
    last_seen_system = SolarSystemChoiceField(
        required=False,
        widget=forms.Select(attrs={"class": "form-select", "id": "id_last_seen_system"}),
    )

    main_character = forms.ModelChoiceField(
        queryset=HostilePilotDossier.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select", "id": "id_main_character"}),
        label="Main Character",
        help_text="If this pilot is an alt, designate their primary/main combat character.",
    )

    cyno_count = forms.IntegerField(
        required=False,
        min_value=0,
        initial=0,
        widget=forms.NumberInput(
            attrs={"class": "form-control form-control-sm", "min": 0}
        ),
    )
    blops_count = forms.IntegerField(
        required=False,
        min_value=0,
        initial=0,
        widget=forms.NumberInput(
            attrs={"class": "form-control form-control-sm", "min": 0}
        ),
    )
    capital_count = forms.IntegerField(
        required=False,
        min_value=0,
        initial=0,
        widget=forms.NumberInput(
            attrs={"class": "form-control form-control-sm", "min": 0}
        ),
    )
    super_count = forms.IntegerField(
        required=False,
        min_value=0,
        initial=0,
        widget=forms.NumberInput(
            attrs={"class": "form-control form-control-sm", "min": 0}
        ),
    )
    titan_count = forms.IntegerField(
        required=False,
        min_value=0,
        initial=0,
        widget=forms.NumberInput(
            attrs={"class": "form-control form-control-sm", "min": 0}
        ),
    )
    fc_score = forms.IntegerField(
        required=False,
        min_value=0,
        initial=0,
        widget=forms.NumberInput(
            attrs={"class": "form-control form-control-sm", "min": 0}
        ),
    )
    awox_count = forms.IntegerField(
        required=False,
        min_value=0,
        initial=0,
        widget=forms.NumberInput(
            attrs={"class": "form-control form-control-sm", "min": 0}
        ),
    )
    alliance_awox_count = forms.IntegerField(
        required=False,
        min_value=0,
        initial=0,
        widget=forms.NumberInput(
            attrs={"class": "form-control form-control-sm", "min": 0}
        ),
    )
    faction_awox_count = forms.IntegerField(
        required=False,
        min_value=0,
        initial=0,
        widget=forms.NumberInput(
            attrs={"class": "form-control form-control-sm", "min": 0}
        ),
    )
    bait_count = forms.IntegerField(
        required=False,
        min_value=0,
        initial=0,
        widget=forms.NumberInput(
            attrs={"class": "form-control form-control-sm", "min": 0}
        ),
    )
    ganker_count = forms.IntegerField(
        required=False,
        min_value=0,
        initial=0,
        widget=forms.NumberInput(
            attrs={"class": "form-control form-control-sm", "min": 0}
        ),
    )
    logi_count = forms.IntegerField(
        required=False,
        min_value=0,
        initial=0,
        widget=forms.NumberInput(
            attrs={"class": "form-control form-control-sm", "min": 0}
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        qs = HostilePilotDossier.objects.all().order_by("character_name")
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        self.fields["main_character"].queryset = qs

    class Meta:
        model = HostilePilotDossier
        fields = [
            "character_id",
            "character_name",
            "corporation_name",
            "alliance_name",
            "associated_alliance_name",
            "main_character",
            "is_alt",
            "alt_inference_reason",
            "alt_confidence",
            "is_out_of_corp",
            "is_cyno_alt",
            "cyno_count",
            "is_blops_pilot",
            "blops_count",
            "is_dread_pilot",
            "is_fax_pilot",
            "is_capital_pilot",
            "capital_count",
            "is_super_pilot",
            "super_count",
            "is_titan_pilot",
            "titan_count",
            "is_fc",
            "fc_level",
            "fc_score",
            "is_awox",
            "awox_count",
            "is_alliance_awox",
            "alliance_awox_count",
            "is_faction_awox",
            "faction_awox_count",
            "is_bait",
            "bait_level",
            "bait_count",
            "is_ganker",
            "ganker_count",
            "is_logi_pilot",
            "logi_count",
            "is_rookie",
            "last_seen_system",
            "notes",
        ]
        widgets = {
            "is_alt": forms.CheckboxInput(
                attrs={"class": "form-check-input", "id": "id_is_alt"}
            ),
            "alt_inference_reason": forms.TextInput(
                attrs={
                    "class": "form-control form-control-sm",
                    "placeholder": "Reason for alt detection",
                    "id": "id_alt_inference_reason",
                }
            ),
            "alt_confidence": forms.Select(
                attrs={"class": "form-select form-select-sm", "id": "id_alt_confidence"},
                choices=[
                    ("", "None"),
                    ("HIGH", "High"),
                    ("MEDIUM", "Medium"),
                    ("LOW", "Low"),
                ],
            ),
            "is_out_of_corp": forms.CheckboxInput(
                attrs={"class": "form-check-input", "id": "id_is_out_of_corp"}
            ),
            "is_cyno_alt": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "cyno_count": forms.NumberInput(
                attrs={"class": "form-control form-control-sm", "min": 0}
            ),
            "is_blops_pilot": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "blops_count": forms.NumberInput(
                attrs={"class": "form-control form-control-sm", "min": 0}
            ),
            "is_dread_pilot": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "is_fax_pilot": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "is_capital_pilot": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "capital_count": forms.NumberInput(
                attrs={"class": "form-control form-control-sm", "min": 0}
            ),
            "is_super_pilot": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "super_count": forms.NumberInput(
                attrs={"class": "form-control form-control-sm", "min": 0}
            ),
            "is_titan_pilot": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "titan_count": forms.NumberInput(
                attrs={"class": "form-control form-control-sm", "min": 0}
            ),
            "is_fc": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "fc_level": forms.Select(
                attrs={"class": "form-select form-select-sm"},
                choices=[
                    ("", "None / Standard"),
                    ("CANDIDATE", "FC Candidate"),
                    ("LOW", "Low (35+)"),
                    ("MEDIUM", "Medium (60+)"),
                    ("HIGH", "High (100+)"),
                ],
            ),
            "fc_score": forms.NumberInput(
                attrs={"class": "form-control form-control-sm", "min": 0}
            ),
            "is_awox": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "awox_count": forms.NumberInput(
                attrs={"class": "form-control form-control-sm", "min": 0}
            ),
            "is_alliance_awox": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "alliance_awox_count": forms.NumberInput(
                attrs={"class": "form-control form-control-sm", "min": 0}
            ),
            "is_faction_awox": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "faction_awox_count": forms.NumberInput(
                attrs={"class": "form-control form-control-sm", "min": 0}
            ),
            "is_bait": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "bait_level": forms.Select(
                attrs={"class": "form-select form-select-sm"},
                choices=[
                    ("", "None / Standard"),
                    ("LOW", "Low (4-6)"),
                    ("MEDIUM", "Medium (7-11)"),
                    ("HIGH", "High (12+)"),
                ],
            ),
            "bait_count": forms.NumberInput(
                attrs={"class": "form-control form-control-sm", "min": 0}
            ),
            "is_ganker": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "ganker_count": forms.NumberInput(
                attrs={"class": "form-control form-control-sm", "min": 0}
            ),
            "is_logi_pilot": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "logi_count": forms.NumberInput(
                attrs={"class": "form-control form-control-sm", "min": 0}
            ),
            "is_rookie": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def clean(self):
        cleaned_data = super().clean()
        character_id = cleaned_data.get("character_id")
        character_name = cleaned_data.get("character_name")

        if not character_id and not character_name:
            raise forms.ValidationError("Please provide a Character Name OR Character ID.")

        from hostile.services.entity_resolver import EntityResolver

        resolved = None
        if not character_id and character_name:
            resolved = EntityResolver.resolve_character(character_name)
            if resolved:
                cleaned_data["character_id"] = resolved["character_id"]
                if not cleaned_data.get("character_name"):
                    cleaned_data["character_name"] = resolved["character_name"]
            else:
                self.add_error(
                    "character_name",
                    f"Could not resolve Character ID for '{character_name}'. Please verify the name or provide the Character ID directly.",
                )
        elif character_id and not character_name:
            resolved = EntityResolver.resolve_character(character_id)
            if resolved:
                cleaned_data["character_name"] = resolved["character_name"]
            else:
                cleaned_data["character_name"] = f"Character {character_id}"

        # Auto-fill corporation and alliance details if resolved and not provided
        if resolved:
            if not cleaned_data.get("corporation_name") and resolved.get("corporation_name"):
                cleaned_data["corporation_name"] = resolved["corporation_name"]
            if not cleaned_data.get("alliance_name") and resolved.get("alliance_name"):
                cleaned_data["alliance_name"] = resolved["alliance_name"]

        if cleaned_data.get("associated_alliance_name"):
            cleaned_data["is_out_of_corp"] = True

        for cnt_field in [
            "cyno_count",
            "blops_count",
            "capital_count",
            "super_count",
            "titan_count",
            "fc_score",
            "awox_count",
            "alliance_awox_count",
            "faction_awox_count",
            "bait_count",
            "ganker_count",
            "logi_count",
        ]:
            if cleaned_data.get(cnt_field) is None:
                cleaned_data[cnt_field] = 0

        return cleaned_data


class StructureTimerForm(forms.ModelForm):
    """Form for adding manual structure vulnerability / anchor timers or registering new structures with timers"""

    structure = forms.ModelChoiceField(
        queryset=HostileStructure.objects.select_related(
            "solar_system", "alliance", "corporation", "structure_type"
        ).order_by("name"),
        required=False,
        empty_label="--- Add New / Unregistered Structure ---",
        widget=forms.Select(attrs={"class": "form-select", "id": "id_timer_structure"}),
        label="Known Structure",
    )
    name = forms.CharField(
        max_length=254,
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Structure Name (e.g. 1DQ1-A - Keepstar Prime)",
                "id": "id_structure_name",
            }
        ),
        label="Structure Name",
    )
    structure_type = StructureTypeChoiceField(
        required=False,
        widget=forms.Select(attrs={"class": "form-select", "id": "id_structure_type"}),
        label="Structure Type",
    )
    solar_system = SolarSystemChoiceField(
        required=False,
        widget=forms.Select(attrs={"class": "form-select", "id": "id_solar_system"}),
        label="Solar System",
    )
    owner_ticker = forms.CharField(
        max_length=10,
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "e.g. CONDI",
                "id": "id_owner_ticker",
            }
        ),
        label="Owner Ticker",
    )
    alliance = forms.ModelChoiceField(
        queryset=HostileAlliance.objects.all().order_by("alliance_name"),
        required=False,
        widget=forms.Select(attrs={"class": "form-select", "id": "id_alliance"}),
        label="Alliance",
    )
    corporation = forms.ModelChoiceField(
        queryset=HostileCorporation.objects.all().order_by("corporation_name"),
        required=False,
        widget=forms.Select(attrs={"class": "form-select", "id": "id_corporation"}),
        label="Corporation",
    )
    untracked_alliance = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Or enter new/untracked alliance name/ticker/ID",
                "id": "id_untracked_alliance",
            }
        ),
        label="Untracked Alliance",
    )
    untracked_corporation = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Or enter new/untracked corp name/ticker/ID",
                "id": "id_untracked_corporation",
            }
        ),
        label="Untracked Corporation",
    )
    state = forms.ChoiceField(
        choices=HostileStructure.STATE_CHOICES,
        required=False,
        initial="ONLINE",
        widget=forms.Select(attrs={"class": "form-select", "id": "id_state"}),
        label="State / Status",
    )
    core_status = forms.ChoiceField(
        choices=HostileStructure.CORE_CHOICES,
        required=False,
        initial="UNKNOWN",
        widget=forms.Select(attrs={"class": "form-select", "id": "id_core_status"}),
        label="Quantum Core Status",
    )
    vulnerability_hour = forms.IntegerField(
        required=False,
        min_value=0,
        max_value=23,
        widget=forms.NumberInput(
            attrs={
                "class": "form-control",
                "placeholder": "UTC Hour (0-23)",
                "min": 0,
                "max": 23,
                "id": "id_vulnerability_hour",
            }
        ),
        label="Vulnerability Hour",
    )
    vulnerability_window = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "e.g. 15:00 - 21:00 UTC",
                "id": "id_vulnerability_window",
            }
        ),
        label="Vulnerability Window",
    )
    location_details = forms.CharField(
        max_length=254,
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Planet / Moon / Gate",
                "id": "id_location_details",
            }
        ),
        label="Location Details",
    )
    timer_type = forms.ChoiceField(
        choices=StructureTimer.TIMER_TYPES,
        initial="ANCHORING",
        widget=forms.Select(attrs={"class": "form-select", "id": "id_timer_type"}),
        label="Timer Type",
    )
    timer_datetime = forms.DateTimeField(
        required=True,
        widget=forms.DateTimeInput(
            attrs={"class": "form-control", "type": "datetime-local", "id": "id_timer_datetime"}
        ),
        label="Timer Expiry Datetime (UTC)",
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 2,
                "id": "id_notes",
                "placeholder": "Notes or fleet intel attached to this timer...",
            }
        ),
        label="Notes",
    )

    class Meta:
        model = StructureTimer
        fields = [
            "structure",
            "solar_system",
            "timer_type",
            "timer_datetime",
            "notes",
        ]

    def clean(self):
        cleaned_data = super().clean()
        structure = cleaned_data.get("structure")
        name = (cleaned_data.get("name") or "").strip()
        solar_system = cleaned_data.get("solar_system")

        if structure:
            if not solar_system:
                cleaned_data["solar_system"] = structure.solar_system
        elif name:
            if not solar_system:
                self.add_error("solar_system", "Solar System is required when adding a new structure.")

            from hostile.services.entity_resolver import EntityResolver

            untracked_alliance = cleaned_data.get("untracked_alliance")
            if untracked_alliance and not cleaned_data.get("alliance"):
                alliance_obj = EntityResolver.get_or_create_alliance(untracked_alliance)
                if alliance_obj:
                    cleaned_data["alliance"] = alliance_obj

            untracked_corporation = cleaned_data.get("untracked_corporation")
            if untracked_corporation and not cleaned_data.get("corporation"):
                corp_obj = EntityResolver.get_or_create_corporation(untracked_corporation)
                if corp_obj:
                    cleaned_data["corporation"] = corp_obj

            if not cleaned_data.get("core_status"):
                cleaned_data["core_status"] = "UNKNOWN"
            if not cleaned_data.get("state"):
                cleaned_data["state"] = "ONLINE"
            if cleaned_data.get("state") == "ANCHORING":
                cleaned_data["core_status"] = "UNFITTED"

            vuln_hour = cleaned_data.get("vulnerability_hour")
            if vuln_hour is not None and not cleaned_data.get("vulnerability_window"):
                start_h = (vuln_hour - 3) % 24
                end_h = (vuln_hour + 3) % 24
                cleaned_data["vulnerability_window"] = f"{start_h:02d}:00 - {end_h:02d}:00 UTC"
        else:
            if not solar_system:
                self.add_error("solar_system", "Solar System is required.")

        return cleaned_data


class HostileAllianceForm(forms.ModelForm):
    """Form for manual entry and editing of hostile alliance intelligence, coalitions, and formup estimations with Name OR ID lookup"""

    alliance_id = forms.IntegerField(
        required=False,
        widget=forms.NumberInput(
            attrs={
                "class": "form-control",
                "placeholder": "Alliance ID (e.g. 99005338)",
                "id": "id_alliance_id",
            }
        ),
        label="Alliance ID",
    )
    alliance_name = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Alliance Name (e.g. Pandemic Horde)",
                "id": "id_alliance_name",
            }
        ),
        label="Alliance Name",
    )
    coalition = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "e.g. Imperium, PanFam, WinterCo, FI.RE",
                "id": "id_coalition",
            }
        ),
        label="Coalition Affiliation",
    )
    is_friendly = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input", "id": "id_is_friendly"}),
        label="Is Friendly / Ignored Alliance",
        help_text="Flag as friendly entity to be ignored across hostile scans and timer boards.",
    )
    is_coalition_leader = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input", "id": "id_is_coalition_leader"}),
        label="Is Coalition Leader / Exec Alliance",
    )
    is_alt_alliance = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input", "id": "id_is_alt_alliance"}),
        label="Is Alt / Holding Alliance",
    )
    main_alliance = forms.ModelChoiceField(
        queryset=HostileAlliance.objects.all().order_by("alliance_name"),
        required=False,
        widget=forms.Select(attrs={"class": "form-select", "id": "id_main_alliance"}),
        label="Main / Parent Alliance (if alt entity)",
    )
    sov_capital = SolarSystemChoiceField(
        required=False,
        widget=forms.Select(attrs={"class": "form-select", "id": "id_sov_capital"}),
        label="Sovereignty Capital",
        help_text="Designated sovereignty capital system (automatically synced to alliance staging).",
    )
    override_timezone = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input", "id": "id_override_timezone"}),
        label="Override Auto-detected Timezone",
        help_text="Check to lock in a manual timezone instead of auto-detecting from zKillboard kills and structure timers.",
    )
    primary_timezone = forms.ChoiceField(
        choices=HostileAlliance.TIMEZONE_CHOICES,
        required=False,
        initial="UNKNOWN",
        widget=forms.Select(attrs={"class": "form-select", "id": "id_primary_timezone"}),
        label="Operational Timezone (Manual Override)",
    )
    max_subcap_form = forms.IntegerField(
        required=False,
        initial=0,
        widget=forms.NumberInput(
            attrs={"class": "form-control", "placeholder": "e.g. 150"}
        ),
    )
    max_cap_form = forms.IntegerField(
        required=False,
        initial=0,
        widget=forms.NumberInput(
            attrs={"class": "form-control", "placeholder": "e.g. 40"}
        ),
    )
    max_super_form = forms.IntegerField(
        required=False,
        initial=0,
        widget=forms.NumberInput(
            attrs={"class": "form-control", "placeholder": "e.g. 15"}
        ),
    )

    class Meta:
        model = HostileAlliance
        fields = [
            "alliance_id",
            "alliance_name",
            "ticker",
            "coalition",
            "is_friendly",
            "is_coalition_leader",
            "is_alt_alliance",
            "main_alliance",
            "sov_capital",
            "override_timezone",
            "primary_timezone",
            "max_subcap_form",
            "max_cap_form",
            "max_super_form",
        ]
        widgets = {
            "ticker": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "e.g. REKTD", "id": "id_ticker"}
            ),
        }

    def clean(self):
        cleaned_data = super().clean()
        alliance_id = cleaned_data.get("alliance_id")
        alliance_name = cleaned_data.get("alliance_name")

        if not alliance_id and not alliance_name:
            raise forms.ValidationError("Please provide an Alliance Name OR Alliance ID.")

        from hostile.services.entity_resolver import EntityResolver

        if not alliance_id and alliance_name:
            resolved = EntityResolver.resolve_alliance(alliance_name)
            if resolved:
                cleaned_data["alliance_id"] = resolved["alliance_id"]
                if not cleaned_data.get("alliance_name"):
                    cleaned_data["alliance_name"] = resolved["alliance_name"]
                if not cleaned_data.get("ticker") and resolved.get("ticker"):
                    cleaned_data["ticker"] = resolved["ticker"]
            else:
                self.add_error(
                    "alliance_name",
                    f"Could not resolve Alliance ID for '{alliance_name}'. Please verify the name or provide the Alliance ID directly.",
                )
        elif alliance_id and not alliance_name:
            resolved = EntityResolver.resolve_alliance(alliance_id)
            if resolved:
                cleaned_data["alliance_name"] = resolved["alliance_name"]
                if not cleaned_data.get("ticker") and resolved.get("ticker"):
                    cleaned_data["ticker"] = resolved["ticker"]
            else:
                cleaned_data["alliance_name"] = f"Alliance {alliance_id}"

        if not cleaned_data.get("primary_timezone"):
            cleaned_data["primary_timezone"] = "UNKNOWN"
        if cleaned_data.get("max_subcap_form") is None:
            cleaned_data["max_subcap_form"] = 0
        if cleaned_data.get("max_cap_form") is None:
            cleaned_data["max_cap_form"] = 0
        if cleaned_data.get("max_super_form") is None:
            cleaned_data["max_super_form"] = 0

        return cleaned_data


class CoalitionAlliancesForm(forms.Form):
    """Form for batch assigning alliances to a coalition and selecting the coalition leader"""

    coalition_name = forms.CharField(
        max_length=128,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "e.g. Imperium, PanFam, WinterCo, FI.RE",
                "id": "id_manage_coalition_name",
            }
        ),
        label="Coalition Name",
    )
    alliances = forms.ModelMultipleChoiceField(
        queryset=HostileAlliance.objects.all().order_by("alliance_name"),
        widget=forms.SelectMultiple(
            attrs={
                "class": "form-select",
                "id": "id_manage_coalition_alliances",
                "size": "6",
            }
        ),
        label="Member Alliances",
        required=True,
    )
    leader_alliance = forms.ModelChoiceField(
        queryset=HostileAlliance.objects.all().order_by("alliance_name"),
        required=False,
        widget=forms.Select(
            attrs={
                "class": "form-select",
                "id": "id_manage_coalition_leader",
            }
        ),
        label="Coalition Leader / Exec Alliance",
        help_text="Designate one member alliance as the coalition leader",
    )

    def clean(self):
        cleaned_data = super().clean()
        leader = cleaned_data.get("leader_alliance")
        alliances = cleaned_data.get("alliances")
        if leader and alliances and leader not in alliances:
            # If a leader alliance was selected that was not in the multi-select, add it
            cleaned_data["alliances"] = HostileAlliance.objects.filter(
                id__in=[a.id for a in alliances] + [leader.id]
            )
        return cleaned_data


class HostileCorporationForm(forms.ModelForm):
    """Form for adding or editing hostile corporations with coalition, alliance, and alt corp affiliations"""

    corporation_id = forms.IntegerField(
        required=False,
        widget=forms.NumberInput(
            attrs={
                "class": "form-control font-monospace",
                "placeholder": "Corporation ID (e.g. 98000001)",
                "id": "id_corporation_id",
            }
        ),
        label="Corporation ID",
    )
    corporation_name = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Corporation Name",
                "id": "id_corporation_name",
            }
        ),
        label="Corporation Name",
    )
    ticker = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "e.g. D3RP", "id": "id_corp_ticker"}
        ),
        label="Ticker",
    )
    alliance = forms.ModelChoiceField(
        queryset=HostileAlliance.objects.all().order_by("alliance_name"),
        required=False,
        widget=forms.Select(attrs={"class": "form-select", "id": "id_corp_alliance"}),
        label="Member Alliance",
    )
    coalition = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "e.g. Imperium, PanFam, WinterCo",
                "id": "id_corp_coalition",
            }
        ),
        label="Coalition Affiliation",
    )
    is_friendly = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input", "id": "id_is_friendly"}),
        label="Is Friendly / Ignored Corporation",
        help_text="Flag as friendly entity to be ignored across hostile scans and intel tracking.",
    )
    is_alt_corp = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input", "id": "id_is_alt_corp"}),
        label="Is Alt / Holding / Cyno Corp",
    )
    main_alliance = forms.ModelChoiceField(
        queryset=HostileAlliance.objects.all().order_by("alliance_name"),
        required=False,
        widget=forms.Select(attrs={"class": "form-select", "id": "id_corp_main_alliance"}),
        label="Main / Parent Alliance (if alt corp)",
    )
    main_corporation = forms.ModelChoiceField(
        queryset=HostileCorporation.objects.all().order_by("corporation_name"),
        required=False,
        widget=forms.Select(attrs={"class": "form-select", "id": "id_main_corporation"}),
        label="Main / Parent Corporation (if alt corp)",
    )
    max_subcap_form = forms.IntegerField(
        required=False,
        initial=0,
        widget=forms.NumberInput(
            attrs={"class": "form-control", "placeholder": "e.g. 50"}
        ),
    )
    max_cap_form = forms.IntegerField(
        required=False,
        initial=0,
        widget=forms.NumberInput(
            attrs={"class": "form-control", "placeholder": "e.g. 10"}
        ),
    )
    max_super_form = forms.IntegerField(
        required=False,
        initial=0,
        widget=forms.NumberInput(
            attrs={"class": "form-control", "placeholder": "e.g. 2"}
        ),
    )

    class Meta:
        model = HostileCorporation
        fields = [
            "corporation_id",
            "corporation_name",
            "ticker",
            "alliance",
            "coalition",
            "is_friendly",
            "is_alt_corp",
            "main_alliance",
            "main_corporation",
            "max_subcap_form",
            "max_cap_form",
            "max_super_form",
        ]

    def clean(self):
        cleaned_data = super().clean()
        corp_id = cleaned_data.get("corporation_id")
        corp_name = cleaned_data.get("corporation_name")

        if not corp_id and not corp_name:
            raise forms.ValidationError("Please provide a Corporation Name OR Corporation ID.")

        from hostile.services.entity_resolver import EntityResolver

        if not corp_id and corp_name:
            resolved = EntityResolver.resolve_corporation(corp_name)
            if resolved:
                cleaned_data["corporation_id"] = resolved["corporation_id"]
                if not cleaned_data.get("corporation_name"):
                    cleaned_data["corporation_name"] = resolved["corporation_name"]
                if not cleaned_data.get("ticker") and resolved.get("ticker"):
                    cleaned_data["ticker"] = resolved["ticker"]
            else:
                self.add_error(
                    "corporation_name",
                    f"Could not resolve Corporation ID for '{corp_name}'. Please verify the name or provide the ID directly.",
                )
        elif corp_id and not corp_name:
            resolved = EntityResolver.resolve_corporation(corp_id)
            if resolved:
                cleaned_data["corporation_name"] = resolved["corporation_name"]
                if not cleaned_data.get("ticker") and resolved.get("ticker"):
                    cleaned_data["ticker"] = resolved["ticker"]
            else:
                cleaned_data["corporation_name"] = f"Corporation {corp_id}"

        if cleaned_data.get("max_subcap_form") is None:
            cleaned_data["max_subcap_form"] = 0
        if cleaned_data.get("max_cap_form") is None:
            cleaned_data["max_cap_form"] = 0
        if cleaned_data.get("max_super_form") is None:
            cleaned_data["max_super_form"] = 0

        return cleaned_data


class HostileStagingSystemForm(forms.ModelForm):
    """Form for reporting and tracking hostile staging systems (in/out of target sov)"""

    solar_system = SolarSystemChoiceField(
        required=True,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    structure = forms.ModelChoiceField(
        queryset=HostileStructure.objects.select_related("solar_system", "structure_type", "alliance").all(),
        required=False,
        empty_label="Unknown / Unassigned",
        widget=forms.Select(attrs={"class": "form-select"}),
        label="Staging Structure (Optional - Unknown if not assigned)",
    )
    max_subcap_form = forms.IntegerField(
        required=False,
        initial=0,
        widget=forms.NumberInput(
            attrs={"class": "form-control", "placeholder": "0 = Auto-estimate from entity zKill"}
        ),
        label="Max Subcaps Formup (Manual Override)",
        help_text="Leave blank or 0 to automatically estimate from entity's 3-month rolling zKill capability.",
    )
    max_cap_form = forms.IntegerField(
        required=False,
        initial=0,
        widget=forms.NumberInput(
            attrs={"class": "form-control", "placeholder": "0 = Auto-estimate from entity zKill"}
        ),
        label="Max Capitals Formup (Manual Override)",
        help_text="Leave blank or 0 to automatically estimate from entity's 3-month rolling zKill capability.",
    )
    max_super_form = forms.IntegerField(
        required=False,
        initial=0,
        widget=forms.NumberInput(
            attrs={"class": "form-control", "placeholder": "0 = Auto-estimate from entity zKill"}
        ),
        label="Max Supers Formup (Manual Override)",
        help_text="Leave blank or 0 to automatically estimate from entity's 3-month rolling zKill capability.",
    )

    class Meta:
        model = HostileStagingSystem
        fields = [
            "solar_system",
            "alliance",
            "corporation",
            "structure",
            "staging_type",
            "is_primary",
            "max_subcap_form",
            "max_cap_form",
            "max_super_form",
            "notes",
        ]
        widgets = {
            "solar_system": forms.Select(attrs={"class": "form-select"}),
            "alliance": forms.Select(attrs={"class": "form-select"}),
            "corporation": forms.Select(attrs={"class": "form-select"}),
            "staging_type": forms.Select(attrs={"class": "form-select"}),
            "is_primary": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "notes": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 3,
                    "placeholder": "Deployment details, cyno beacons, JB access...",
                }
            ),
        }

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("max_subcap_form") is None:
            cleaned_data["max_subcap_form"] = 0
        if cleaned_data.get("max_cap_form") is None:
            cleaned_data["max_cap_form"] = 0
        if cleaned_data.get("max_super_form") is None:
            cleaned_data["max_super_form"] = 0
        return cleaned_data


class HostileDoctrineForm(forms.ModelForm):
    """Form for defining hostile doctrines (general or staging-linked)"""

    class Meta:
        model = HostileDoctrine
        fields = [
            "name",
            "alliance",
            "corporation",
            "role_type",
            "is_general_doctrine",
            "stagings",
            "primary_ship_types",
            "description",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. TFIs (Armor Battleships), RLML Cerberus"}),
            "alliance": forms.Select(attrs={"class": "form-select"}),
            "corporation": forms.Select(attrs={"class": "form-select"}),
            "role_type": forms.Select(attrs={"class": "form-select"}),
            "is_general_doctrine": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "stagings": forms.SelectMultiple(attrs={"class": "form-select"}),
            "primary_ship_types": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "e.g. Tempest Fleet Issue, Guardian, Damnation, Huginn, Loki"}
            ),
            "description": forms.Textarea(
                attrs={"class": "form-control", "rows": 3, "placeholder": "Tactical notes, engagement envelopes, counterplays..."}
            ),
        }


class DoctrineFitForm(forms.ModelForm):
    """Form for adding example fits linkable to zKillboard or EFT"""

    class Meta:
        model = DoctrineFit
        fields = ["doctrine", "ship_type", "name", "role", "zkill_link", "eft_format", "notes"]
        widgets = {
            "doctrine": forms.Select(attrs={"class": "form-select"}),
            "ship_type": forms.Select(attrs={"class": "form-select"}),
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Main DPS TFI, Armor Logi Guardian"}),
            "role": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. DPS, Logi, Tackle, Boosts"}),
            "zkill_link": forms.URLInput(attrs={"class": "form-control", "placeholder": "https://zkillboard.com/kill/12345678/"}),
            "eft_format": forms.Textarea(attrs={"class": "form-control font-monospace", "rows": 6, "placeholder": "Paste [Tempest Fleet Issue, Fit] EFT format here..."}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
        }


class ReinforcementCalculatorForm(forms.Form):
    """Form for calculating guesstimated Upwell and POCO reinforce exit windows"""

    structure = forms.ModelChoiceField(
        queryset=HostileStructure.objects.select_related("solar_system", "structure_type", "alliance").all(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select", "id": "id_calculator_structure"}),
        help_text="Select a known hostile structure to automatically fill category, location, and scanned vulnerability hours.",
    )
    solar_system = SolarSystemChoiceField(
        required=False,
        widget=forms.Select(attrs={"class": "form-select", "id": "id_calculator_system"}),
        help_text="Select solar system to automatically infer Highsec / Lowsec / Nullsec / Wormhole.",
    )
    structure_size = forms.ChoiceField(
        choices=StructureCategory.CHOICES,
        initial=StructureCategory.MEDIUM,
        widget=forms.Select(attrs={"class": "form-select", "id": "id_calculator_size"}),
    )
    location_type = forms.ChoiceField(
        choices=LocationSecurity.CHOICES,
        initial=LocationSecurity.NULLSEC_LOWSEC,
        widget=forms.Select(attrs={"class": "form-select", "id": "id_calculator_location"}),
    )
    is_low_power = forms.BooleanField(
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input", "id": "id_calculator_low_power"}),
        help_text="Structure is unfueled / in Low Power mode (no active service modules). Large/XL structures in Low Power skip armor reinforcement directly to vulnerable armor.",
    )
    timer_stage = forms.ChoiceField(
        choices=TimerStage.CHOICES,
        initial=TimerStage.SHIELD_REINFORCE,
        widget=forms.Select(attrs={"class": "form-select", "id": "id_calculator_stage"}),
        help_text="Shield Depleted → Armor Timer Exit, or Armor Depleted → Hull / Final Timer Exit (availability depends on structure size and power state).",
    )
    power_state = forms.ChoiceField(
        choices=PowerState.CHOICES,
        initial=PowerState.FULL_POWER,
        required=False,
        widget=forms.Select(attrs={"class": "form-select", "id": "id_calculator_power"}),
    )
    reinforced_at = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(
            attrs={"class": "form-control font-mono", "type": "datetime-local", "id": "id_calculator_reinforced_at"}
        ),
        help_text="Timestamp when the structure entered reinforcement in UTC. Defaults to current time if blank.",
    )
    vulnerability_hour = forms.IntegerField(
        required=False,
        min_value=0,
        max_value=23,
        widget=forms.NumberInput(
            attrs={
                "class": "form-control font-mono",
                "placeholder": "UTC Hour (0-23)",
                "min": 0,
                "max": 23,
                "id": "id_calculator_vuln_hour",
            }
        ),
        help_text="Crowd-sourced or scanned defender vulnerability / reinforcement hour in UTC.",
    )
    vulnerability_window = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "e.g. 18:00 - 22:00 UTC or 19:00 UTC",
                "id": "id_calculator_vuln_window",
            }
        ),
    )
    save_to_timers = forms.BooleanField(
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input", "id": "id_calculator_save_timer"}),
        help_text="Check to automatically save this calculated exit time to the Active Timer Board.",
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 2,
                "placeholder": "Notes or fleet intel attached to this timer...",
                "id": "id_calculator_notes",
            }
        ),
    )

    def clean(self):
        cleaned_data = super().clean()
        is_low_power = cleaned_data.get("is_low_power", False)
        power_state = cleaned_data.get("power_state")

        if is_low_power and power_state != PowerState.ABANDONED:
            cleaned_data["power_state"] = PowerState.LOW_POWER
            power_state = PowerState.LOW_POWER
        elif not is_low_power and not power_state:
            cleaned_data["power_state"] = PowerState.FULL_POWER

        structure_size = cleaned_data.get("structure_size")
        timer_stage = cleaned_data.get("timer_stage")

        if structure_size in (StructureCategory.MEDIUM, StructureCategory.POCO) and timer_stage == TimerStage.ARMOR_REINFORCE:
            self.add_error(
                "timer_stage",
                "Medium structures and POCOs do not have a hull reinforcement stage. Select 'Shield Depleted → Armor Timer Exit'.",
            )
        elif structure_size == StructureCategory.FLEX and timer_stage == TimerStage.SHIELD_REINFORCE:
            self.add_error(
                "timer_stage",
                "FLEX structures do not have an armor reinforcement stage. Select 'Armor Depleted → Hull / Final Timer Exit'.",
            )
        elif structure_size == StructureCategory.LARGE_XL and is_low_power and timer_stage == TimerStage.SHIELD_REINFORCE:
            self.add_error(
                "timer_stage",
                "Large/XL structures in Low Power mode skip armor reinforcement. Select 'Armor Depleted → Hull / Final Timer Exit'.",
            )

        return cleaned_data
