# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Rules

**Rule 1 — Think Before Coding**: State assumptions explicitly. Ask rather than guess. Push back when a simpler approach exists. Stop when confused.

**Rule 2 — Simplicity First**: Minimum code that solves the problem. Nothing speculative. No abstractions for single-use code.

**Rule 3 — Surgical Changes**: Touch only what you must. Don't improve adjacent code. Match existing style. Don't refactor what isn't broken.

**Rule 4 — Goal-Driven Execution**: Define success criteria. Loop until verified.

## Running the script

```bash
python3 jira_cttd.py              # generates index.html
python3 jira_cttd.py --output report.html  # custom output path
open index.html
```

Requires Python 3.10+ and `requests`: `pip install requests`

Credentials are read from `.env` (`JIRA_URL`, `JIRA_EMAIL`, `JIRA_PAT`). If missing, an interactive wizard runs on first launch to create it. The PAT prompt uses `getpass` (no terminal echo).

## Architecture

Single-file script (`jira_cttd.py`), no framework. Three phases:

**1. Data collection** (`collect_data`)
- `get_projects()` — paginates `/rest/api/3/project/search`
- `get_in_progress_status_names()` — fetches `/rest/api/3/project/{key}/statuses` to map status names → "In Progress" category (changelogs expose names, not categories)
- `get_in_progress_issues()` — JQL filtering `issuetype in (Story, Task, Bug)` and `statusCategory = "In Progress"`
- `get_cttd()` — walks the full paginated changelog to find the latest **uninterrupted** entry into an In Progress status; resets if the issue left In Progress; falls back to creation date
- `get_orphan_count()` / `get_orphan_epic_count()` — client-side counting only (server-side `parent is EMPTY` JQL 400s on team-managed projects); returns both count and a JQL string used purely as a click-through URL
- `get_blocker_count()` — JQL-based count of issues matching blocker labels, blocker statuses, or `flagged is not EMPTY`; falls back to a JQL without the `flagged` clause if the field isn't supported (400)

**2. HTML generation** (`generate_html`)
- Pure Python string templating; outputs a single self-contained HTML page (no tabs)
- CSS Grid layout: fixed 220px project-name column, 80px Total WIP column, one `1fr` column per age bin, then three `1fr` columns (Blockers, Orphaned Stories, Orphaned Epics)
- Age bins run oldest-left → newest-right: `91+ | 61–90 | 31–60 | 15–30 | 8–14 | 0–7` days
- Cards are 10×10px coloured squares (green ≤14d, amber ≤30d, red >30d) with tooltip; clicking opens the Jira issue
- Blocker/orphan cells show a count linking to Jira search, or `—` if zero; rendered by shared `_orphan_cell_html()`

**3. HTTP layer** (`api_get`)
- Shared `requests.Session` with Basic auth; retries on 429 (respects `Retry-After`) and 5xx with exponential backoff
- Per-project 403/404 errors are caught and skipped
- Changelog pagination: initial fetch via `?expand=changelog`; subsequent pages via `/rest/api/3/issue/{key}/changelog`
- `_paginate_jql` — shared cursor-based pagination primitive using `nextPageToken`

## Key constants

Near the top of `jira_cttd.py` — safe to adjust without touching logic:

| Constant | Purpose |
|---|---|
| `GREEN_MAX_DAYS` / `AMBER_MAX_DAYS` | Colour thresholds |
| `BINS` | Column definitions (label, min, max days) |
| `BLOCKER_LABELS` / `BLOCKER_STATUSES` | Sets of lowercase label/status names treated as blockers |
| `SHOW_EMPTY_PROJECTS` | Whether to render projects with no in-progress issues |
| `MAX_RETRIES` / `RATE_LIMIT_SLEEP` | API retry behaviour |
| `REQUEST_TIMEOUT` | Per-request timeout in seconds (default 30) |
