# Change Log

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](http://keepachangelog.com/)
and this project adheres to [Semantic Versioning](http://semver.org/).

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
