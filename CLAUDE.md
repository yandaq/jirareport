# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Rule 1 — Think Before Coding
State assumptions explicitly. Ask rather than guess.
Push back when a simpler approach exists. Stop when confused.

## Rule 2 — Simplicity First
Minimum code that solves the problem. Nothing speculative.
No abstractions for single-use code.

## Rule 3 — Surgical Changes
Touch only what you must. Don't improve adjacent code.
Match existing style. Don't refactor what isn't broken.

## Rule 4 — Goal-Driven Execution
Define success criteria. Loop until verified.
Strong success criteria let Claude loop


## Running the script

Requires Python 3.10+ and `requests`: `python3 -m pip install requests --break-system-packages`

```bash
python3 jira_cttd.py              # generates index.html
python3 jira_cttd.py --output report.html  # custom output path
open index.html                   # view the report
```

## Credentials

Stored in `.env` (gitignored). If missing or incomplete, the script runs an interactive wizard on startup to collect and save them. The three required keys are `JIRA_URL`, `JIRA_EMAIL`, and `JIRA_PAT`.

## Architecture

**CTTD** = Cycle Time to Date — the number of days an issue has been continuously in an "In Progress" status category.

Single-file script (`jira_cttd.py`) with no framework. Execution flows through three phases:

**1. Data collection** (`collect_data`)
- `get_projects()` — paginates `/rest/api/3/project/search`
- Per project: `get_in_progress_status_names()` fetches `/rest/api/3/project/{key}/statuses` to build a set of status names in the "In Progress" category (changelog items expose status names, not categories, so this mapping is necessary)
- `get_in_progress_issues()` — JQL via `/rest/api/3/search/jql` filtering `issuetype in (Story, Task, Bug)` and `statusCategory = "In Progress"`
- `get_cttd()` — walks the full paginated changelog for each issue to find the timestamp of the **latest uninterrupted entry** into an In Progress status; if the issue has since left In Progress, that entry is discarded (latest_entry_dt reset to None) so only the current continuous run is measured; falls back to issue creation date if no matching transition exists

**2. HTML generation** (`generate_html`)
- Pure Python string templating, no external template engine
- Output has two tabs: **Aged WIP** and **Jira Hygiene**, both fully implemented
- Tab 1 (Aged WIP): CSS Grid layout with fixed project-name column + one column per time bin; bins run oldest-left → newest-right: `91+ | 61–90 | 31–60 | 15–30 | 8–14 | 0–7` days; cards are 10×10px coloured squares (green ≤14d, amber ≤30d, red >30d) with tooltip showing key, summary, and CTTD; clicking opens the Jira issue
- Tab 2 (Jira Hygiene): CSS Grid table with one row per project and two columns — **Orphaned Stories** (Story/Task with no parent) and **Orphaned Epics** (Epic with no parent); clicking a non-zero count opens Jira's issue search filtered to those orphans

**Shared pagination primitive** (`_paginate_jql`)
- Used by `get_in_progress_issues`, `_count_orphans_client_side`, and `_count_orphan_epics_client_side`
- Uses cursor-based pagination via `nextPageToken` (Jira's newer `/rest/api/3/search/jql` endpoint)

**2b. Orphan counting** (`get_orphan_count`, `get_orphan_epic_count`)
- Both helpers do **client-side counting only**: they paginate the project's issues via `_paginate_jql` requesting `fields=parent,issuetype`, then filter locally for the relevant issuetypes with `parent is None`. Server-side JQL `parent is EMPTY` is deliberately avoided at fetch time because it 400s on some team-managed Jira configurations
- The search URL returned alongside each count is a reconstructed JQL string (e.g. `project = "X" AND parent is EMPTY AND issuetype in (Story, Task)`) used purely as a click-through; the count itself never depends on that JQL being valid server-side

**3. HTTP layer** (`api_get`)
- Shared `requests.Session` with Basic auth (`JIRA_EMAIL:JIRA_PAT`)
- Retries on 429 (respects `Retry-After`) and 5xx with exponential backoff
- Per-project errors (403/404) are caught and skipped with a warning
- Changelog pagination uses two endpoints: the initial fetch embeds changelog via `?expand=changelog` on the issue endpoint; subsequent pages use `/rest/api/3/issue/{key}/changelog` directly

## Key constants

Defined near the top of `jira_cttd.py` — adjust these without touching logic:

| Constant | Purpose |
|---|---|
| `GREEN_MAX_DAYS` / `AMBER_MAX_DAYS` | Colour thresholds |
| `BINS` | Column definitions (label, min, max days) |
| `SHOW_EMPTY_PROJECTS` | Whether to render projects with no in-progress issues |
| `MAX_RETRIES` / `RATE_LIMIT_SLEEP` | API retry behaviour |
| `REQUEST_TIMEOUT` | Per-request timeout in seconds (default 30) |
