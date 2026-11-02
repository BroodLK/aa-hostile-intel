"""
Tests for creating friendly Structure Timers from corptools-stored EVE notifications
"""

# Standard Library
import unittest
from datetime import UTC, datetime, timedelta

# Third Party
from eve_sde.models import ItemCategory, ItemGroup, ItemType, Planet, SolarSystem

try:
    # Third Party
    from corptools.models import CharacterAudit, Notification, NotificationText
    from eveuniverse.models import (
        EveCategory,
        EveConstellation,
        EveGroup,
        EveRegion,
        EveSolarSystem,
        EveType,
    )
    from structuretimers.models import Timer as STTimer

    HAS_DEPS = True
except (ImportError, RuntimeError):
    HAS_DEPS = False

# Django
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

# Alliance Auth
from allianceauth.eveonline.models import EveCharacter, EveCorporationInfo
from allianceauth.tests.auth_utils import AuthUtils
from allianceauth.timerboard.models import Timer as TBTimer

# AA Hostile Intel
from hostile.models import HostileAlliance, HostileStructure, StructureTimer
from hostile.services.corptools_notifications import (
    CorptoolsNotificationTimerService,
    filetime_to_datetime,
    parse_notification,
    ticks_to_timedelta,
)
from hostile.services.timer_sync import sync_operation
from hostile.signals import connect_all_timer_signals
from hostile.tasks import import_corptools_notification_timers

_FILETIME_EPOCH_OFFSET = 116444736000000000

# Real MercenaryDenReinforced body as stored by corptools
MERC_DEN_SAMPLE = """aggressorAllianceName: <a href="showinfo:16159//1644918530">Broken Chains Alliance</a>
aggressorCharacterID: 1244190789
aggressorCorporationName: <a href="showinfo:2//1860753927">Minmatar Death Squad</a>
itemID: &id001 1055583242237
mercenaryDenShowInfoData:
- showinfo
- 85230
- *id001
planetID: 40147619
planetShowInfoData:
- showinfo
- 11
- 40147619
solarsystemID: 30002318
timestampEntered: 134343286894627007
timestampExited: 134344249874627007
typeID: 85230
"""


def to_filetime(value: datetime) -> int:
    return int(value.timestamp() * 10_000_000) + _FILETIME_EPOCH_OFFSET


def to_ticks(delta: timedelta) -> int:
    return int(delta.total_seconds() * 10_000_000)


def upwell_yaml(time_left: timedelta, structure_id=1000000000001) -> str:
    return (
        "solarsystemID: 30004759\n"
        f"structureID: &id001 {structure_id}\n"
        "structureShowInfoData:\n"
        "- showinfo\n"
        "- 35832\n"
        "- *id001\n"
        "structureTypeID: 35832\n"
        f"timeLeft: {to_ticks(time_left)}\n"
        "timestamp: 133414701190000000\n"
        "vulnerableTime: 9000000000\n"
    )


def merc_den_yaml(exited: datetime) -> str:
    return MERC_DEN_SAMPLE.replace(
        "timestampExited: 134344249874627007",
        f"timestampExited: {to_filetime(exited)}",
    )


class TestNotificationParsing(TestCase):
    def test_filetime_and_ticks(self):
        moment = datetime(2026, 11, 2, 18, 30, tzinfo=UTC)
        self.assertEqual(filetime_to_datetime(to_filetime(moment)), moment)
        self.assertEqual(ticks_to_timedelta(36_000_000_000), timedelta(hours=1))
        self.assertIsNone(filetime_to_datetime("garbage"))

    def test_lost_shields_is_armor_timer(self):
        sent = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
        parsed = parse_notification(
            "StructureLostShields", upwell_yaml(timedelta(hours=26)), sent
        )
        self.assertEqual(parsed.timer_type, "ARMOR")
        self.assertEqual(parsed.structure_state, "ARMOR")
        self.assertEqual(parsed.timer_datetime, sent + timedelta(hours=26))
        self.assertEqual(parsed.solar_system_id, 30004759)
        self.assertEqual(parsed.structure_type_id, 35832)
        self.assertEqual(parsed.structure_id, 1000000000001)

    def test_lost_armor_is_hull_timer(self):
        sent = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
        parsed = parse_notification(
            "StructureLostArmor", upwell_yaml(timedelta(hours=40)), sent
        )
        self.assertEqual(parsed.timer_type, "HULL")
        self.assertEqual(parsed.structure_state, "HULL")

    def test_real_mercenary_den_sample(self):
        parsed = parse_notification(
            "MercenaryDenReinforced",
            MERC_DEN_SAMPLE,
            datetime(2026, 9, 19, 22, 0, tzinfo=UTC),
        )
        self.assertEqual(parsed.timer_type, "HULL")
        self.assertEqual(parsed.structure_state, "REINFORCED")
        self.assertEqual(
            parsed.timer_datetime.replace(microsecond=0),
            datetime(2026, 9, 21, 0, 43, 7, tzinfo=UTC),
        )
        self.assertEqual(parsed.solar_system_id, 30002318)
        self.assertEqual(parsed.structure_type_id, 85230)
        self.assertEqual(parsed.structure_id, 1055583242237)
        self.assertEqual(parsed.planet_id, 40147619)
        self.assertEqual(
            parsed.aggressor, "Minmatar Death Squad / Broken Chains Alliance"
        )

    def test_orbital_reinforced_uses_absolute_exit_time(self):
        exit_time = datetime(2026, 11, 4, 19, 0, tzinfo=UTC)
        text = (
            "aggressorAllianceID: 933731581\n"
            "aggressorCorpID: 98656901\n"
            "aggressorID: 2112625428\n"
            "planetID: 40161469\n"
            "planetTypeID: 2016\n"
            f"reinforceExitTime: {to_filetime(exit_time)}\n"
            "solarSystemID: 30004759\n"
            "typeID: 2233\n"
        )
        parsed = parse_notification(
            "OrbitalReinforced", text, datetime(2026, 11, 2, tzinfo=UTC)
        )
        self.assertEqual(parsed.timer_type, "HULL")
        self.assertEqual(parsed.timer_datetime, exit_time)
        self.assertEqual(parsed.planet_id, 40161469)

    def test_unhandled_or_broken_notifications(self):
        sent = timezone.now()
        self.assertIsNone(parse_notification("StructureUnderAttack", "a: 1", sent))
        self.assertIsNone(parse_notification("StructureLostShields", "", sent))
        self.assertIsNone(
            parse_notification("StructureLostShields", "solarsystemID: 1\n", sent)
        )


@unittest.skipUnless(
    HAS_DEPS,
    "allianceauth-corptools, aa-structuretimers and django-eveuniverse required",
)
@override_settings(
    HOSTILE_CORPTOOLS_NOTIFICATIONS_ENABLED=True,
    HOSTILE_MAIN_ALLIANCE_ID=None,
    HOSTILE_MAIN_ALLIANCE_NAME=None,
)
class TestCorptoolsNotificationTimers(TestCase):
    @classmethod
    def setUpTestData(cls):
        connect_all_timer_signals()

        # eveuniverse (aa-structuretimers)
        region = EveRegion.objects.create(id=10000060, name="Delve")
        constellation = EveConstellation.objects.create(
            id=20000696, name="O-EIMK", eve_region=region
        )
        cls.eve_1dq = EveSolarSystem.objects.create(
            id=30004759,
            name="1DQ1-A",
            eve_constellation=constellation,
            security_status=-0.4,
        )
        cls.eve_merc_system = EveSolarSystem.objects.create(
            id=30002318,
            name="Merc System",
            eve_constellation=constellation,
            security_status=-0.4,
        )
        eve_cat = EveCategory.objects.create(id=65, name="Structure", published=True)
        eve_group = EveGroup.objects.create(
            id=1657, name="Citadel", eve_category=eve_cat, published=True
        )
        cls.eve_astrahus = EveType.objects.create(
            id=35832, name="Astrahus", eve_group=eve_group, published=True
        )
        cls.eve_merc_den = EveType.objects.create(
            id=85230, name="Mercenary Den", eve_group=eve_group, published=True
        )

        # eve_sde (Hostile Intel)
        category = ItemCategory.objects.create(id=65, name="Structure", published=True)
        group = ItemGroup.objects.create(
            id=1657, name="Citadel", category=category, published=True
        )
        cls.astrahus = ItemType.objects.create(
            id=35832, name="Astrahus", group=group, published=True
        )
        cls.merc_den = ItemType.objects.create(
            id=85230, name="Mercenary Den", group=group, published=True
        )
        planet_type = ItemType.objects.create(
            id=11, name="Planet (Temperate)", group=group, published=True
        )
        cls.system = SolarSystem.objects.create(
            id=30004759, name="1DQ1-A", security_status=-0.4
        )
        cls.merc_system = SolarSystem.objects.create(
            id=30002318, name="Merc System", security_status=-0.4
        )
        Planet.objects.create(
            id=40147619,
            name="Merc System IV",
            item_type=planet_type,
            solar_system=cls.merc_system,
        )

        cls.corp = EveCorporationInfo.objects.create(
            corporation_id=98000001,
            corporation_name="Friendly Corp",
            corporation_ticker="FRND",
            member_count=10,
        )
        cls.user = AuthUtils.create_user(username="director")
        AuthUtils.add_main_character(cls.user, "Director A", 21100001, corp_id=98000001)
        cls.char_a = EveCharacter.objects.get(character_id=21100001)
        cls.char_a.corporation_name = "Friendly Corp"
        cls.char_a.corporation_ticker = "FRND"
        cls.char_a.alliance_id = 99000001
        cls.char_a.alliance_name = "Friendly Alliance"
        cls.char_a.alliance_ticker = "FRALL"
        cls.char_a.save()
        cls.char_b = EveCharacter.objects.create(
            character_id=21100002,
            character_name="Director B",
            corporation_id=98000001,
            corporation_name="Friendly Corp",
            corporation_ticker="FRND",
            alliance_id=99000001,
            alliance_name="Friendly Alliance",
            alliance_ticker="FRALL",
        )
        cls.audit_a = CharacterAudit.objects.create(character=cls.char_a)
        cls.audit_b = CharacterAudit.objects.create(character=cls.char_b)

    def setUp(self):
        cache.delete("hostile_notif_timer_import_lock")

    def _notify(self, audit, notification_id, notification_type, text, sent=None):
        NotificationText.objects.create(
            notification_id=notification_id, notification_text=text
        )
        return Notification.objects.create(
            character=audit,
            notification_id=notification_id,
            sender_id=1000137,
            sender_type="corporation",
            timestamp=sent or timezone.now() - timedelta(minutes=5),
            notification_type=notification_type,
            is_read=False,
            notification_text_id=notification_id,
        )

    def _external_st_timer(self, **kwargs):
        """An aa-structuretimers timer posted by someone else (no sync triggered)."""
        values = {
            "eve_solar_system": self.eve_1dq,
            "structure_type": self.eve_astrahus,
            "structure_name": "",
            "timer_type": STTimer.Type.ARMOR,
            "objective": STTimer.Objective.FRIENDLY,
            "user": self.user,
        }
        values.update(kwargs)
        with sync_operation("test", 1):
            return STTimer.objects.create(**values)

    # ------------------------------------------------------------------

    def test_creates_friendly_structuretimers_timer_and_no_timerboard_timer(self):
        sent = timezone.now().replace(microsecond=0) - timedelta(minutes=5)
        self._notify(
            self.audit_a,
            1001,
            "StructureLostShields",
            upwell_yaml(timedelta(hours=26)),
            sent=sent,
        )

        msg = import_corptools_notification_timers()
        self.assertIn("created=1", msg)

        timer = StructureTimer.objects.get(source_notification_id=1001)
        self.assertEqual(timer.source, StructureTimer.SOURCE_NOTIFICATION)
        self.assertEqual(timer.timer_type, "ARMOR")
        self.assertEqual(timer.timer_datetime, sent + timedelta(hours=26))
        self.assertEqual(timer.defender_id, 99000001)
        self.assertEqual(timer.created_by, self.user)

        st_timer = STTimer.objects.get(id=timer.structuretimers_timer_id)
        self.assertEqual(st_timer.objective, STTimer.Objective.FRIENDLY)
        self.assertEqual(st_timer.timer_type, STTimer.Type.ARMOR)
        self.assertEqual(st_timer.eve_solar_system, self.eve_1dq)
        self.assertEqual(STTimer.objects.count(), 1)

        self.assertIsNone(timer.timerboard_timer_id)
        self.assertEqual(TBTimer.objects.count(), 0)

        # Reconciliation does not copy it to the Timerboard either
        call_command_output = import_corptools_notification_timers()
        self.assertIn("created=0", call_command_output)
        # AA Hostile Intel
        from hostile.tasks import sync_timerboard_timers

        sync_timerboard_timers()
        self.assertEqual(TBTimer.objects.count(), 0)
        self.assertEqual(STTimer.objects.count(), 1)

    def test_same_notification_for_two_directors_posts_once(self):
        sent = timezone.now() - timedelta(minutes=5)
        text = upwell_yaml(timedelta(hours=26))
        self._notify(self.audit_a, 2001, "StructureLostShields", text, sent=sent)
        self._notify(
            self.audit_b,
            2002,
            "StructureLostShields",
            text,
            sent=sent + timedelta(seconds=3),
        )

        stats = CorptoolsNotificationTimerService.import_notification_timers()
        self.assertEqual(stats["created"], 1)
        stats = CorptoolsNotificationTimerService.import_notification_timers()
        self.assertEqual(stats["created"], 0)

        self.assertEqual(StructureTimer.objects.count(), 1)
        self.assertEqual(STTimer.objects.count(), 1)

    def test_existing_aa_structures_timer_is_linked_not_duplicated(self):
        sent = timezone.now().replace(microsecond=0) - timedelta(minutes=5)
        st_timer = self._external_st_timer(
            structure_name="1DQ1-A - Home Astrahus",
            date=sent + timedelta(hours=26, minutes=2),
            details_notes="Posted by aa-structures",
        )
        self.assertEqual(StructureTimer.objects.count(), 0)

        self._notify(
            self.audit_a,
            3001,
            "StructureLostShields",
            upwell_yaml(timedelta(hours=26)),
            sent=sent,
        )
        stats = CorptoolsNotificationTimerService.import_notification_timers()
        self.assertEqual(stats["linked"], 1)
        self.assertEqual(STTimer.objects.count(), 1)

        timer = StructureTimer.objects.get()
        self.assertEqual(timer.structuretimers_timer_id, st_timer.id)
        self.assertEqual(timer.source_notification_id, 3001)

        # The external timer was left untouched
        st_timer.refresh_from_db()
        self.assertEqual(st_timer.details_notes, "Posted by aa-structures")

        # Reconciliation still doesn't double-post
        # AA Hostile Intel
        from hostile.tasks import sync_timerboard_timers

        sync_timerboard_timers()
        self.assertEqual(STTimer.objects.count(), 1)
        self.assertEqual(TBTimer.objects.count(), 0)

    def test_existing_final_timer_matches_hull(self):
        exited = timezone.now().replace(microsecond=0) + timedelta(hours=20)
        self._external_st_timer(
            eve_solar_system=self.eve_merc_system,
            structure_type=self.eve_merc_den,
            timer_type=STTimer.Type.FINAL,
            date=exited,
        )
        self._notify(
            self.audit_a, 3101, "MercenaryDenReinforced", merc_den_yaml(exited)
        )
        stats = CorptoolsNotificationTimerService.import_notification_timers()
        self.assertEqual(stats["linked"], 1)
        self.assertEqual(STTimer.objects.count(), 1)

    def test_two_named_structures_same_system_same_time_get_two_timers(self):
        sent = timezone.now().replace(microsecond=0) - timedelta(minutes=5)
        self._external_st_timer(
            structure_name="1DQ1-A - Other Astrahus",
            date=sent + timedelta(hours=26, minutes=1),
        )
        # Our structure has a real (corptools) name that differs
        HostileStructure.objects.create(
            name="1DQ1-A - Home Astrahus",
            structure_id=1000000000001,
            structure_type=self.astrahus,
            solar_system=self.system,
        )
        self._notify(
            self.audit_a,
            3201,
            "StructureLostShields",
            upwell_yaml(timedelta(hours=26)),
            sent=sent,
        )
        stats = CorptoolsNotificationTimerService.import_notification_timers()
        self.assertEqual(stats["created"], 1)
        self.assertEqual(STTimer.objects.count(), 2)

    def test_merc_den_updates_registry_and_notes(self):
        exited = timezone.now().replace(microsecond=0) + timedelta(hours=20)
        self._notify(
            self.audit_a, 4001, "MercenaryDenReinforced", merc_den_yaml(exited)
        )
        stats = CorptoolsNotificationTimerService.import_notification_timers()
        self.assertEqual(stats["created"], 1)

        structure = HostileStructure.objects.get(structure_id=1055583242237)
        self.assertEqual(structure.state, "REINFORCED")
        self.assertEqual(structure.structure_type, self.merc_den)
        self.assertEqual(structure.solar_system, self.merc_system)
        self.assertEqual(structure.location_details, "Merc System IV")
        self.assertEqual(structure.owner_ticker, "FRALL")

        timer = StructureTimer.objects.get(source_notification_id=4001)
        self.assertIn("Minmatar Death Squad / Broken Chains Alliance", timer.notes)
        self.assertEqual(timer.timer_datetime.replace(microsecond=0), exited)

    def test_registry_owner_links_existing_alliance_only(self):
        alliance = HostileAlliance.objects.create(
            alliance_id=99000001, alliance_name="Friendly Alliance", ticker="FRALL"
        )
        self._notify(
            self.audit_a,
            4101,
            "StructureLostShields",
            upwell_yaml(timedelta(hours=26)),
        )
        CorptoolsNotificationTimerService.import_notification_timers()
        structure = HostileStructure.objects.get(structure_id=1000000000001)
        self.assertEqual(structure.alliance, alliance)
        self.assertIsNone(structure.corporation)
        alliance.refresh_from_db()
        self.assertFalse(alliance.is_friendly)  # never re-flagged

    def test_older_notification_does_not_roll_state_back(self):
        sent = timezone.now() - timedelta(hours=3)
        self._notify(
            self.audit_a,
            5001,
            "StructureLostArmor",
            upwell_yaml(timedelta(hours=30)),
            sent=sent,
        )
        CorptoolsNotificationTimerService.import_notification_timers()
        structure = HostileStructure.objects.get(structure_id=1000000000001)
        self.assertEqual(structure.state, "HULL")

        # Another director's copy of the earlier shield notification shows up later
        self._notify(
            self.audit_b,
            5002,
            "StructureLostShields",
            upwell_yaml(timedelta(hours=26)),
            sent=sent - timedelta(hours=2),
        )
        CorptoolsNotificationTimerService.import_notification_timers()
        structure.refresh_from_db()
        self.assertEqual(structure.state, "HULL")

    def test_old_and_past_notifications_are_ignored(self):
        # Older than the 24h lookback, timer still in the future
        self._notify(
            self.audit_a,
            6001,
            "StructureLostShields",
            upwell_yaml(timedelta(hours=40)),
            sent=timezone.now() - timedelta(hours=25),
        )
        # Inside the lookback but the timer already passed
        self._notify(
            self.audit_a,
            6002,
            "StructureLostArmor",
            upwell_yaml(timedelta(hours=1)),
            sent=timezone.now() - timedelta(hours=2),
        )
        stats = CorptoolsNotificationTimerService.import_notification_timers()
        self.assertEqual(stats["processed"], 1)
        self.assertEqual(stats["created"], 0)
        self.assertEqual(StructureTimer.objects.count(), 0)
        self.assertEqual(STTimer.objects.count(), 0)

    def test_existing_manual_timer_is_not_duplicated(self):
        sent = timezone.now().replace(microsecond=0) - timedelta(minutes=5)
        structure = HostileStructure.objects.create(
            name="Home Astrahus",
            structure_type=self.astrahus,
            solar_system=self.system,
        )
        with sync_operation("test", 2):
            StructureTimer.objects.create(
                structure=structure,
                solar_system=self.system,
                timer_type="ARMOR",
                timer_datetime=sent + timedelta(hours=26, minutes=4),
            )
        self._notify(
            self.audit_a,
            7001,
            "StructureLostShields",
            upwell_yaml(timedelta(hours=26)),
            sent=sent,
        )
        stats = CorptoolsNotificationTimerService.import_notification_timers()
        self.assertEqual(stats["created"], 0)
        self.assertEqual(StructureTimer.objects.count(), 1)
        self.assertEqual(StructureTimer.objects.get().source_notification_id, 7001)

    def test_concurrent_run_is_refused(self):
        self._notify(
            self.audit_a,
            8001,
            "StructureLostShields",
            upwell_yaml(timedelta(hours=26)),
        )
        cache.add("hostile_notif_timer_import_lock", True, 60)
        try:
            msg = import_corptools_notification_timers()
        finally:
            cache.delete("hostile_notif_timer_import_lock")
        self.assertIn("already running", msg)
        self.assertEqual(StructureTimer.objects.count(), 0)

    @override_settings(HOSTILE_CORPTOOLS_NOTIFICATIONS_ENABLED=False)
    def test_disabled_setting_skips_import(self):
        self._notify(
            self.audit_a,
            9001,
            "StructureLostShields",
            upwell_yaml(timedelta(hours=26)),
        )
        msg = import_corptools_notification_timers()
        self.assertIn("skipped", msg)
        self.assertEqual(StructureTimer.objects.count(), 0)

    # Regression tests from the end-to-end recheck ---------------------

    def test_bulk_created_notifications_like_corptools_create_timer(self):
        sent = timezone.now().replace(microsecond=0) - timedelta(minutes=5)
        NotificationText.objects.bulk_create(
            [
                NotificationText(
                    notification_id=10001,
                    notification_text=upwell_yaml(timedelta(hours=26)),
                )
            ]
        )
        Notification.objects.bulk_create(
            [
                Notification(
                    character=self.audit_a,
                    notification_id=10001,
                    sender_id=1000137,
                    sender_type="corporation",
                    timestamp=sent,
                    notification_type="StructureLostShields",
                    is_read=False,
                    notification_text_id=10001,
                )
            ]
        )
        msg = import_corptools_notification_timers()
        self.assertIn("created=1", msg)
        timer = StructureTimer.objects.get(source_notification_id=10001)
        self.assertIsNotNone(timer.structuretimers_timer_id)
        self.assertEqual(STTimer.objects.count(), 1)

    def test_linked_aa_structures_timer_is_never_overwritten(self):
        # AA Hostile Intel
        from hostile.tasks import sync_timerboard_timers

        sent = timezone.now().replace(microsecond=0) - timedelta(minutes=5)
        st_timer = self._external_st_timer(
            structure_name="1DQ1-A - Home Astrahus",
            owner_name="Friendly Corp",
            location_details="Planet V",
            date=sent + timedelta(hours=26, minutes=2),
            details_notes="Posted by aa-structures",
        )
        self._notify(
            self.audit_a,
            10101,
            "StructureLostShields",
            upwell_yaml(timedelta(hours=26)),
            sent=sent,
        )
        CorptoolsNotificationTimerService.import_notification_timers()
        sync_timerboard_timers()
        sync_timerboard_timers()

        st_timer.refresh_from_db()
        self.assertEqual(st_timer.structure_name, "1DQ1-A - Home Astrahus")
        self.assertEqual(st_timer.owner_name, "Friendly Corp")
        self.assertEqual(st_timer.location_details, "Planet V")
        self.assertEqual(st_timer.details_notes, "Posted by aa-structures")
        self.assertEqual(STTimer.objects.count(), 1)
        self.assertEqual(TBTimer.objects.count(), 0)

    def test_adopted_timer_edit_on_structuretimers_is_pulled(self):
        # AA Hostile Intel
        from hostile.tasks import sync_timerboard_timers

        sent = timezone.now().replace(microsecond=0) - timedelta(minutes=5)
        st_timer = self._external_st_timer(
            structure_name="1DQ1-A - Home Astrahus",
            date=sent + timedelta(hours=26),
        )
        self._notify(
            self.audit_a,
            10201,
            "StructureLostShields",
            upwell_yaml(timedelta(hours=26)),
            sent=sent,
        )
        CorptoolsNotificationTimerService.import_notification_timers()

        new_date = sent + timedelta(hours=27)
        with sync_operation("test", 3):
            STTimer.objects.filter(pk=st_timer.pk).update(date=new_date)
        sync_timerboard_timers()

        timer = StructureTimer.objects.get(structuretimers_timer_id=st_timer.id)
        self.assertEqual(timer.timer_datetime, new_date)
        self.assertEqual(STTimer.objects.count(), 1)

    def test_hostile_timer_in_same_system_does_not_block_ours(self):
        sent = timezone.now().replace(microsecond=0) - timedelta(minutes=5)
        hostile_alliance = HostileAlliance.objects.create(
            alliance_id=99000666, alliance_name="Enemy Alliance", ticker="ENEMY"
        )
        enemy_structure = HostileStructure.objects.create(
            name="Enemy Astrahus",
            structure_type=self.astrahus,
            solar_system=self.system,
            alliance=hostile_alliance,
        )
        with sync_operation("test", 4):
            enemy_timer = StructureTimer.objects.create(
                structure=enemy_structure,
                solar_system=self.system,
                timer_type="ARMOR",
                timer_datetime=sent + timedelta(hours=26, minutes=2),
            )
        self._notify(
            self.audit_a,
            10301,
            "StructureLostShields",
            upwell_yaml(timedelta(hours=26)),
            sent=sent,
        )
        stats = CorptoolsNotificationTimerService.import_notification_timers()
        self.assertEqual(stats["created"], 1)
        enemy_timer.refresh_from_db()
        self.assertIsNone(enemy_timer.source_notification_id)
        self.assertTrue(
            StructureTimer.objects.filter(source_notification_id=10301).exists()
        )

    def test_hostile_objective_structuretimers_timer_does_not_block_ours(self):
        sent = timezone.now().replace(microsecond=0) - timedelta(minutes=5)
        self._external_st_timer(
            objective=STTimer.Objective.HOSTILE,
            date=sent + timedelta(hours=26, minutes=1),
        )
        self._notify(
            self.audit_a,
            10401,
            "StructureLostShields",
            upwell_yaml(timedelta(hours=26)),
            sent=sent,
        )
        stats = CorptoolsNotificationTimerService.import_notification_timers()
        self.assertEqual(stats["created"], 1)
        self.assertEqual(STTimer.objects.count(), 2)

    def test_dry_run_command_reports_and_writes_nothing(self):
        # Standard Library
        from io import StringIO

        self._notify(
            self.audit_a,
            10501,
            "StructureLostShields",
            upwell_yaml(timedelta(hours=26)),
        )
        out = StringIO()
        call_command("hostile_notification_timers", "--dry-run", stdout=out)
        self.assertIn("would create", out.getvalue())
        self.assertEqual(StructureTimer.objects.count(), 0)
        self.assertEqual(STTimer.objects.count(), 0)
        self.assertEqual(HostileStructure.objects.count(), 0)


class TestSetupTasksCommand(TestCase):
    def test_setup_registers_sync_and_notification_tasks(self):
        # Third Party
        from django_celery_beat.models import PeriodicTask

        call_command("hostile_setup_tasks")
        tasks = set(PeriodicTask.objects.values_list("task", flat=True))
        self.assertIn("hostile.tasks.sync_timerboard_timers", tasks)
        self.assertIn("hostile.tasks.import_corptools_notification_timers", tasks)
