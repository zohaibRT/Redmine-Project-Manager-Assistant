# Redmine Project Manager Assistant — Build Guide

You are building an **AI Project Manager Assistant** for Redmine.

It helps PMs and team leads search issues, check project status, create tickets, track time, and route questions to the correct Redmine tool via a single agent in `agent.py`.

---

## Project layout

```
multi_agent_redmine/
├── .env
├── config.py              # LLM connection
├── tool_helpers.py        # Date parsing, tool-call parser
├── agent.py               # Live Redmine PM chat (all tools)
└── test_*.py              # Unit tests (mocked Redmine)
```

---

## Single agent (Level 7)

One `build_redmine_agent()` registers every tool. The model picks the right tool via the `<tool_call>` pattern handled by `tool_helpers.run_agent_turn()`.

### All tools (19)

| Category | Tools |
|----------|-------|
| Account & projects | `get_my_profile`, `list_my_projects`, `list_project_members`, `list_user_projects`, `get_project_status`, `get_project_manager` |
| Issues | `list_my_issues`, `search_issues`, `search_high_priority_issues`, `get_issue` |
| Time tracking | `get_my_time_logged`, `get_user_time_logged`, `get_project_time_logged`, `get_project_time_by_member`, `get_last_logged_day`, `draft_time_entry`, `log_time` |
| Drafting | `draft_issue`, `create_issue_in_redmine` |

### Tool routing (disambiguation)

| User intent | Tool |
|-------------|------|
| My projects only | `list_my_projects` |
| Members/developers on a **specific** project | `list_project_members` |
| Projects for a **named** person | `list_user_projects` |
| Total team hours on a project | `get_project_time_logged` |
| Per-developer time on a project | `get_project_time_by_member` |
| Who manages/leads a project | `get_project_manager` |

### Redmine API endpoints

| Tool | API endpoint |
|------|----------------|
| `get_my_profile` | `/users/current.json` |
| `list_my_projects` | `/users/current.json?include=memberships` |
| `list_project_members` | `/projects/{id}/memberships.json` |
| `list_user_projects` | `/users/{id}.json?include=memberships` (fallback: scan project memberships) |
| `get_my_time_logged` / `get_user_time_logged` / `get_project_time_logged` / `get_project_time_by_member` | `/time_entries.json` |
| `get_last_logged_day` | `/time_entries.json?sort=spent_on:desc&limit=1` |
| `draft_time_entry` / `log_time` | `POST /time_entries.json` |

---

## Date parsing (DD-MM-YYYY)

`tool_helpers.parse_date_range` prefers **DD-MM-YYYY** for ambiguous dash dates (e.g. Pakistan locale).

- `03-07-2026` → `2026-07-03` (3 July 2026), not US MM-DD.
- Also supports natural language: `today`, `yesterday`, `this week`, `last month`, `from 2026-06-08 till today`, etc.
- `""` or `all` → no date filter.

---

## Billable vs spent hours

When Redmine time entries include a custom field named **Billable** (or similar), `_format_time_summary` adds:

```
By billable: X.XXh billable, Y.YYh non-billable
```

If no billable custom field appears in API responses, the summary states that billable tracking is not available.

---

## Project name resolution

`_resolve_project` resolves names, identifiers, or numeric IDs:

1. Exact / normalized match (handles `RefocusAI` → `Refocus AI`)
2. **Token match** — all query words must appear (so `Association analytics` → #1576, not a generic "analytics" project)
3. Association Analytics alias (`association analyt…` typos)
4. Fuzzy match as fallback

---

## Example PM questions

| PM asks | Tool |
|---------|------|
| `list developers on Association Analytics` | `list_project_members` |
| `projects of uzair aziz` | `list_user_projects` |
| `complete report with time per developer on Association Analytics` | `get_project_time_by_member` |
| `total team hours on Association Analytics` | `get_project_time_logged` |
| `how much billable time did zahid log` | `get_user_time_logged` |
| `zahid logged time on 03-07-2026` | `get_user_time_logged` with `date_range: "03-07-2026"` |

### Example per-member report output (Association Analytics #1576)

```
Project: Association Analytics (#1576)
Total hours (all members): 16.00
Entries: 3

Developer | Hours | Entries
---------|-------|--------
Zahid Abbas | 8.00 | 1
Rauf Khan | 8.00 | 2

Date range: all time
```

---

## Required `.env`

```env
REDMINE_URL=https://redmine.example.com
REDMINE_API_KEY=your-redmine-api-key
```

---

## Run & test

```powershell
.\.venv\Scripts\python.exe agent.py
python test_routing.py          # confirms 19 tools registered
.\.venv\Scripts\python.exe -m pytest test_*.py -q
```
