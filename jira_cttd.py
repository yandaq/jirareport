import datetime
import getpass
import html
import os
import re
import sys
import time
import urllib.parse
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Configuration — loaded from .env
# ---------------------------------------------------------------------------

def _load_env(path: str = ".env", force: bool = False) -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if force or not os.environ.get(key):
            os.environ[key] = value

_load_env()


def _credentials_wizard(env_path: Path) -> None:
    existing = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            existing[k.strip()] = v.strip()

    print("\n=== Jira Credentials Setup ===")
    print(f"Writing to: {env_path.resolve()}\n")

    token_url = "https://id.atlassian.com/manage-profile/security/api-tokens"
    token_link = f"\033]8;;{token_url}\033\\{token_url}\033]8;;\033\\"
    fields = [
        ("JIRA_URL",   "Jira instance URL", "https://your-org.atlassian.net", ""),
        ("JIRA_EMAIL", "Atlassian account email", "you@example.com", ""),
        ("JIRA_PAT",   "Personal Access Token (API token)", "", f"  Generate one at: {token_link}"),
    ]

    updated = dict(existing)
    for key, label, placeholder, note in fields:
        current = existing.get(key, "")
        if current:
            hint = f" [{current[:6]}{'*' * max(0, len(current) - 6)}]" if key == "JIRA_PAT" else f" [{current}]"
        else:
            hint = f" (e.g. {placeholder})" if placeholder else ""
        if note:
            print(note)
        try:
            prompt = f"{label}{hint}: "
            value = (getpass.getpass(prompt) if key == "JIRA_PAT" else input(prompt)).strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            sys.exit(1)
        updated[key] = value if value else current

    missing = [k for k, _, _, _ in fields if not updated.get(k)]
    if missing:
        print(f"\nError: missing required values: {', '.join(missing)}")
        sys.exit(1)

    lines = [f"{k}={v}" for k, v in updated.items()]
    env_path.write_text("\n".join(lines) + "\n")
    print(f"\n.env saved. Re-loading credentials...\n")
    _load_env(str(env_path), force=True)


_env_path = Path(".env")
JIRA_URL   = os.environ.get("JIRA_URL",   "")
PAT        = os.environ.get("JIRA_PAT",   "")
JIRA_EMAIL = os.environ.get("JIRA_EMAIL", "")

if not all([JIRA_URL, PAT, JIRA_EMAIL]):
    _credentials_wizard(_env_path)
    JIRA_URL   = os.environ.get("JIRA_URL",   "")
    PAT        = os.environ.get("JIRA_PAT",   "")
    JIRA_EMAIL = os.environ.get("JIRA_EMAIL", "")
    if not all([JIRA_URL, PAT, JIRA_EMAIL]):
        sys.exit("Error: credentials still incomplete after setup.")

GREEN_MAX_DAYS = 14   # <= green
AMBER_MAX_DAYS = 30   # <= amber; > red

# Columns: oldest on the LEFT, newest on the RIGHT
BINS = [
    ("91+ days",   91, None),
    ("61–90 days", 61, 90),
    ("31–60 days", 31, 60),
    ("15–30 days", 15, 30),
    ("8–14 days",  8,  14),
    ("0–7 days",   0,  7),
]

SHOW_EMPTY_PROJECTS = False
MAX_RETRIES = 3
RATE_LIMIT_SLEEP = 60
REQUEST_TIMEOUT = 30

# ---------------------------------------------------------------------------
# HTTP session
# ---------------------------------------------------------------------------

_session: requests.Session | None = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.auth = (JIRA_EMAIL, PAT)
        _session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
    return _session


def api_get(path: str, params: dict | None = None) -> dict:
    url = f"{JIRA_URL}{path}"
    session = _get_session()
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            if attempt == MAX_RETRIES:
                raise RuntimeError(f"Network error fetching {url}: {exc}") from exc
            time.sleep(2 ** attempt)
            continue

        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", RATE_LIMIT_SLEEP))
            print(f"  Rate limited — sleeping {wait}s", file=sys.stderr)
            time.sleep(wait)
            continue
        if resp.status_code >= 500:
            if attempt == MAX_RETRIES:
                resp.raise_for_status()
            time.sleep(2 ** attempt)
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError(f"Failed GET {url} after {MAX_RETRIES} attempts")

# ---------------------------------------------------------------------------
# Jira helpers
# ---------------------------------------------------------------------------

def parse_jira_datetime(s: str) -> datetime.datetime:
    # Normalize +0000 → +00:00 (Python 3.6 fromisoformat quirk)
    s = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", s)
    # Strip fractional seconds if needed for older Pythons
    s = re.sub(r"\.\d+(?=[+-])", "", s)
    return datetime.datetime.fromisoformat(s)


def get_projects() -> list[dict]:
    projects = []
    start_at = 0
    max_results = 50
    while True:
        data = api_get("/rest/api/3/project/search", params={
            "startAt": start_at,
            "maxResults": max_results,
            "orderBy": "name",
        })
        projects.extend(data.get("values", []))
        if data.get("isLast", True) or len(data.get("values", [])) < max_results:
            break
        start_at += max_results
    return projects


def get_in_progress_status_names(project_key: str) -> set[str]:
    try:
        data = api_get(f"/rest/api/3/project/{project_key}/statuses")
    except requests.HTTPError:
        return set()
    names: set[str] = set()
    for issue_type in data:
        for status in issue_type.get("statuses", []):
            if status.get("statusCategory", {}).get("name") == "In Progress":
                names.add(status["name"])
    return names


def _paginate_jql(jql: str, fields: str):
    """Yield issues from /rest/api/3/search/jql using cursor pagination."""
    next_page_token: str | None = None
    while True:
        params: dict = {
            "jql": jql,
            "maxResults": 100,
            "fields": fields,
        }
        if next_page_token:
            params["nextPageToken"] = next_page_token
        data = api_get("/rest/api/3/search/jql", params=params)
        for issue in data.get("issues", []):
            yield issue
        next_page_token = data.get("nextPageToken")
        if not next_page_token or data.get("isLast"):
            break


def get_in_progress_issues(project_key: str) -> list[dict]:
    jql = (
        f'project = "{project_key}" '
        f'AND issuetype in (Story, Task, Bug) '
        f'AND statusCategory = "In Progress"'
    )
    return list(_paginate_jql(jql, "summary,issuetype,status,created"))


def _count_orphans_client_side(project_key: str) -> int:
    """Count non-epic, non-subtask issues with no parent by fetching and filtering locally."""
    jql = f'project = "{project_key}"'
    count = 0
    for issue in _paginate_jql(jql, "parent,issuetype"):
        fields = issue.get("fields") or {}
        itype = fields.get("issuetype") or {}
        if itype.get("name") not in ("Story", "Task"):
            continue
        if fields.get("parent") is None:
            count += 1
    return count


def get_orphan_count(project_key: str) -> tuple[int, str]:
    count = _count_orphans_client_side(project_key)
    search_jql = f'project = "{project_key}" AND parent is EMPTY AND issuetype in (Story, Task)'
    return count, search_jql


def _count_orphan_epics_client_side(project_key: str) -> int:
    """Count epics with no parent by fetching and filtering locally."""
    jql = f'project = "{project_key}" AND issuetype in (Epic)'
    count = 0
    for issue in _paginate_jql(jql, "parent"):
        fields = issue.get("fields") or {}
        if fields.get("parent") is None:
            count += 1
    return count


def get_orphan_epic_count(project_key: str) -> tuple[int, str]:
    count = _count_orphan_epics_client_side(project_key)
    search_jql = f'project = "{project_key}" AND issuetype in (Epic) AND parent is EMPTY'
    return count, search_jql


def get_all_changelog_histories(issue_key: str) -> list[dict]:
    data = api_get(f"/rest/api/3/issue/{issue_key}", params={"expand": "changelog"})
    changelog = data.get("changelog", {})
    histories = list(changelog.get("histories", []))
    total = changelog.get("total", len(histories))

    start_at = len(histories)
    while start_at < total:
        page = api_get(f"/rest/api/3/issue/{issue_key}/changelog", params={
            "startAt": start_at,
            "maxResults": 100,
        })
        batch = page.get("values", [])
        if not batch:
            break
        histories.extend(batch)
        start_at += len(batch)

    return histories


def get_cttd(issue_key: str, in_progress_status_names: set[str]) -> int | None:
    histories = get_all_changelog_histories(issue_key)

    # Walk changelog chronologically. Reset the entry timestamp each time the
    # item leaves In Progress, so we measure only the current uninterrupted run.
    latest_entry_dt: datetime.datetime | None = None

    for history in sorted(histories, key=lambda h: h["created"]):
        for item in history.get("items", []):
            if item.get("field") != "status":
                continue
            entered = item.get("toString") in in_progress_status_names
            left    = item.get("fromString") in in_progress_status_names
            if entered and not left:
                latest_entry_dt = parse_jira_datetime(history["created"])
            elif left and not entered:
                latest_entry_dt = None  # item left In Progress; discard previous entry

    if latest_entry_dt is None:
        return None

    today = datetime.date.today()
    return (today - latest_entry_dt.date()).days

# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

def collect_data() -> list[dict]:
    print("Fetching projects...")
    projects = get_projects()
    print(f"Found {len(projects)} projects.")

    result = []
    for proj in projects:
        key = proj["key"]
        name = proj["name"]
        print(f"\nProject: {key} — {name}")

        try:
            in_progress_names = get_in_progress_status_names(key)
            raw_issues = get_in_progress_issues(key)
        except requests.HTTPError as exc:
            print(f"  Skipping — HTTP {exc.response.status_code}", file=sys.stderr)
            continue

        if not raw_issues:
            print("  No in-progress issues.")

        orphan_count, orphan_jql = get_orphan_count(key)
        orphan_epic_count, orphan_epic_jql = get_orphan_epic_count(key)

        issues_out = []
        for issue in raw_issues:
            issue_key = issue["key"]
            summary = issue["fields"]["summary"]
            print(f"  {issue_key}: {summary[:60]}")

            try:
                cttd = get_cttd(issue_key, in_progress_names)
            except Exception as exc:
                print(f"    Warning: could not fetch changelog — {exc}", file=sys.stderr)
                cttd = None

            if cttd is None:
                # Fallback: use creation date
                created_str = issue["fields"].get("created", "")
                if created_str:
                    dt = parse_jira_datetime(created_str)
                    cttd = (datetime.date.today() - dt.date()).days
                else:
                    cttd = 0
                print(f"    CTTD (fallback to created): {cttd}d")
            else:
                print(f"    CTTD: {cttd}d")

            issues_out.append({
                "key": issue_key,
                "summary": summary,
                "cttd_days": cttd,
                "url": f"{JIRA_URL}/browse/{issue_key}",
            })

        result.append({
            "key": key,
            "name": name,
            "issues": issues_out,
            "orphan_count": orphan_count,
            "orphan_jql": orphan_jql,
            "orphan_epic_count": orphan_epic_count,
            "orphan_epic_jql": orphan_epic_jql,
        })

    return result

# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

def card_color(days: int) -> str:
    if days <= GREEN_MAX_DAYS:
        return "#16a34a"
    if days <= AMBER_MAX_DAYS:
        return "#d97706"
    return "#dc2626"


def card_text_color(days: int) -> str:
    return "#ffffff"


def build_card(issue: dict) -> str:
    color = card_color(issue["cttd_days"])
    esc_key = html.escape(issue["key"])
    esc_summary = html.escape(issue["summary"])
    truncated = html.escape(issue["summary"][:32] + ("…" if len(issue["summary"]) > 32 else ""))
    esc_url = html.escape(issue["url"])
    tooltip = html.escape(f'{issue["key"]}: {issue["summary"]} ({issue["cttd_days"]}d)')

    return (
        f'<a class="card" href="{esc_url}" target="_blank" rel="noopener noreferrer" '
        f'style="background:{color}" title="{tooltip}">'
        f'<span class="card-key">{esc_key}</span>'
        f'<span class="card-summary">{truncated}</span>'
        f'<span class="card-days">{issue["cttd_days"]}d</span>'
        f'</a>'
    )


def bucket_issues(issues: list[dict]) -> list[list[dict]]:
    buckets = [[] for _ in BINS]
    for issue in issues:
        d = issue["cttd_days"]
        for i, (_, lo, hi) in enumerate(BINS):
            if d >= lo and (hi is None or d <= hi):
                buckets[i].append(issue)
                break
    return buckets


def generate_html(data: list[dict]) -> str:
    report_date = datetime.date.today().isoformat()
    num_bins = len(BINS)
    grid_cols = f"220px 80px repeat({num_bins}, 1fr) 1fr 1fr"

    def _orphan_cell_html(count: int, jql: str, parity: str) -> str:
        if count > 0 and jql:
            search_url = html.escape(f"{JIRA_URL}/issues/?jql={urllib.parse.quote(jql)}")
            inner = (
                f'<a class="orphan-link" href="{search_url}" '
                f'target="_blank" rel="noopener noreferrer">{count}</a>'
            )
        elif count > 0:
            inner = f'<span class="orphan-count">{count}</span>'
        else:
            inner = '<span class="empty">—</span>'
        return f'<div class="cell row-{parity}">{inner}</div>'

    # Header row
    header_cells = ['<div class="hdr hdr-label">Project</div>', '<div class="hdr">Total WIP</div>']
    for label, _, _ in BINS:
        header_cells.append(f'<div class="hdr">{html.escape(label)}</div>')
    header_cells.append('<div class="hdr">Orphaned Stories</div>')
    header_cells.append('<div class="hdr">Orphaned Epics</div>')
    header_html = "\n    ".join(header_cells)

    # Data rows
    row_parts = []
    for idx, project in enumerate(data):
        if not project["issues"] and not SHOW_EMPTY_PROJECTS:
            continue
        parity = "even" if idx % 2 == 0 else "odd"
        proj_name = html.escape(project["name"])
        proj_key = html.escape(project["key"])
        count = len(project["issues"])

        wip_jql = (
            f'project = "{project["key"]}" AND issuetype in (Story, Task, Bug)'
            f' AND statusCategory = "In Progress"'
        )
        wip_url = html.escape(f"{JIRA_URL}/issues/?jql={urllib.parse.quote(wip_jql)}")
        wip_cell = (
            f'<div class="cell wip-cell row-{parity}">'
            f'<a class="wip-link" href="{wip_url}" target="_blank" rel="noopener noreferrer">{count}</a>'
            f'</div>'
        )

        row_cells = [
            f'<div class="proj-label row-{parity}">'
            f'<span class="proj-name">{proj_name}</span>'
            f'<span class="proj-key">{proj_key}</span>'
            f'</div>',
            wip_cell,
        ]

        buckets = bucket_issues(project["issues"])
        for i, bucket in enumerate(buckets):
            if bucket:
                cards_html = "\n".join(build_card(issue) for issue in bucket)
                cell_content = cards_html
            else:
                cell_content = '<span class="empty">—</span>'
            row_cells.append(
                f'<div class="cell row-{parity}">{cell_content}</div>'
            )

        row_cells.append(_orphan_cell_html(project.get("orphan_count", 0), project.get("orphan_jql", ""), parity))
        row_cells.append(_orphan_cell_html(project.get("orphan_epic_count", 0), project.get("orphan_epic_jql", ""), parity))

        row_parts.append("\n    ".join(row_cells))

    rows_html = "\n    ".join(row_parts)

    total_issues = sum(len(p["issues"]) for p in data)
    total_projects = len(data)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Jira Flow Report — {report_date}</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    margin: 0; background: #f1f5f9; color: #1e293b;
  }}
  /* ── Content ── */
  h1 {{ font-size: 1.3rem; margin: 0 0 4px; }}
  .meta {{ font-size: 0.8rem; color: #64748b; margin-bottom: 16px; }}
  .legend {{
    display: flex; gap: 16px; margin-bottom: 16px; align-items: center;
    font-size: 0.78rem;
  }}
  .legend-item {{ display: flex; align-items: center; gap: 5px; }}
  .legend-dot {{ width: 12px; height: 12px; border-radius: 3px; flex-shrink: 0; }}
  .grid-wrapper {{ overflow-x: auto; border-radius: 10px; box-shadow: 0 1px 4px rgba(0,0,0,.12); }}
  .grid {{
    display: grid;
    grid-template-columns: {grid_cols};
    border: 1px solid #e2e8f0;
    border-radius: 10px;
    overflow: hidden;
    min-width: 900px;
    background: #fff;
  }}
  .hdr {{
    background: #1e293b; color: #f8fafc;
    padding: 10px 8px; font-size: 0.72rem; font-weight: 700;
    text-align: center; border-right: 1px solid #334155;
    letter-spacing: 0.03em; text-transform: uppercase;
  }}
  .hdr:last-child {{ border-right: none; }}
  .hdr-label {{ text-align: left; padding-left: 12px; }}
  .proj-label {{
    padding: 10px 12px; font-size: 0.8rem;
    border-right: 1px solid #e2e8f0; border-top: 1px solid #e2e8f0;
    display: flex; flex-direction: column; justify-content: center; gap: 2px;
  }}
  .proj-name {{ font-weight: 600; color: #1e293b; }}
  .proj-key {{ font-size: 0.7rem; color: #94a3b8; }}
  .cell {{
    padding: 8px 6px; border-right: 1px solid #e2e8f0; border-top: 1px solid #e2e8f0;
    display: flex; flex-wrap: wrap; gap: 2px; align-content: flex-start;
    min-height: 20px;
  }}
  .cell:last-child {{ border-right: none; }}
  .row-even .proj-label, .row-even .cell {{ background: #f8fafc; }}
  .row-odd  .proj-label, .row-odd  .cell  {{ background: #ffffff; }}
  .card {{
    display: inline-block; text-decoration: none;
    width: 10px; height: 10px; border-radius: 2px;
    cursor: pointer; flex-shrink: 0;
    transition: opacity 0.15s, transform 0.1s;
  }}
  .card:hover {{ opacity: 0.75; transform: scale(1.4); }}
  .card-key, .card-summary, .card-days {{ display: none; }}
  .empty {{ color: #cbd5e1; font-size: 0.72rem; align-self: center; padding: 2px 4px; }}
  /* ── Placeholder ── */
  .placeholder {{
    display: flex; align-items: center; justify-content: center;
    height: 300px; color: #94a3b8; font-size: 0.9rem;
    border: 2px dashed #e2e8f0; border-radius: 10px; background: #fff;
  }}
  .orphan-link {{ color: #dc2626; font-weight: 700; text-decoration: none; }}
  .orphan-link:hover {{ text-decoration: underline; }}
  .orphan-count {{ color: #dc2626; font-weight: 700; }}
  .wip-cell {{ justify-content: center; }}
  .wip-link {{ font-weight: 700; color: #1e293b; text-decoration: none; font-size: 0.9rem; }}
  .wip-link:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<div style="padding: 20px;">
  <h1>Jira Flow Report</h1>
  <p class="meta">Generated: {report_date} &nbsp;&bull;&nbsp; {total_projects} project{"s" if total_projects != 1 else ""} &nbsp;&bull;&nbsp; {total_issues} in-progress item{"s" if total_issues != 1 else ""}</p>
  <div class="legend">
    <strong style="font-size:0.78rem">Colour key:</strong>
    <div class="legend-item"><div class="legend-dot" style="background:#16a34a"></div> Green &mdash; 0&ndash;{GREEN_MAX_DAYS} days</div>
    <div class="legend-item"><div class="legend-dot" style="background:#d97706"></div> Amber &mdash; {GREEN_MAX_DAYS + 1}&ndash;{AMBER_MAX_DAYS} days</div>
    <div class="legend-item"><div class="legend-dot" style="background:#dc2626"></div> Red &mdash; &gt;{AMBER_MAX_DAYS} days</div>
  </div>
  <div class="grid-wrapper">
    <div class="grid">
      {header_html}
      {rows_html}
    </div>
  </div>
</div>
<footer style="margin-top:2rem;padding:1rem 1.5rem;border-top:1px solid #e5e7eb;text-align:center;color:#6b7280;font-size:0.8rem;">
  <p>This Jira WIP &amp; hygiene report was built by <a href="https://www.solutioneers.co.uk" target="_blank" rel="noopener" style="color:#2563eb;text-decoration:none;font-weight:500;">Solutioneers</a> &mdash; a consultancy that helps teams ship better software through lean delivery practices, flow metrics, and hands-on coaching. If your team wants clearer visibility into cycle time and delivery health, <a href="https://www.solutioneers.co.uk" target="_blank" rel="noopener" style="color:#2563eb;text-decoration:none;">get in touch</a>.</p>
</footer>
</body>
</html>"""

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Generate Jira CTTD confetti report.")
    parser.add_argument("--output", default="index.html", help="Output HTML file (default: index.html)")
    args = parser.parse_args()

    data = collect_data()

    if not data:
        print("\nNo accessible projects found.")
        return

    print(f"\nGenerating HTML report for {len(data)} project(s)...")
    report_html = generate_html(data)

    with open(args.output, "w", encoding="utf-8") as fh:
        fh.write(report_html)

    total = sum(len(p["issues"]) for p in data)
    print(f"Report written to: {args.output}")
    print(f"Summary: {len(data)} projects, {total} in-progress items.")


if __name__ == "__main__":
    main()
