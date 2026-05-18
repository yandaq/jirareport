# Jira Report

A single-page HTML report for Jira Cloud that shows **Aged WIP** (how long each in-progress issue has been active) and **Jira Hygiene** (orphaned stories and epics) across all your projects.

## Requirements

- Python 3.10+
- [`requests`](https://pypi.org/project/requests/) library

## Installation

```bash
pip install requests
```

## Configuration

Credentials are stored in a `.env` file in the same directory as the script. If the file is missing or incomplete, the script runs an interactive setup wizard on first run to collect and save them.

The three required values are:

| Key | Description |
|---|---|
| `JIRA_URL` | Your Jira base URL, e.g. `https://yourcompany.atlassian.net` |
| `JIRA_EMAIL` | The email address of your Jira account |
| `JIRA_PAT` | A Jira API token |

To generate an API token: go to [https://id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens) → **Create API token**.

You can also create the `.env` file manually:

```
JIRA_URL=https://yourcompany.atlassian.net
JIRA_EMAIL=you@example.com
JIRA_PAT=your_api_token_here
```

## Usage

```bash
python3 jira_cttd.py              # generates index.html
python3 jira_cttd.py --output report.html  # custom output path
open index.html                   # open in browser (macOS)
```

## Report columns

| Column | Description |
|---|---|
| **Total WIP** | Count of in-progress issues for the project; click to open in Jira |
| **91+, 61–90, 31–60, 15–30, 8–14, 0–7** | Age bins (days in current In Progress state); each coloured square represents one issue — green ≤14d, amber ≤30d, red >30d; hover for details, click to open the issue |
| **Blockers** | Issues that are labelled Blocked/Blocker/Onhold/On-Hold/On Hold, are Flagged, or are in a status named Blocked/Blockers/Onhold/On-Hold/On Hold; click a non-zero count to view them in Jira |
| **Orphaned Stories** | Stories/Tasks with no parent epic; click a non-zero count to view them in Jira |
| **Orphaned Epics** | Epics with no parent; click a non-zero count to view them in Jira |
