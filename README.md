# AA Hostile Intel

[![PyPI](https://img.shields.io/pypi/v/aa-hostile-intel)](https://pypi.org/project/aa-hostile-intel/)
[![Python](https://img.shields.io/pypi/pyversions/aa-hostile-intel)](https://pypi.org/project/aa-hostile-intel/)
[![Django](https://img.shields.io/badge/django-4.2%20%7C%205.2-blue)](https://www.djangoproject.com/)
[![Alliance Auth](https://img.shields.io/badge/alliance--auth-%3E%3D5.0-success)](https://gitlab.com/allianceauth/allianceauth)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)

**AA Hostile Intel** is an operational tactical intelligence, non-ESI structure tracking, fitting inspection, and combat threat analysis platform for [Alliance Auth](https://gitlab.com/allianceauth/allianceauth).

Designed specifically to eliminate cognitive friction and decision latency during live operations, it empowers fleet commanders (FCs), scouts, and intelligence officers to track hostile infrastructure, parse non-ESI fittings, calculate combat drop risks, monitor sovereignty vulnerability windows, and profile hostile alliance timezones.

---

## Key Features

- **⚡ Tactical Command Center Dashboard**:
  - **Universal Fast-Triage Hero Deck**: Omnisearch bar for instant client-side queries across pilots, systems, alliances, and timers; direct smart clipboard parser dropzone; and a Home Staging Anchor filter calculating live jump routing across all cards.
  - **Temporal Urgency KPI Deck**: Time-sensitive operational metrics (Timers <6h, Timers <24h, Active Sov Campaigns, Active Gate Camps, 7-Day Hotspots, Tracked Supers & Cynos).
  - **Two-Column Operational Layout**: Separates immediate tactical threats (urgent timers with live ticking countdowns and 1-click FC Discord pings, active gate camps with time-decay status badges, recent threat scans) from strategic landscape monitoring (hotspot activity rankings, active sovereignty battles, hostile staging hubs, and fleet doctrines).
  - **Real-Time Live Intelligence Feed**: Unified chronological ticker tracking sitreps, structure discoveries, timer updates, and threat scans with 60-second background auto-synchronization.

- **🛰️ Non-ESI Structure & Fitting Ingestion**:
  - **Smart Multi-Format Parser**: Automatically classifies D-Scans, structure show-info copy-pastes, and EFT fitting blocks.
  - **SDE Asset Identification**: Resolves structure types, celestial locations, owner tickers, quantum core statuses, and fitted modules via `eve_sde`.
  - **Interactive Module Inspection**: Corptools-inspired High, Medium, Low, Rig, and Service slot modals with official CCP asset icons and 1-click **Copy EFT to Clipboard** export for Pyfa and EVE Online.

- **⚔️ Sovereignty Campaign & Contest Timers**:
  - **Dynamic Real-Time Polling**: Tracks active and upcoming ADM sovereignty campaigns with 60-second change detection polling and live DOM updates.
  - **Contest Score Donuts**: Visual 60/40 circular donut charts, ongoing contest indicators, and defender/attacker score tracking.
  - **Staging Proximity Routing**: Real-time gate jump counts and jump drive light-year calculations from your home staging base.

- **⏱️ Structure Reinforcement Timers Board**:
  - Dedicated timer board tracking hostile armor and hull vulnerability windows.
  - Live client-side ticking countdowns with multi-layer ambient tactical pulse glows for imminent timers.
  - One-click Fleet Commander (FC) Discord / broadcast ping generator formatted with target system, structure type, and time remaining.

- **🎯 Local Threat Scanner & Drop Risk Engine**:
  - **Asynchronous Pilot Intelligence Streaming**: Non-blocking local chat paste ingestion that eliminates HTTP gateway timeouts by streaming pilot corporation, alliance, top flown ships, and loss data in the background.
  - **Drop Probability Algorithms**: Evaluates pilot combat histories, cyno loss records, and zKillboard tags to estimate real-time Black Ops (Blops) and Capital drop risks.
  - **Grid Anchor & Ship Composition**: Combines Local chat rosters with D-Scan items to classify grid composition and identify hostile FCs.

- **📡 Tactical Sitreps & Gate Camp Ingestion**:
  - **Time Seen & Freshness Tracking**: UTC timestamps with 1-click "Now" and relative time offsets (`-15m`, `-30m`, `-1h`, `-2h`, `-4h`).
  - **Interactive Threat Classification Tags**: Preselectable pills (`Gate Camp`, `Bubbles on Gate`, `Smartbomb Trap`, `Blops Hunter`, `Capital Fleet`, `Cyno Beacon`, `Roving Gang`) with on-the-fly custom tag creation.
  - **Integrated Local & D-Scan Scanner**: Submitting camp sitreps allows optional Local chat and D-Scan pastes, generating linked `LocalThreatScan` records and auto-applying threat tags.
  - **1-Click FC Ping Generator**: Instant Discord-formatted broadcast pings detailing system, freshness, threat level, bubble status, and linked threat analysis.

- **👤 Hostile Pilot Dossiers Registry**:
  - Comprehensive database tracking hostile cyno alts, supercarrier/titan pilots, fleet commanders, and scouts.
  - Searchable across character names, corporations, alliances, alt associations, notes, and behavioral tags with multi-role category filtering.

- **🏛️ Alliances, Corporations & Coalitions Profiling**:
  - Automated ESI and zKillboard tracking of hostile coalition hierarchies, sovereignty footprints, and member counts.
  - Primary operational timezone profiling (`USTZ`, `EUTZ`, `AUTZ`) derived from vulnerability distributions and 7x24 activity heatmaps.
  - Formup potential estimations for subcap, capital, and supercapital fleets.

- **🚀 Hostile Staging Hubs & Fleet Doctrines**:
  - Track forward deployment bases, lowsec staging hubs, and nullsec citadels outside target sovereignty.
  - Catalog fleet doctrines (Mainline Fleet, Skirmish / HACs, Capital / Super, Home Defense, Roaming) with example fittings, zKillboard killmail references, and interactive slot inspection.

- **🔒 Tiered Moderation & Audit Logging**:
  - Role-based access control with granular permissions (`basic_access`, `manage_intel`, `officer_access`).
  - Immutable audit logs recording all operator creations, edits, and deletions.

---

## Installation

### 1. Install Package

Install the package into your Alliance Auth virtual environment:

```bash
pip install aa-hostile-intel
```

### 2. Configure Alliance Auth Settings

In your `local.py` settings file, add `hostile` to `INSTALLED_APPS`:

```python
INSTALLED_APPS += [
    "hostile",
]
```

### 3. Configure Periodic Celery Tasks

Add the periodic tasks to `CELERYBEAT_SCHEDULE` in `local.py`:

```python
from celery.schedules import crontab

# Periodic ESI Sovereignty & Structure Map Synchronization (Every 2 Hours)
CELERYBEAT_SCHEDULE["hostile_update_sovereignty_intelligence"] = {
    "task": "hostile.tasks.update_sovereignty_intelligence",
    "schedule": crontab(minute="0", hour="*/2"),
}

# Periodic Active Sovereignty Campaign Synchronization (Every 15 Minutes)
CELERYBEAT_SCHEDULE["hostile_update_sovereignty_campaigns"] = {
    "task": "hostile.tasks.update_sovereignty_campaigns",
    "schedule": crontab(minute="*/15"),
}

# Periodic zKillboard Entity Statistics & Member Counts (Every 6 Hours)
CELERYBEAT_SCHEDULE["hostile_update_all_zkill_stats"] = {
    "task": "hostile.tasks.update_all_zkill_stats",
    "schedule": crontab(minute="30", hour="*/6"),
}
```

### 4. Run Migrations & Collect Static Files

```bash
python manage.py migrate
python manage.py collectstatic --noinput
```

### 5. Restart Services

Restart your Alliance Auth supervisor or systemd services:

```bash
supervisorctl restart all
```

---

## Configuration Settings

You can customize app behavior by adding the following optional settings to your `local.py`:

| Setting | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `HOSTILE_SOV_SYNC_MINUTES` | `int` | `60` | Interval (in minutes) for caching ESI sovereignty map and structure vulnerability syncs. |
| `HOSTILE_CAMPAIGN_SYNC_MINUTES` | `int` | `15` | Interval (in minutes) for caching active ESI sovereignty campaign syncs. |
| `HOSTILE_AUTO_VERIFY_OFFICER_POSTS` | `bool` | `True` | Automatically marks system observations submitted by users with `hostile.manage_intel` as verified. |
| `HOSTILE_MAX_ACTIVE_OBSERVATION_DAYS` | `int` | `7` | Number of days before unverified or inactive system observations are pruned from active feeds. |
| `HOSTILE_ZKILL_ENABLED` | `bool` | `True` | Enables automated background zKillboard statistics and pilot intelligence queries. |
| `HOSTILE_ZKILL_SYNC_HOURS` | `int` | `6` | Cache duration and sync interval for entity-level zKillboard statistics. |
| `HOSTILE_ZKILL_MIN_REQUEST_INTERVAL` | `float` | `0.5` | Minimum delay in seconds between successive zKillboard API calls to ensure respectful rate limiting. |
| `HOSTILE_ZKILL_USER_AGENT` | `str` | `None` | Custom User-Agent header for zKillboard requests (defaults to auto-generated AA Hostile Intel User-Agent). |
| `HOSTILE_MAIN_ALLIANCE_ID` | `int` | `None` | Friendly / home alliance ID (defaults to `ALLIANCE_ID` if defined). Used for threat filtering. |
| `HOSTILE_MAIN_ALLIANCE_NAME` | `str` | `None` | Friendly / home alliance name (defaults to `ALLIANCE_NAME` if defined). |
| `HOSTILE_IGNORED_ALLIANCE_IDS` | `list[int]` | `[]` | List of alliance IDs to exclude from hostile threat tracking and classification. |
| `HOSTILE_IGNORED_ALLIANCE_NAMES` | `list[str]` | `[]` | List of alliance names to exclude from hostile tracking. |
| `HOSTILE_IGNORED_ALLIANCE_TICKERS` | `list[str]` | `[]` | List of alliance tickers to exclude from hostile tracking. |
| `HOSTILE_IGNORED_CORP_IDS` | `list[int]` | `[]` | List of corporation IDs to exclude from hostile tracking. |
| `HOSTILE_IGNORED_CORP_NAMES` | `list[str]` | `[]` | List of corporation names to exclude from hostile tracking. |

---

## Permissions

AA Hostile Intel provides three granular permission levels:

| Permission | Description | Recommended Roles |
| :--- | :--- | :--- |
| `hostile.basic_access` | View access to all intelligence catalog pages, dashboards, and timers. Can create new alliances, member/alt corporations, structures, timers, pilot dossiers, stagings, fleet doctrines, and tactical sitreps. Can edit their own submissions. Cannot delete any entries or view audit logs. | General Members, Scouts, Line Pilots |
| `hostile.manage_intel` | Full operational management: can edit all submissions regardless of author, delete any intelligence records (structures, timers, sitreps, pilots, stagings, doctrines, scans), verify and pin sitreps, manage coalitions, and view the complete audit log trail. | Fleet Commanders, Intel Scouts, Moderators |
| `hostile.officer_access` | Senior intel officer access: bulk database operations and advanced system reconfiguration. | Intel Directors, Lead FCs, Auth Admins |

---

## Changelog

See [CHANGELOG.md](https://github.com/BroodLK/aa-hostile-intel/blob/master/CHANGELOG.md) for details on changes in each release.

---

## License

This project is licensed under the GNU General Public License v3.0 (GPLv3). See the [LICENSE](LICENSE) file for details.
