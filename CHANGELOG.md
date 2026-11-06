# Change Log

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](http://keepachangelog.com/)
and this project adheres to [Semantic Versioning](http://semver.org/).

## [0.0.29] - 2026-11-05

### Added

- **Drill Hourly Value**: The Structures tab now shows each drill's per-hour output next to its monthly value (`Drill 5.2b (7.2m/h)`), using a 720-hour month.
- **Reinforcement ISK Lost**: Reinforced drills show how much ISK the owner loses while reinforced, next to the state (`Hull Reinforced (133,851,851.85isk Lost)`). It is the drill's hourly value times the time from when the latest Armor/Hull timer was submitted to when it exits.

## [0.0.28] - 2026-11-02

### Changed

- **Python 3.11 Support Restored**: The minimum supported version is Python 3.11 again.

## [0.0.27] - 2026-11-02

### Added

- **Friendly Timers from Corptools Notifications**: A new periodic task (`hostile.tasks.import_corptools_notification_timers`, every 5 minutes) reads structure notifications from the last 24 hours that allianceauth-corptools has stored (`StructureLostShields`, `StructureLostArmor`, `StructureAnchoring`, `StructureUnanchoring`, `OrbitalReinforced`, `SkyhookLostShields`, `MercenaryDenReinforced`). It turns them into friendly Structure Timers posted to aa-structuretimers, so aa-structures is no longer needed. Several checks stop a timer from being posted twice: notifications already handled are skipped, a matching timer already in Hostile Intel is reused, an equivalent timer already on aa-structuretimers is linked rather than reposted, and only one import runs at a time. Mercenary Den timers include the aggressor's corporation and alliance in the notes. New settings: `HOSTILE_CORPTOOLS_NOTIFICATIONS_ENABLED`, `HOSTILE_CORPTOOLS_NOTIFICATION_LOOKBACK_HOURS` (24), `HOSTILE_NOTIFICATION_TIMER_TOLERANCE_MINUTES` (10).
- **Registry Updates from Notifications**: Notifications create or update the structure in the registry: ID, in-game name, type, planet, owner ticker, existing owner alliance/corp links, and reinforcement state. An older notification never overwrites a newer state.
- **Paste Reinforcement Timers**: *Add Timer* has a paste box for the timer copied from a reinforced structure (`Reinforced until 2026.11.03 19:13:30`). Known structures are matched by name (including ticker-prefixed and `<System> - <Name>` variants) and filled in, with the time and likely stage. Generic names like `Mercenary Den` show the matching structures to pick from. Unknown names pre-fill the new-structure fields, so the timer and the structure are added in one submit. A read-only API, `api/parse-reinforcement/`, backs the paste box.
- **Timer Status Updates**: Adding a timer now sets the structure's registry state (Armor / Hull / Reinforced / Anchoring / Unanchoring) using the same rules as notification timers. Adding a timer that already exists (same structure, type and time within the tolerance) no longer creates a duplicate.
- **Timer Source Tracking**: Structure Timers record where they came from (`source`) and which notification created them (`source_notification_id`).
- **Dry-run Check**: `python manage.py hostile_notification_timers --dry-run` lists every structure notification in the lookback window and whether it would create, link or skip a timer (with the reason), without changing anything.
- **Timer Board Sync Scheduling**: `hostile_setup_tasks` now also registers `hostile.tasks.sync_timerboard_timers` (every `HOSTILE_TIMERBOARD_SYNC_MINUTES`).

### Changed

- **No Friendly Timers on the Timerboard**: Friendly structure timers are no longer copied to the Alliance Auth Timerboard, which is for fleet operations. Hostile timers still sync to both boards, and timers that were already linked keep updating.
- **No Duplicate Posts to aa-structuretimers**: Before creating a timer on aa-structuretimers, the sync now looks for an equivalent existing timer (for example one posted by aa-structures) and links to it. Linked timers are never overwritten from Hostile Intel; edits made to them on aa-structuretimers are pulled in. Hostile-objective timers are never treated as the same timer as a friendly one.

## [0.0.26] - 2026-10-28

### Fixed

- **Duplicate Moon Drills from Timer Sync**: Timers synced from aa-structuretimers or Timerboard now update the existing drill instead of creating a new one when the timer's structure name differs from the registry name. Drills are matched on solar system + planet/moon (`P6M2`, `Planet 6 Moon 2`, or `BNX-AS VI - Moon 2`), and drills on a different moon are never matched by generic name.
- **Correlated Drill Deduplication**: Auto-deduplication and suspected-duplicate detection now recognise a moon-correlated drill and an uncorrelated copy at the same planet/moon as the same structure.

### Removed

- **Python 3.11 Support**: The minimum supported version is now Python 3.12.

## [0.0.25] - 2026-10-28

### Changed

- **Drill Value Roll-ups**: Owner, region, and system headers in the Structures registry now show the rolled-up drill value alongside the full moon value (`12.3b / Drill 4.9b ISK/mo`), and the per-row "Drill" label is capitalised.

## [0.0.24] - 2026-10-26

### Changed

- **Grouped Structures Registry**: The registry is now organised Alliance › Region › System with collapsible groups showing active/destroyed counts, system counts, and moon ISK totals. An "Expand to" control jumps between Alliances, Regions, Systems, and All, quick-filter chips sit alongside State and Quantum Core filters, and a sticky column header sorts rows within each system. Row actions are compact icons revealed on hover (always visible on touch devices).

### Added

- **Shared System Indicators**: Systems where other owners also hold structures show `also [TICKER] n` at the system level and shared-system counts at the region and alliance levels; clicking a ticker jumps to that owner's copy of the system.
- **Live Regrouping**: Inline owner and state edits move rows to their new group and refresh totals without a page reload.

### Fixed

- The suspected-duplicates review view now loads moon scan data like the main registry.
- The registry no longer errors when the optional Moon Mining integration fails to import.
- Structures with a 00:00 UTC vulnerability hour now sort correctly instead of being treated as unknown.

## [0.0.18] - 2026-10-24

### Added

- **Flexible Intel Scanner submissions**: Local chat, D-Scan, or both can now be submitted. D-Scan-only reports skip pilot enrichment and provide fast ship class and hull breakdowns.

## [0.0.6] - 2026-10-11

### Added

- **Ansiblex & FLEX Structure Timers**: Added full support for Ansiblex Jump Gates, Pharolux Cyno Beacons, Tenebrex Cyno Jammers, and Metenox Moon Drills across structure type selectors, structure registrations, and Structure Timers board.
- **Two-Way aa-structuretimers & Timerboard Synchronization**: Real-time invisible bidirectional synchronization between Hostile Intel Structure Timers, `aa-structuretimers` (v3.2.0+), and Alliance Auth built-in Structure Timers / Timerboard (`allianceauth.timerboard`).
- **Deterministic State Marker & Untracked Change Detection**: Implemented SHA-256 state hashing marker (`last_sync_hash`) on `StructureTimer` for instant change detection, distinguishing backend-initiated modifications from external untracked changes across Discord bots and external apps without recursion loops.
- **Self-Correcting Database Reconciliation**: Added automated periodic database sync scan and manual on-demand officer synchronization action (`hostile:timerboard_sync`) ensuring 100% parity across all boards with cross-app drift correction.
- **Consolidated Database Migrations**: Merged all external synchronization fields (`timerboard_timer_id`, `structuretimers_timer_id`, `last_sync_hash`) into migration `0003_structuretimer_timerboard_timer_id`.

## [0.0.1] - 2026-10-03

### Initial Release

- **Tactical Command Center Dashboard**: Real-time operational intelligence terminal featuring a Universal Fast-Triage Hero Deck (Omnisearch, Smart Ingest clipboard dropzone, Home Staging Anchor filter), Temporal Urgency KPI Deck, and dynamic 60-second auto-synchronization.
- **Non-ESI Structure & Fitting Ingestion**: Smart multi-format parsing for D-Scans, structure show-info copy-pastes, and EFT fitting blocks with automatic SDE module identification and quantum core status tracking.
- **Live Sovereignty Campaign Timers**: Dynamic real-time tracking of active and upcoming ADM sovereignty campaigns with 60-second change detection polling, live donut contest scores, and staging jump distance calculations.
- **Structure Reinforcement Timers Board**: Dedicated tracking for hostile armor and hull timers with live ticking countdowns, jump routing from home staging, and one-click FC Discord / broadcast ping generation.
- **Local Threat Scanner & Combat Drop Risk**: Non-blocking asynchronous pilot intelligence parsing with zKillboard integration, cyno alt detection, behavioral role tagging, real-time calculation of Black Ops and Capital drop probabilities, and comprehensive Local Activity Profile aggregating combat timezone breakdown (EUTZ/USTZ/AUTZ) and primary operational timezone across pilots.
- **Crowd-Sourced Tactical Sitreps & Gate Camp Ingestion**: System observation reporting with precise UTC Time Seen timestamps, interactive threat classification tags, quick operational presets, and integrated Local chat and D-Scan ingestion.
- **Hostile Pilot Dossiers Registry**: Comprehensive intelligence profiles for hostile cyno alts, supercarrier/titan pilots, fleet commanders, and scouts, with dedicated pilot detail dossier pages, alt character network visualization, threat scan sighting history, multi-field search, behavioral tag filtering, 3-box zKillboard danger ratio indicators, security status and character age displays, corporation/alliance entity tickers, top flown ships, and 1-click zKillboard combat synchronization.
- **Hostile Alliances & Corporations Profiling**: Automatic tracking of hostile coalition hierarchies, sovereignty footprints, timezone distributions, active PvP member metrics, formup estimations, 7x24 activity heatmaps via ESI and zKillboard, and support for friendly/ignored entity filtering across threat scanners.
- **Threat Scanner High-Contrast Legibility**: Tactical row coloring and calibrated dark table hover styling for extreme/warning pilots, eliminating red hover washouts and ensuring clear text readability.
- **Hostile Staging Hubs & Fleet Doctrines**: Forward deployment and staging base tracking with integrated doctrine fittings, interactive module slot modals, and copy-to-clipboard EFT actions.
- **Audit Logging & Tiered Permissions**: Role-based access control (`basic_access`, `manage_intel`, `officer_access`) with immutable audit trails for intel integrity.
