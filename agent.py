"""Redmine PM Assistant — live API only, chat interface."""

import difflib
import os
import re
from datetime import date
from urllib.parse import quote, urljoin

import requests
from langchain.agents import create_agent
from langchain.tools import tool

from config import get_chat_model
from tool_helpers import (
    DATE_RANGE_HINT,
    is_date_range_token,
    is_person_name_token,
    parse_date_range,
    run_agent_turn,
)

_users_api_search_allowed: bool | None = None
_membership_users_cache: list[dict] | None = None
_projects_cache: list[dict] | None = None
_priority_cache: dict[str, int] | None = None
_activity_cache: dict[str, int] | None = None
_time_entry_custom_field_cache: dict[str, int] | None = None
_tracker_cache: int | None = None
_tracker_lookup_done: bool = False
_pending_draft: dict | None = None
_pending_clarification: dict | None = None

_ISSUE_ID_REF_RE = re.compile(r"^#?(\d+)$")

_ASSOCIATION_ANALYTICS_RE = re.compile(
    r"\bassociation\s+analy(?:tics|st)\b",
    re.IGNORECASE,
)


class AmbiguousUserMessage(str):
    """User-facing ambiguity message that code can recognize without parsing wording."""


def _redmine_configured() -> bool:
    return bool(os.getenv("REDMINE_URL") and os.getenv("REDMINE_API_KEY"))


def _redmine_error() -> str:
    return (
        "Redmine is not configured. Set REDMINE_URL and REDMINE_API_KEY in your .env file."
    )


def redmine_get(path: str) -> dict:
    if not _redmine_configured():
        raise RuntimeError(_redmine_error())

    url = f"{os.getenv('REDMINE_URL', '').rstrip('/')}{path}"
    headers = {
        "X-Redmine-API-Key": os.getenv("REDMINE_API_KEY", ""),
        "Content-Type": "application/json",
    }
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


def _format_redmine_post_error(response: requests.Response) -> str:
    """Build a readable message from a failed Redmine POST (no secrets)."""
    status = response.status_code
    body: object = None
    try:
        body = response.json()
    except ValueError:
        body = None

    errors = body.get("errors") if isinstance(body, dict) else None
    if errors:
        if isinstance(errors, list):
            detail = "; ".join(str(e) for e in errors)
        else:
            detail = str(errors)
        return f"Redmine rejected the request (HTTP {status}): {detail}"

    text = (response.text or "").strip()
    if text:
        return f"Redmine request failed (HTTP {status}): {text[:300]}"
    return f"Redmine request failed (HTTP {status})."


def redmine_post(path: str, payload: dict) -> dict:
    if not _redmine_configured():
        raise RuntimeError(_redmine_error())

    url = f"{os.getenv('REDMINE_URL', '').rstrip('/')}{path}"
    headers = {
        "X-Redmine-API-Key": os.getenv("REDMINE_API_KEY", ""),
        "Content-Type": "application/json",
    }
    # Do not let requests auto-follow redirects: on a 301/302 (e.g. http->https)
    # requests downgrades POST to GET, silently turning a create into a list
    # request whose body lacks the "issue" key (the original 'Redmine error: issue').
    response = requests.post(
        url, headers=headers, json=payload, timeout=30, allow_redirects=False
    )
    redirects = 0
    while (
        response.is_redirect
        and response.headers.get("Location")
        and redirects < 5
    ):
        url = urljoin(url, response.headers["Location"])
        response = requests.post(
            url, headers=headers, json=payload, timeout=30, allow_redirects=False
        )
        redirects += 1

    if response.status_code not in (200, 201):
        raise RuntimeError(_format_redmine_post_error(response))

    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"Redmine returned a non-JSON response (HTTP {response.status_code}) "
            f"for POST {path}."
        ) from exc


def _get_current_user_id() -> int:
    return redmine_get("/users/current.json")["user"]["id"]


def _resolve_priority_id(priority_name: str) -> int | str:
    global _priority_cache
    if _priority_cache is None:
        data = redmine_get("/enumerations/issue_priorities.json")
        _priority_cache = {
            p["name"].lower(): p["id"] for p in data.get("issue_priorities", [])
        }

    needle = (priority_name or "Normal").strip().lower()
    if needle in _priority_cache:
        return _priority_cache[needle]

    for name, priority_id in _priority_cache.items():
        if needle in name or name in needle:
            return priority_id

    available = ", ".join(sorted(_priority_cache))
    return f"Unknown priority '{priority_name}'. Available: {available}."


def _resolve_tracker_id() -> int | None:
    """Resolve a tracker id for new issues.

    Many Redmine instances require ``tracker_id``. Prefer the explicit
    REDMINE_DEFAULT_TRACKER_ID env var, else fall back to the first tracker
    returned by /trackers.json. Best-effort: returns None if unavailable.
    """
    env_value = os.getenv("REDMINE_DEFAULT_TRACKER_ID", "").strip()
    if env_value.isdigit():
        return int(env_value)

    global _tracker_cache, _tracker_lookup_done
    if _tracker_lookup_done:
        return _tracker_cache

    _tracker_lookup_done = True
    try:
        trackers = redmine_get("/trackers.json").get("trackers", [])
    except (requests.RequestException, RuntimeError, KeyError, ValueError):
        trackers = []
    if trackers:
        _tracker_cache = trackers[0].get("id")
    return _tracker_cache


def _default_project_identifier() -> str:
    return (
        os.getenv("REDMINE_DEFAULT_PROJECT_ID", "")
        or os.getenv("REDMINE_PROJECT_ID", "")
    ).strip()


def create_issue(
    project_id: int,
    subject: str,
    description: str,
    *,
    assigned_to_id: int | None = None,
    priority_id: int | None = None,
) -> dict:
    issue_body: dict = {
        "project_id": int(project_id),
        "subject": subject,
        "description": description,
    }
    if assigned_to_id is not None:
        issue_body["assigned_to_id"] = assigned_to_id
    if priority_id is not None:
        issue_body["priority_id"] = priority_id
    tracker_id = _resolve_tracker_id()
    if tracker_id is not None:
        issue_body["tracker_id"] = tracker_id
    return redmine_post("/issues.json", {"issue": issue_body})


def get_pending_draft() -> dict | None:
    return _pending_draft


def clear_pending_draft() -> None:
    global _pending_draft
    _pending_draft = None


def _resolve_draft_project(
    project_identifier_or_id: str,
) -> tuple[int | None, str | None, str | None]:
    """Return (project_id, project_name, error_message)."""
    raw = (project_identifier_or_id or "").strip()
    if not raw:
        raw = _default_project_identifier()
    if not raw:
        return None, None, None

    resolved = _resolve_project(raw)
    if isinstance(resolved, str):
        return None, None, resolved
    if resolved is None:
        return None, None, None
    project_id, project_name = resolved
    return project_id, project_name, None


def create_issue_from_draft(draft: dict) -> str:
    project_id = draft.get("project_id")
    if not project_id:
        return (
            "Cannot create issue: no project specified.\n"
            "Re-draft with project_identifier_or_id, or set REDMINE_DEFAULT_PROJECT_ID "
            "(or REDMINE_PROJECT_ID) in your .env file."
        )

    priority_id = _resolve_priority_id(draft.get("priority", "Normal"))
    if isinstance(priority_id, str):
        return priority_id

    assigned_to_id: int | None = None
    if draft.get("assign_to_me", True):
        try:
            assigned_to_id = _get_current_user_id()
        except (requests.RequestException, RuntimeError, KeyError) as exc:
            return f"Redmine error resolving current user: {exc}"

    try:
        data = create_issue(
            project_id,
            draft["title"],
            draft["description"],
            assigned_to_id=assigned_to_id,
            priority_id=priority_id,
        )
        issue = data.get("issue") if isinstance(data, dict) else None
        if not isinstance(issue, dict) or "id" not in issue:
            return (
                "Redmine did not return a created issue. "
                f"Unexpected response: {data}"
            )
        assignee = issue.get("assigned_to", {}).get("name", "Unassigned")
        project = issue.get("project", {}).get("name", draft.get("project_name", "N/A"))
        return (
            f"Created issue #{issue['id']}: {issue['subject']}\n"
            f"Project: {project}\n"
            f"Priority: {issue.get('priority', {}).get('name', draft.get('priority', 'Normal'))}\n"
            f"Assignee: {assignee}\n"
            f"URL: {os.getenv('REDMINE_URL', '').rstrip('/')}/issues/{issue['id']}"
        )
    except requests.HTTPError as exc:
        detail = ""
        if exc.response is not None:
            try:
                detail = exc.response.json().get("errors", exc.response.text)
            except ValueError:
                detail = exc.response.text
        return f"Redmine error creating issue: {exc} {detail}".strip()
    except (requests.RequestException, RuntimeError, KeyError) as exc:
        return f"Redmine error: {exc}"


def _issue_create_succeeded(answer: str) -> bool:
    return answer.startswith("Created issue #") and "URL:" in answer


def _store_pending_draft(draft: dict) -> None:
    global _pending_draft
    _pending_draft = draft


def _store_pending_clarification(clarification: dict) -> None:
    global _pending_clarification
    _pending_clarification = clarification


def clear_pending_clarification() -> None:
    global _pending_clarification
    _pending_clarification = None


def get_pending_clarification() -> dict | None:
    return _pending_clarification


def _remember_user_clarification(
    tool_name: str,
    arguments: dict,
    message: str,
) -> str:
    if isinstance(message, AmbiguousUserMessage):
        _store_pending_clarification(
            {
                "tool": tool_name,
                "missing_argument": "user_name",
                "arguments": arguments,
                "message": message,
            }
        )
    return message


def _approve_without_project_message() -> str:
    try:
        projects = list_my_projects.invoke({})
    except (requests.RequestException, RuntimeError, KeyError) as exc:
        projects = f"(could not list projects: {exc})"
    return (
        "Cannot create issue: no project specified.\n"
        f"{projects}\n"
        "Specify a project, then approve when you want it created."
    )


def _pending_draft_context() -> str:
    draft = get_pending_draft()
    if draft is None:
        return ""

    if draft.get("kind") == "time_entry":
        return (
            "There is a pending time-entry draft. Decide whether the user is "
            "providing missing hours/details, approving it, changing it, or asking "
            "a new Redmine question. Use approve_pending_draft to post it when the "
            "user approves, or update_pending_time_entry when the user provides "
            "hours/date/activity/comments/billable hours/time-entry comments.\n"
            f"Pending draft: {draft}"
        )

    return (
        "There is a pending issue draft. Decide whether the user is approving it, "
        "setting/changing its project, changing details, or asking a new Redmine "
        "question. Use approve_pending_draft to create it when the user approves, "
        "or update_pending_issue_project when the user provides a project.\n"
        f"Pending draft: {draft}"
    )


def _pending_clarification_context() -> str:
    clarification = get_pending_clarification()
    if clarification is None:
        return ""

    return (
        "There is a pending clarification from the previous Redmine request. "
        f"The previous tool was {clarification.get('tool')}; it is missing "
        f"{clarification.get('missing_argument')}. Preserve the previous tool "
        "arguments and use the user's reply as the missing value unless the user "
        "clearly asks a new unrelated question.\n"
        f"Previous arguments: {clarification.get('arguments', {})}\n"
        f"Clarification message: {clarification.get('message', '')}"
    )


def _format_draft_message(draft: dict) -> str:
    lines = [
        "DRAFT ISSUE (not created yet)",
        f"Title: {draft['title']}",
        f"Priority: {draft.get('priority', 'Normal')}",
    ]
    if draft.get("project_id"):
        lines.append(f"Project: {draft.get('project_name')} (#{draft['project_id']})")
    else:
        default_hint = _default_project_identifier()
        if default_hint:
            lines.append(
                f"Project: (could not resolve '{default_hint}' — specify a valid project)"
            )
        else:
            lines.append(
                "Project: (not specified — include project in request or set "
                "REDMINE_DEFAULT_PROJECT_ID in .env)"
            )
    if draft.get("assign_to_me", True):
        lines.append("Assignee: You (current user)")
    else:
        lines.append("Assignee: Unassigned")
    lines.append(f"Description: {draft['description']}")
    lines.append(
        "Tell me when to create it in Redmine, or provide a project to update the draft first."
    )
    return "\n".join(lines)


def _format_issue(i: dict) -> str:
    return (
        f"#{i['id']}: {i['subject']} [{i['status']['name']}] "
        f"Priority: {i.get('priority', {}).get('name', 'N/A')} — "
        f"Project: {i.get('project', {}).get('name', 'N/A')} — "
        f"Assignee: {i.get('assigned_to', {}).get('name', 'Unassigned')}"
    )


def _user_display_name(user: dict) -> str:
    name = " ".join((user.get("name") or "").split())
    if name:
        return name
    return " ".join(
        f"{user.get('firstname', '')} {user.get('lastname', '')}".split()
    )


_CURRENT_USER_ALIASES = frozenset({"me", "myself", "current user"})
_USER_AMBIGUITY_LIMIT = 5


def _normalize_lookup_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _lookup_tokens(value: object) -> list[str]:
    return [
        token
        for token in re.split(r"[\s._@'-]+", _normalize_lookup_text(value))
        if token
    ]


def _user_identity_parts(user: dict) -> list[str]:
    fields = [
        _user_display_name(user),
        user.get("login"),
        user.get("firstname"),
        user.get("lastname"),
    ]
    parts: list[str] = []
    for field in fields:
        normalized = _normalize_lookup_text(field)
        if normalized:
            parts.append(normalized)
            parts.extend(_lookup_tokens(normalized))
    return list(dict.fromkeys(parts))


def _similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if left in right or right in left:
        return 0.95
    return difflib.SequenceMatcher(None, left, right).ratio()


def _get_current_user() -> tuple[int, str]:
    data = redmine_get("/users/current.json")
    user = data["user"]
    return user["id"], _user_display_name(user)


def _user_matches_name(user: dict, needle: str) -> bool:
    if needle in _CURRENT_USER_ALIASES:
        return False
    normalized_needle = _normalize_lookup_text(needle)
    identity_parts = _user_identity_parts(user)
    if normalized_needle in identity_parts:
        return True
    if any(normalized_needle in part for part in identity_parts):
        return True
    tokens = _lookup_tokens(normalized_needle)
    if tokens and all(any(token in part for part in identity_parts) for token in tokens):
        return True
    return False


def _collect_users_from_memberships() -> list[dict]:
    """Collect visible users from project memberships (users.json may be forbidden)."""
    global _membership_users_cache
    if _membership_users_cache is not None:
        return _membership_users_cache

    users_by_id: dict[int, dict] = {}
    offset = 0
    limit = 100
    while True:
        data = redmine_get(f"/projects.json?limit={limit}&offset={offset}")
        projects = data.get("projects", [])
        for project in projects:
            memberships = redmine_get(
                f"/projects/{project['id']}/memberships.json?limit=100"
            ).get("memberships", [])
            for membership in memberships:
                user = membership.get("user")
                if user and user.get("id"):
                    users_by_id[user["id"]] = user
        total = data.get("total_count", len(projects))
        offset += limit
        if offset >= total or not projects:
            break
    _membership_users_cache = list(users_by_id.values())
    return _membership_users_cache


def _filter_users_by_name(users: list[dict], needle: str) -> list[dict]:
    return [user for user in users if _user_matches_name(user, needle)]


_FUZZY_MATCH_THRESHOLD = 0.8
_FUZZY_MATCH_MIN_GAP = 0.05
_FUZZY_TOKEN_THRESHOLD = 0.72

_BILLABLE_FIELD_NAMES = frozenset(
    {
        "billable",
        "is billable",
        "billable hours",
        "billable time",
    }
)


def _fuzzy_user_score(user: dict, needle: str) -> float:
    normalized_needle = _normalize_lookup_text(needle)
    identity_parts = _user_identity_parts(user)
    scores = [_similarity(normalized_needle, part) for part in identity_parts]

    needle_tokens = _lookup_tokens(normalized_needle)
    if needle_tokens and identity_parts:
        token_scores = [
            max(_similarity(token, part) for part in identity_parts)
            for token in needle_tokens
        ]
        scores.append(sum(token_scores) / len(token_scores))
        if all(score >= _FUZZY_TOKEN_THRESHOLD for score in token_scores):
            scores.append(0.85)
    return max(scores) if scores else 0.0


def _fuzzy_match_users(users: list[dict], needle: str) -> list[tuple[dict, float]]:
    scored = [(user, _fuzzy_user_score(user, needle)) for user in users]
    return [(user, score) for user, score in scored if score >= _FUZZY_MATCH_THRESHOLD]


def _resolve_user_fuzzy(
    users: list[dict], raw: str, needle: str
) -> tuple[int, str] | str | None:
    scored = _fuzzy_match_users(users, needle)
    if not scored:
        return None
    scored.sort(key=lambda item: item[1], reverse=True)
    if len(scored) == 1:
        user, _ = scored[0]
        return user["id"], _user_display_name(user)
    best_score = scored[0][1]
    second_score = scored[1][1]
    if best_score - second_score >= _FUZZY_MATCH_MIN_GAP:
        user, _ = scored[0]
        return user["id"], _user_display_name(user)
    names = ", ".join(_user_display_name(user) for user, _ in scored[:_USER_AMBIGUITY_LIMIT])
    return AmbiguousUserMessage(
        f"Multiple users match '{raw}': {names}. Please be more specific."
    )


def _resolve_from_user_list(
    users: list[dict], raw: str, needle: str
) -> tuple[int, str] | str | None:
    matches = _filter_users_by_name(users, needle)
    if len(matches) == 1:
        user = matches[0]
        return user["id"], _user_display_name(user)
    if len(matches) > 1:
        names = ", ".join(_user_display_name(u) for u in matches[:_USER_AMBIGUITY_LIMIT])
        return AmbiguousUserMessage(
            f"Multiple users match '{raw}': {names}. Please be more specific."
        )
    return _resolve_user_fuzzy(users, raw, needle)


def _resolve_user(user_name: str) -> tuple[int, str] | str:
    """Resolve a person by partial firstname/lastname/login match to (id, display_name)."""
    if not user_name or not user_name.strip():
        return "user_name is required."

    raw = user_name.strip()
    needle = _normalize_lookup_text(raw)

    if needle in _CURRENT_USER_ALIASES:
        try:
            return _get_current_user()
        except (requests.RequestException, RuntimeError, KeyError) as exc:
            return f"Redmine error: {exc}"

    global _users_api_search_allowed
    if _users_api_search_allowed is not False:
        try:
            query = quote(f"~{raw}", safe="")
            data = redmine_get(f"/users.json?name={query}&status=1&limit=25")
            _users_api_search_allowed = True
            result = _resolve_from_user_list(data.get("users", []), raw, needle)
            if result is not None:
                return result
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 403:
                _users_api_search_allowed = False
            elif exc.response is not None:
                return f"Redmine error: {exc}"
        except (requests.RequestException, RuntimeError, KeyError) as exc:
            return f"Redmine error: {exc}"

    result = _resolve_from_user_list(_collect_users_from_memberships(), raw, needle)
    if result is not None:
        return result
    return f"No user found matching '{raw}'."


def _fetch_all_projects() -> list[dict]:
    """Fetch all visible projects with pagination (limit=100 per page), cached per session."""
    global _projects_cache
    if _projects_cache is not None:
        return _projects_cache

    projects: list[dict] = []
    offset = 0
    limit = 100
    while True:
        data = redmine_get(f"/projects.json?limit={limit}&offset={offset}")
        batch = data.get("projects", [])
        projects.extend(batch)
        total = data.get("total_count", len(projects))
        offset += limit
        if offset >= total or not batch:
            break
    _projects_cache = projects
    return projects


def _normalize_project_key(value: str) -> str:
    """Lowercase and strip non-alphanumerics so 'RefocusAI' matches 'Refocus AI'."""
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _fuzzy_project_score(project: dict, needle: str, needle_key: str) -> float:
    name = project.get("name", "").lower()
    identifier = project.get("identifier", "").lower()
    pairs = [(name, needle), (identifier, needle)]
    if needle_key:
        pairs.append((_normalize_project_key(name), needle_key))
        pairs.append((_normalize_project_key(identifier), needle_key))
    scores = [
        difflib.SequenceMatcher(None, candidate, target).ratio()
        for candidate, target in pairs
        if candidate
    ]
    tokens = _project_query_tokens(needle)
    if tokens:
        haystacks = [name, identifier]
        token_hits = sum(
            1
            for token in tokens
            if any(token in hay for hay in haystacks if hay)
        )
        scores.append(token_hits / len(tokens))
    return max(scores) if scores else 0.0


def _canonicalize_project_query(raw: str) -> str:
    if _ASSOCIATION_ANALYTICS_RE.search(raw):
        return "Association Analytics"
    return raw


def _project_query_tokens(needle: str) -> list[str]:
    return _lookup_tokens(needle)


def _project_token_match_score(project: dict, tokens: list[str]) -> float:
    if not tokens:
        return 0.0
    name = project.get("name", "").lower()
    identifier = project.get("identifier", "").lower()
    haystacks = [name, identifier]
    hits = sum(1 for token in tokens if any(token in hay for hay in haystacks if hay))
    if hits != len(tokens):
        return 0.0
    full_needle = " ".join(tokens)
    if full_needle == name or full_needle == identifier:
        return 1.0
    if full_needle in name or full_needle in identifier:
        return 0.95
    return hits / len(tokens)


def _resolve_project_fuzzy(
    projects: list[dict], raw: str, needle_key: str
) -> tuple[int, str] | str | None:
    scored = [
        (p, _fuzzy_project_score(p, raw.lower(), needle_key)) for p in projects
    ]
    scored = [(p, s) for p, s in scored if s >= _FUZZY_MATCH_THRESHOLD]
    if not scored:
        return None
    scored.sort(key=lambda item: item[1], reverse=True)
    if len(scored) == 1 or scored[0][1] - scored[1][1] >= _FUZZY_MATCH_MIN_GAP:
        return scored[0][0]["id"], scored[0][0]["name"]
    names = ", ".join(f"{p['name']} (#{p['id']})" for p, _ in scored[:5])
    return f"Multiple projects match '{raw}': {names}. Please be more specific."


def _resolve_project(project_identifier_or_id: str) -> tuple[int, str] | None | str:
    """Resolve project name, identifier, or numeric id to (id, name). None = no filter."""
    if not project_identifier_or_id or not project_identifier_or_id.strip():
        return None

    raw = _canonicalize_project_query(project_identifier_or_id.strip())
    if is_date_range_token(raw):
        return (
            f"'{raw}' is a date range, not a project. "
            "Use the date_range parameter on get_my_time_logged or get_user_time_logged instead."
        )
    if raw.isdigit():
        try:
            project = redmine_get(f"/projects/{raw}.json")["project"]
            return project["id"], project["name"]
        except (requests.RequestException, RuntimeError, KeyError) as exc:
            return f"Redmine error: {exc}"

    needle = raw.lower()
    needle_key = _normalize_project_key(raw)
    projects = _fetch_all_projects()

    # 1. Exact match on name or identifier (case-insensitive).
    for project in projects:
        name = project.get("name", "")
        identifier = project.get("identifier", "")
        if name.lower() == needle or identifier.lower() == needle:
            return project["id"], name

    # 2. Normalized exact match: handles spacing/punctuation ("RefocusAI" -> "Refocus AI").
    if needle_key:
        for project in projects:
            if (
                _normalize_project_key(project.get("name", "")) == needle_key
                or _normalize_project_key(project.get("identifier", "")) == needle_key
            ):
                return project["id"], project["name"]

    # 3. Token match — all query words must appear in name or identifier.
    tokens = _project_query_tokens(needle)
    if tokens:
        scored = [
            (project, _project_token_match_score(project, tokens)) for project in projects
        ]
        scored = [(project, score) for project, score in scored if score > 0]
        scored.sort(key=lambda item: item[1], reverse=True)
        if len(scored) == 1:
            project, _ = scored[0]
            return project["id"], project["name"]
        if len(scored) > 1:
            best_score = scored[0][1]
            close = [project for project, score in scored if score >= best_score - 0.05]
            if len(close) == 1:
                project = close[0]
                return project["id"], project["name"]
            names = ", ".join(
                f"{project['name']} (#{project['id']})" for project, _ in scored[:5]
            )
            return f"Multiple projects match '{raw}': {names}. Please be more specific."

    # 4. Full-string substring match (single phrase in name/identifier).
    matches = [
        p
        for p in projects
        if needle in p.get("name", "").lower() or needle in p.get("identifier", "").lower()
    ]
    if len(matches) == 1:
        return matches[0]["id"], matches[0]["name"]
    if len(matches) > 1:
        names = ", ".join(f"{p['name']} (#{p['id']})" for p in matches[:5])
        return f"Multiple projects match '{raw}': {names}. Please be more specific."

    # 5. Fuzzy match (difflib) before rejecting the input as a person name.
    fuzzy = _resolve_project_fuzzy(projects, raw, needle_key)
    if fuzzy is not None:
        return fuzzy

    # 6. Only now consider whether the input is actually a person name.
    user_match = _resolve_user(raw)
    if isinstance(user_match, tuple):
        _user_id, display_name = user_match
        return (
            f"'{raw}' matches user '{display_name}', not a project. "
            "Use get_user_time_logged for time logged by this person."
        )
    if is_person_name_token(raw):
        return (
            f"'{raw}' looks like a person name, not a project. "
            "Use get_user_time_logged for time logged by a team member."
        )
    return f"No project found matching '{raw}'."


def _append_date_filters(query: str, from_date: str, to_date: str) -> str:
    return f"{query}&from={from_date}&to={to_date}"


def _fetch_project_memberships(project_id: int) -> list[dict]:
    """Fetch all memberships for a project with pagination (limit=100 per page)."""
    memberships: list[dict] = []
    offset = 0
    limit = 100
    while True:
        data = redmine_get(
            f"/projects/{project_id}/memberships.json?limit={limit}&offset={offset}"
        )
        batch = data.get("memberships", [])
        memberships.extend(batch)
        total = data.get("total_count", len(memberships))
        offset += limit
        if offset >= total or not batch:
            break
    return memberships


def _is_manager_role(role: dict) -> bool:
    return "manager" in (role.get("name") or "").lower()


def _fetch_time_entries(query: str) -> list[dict]:
    """Fetch time entries with pagination (limit=100 per page)."""
    entries: list[dict] = []
    offset = 0
    limit = 100
    while True:
        data = redmine_get(f"/time_entries.json?{query}&limit={limit}&offset={offset}")
        batch = data.get("time_entries", [])
        entries.extend(batch)
        total = data.get("total_count", len(entries))
        offset += limit
        if offset >= total or not batch:
            break
    return entries


def _significant_issue_tokens(raw: str) -> list[str]:
    tokens = [t.lower() for t in re.findall(r"[a-z0-9]+", raw) if t]
    return [
        token
        for token in tokens
        if len(token) >= 3 or any(char.isdigit() for char in token)
    ]


def _tokenize_subject(subject: str) -> list[str]:
    return [t.lower() for t in re.findall(r"[a-z0-9]+", subject) if t]


def _subject_token_matches(query_token: str, subject_token: str) -> bool:
    if query_token == subject_token:
        return True
    if query_token in subject_token or subject_token in query_token:
        return True
    if len(query_token) >= 4 and (
        subject_token.startswith(query_token) or query_token.startswith(subject_token)
    ):
        return True
    return difflib.SequenceMatcher(None, query_token, subject_token).ratio() >= 0.8


def _score_issue_subject(tokens: list[str], subject: str) -> float:
    if not tokens:
        return 0.0
    subject_tokens = _tokenize_subject(subject)
    if not subject_tokens:
        return 0.0
    matched = sum(
        1
        for token in tokens
        if any(_subject_token_matches(token, st) for st in subject_tokens)
    )
    return matched / len(tokens)


def _fetch_issues_by_subject_token(
    token: str, *, project_filter: str = ""
) -> list[dict]:
    issues: list[dict] = []
    offset = 0
    limit = 100
    while True:
        data = redmine_get(
            f"/issues.json?subject=~{token}&status_id=*&limit={limit}"
            f"&offset={offset}{project_filter}"
        )
        batch = data.get("issues", [])
        issues.extend(batch)
        total = data.get("total_count", len(issues))
        offset += limit
        if offset >= total or not batch:
            break
    return issues


def _collect_issue_candidates(
    tokens: list[str], *, project_filter: str = ""
) -> list[dict]:
    if not tokens:
        return []
    by_id: dict[int, dict] = {}
    for token in tokens:
        for issue in _fetch_issues_by_subject_token(token, project_filter=project_filter):
            by_id[issue["id"]] = issue
    return list(by_id.values())


def _rank_issues_by_tokens(
    tokens: list[str], issues: list[dict]
) -> list[tuple[dict, float]]:
    ranked: list[tuple[dict, float]] = []
    for issue in issues:
        score = _score_issue_subject(tokens, issue.get("subject") or "")
        if score > 0:
            ranked.append((issue, score))
    ranked.sort(key=lambda item: (-item[1], -item[0]["id"]))
    return ranked


def _resolve_issue_id_reference(raw: str) -> set[int] | str | None:
    """Return {issue_id} when raw is #123 or 123; None when not numeric."""
    match = _ISSUE_ID_REF_RE.match(raw.strip())
    if not match:
        return None
    issue_id = int(match.group(1))
    try:
        redmine_get(f"/issues/{issue_id}.json")
        return {issue_id}
    except (requests.RequestException, RuntimeError, KeyError) as exc:
        return f"Issue #{issue_id} not found: {exc}"


def _search_issues_by_keyword(
    query: str, *, project_filter: str = ""
) -> list[tuple[dict, float]]:
    raw = query.strip()
    if not raw:
        return []

    numeric = _resolve_issue_id_reference(raw)
    if isinstance(numeric, str):
        return []
    if numeric is not None:
        try:
            issue = redmine_get(f"/issues/{next(iter(numeric))}.json")["issue"]
            return [(issue, 1.0)]
        except (requests.RequestException, RuntimeError, KeyError):
            return []

    tokens = _significant_issue_tokens(raw)
    if not tokens:
        tokens = [t.lower() for t in raw.split() if t]

    candidates = _collect_issue_candidates(tokens, project_filter=project_filter)
    return _rank_issues_by_tokens(tokens, candidates)


def _resolve_issue_ids_by_keyword(issue_keyword: str) -> set[int] | str:
    """Find issue ids whose subject matches keyword tokens (OR + ranking)."""
    raw = issue_keyword.strip()
    if not raw:
        return set()

    numeric = _resolve_issue_id_reference(raw)
    if isinstance(numeric, str):
        return numeric
    if numeric is not None:
        return numeric

    tokens = _significant_issue_tokens(raw)
    if not tokens:
        tokens = [t.lower() for t in raw.split() if t]
    if not tokens:
        return set()

    ranked = _search_issues_by_keyword(raw)
    if not ranked:
        return f"No issues found matching '{raw}'."

    return {issue["id"] for issue, _score in ranked}


def _filter_entries_by_issue_keyword(
    entries: list[dict], issue_keyword: str
) -> tuple[list[dict], str | None]:
    if not issue_keyword.strip():
        return entries, None
    resolved = _resolve_issue_ids_by_keyword(issue_keyword)
    if isinstance(resolved, str):
        return [], resolved
    filtered = [
        entry
        for entry in entries
        if entry.get("issue") and entry["issue"].get("id") in resolved
    ]
    return filtered, None


def _fetch_last_logged_entry(user_id_query: str) -> dict | None:
    """Return the time entry with the most recent spent_on for a user (me or numeric id).

    Prefers GET ...?sort=spent_on:desc&limit=1; verifies sort when multiple entries exist,
    otherwise falls back to max(spent_on) over all paginated entries.
    """
    sorted_entry: dict | None = None
    try:
        data = redmine_get(
            f"/time_entries.json?user_id={user_id_query}&sort=spent_on:desc&limit=1"
        )
        batch = data.get("time_entries", [])
        if batch:
            sorted_entry = batch[0]
            total = data.get("total_count", len(batch))
            if total <= 1:
                return sorted_entry

            asc_data = redmine_get(
                f"/time_entries.json?user_id={user_id_query}&sort=spent_on:asc&limit=1"
            )
            asc_batch = asc_data.get("time_entries", [])
            if asc_batch:
                desc_date = sorted_entry.get("spent_on", "")
                asc_date = asc_batch[0].get("spent_on", "")
                if desc_date >= asc_date:
                    return sorted_entry
    except (requests.RequestException, RuntimeError, KeyError):
        sorted_entry = None

    entries = _fetch_time_entries(f"user_id={user_id_query}")
    if not entries:
        return None
    return max(entries, key=lambda e: e.get("spent_on", ""))


def _format_last_logged_day(
    entry: dict,
    *,
    display_name: str | None = None,
) -> str:
    spent_on = entry.get("spent_on", "unknown")
    hours = entry.get("hours", 0)
    issue = entry.get("issue")
    hours_str = f"{hours:g}h" if isinstance(hours, (int, float)) else str(hours)
    if issue and issue.get("id"):
        detail = f"{hours_str} on #{issue['id']}"
    else:
        detail = hours_str

    if display_name:
        return (
            f"{display_name}'s most recent time log was on {spent_on} ({detail})."
        )
    return f"Your most recent time log was on {spent_on} ({detail})."


def _parse_billable_custom_value(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "billable"}:
        return True
    if text in {"0", "false", "no", "n", "non-billable", "nonbillable"}:
        return False
    return None


def _entry_billable_status(entry: dict) -> bool | None:
    for field in entry.get("custom_fields") or []:
        name = (field.get("name") or "").strip().lower()
        if name in _BILLABLE_FIELD_NAMES:
            return _parse_billable_custom_value(field.get("value"))
    return None


def _aggregate_billable_hours(entries: list[dict]) -> tuple[float, float, float, bool]:
    """Return billable, non-billable, unclassified hours, and whether the field appears."""
    billable = 0.0
    non_billable = 0.0
    unclassified = 0.0
    field_seen = False
    for entry in entries:
        hours = float(entry.get("hours") or 0)
        status = _entry_billable_status(entry)
        if status is None:
            unclassified += hours
            continue
        field_seen = True
        if status:
            billable += hours
        else:
            non_billable += hours
    return billable, non_billable, unclassified, field_seen


def _format_time_summary(
    entries: list[dict],
    *,
    scope_label: str,
    project_name: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    issue_keyword: str | None = None,
) -> str:
    if not entries:
        if issue_keyword:
            if project_name:
                return (
                    f"No time logged for {scope_label} on project '{project_name}' "
                    f"for issues matching '{issue_keyword}'."
                )
            return (
                f"No time logged for {scope_label} "
                f"for issues matching '{issue_keyword}'."
            )
        if project_name:
            return f"No time logged for {scope_label} on project '{project_name}'."
        return f"No time logged for {scope_label}."

    total_hours = sum(e.get("hours", 0) for e in entries)
    lines = [
        f"Total hours ({scope_label}): {total_hours:.2f}",
        f"Entries: {len(entries)}",
    ]
    if issue_keyword:
        lines.insert(0, f"Issue filter: {issue_keyword}")
    if project_name:
        lines.insert(0 if not issue_keyword else 1, f"Project: {project_name}")

    by_activity: dict[str, float] = {}
    by_issue: dict[str, float] = {}
    for entry in entries:
        activity = entry.get("activity", {}).get("name", "Unknown")
        by_activity[activity] = by_activity.get(activity, 0) + entry.get("hours", 0)
        issue = entry.get("issue")
        issue_key = f"#{issue['id']}" if issue else "No issue"
        by_issue[issue_key] = by_issue.get(issue_key, 0) + entry.get("hours", 0)

    lines.append("\nBy activity:")
    for activity, hours in sorted(by_activity.items(), key=lambda x: -x[1]):
        lines.append(f"  {activity}: {hours:.2f}h")

    top_issues = sorted(by_issue.items(), key=lambda x: -x[1])[:10]
    lines.append("\nBy issue (top 10):")
    for issue_key, hours in top_issues:
        lines.append(f"  {issue_key}: {hours:.2f}h")

    (
        billable_hours,
        non_billable_hours,
        unclassified_hours,
        has_billable_field,
    ) = _aggregate_billable_hours(entries)
    if has_billable_field:
        lines.append(
            f"\nBillable breakdown for classified entries: "
            f"{billable_hours:.2f}h billable, "
            f"{non_billable_hours:.2f}h non-billable"
        )
        if unclassified_hours:
            lines.append(
                f"Unclassified hours: {unclassified_hours:.2f}h "
                "(entries without a billable custom field or recognizable value)"
            )
    else:
        lines.append(
            "\nBillable breakdown: not available — no billable custom field "
            "in time entry API responses."
        )

    if date_from and date_to:
        lines.append(f"\nDate range: {date_from} to {date_to}")
    else:
        lines.append("\nDate range: all time")

    return "\n".join(lines)


@tool
def get_my_profile() -> str:
    """Get the current Redmine user profile (my account). Use when PM asks who they are or about their account."""
    try:
        data = redmine_get("/users/current.json")
        user = data["user"]
        return (
            f"Name: {user.get('firstname', '')} {user.get('lastname', '')}\n"
            f"Login: {user.get('login', 'N/A')}\n"
            f"Email: {user.get('mail', 'N/A')}\n"
            f"Last login: {user.get('last_login_on', 'N/A')}"
        )
    except (requests.RequestException, RuntimeError, KeyError) as exc:
        return f"Redmine error: {exc}"


@tool
def list_my_projects() -> str:
    """List Redmine projects the current user is a member of. Use for 'my projects'."""
    try:
        data = redmine_get("/users/current.json?include=memberships")
        memberships = data.get("user", {}).get("memberships", [])
        if not memberships:
            return "You are not a member of any projects."
        lines: list[str] = []
        for membership in memberships:
            project = membership.get("project", {})
            project_id = project.get("id")
            if project_id:
                lines.append(f"#{project_id}: {project.get('name', 'N/A')}")
        if not lines:
            return "You are not a member of any projects."
        return "\n".join(lines)
    except (requests.RequestException, RuntimeError, KeyError) as exc:
        return f"Redmine error: {exc}"


@tool
def list_my_issues() -> str:
    """List open issues assigned to the current user. Use for 'my issues' or 'my tasks'."""
    try:
        data = redmine_get("/issues.json?assigned_to_id=me&status_id=open&limit=25")
        issues = data.get("issues", [])
        if not issues:
            return "No open issues assigned to you."
        return "\n".join(_format_issue(i) for i in issues)
    except (requests.RequestException, RuntimeError) as exc:
        return f"Redmine error: {exc}"


@tool
def search_issues(query: str) -> str:
    """Search Redmine issues by keyword in subject (open, New, and closed)."""
    try:
        project_id = os.getenv("REDMINE_PROJECT_ID", "")
        project_filter = f"&project_id={project_id}" if project_id else ""

        ranked = _search_issues_by_keyword(query, project_filter=project_filter)
        if not ranked:
            return f"No issues found matching '{query}'."

        return "\n".join(_format_issue(issue) for issue, _score in ranked[:25])
    except (requests.RequestException, RuntimeError) as exc:
        return f"Redmine error: {exc}"


@tool
def search_high_priority_issues() -> str:
    """List open high-priority or urgent issues."""
    try:
        project_id = os.getenv("REDMINE_PROJECT_ID", "")
        project_filter = f"&project_id={project_id}" if project_id else ""
        data = redmine_get(f"/issues.json?status_id=open&limit=100{project_filter}")
        issues = [
            i
            for i in data.get("issues", [])
            if any(
                word in i.get("priority", {}).get("name", "").lower()
                for word in ("high", "urgent", "immediate")
            )
        ]
        if not issues:
            return "No high-priority open issues found."
        return "\n".join(_format_issue(i) for i in issues)
    except (requests.RequestException, RuntimeError) as exc:
        return f"Redmine error: {exc}"


@tool
def get_issue(issue_id: int) -> str:
    """Get details of a Redmine issue by ID."""
    try:
        data = redmine_get(f"/issues/{issue_id}.json")
        i = data["issue"]
        return (
            f"#{i['id']}: {i['subject']}\n"
            f"Status: {i['status']['name']}\n"
            f"Priority: {i.get('priority', {}).get('name', 'N/A')}\n"
            f"Project: {i.get('project', {}).get('name', 'N/A')}\n"
            f"Assignee: {i.get('assigned_to', {}).get('name', 'Unassigned')}\n"
            f"Author: {i.get('author', {}).get('name', 'N/A')}\n"
            f"Updated: {i.get('updated_on', 'N/A')}"
        )
    except (requests.RequestException, RuntimeError, KeyError) as exc:
        return f"Redmine error: {exc}"


@tool
def get_project_manager(project_identifier_or_id: str) -> str:
    """Return the project manager(s) for a Redmine project."""
    try:
        resolved = _resolve_project(project_identifier_or_id)
        if isinstance(resolved, str):
            return resolved
        if resolved is None:
            return "Project name or id is required."

        project_id, project_name = resolved
        memberships = _fetch_project_memberships(project_id)
        managers: list[str] = []

        for membership in memberships:
            roles = membership.get("roles", [])
            manager_roles = [r["name"] for r in roles if _is_manager_role(r)]
            if not manager_roles:
                continue

            role_label = ", ".join(manager_roles)
            user = membership.get("user")
            if user:
                name = _user_display_name(user)
                login = (user.get("login") or "").strip()
                if login:
                    managers.append(f"- {name} (login: {login}) — {role_label}")
                else:
                    managers.append(f"- {name} — {role_label}")
                continue

            group = membership.get("group")
            if group:
                group_name = group.get("name", "Unknown group")
                managers.append(f"- Group: {group_name} — {role_label}")

        header = f"Project: {project_name} (#{project_id})"
        if not managers:
            return (
                f"{header}\n"
                "No members with a Manager role found on this project."
            )
        return f"{header}\nProject manager(s):\n" + "\n".join(managers)
    except (requests.RequestException, RuntimeError, KeyError) as exc:
        return f"Redmine error: {exc}"


@tool
def get_project_status(project_id: int) -> str:
    """Get open and closed issue counts for a Redmine project by ID."""
    try:
        project = redmine_get(f"/projects/{project_id}.json")["project"]
        open_data = redmine_get(
            f"/issues.json?project_id={project_id}&status_id=open&limit=1"
        )
        closed_data = redmine_get(
            f"/issues.json?project_id={project_id}&status_id=closed&limit=1"
        )
        return (
            f"Project #{project_id}: {project['name']}\n"
            f"Open issues: {open_data.get('total_count', 0)}\n"
            f"Closed issues: {closed_data.get('total_count', 0)}"
        )
    except (requests.RequestException, RuntimeError, KeyError) as exc:
        return f"Redmine error: {exc}"


_GET_MY_TIME_LOGGED_DOC = f"""Get total hours logged by the current user, optionally filtered by project, date, and issue.

Use when the PM asks how much time they logged, spent time, hours on a project,
or their timesheet.
- project_identifier_or_id: project name, identifier, or numeric id; leave empty for all projects.
- date_range: optional — {DATE_RANGE_HINT}. Date words are NOT project names.
- issue_keyword: optional — keywords from an issue/ticket subject (e.g. "dbt skills"), or a numeric issue id / #174022; filters time to that issue."""

_GET_USER_TIME_LOGGED_DOC = f"""Get total hours logged by a specific Redmine user, optionally filtered by project, date, and issue.

Use when the PM asks how much time another person logged — e.g. 'time logged by Rauf',
'hours did Abdul Rauf spend', 'how much time rauf logged in june'.
- user_name: partial or full match on firstname, lastname, or login (case-insensitive).
  Use "me", "myself", or "current user" for the current user.
- project_identifier_or_id: optional project name, identifier, or id; empty for all projects.
- date_range: optional — {DATE_RANGE_HINT}. Pass dates in date_range, NOT as project_identifier_or_id.
- issue_keyword: optional — keywords from an issue/ticket subject to filter time entries."""

_GET_PROJECT_TIME_LOGGED_DOC = f"""Get total hours logged by all users on a project, optionally filtered by date.

Use when the PM asks about total project time, team hours, or time spent on a project
by everyone. Requires project name, identifier, or numeric id.
date_range: optional — {DATE_RANGE_HINT}."""


@tool(description=_GET_MY_TIME_LOGGED_DOC)
def get_my_time_logged(
    project_identifier_or_id: str = "",
    date_range: str = "",
    issue_keyword: str = "",
) -> str:
    try:
        project_name = None
        query = "user_id=me"
        if project_identifier_or_id.strip():
            resolved = _resolve_project(project_identifier_or_id)
            if isinstance(resolved, str):
                return resolved
            project_id, project_name = resolved
            query = f"user_id=me&project_id={project_id}"

        scope_label = "you"
        from_date = to_date = None
        if date_range.strip():
            parsed = parse_date_range(date_range)
            if isinstance(parsed, str):
                return parsed
            if parsed is not None:
                from_date, to_date, label = parsed
                query = _append_date_filters(query, from_date, to_date)
                scope_label = f"you ({label})"

        entries = _fetch_time_entries(query)
        entries, issue_error = _filter_entries_by_issue_keyword(entries, issue_keyword)
        if issue_error:
            return issue_error
        return _format_time_summary(
            entries,
            scope_label=scope_label,
            project_name=project_name,
            date_from=from_date,
            date_to=to_date,
            issue_keyword=issue_keyword.strip() or None,
        )
    except (requests.RequestException, RuntimeError, KeyError) as exc:
        return f"Redmine error: {exc}"


@tool(description=_GET_USER_TIME_LOGGED_DOC)
def get_user_time_logged(
    user_name: str,
    project_identifier_or_id: str = "",
    date_range: str = "",
    issue_keyword: str = "",
) -> str:
    try:
        resolved_user = _resolve_user(user_name)
        if isinstance(resolved_user, str):
            return _remember_user_clarification(
                "get_user_time_logged",
                {
                    "project_identifier_or_id": project_identifier_or_id,
                    "date_range": date_range,
                    "issue_keyword": issue_keyword,
                },
                resolved_user,
            )
        user_id, display_name = resolved_user
        pending_clarification = get_pending_clarification()
        if (
            pending_clarification
            and pending_clarification.get("tool") == "get_user_time_logged"
        ):
            clear_pending_clarification()

        project_name = None
        query = f"user_id={user_id}"
        if project_identifier_or_id.strip():
            resolved = _resolve_project(project_identifier_or_id)
            if isinstance(resolved, str):
                return resolved
            project_id, project_name = resolved
            query = f"user_id={user_id}&project_id={project_id}"

        scope_label = display_name
        from_date = to_date = None
        if date_range.strip():
            parsed = parse_date_range(date_range)
            if isinstance(parsed, str):
                return parsed
            if parsed is not None:
                from_date, to_date, label = parsed
                query = _append_date_filters(query, from_date, to_date)
                scope_label = f"{display_name} ({label})"

        entries = _fetch_time_entries(query)
        entries, issue_error = _filter_entries_by_issue_keyword(entries, issue_keyword)
        if issue_error:
            return issue_error
        return _format_time_summary(
            entries,
            scope_label=scope_label,
            project_name=project_name,
            date_from=from_date,
            date_to=to_date,
            issue_keyword=issue_keyword.strip() or None,
        )
    except (requests.RequestException, RuntimeError, KeyError) as exc:
        return f"Redmine error: {exc}"


@tool(description=_GET_PROJECT_TIME_LOGGED_DOC)
def get_project_time_logged(
    project_identifier_or_id: str,
    date_range: str = "",
) -> str:
    try:
        resolved = _resolve_project(project_identifier_or_id)
        if isinstance(resolved, str):
            return resolved
        if resolved is None:
            return "Project name or id is required."

        project_id, project_name = resolved
        query = f"project_id={project_id}"
        scope_label = "all users"
        from_date = to_date = None
        if date_range.strip():
            parsed = parse_date_range(date_range)
            if isinstance(parsed, str):
                return parsed
            if parsed is not None:
                from_date, to_date, label = parsed
                query = _append_date_filters(query, from_date, to_date)
                scope_label = f"all users ({label})"

        entries = _fetch_time_entries(query)
        return _format_time_summary(
            entries,
            scope_label=scope_label,
            project_name=project_name,
            date_from=from_date,
            date_to=to_date,
        )
    except (requests.RequestException, RuntimeError, KeyError) as exc:
        return f"Redmine error: {exc}"


def _format_project_member_line(membership: dict) -> str | None:
    roles = membership.get("roles") or []
    role_names = ", ".join(role.get("name", "Unknown") for role in roles if role)
    role_label = role_names or "Member"

    user = membership.get("user")
    if user:
        name = _user_display_name(user)
        login = (user.get("login") or "").strip()
        if login:
            return f"- {name} (login: {login}) — {role_label}"
        return f"- {name} — {role_label}"

    group = membership.get("group")
    if group:
        return f"- Group: {group.get('name', 'Unknown group')} — {role_label}"
    return None


def _fetch_user_project_memberships(user_id: int) -> list[dict]:
    try:
        data = redmine_get(f"/users/{user_id}.json?include=memberships")
        memberships = data.get("user", {}).get("memberships") or []
        if memberships:
            return memberships
    except requests.HTTPError as exc:
        if exc.response is None or exc.response.status_code not in (403, 404):
            raise

    projects: list[dict] = []
    for project in _fetch_all_projects():
        for membership in _fetch_project_memberships(project["id"]):
            member_user = membership.get("user")
            if member_user and member_user.get("id") == user_id:
                projects.append(
                    {
                        "project": project,
                        "roles": membership.get("roles") or [],
                    }
                )
                break
    return projects


def _format_project_time_by_member(
    entries: list[dict],
    *,
    project_name: str,
    project_id: int,
    date_from: str | None = None,
    date_to: str | None = None,
) -> str:
    if not entries:
        header = f"Project: {project_name} (#{project_id})"
        if date_from and date_to:
            return f"{header}\nNo time logged in range {date_from} to {date_to}."
        return f"{header}\nNo time logged on this project."

    by_user: dict[str, dict[str, float | int]] = {}
    for entry in entries:
        user = entry.get("user") or {}
        name = _user_display_name(user) if user else "Unknown"
        bucket = by_user.setdefault(name, {"hours": 0.0, "entries": 0})
        bucket["hours"] = float(bucket["hours"]) + float(entry.get("hours") or 0)
        bucket["entries"] = int(bucket["entries"]) + 1

    lines = [
        f"Project: {project_name} (#{project_id})",
        f"Total hours (all members): {sum(e.get('hours', 0) for e in entries):.2f}",
        f"Entries: {len(entries)}",
        "",
        "Developer | Hours | Entries",
        "---------|-------|--------",
    ]
    for name, stats in sorted(by_user.items(), key=lambda item: -float(item[1]["hours"])):
        lines.append(f"{name} | {float(stats['hours']):.2f} | {int(stats['entries'])}")

    if date_from and date_to:
        lines.append(f"\nDate range: {date_from} to {date_to}")
    else:
        lines.append("\nDate range: all time")
    return "\n".join(lines)


_LIST_PROJECT_MEMBERS_DOC = """List members/developers on a specific Redmine project with their roles.

Use when the PM asks who is on the team, list members, list developers, or project roster
for a named project — NOT list_my_projects (that is only the current user's projects)."""

_LIST_USER_PROJECTS_DOC = """List Redmine projects a named user belongs to.

Use when the PM asks which projects a specific person is on (e.g. 'projects of uzair aziz').
NOT list_my_projects — that returns only the current user's projects."""

_GET_PROJECT_TIME_BY_MEMBER_DOC = f"""Per-developer time report for a Redmine project.

Use when the PM asks for time log by each member/developer, per-user breakdown, or a complete
report with individual developer hours on a project. For project total only, use get_project_time_logged.
date_range: optional — {DATE_RANGE_HINT}."""


@tool(description=_LIST_PROJECT_MEMBERS_DOC)
def list_project_members(project_identifier_or_id: str) -> str:
    try:
        resolved = _resolve_project(project_identifier_or_id)
        if isinstance(resolved, str):
            return resolved
        if resolved is None:
            return "Project name or id is required."

        project_id, project_name = resolved
        memberships = _fetch_project_memberships(project_id)
        lines = [f"Project: {project_name} (#{project_id})", "Members:"]
        for membership in memberships:
            line = _format_project_member_line(membership)
            if line:
                lines.append(line)

        if len(lines) == 2:
            return f"Project: {project_name} (#{project_id})\nNo members found."
        return "\n".join(lines)
    except (requests.RequestException, RuntimeError, KeyError) as exc:
        return f"Redmine error: {exc}"


@tool(description=_LIST_USER_PROJECTS_DOC)
def list_user_projects(user_name: str) -> str:
    try:
        resolved_user = _resolve_user(user_name)
        if isinstance(resolved_user, str):
            return resolved_user
        user_id, display_name = resolved_user

        memberships = _fetch_user_project_memberships(user_id)
        if not memberships:
            return f"{display_name} is not a member of any visible projects."

        lines = [f"Projects for {display_name}:"]
        for membership in memberships:
            project = membership.get("project") or {}
            project_id = project.get("id")
            project_name = project.get("name", "N/A")
            roles = membership.get("roles") or []
            role_names = ", ".join(role.get("name", "Member") for role in roles if role)
            role_label = role_names or "Member"
            if project_id:
                lines.append(f"- #{project_id}: {project_name} — {role_label}")
            else:
                lines.append(f"- {project_name} — {role_label}")

        return "\n".join(lines)
    except (requests.RequestException, RuntimeError, KeyError) as exc:
        return f"Redmine error: {exc}"


@tool(description=_GET_PROJECT_TIME_BY_MEMBER_DOC)
def get_project_time_by_member(
    project_identifier_or_id: str,
    date_range: str = "",
) -> str:
    try:
        resolved = _resolve_project(project_identifier_or_id)
        if isinstance(resolved, str):
            return resolved
        if resolved is None:
            return "Project name or id is required."

        project_id, project_name = resolved
        query = f"project_id={project_id}"
        from_date = to_date = None
        if date_range.strip():
            parsed = parse_date_range(date_range)
            if isinstance(parsed, str):
                return parsed
            if parsed is not None:
                from_date, to_date, _label = parsed
                query = _append_date_filters(query, from_date, to_date)

        entries = _fetch_time_entries(query)
        return _format_project_time_by_member(
            entries,
            project_name=project_name,
            project_id=project_id,
            date_from=from_date,
            date_to=to_date,
        )
    except (requests.RequestException, RuntimeError, KeyError) as exc:
        return f"Redmine error: {exc}"


@tool
def get_last_logged_day(user_name: str = "") -> str:
    """Return the most recent date on which time was logged (current user, or a named user).

    Use when the PM asks when they last logged time, what is the last day they logged time,
    their most recent time entry, or when a colleague last logged time — NOT for hours on a
    specific date range (use get_my_time_logged / get_user_time_logged for that).
    """
    try:
        if user_name.strip():
            resolved_user = _resolve_user(user_name)
            if isinstance(resolved_user, str):
                return resolved_user
            user_id, display_name = resolved_user
            user_id_query = str(user_id)
        else:
            user_id_query = "me"
            display_name = None

        entry = _fetch_last_logged_entry(user_id_query)
        if entry is None:
            if display_name:
                return f"No time entries found for {display_name}."
            return "You have no time entries in Redmine."

        return _format_last_logged_day(entry, display_name=display_name)
    except (requests.RequestException, RuntimeError, KeyError) as exc:
        return f"Redmine error: {exc}"


def _resolve_activity_id(activity_name: str) -> int | str:
    global _activity_cache
    if _activity_cache is None:
        data = redmine_get("/enumerations/time_entry_activities.json")
        _activity_cache = {
            a["name"].lower(): a["id"] for a in data.get("time_entry_activities", [])
        }

    env_value = os.getenv("REDMINE_DEFAULT_ACTIVITY_ID", "").strip()
    if env_value.isdigit():
        return int(env_value)

    if not activity_name or not activity_name.strip():
        if _activity_cache:
            for preferred in ("development", "design", "meeting"):
                if preferred in _activity_cache:
                    return _activity_cache[preferred]
            return next(iter(_activity_cache.values()))
        return "No time entry activities available."

    needle = activity_name.strip().lower()
    if needle in _activity_cache:
        return _activity_cache[needle]

    for name, activity_id in _activity_cache.items():
        if needle in name or name in needle:
            return activity_id

    available = ", ".join(sorted(_activity_cache))
    return f"Unknown activity '{activity_name}'. Available: {available}."


def _normalize_custom_field_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _time_entry_custom_fields() -> dict[str, int]:
    global _time_entry_custom_field_cache
    if _time_entry_custom_field_cache is not None:
        return _time_entry_custom_field_cache

    data = redmine_get("/custom_fields.json")
    fields: dict[str, int] = {}
    for field in data.get("custom_fields", []):
        if field.get("customized_type") != "time_entry":
            continue
        field_id = field.get("id")
        name = field.get("name")
        if field_id and name:
            fields[_normalize_custom_field_name(name)] = int(field_id)
    _time_entry_custom_field_cache = fields
    return fields


def _resolve_time_entry_custom_field_id(
    env_var: str,
    names: tuple[str, ...],
) -> int | str | None:
    env_value = os.getenv(env_var, "").strip()
    if env_value.isdigit():
        return int(env_value)

    try:
        fields = _time_entry_custom_fields()
    except (requests.RequestException, RuntimeError, KeyError, ValueError) as exc:
        return (
            f"Could not resolve time-entry custom fields from Redmine: {exc}. "
            f"Set {env_var} in .env."
        )

    normalized_names = [_normalize_custom_field_name(name) for name in names]
    for normalized_name in normalized_names:
        if normalized_name in fields:
            return fields[normalized_name]

    for field_name, field_id in fields.items():
        if any(name in field_name or field_name in name for name in normalized_names):
            return field_id

    return None


def _time_entry_custom_fields_payload(draft: dict) -> list[dict] | str:
    custom_fields: list[dict] = []

    if draft.get("billable_hours") is not None:
        field_id = _resolve_time_entry_custom_field_id(
            "REDMINE_BILLABLE_HOURS_CUSTOM_FIELD_ID",
            ("Billable Hours", "Billable", "Billable Time"),
        )
        if isinstance(field_id, str):
            return field_id
        if field_id is not None:
            custom_fields.append(
                {"id": field_id, "value": str(draft.get("billable_hours"))}
            )

    time_entry_comments = (draft.get("time_entry_comments") or "").strip()
    if time_entry_comments:
        field_id = _resolve_time_entry_custom_field_id(
            "REDMINE_TIME_ENTRY_COMMENTS_CUSTOM_FIELD_ID",
            ("Time Entry Comments", "Time Entry Comment", "Comments"),
        )
        if isinstance(field_id, str):
            return field_id
        if field_id is not None:
            custom_fields.append({"id": field_id, "value": time_entry_comments})

    return custom_fields


def _resolve_issue_for_time_entry(issue_id_or_keyword: str) -> tuple[int, str] | str:
    raw = (issue_id_or_keyword or "").strip()
    if not raw:
        return "issue_id_or_keyword is required."

    numeric = _resolve_issue_id_reference(raw)
    if isinstance(numeric, str):
        return numeric
    if numeric is not None:
        issue_id = next(iter(numeric))
        try:
            issue = redmine_get(f"/issues/{issue_id}.json")["issue"]
            return issue_id, issue.get("subject", "")
        except (requests.RequestException, RuntimeError, KeyError) as exc:
            return f"Redmine error: {exc}"

    resolved = _resolve_issue_ids_by_keyword(raw)
    if isinstance(resolved, str):
        return resolved
    if not resolved:
        return f"No issues found matching '{raw}'."
    if len(resolved) > 1:
        ids = ", ".join(f"#{i}" for i in sorted(resolved)[:5])
        return f"Multiple issues match '{raw}': {ids}. Please specify an issue number."
    issue_id = next(iter(resolved))
    try:
        issue = redmine_get(f"/issues/{issue_id}.json")["issue"]
        return issue_id, issue.get("subject", "")
    except (requests.RequestException, RuntimeError, KeyError) as exc:
        return f"Redmine error: {exc}"


def _format_time_draft_message(draft: dict) -> str:
    hours = draft.get("hours")
    hours_line = f"{hours:g}h" if isinstance(hours, (int, float)) and hours > 0 else "(not specified)"
    lines = [
        "DRAFT TIME ENTRY (not logged yet)",
        f"Issue: #{draft['issue_id']} — {draft.get('issue_subject', '')}",
        f"Hours: {hours_line}",
        f"Date: {draft.get('spent_on', date.today().isoformat())}",
        f"Activity: {draft.get('activity_name', 'Default')}",
    ]
    if draft.get("comments"):
        lines.append(f"Comments: {draft['comments']}")
    if draft.get("billable_hours") is not None:
        lines.append(f"Billable Hours: {draft['billable_hours']}")
    if draft.get("time_entry_comments"):
        lines.append(f"Time Entry Comments: {draft['time_entry_comments']}")
    if not isinstance(hours, (int, float)) or hours <= 0:
        lines.append("How many hours should I log?")
    else:
        lines.append("Tell me when to log this time in Redmine.")
    return "\n".join(lines)


def create_time_entry_from_draft(draft: dict) -> str:
    hours = draft.get("hours")
    if not isinstance(hours, (int, float)) or hours <= 0:
        return "Cannot log time: hours are required. How many hours should I log?"

    activity_id = draft.get("activity_id")
    if not activity_id:
        resolved = _resolve_activity_id(draft.get("activity", ""))
        if isinstance(resolved, str):
            return resolved
        activity_id = resolved

    payload = {
        "time_entry": {
            "issue_id": draft["issue_id"],
            "hours": hours,
            "spent_on": draft.get("spent_on") or date.today().isoformat(),
            "activity_id": activity_id,
            "comments": draft.get("comments", ""),
        }
    }
    custom_fields = _time_entry_custom_fields_payload(draft)
    if isinstance(custom_fields, str):
        return custom_fields
    if custom_fields:
        payload["time_entry"]["custom_fields"] = custom_fields
    try:
        data = redmine_post("/time_entries.json", payload)
        entry = data.get("time_entry") if isinstance(data, dict) else None
        if not isinstance(entry, dict) or "id" not in entry:
            return f"Redmine did not return a created time entry. Unexpected response: {data}"
        return (
            f"Logged {hours:g}h on #{draft['issue_id']} ({draft.get('issue_subject', '')})\n"
            f"Date: {draft.get('spent_on', date.today().isoformat())}\n"
            f"Time entry id: {entry['id']}"
        )
    except (requests.RequestException, RuntimeError) as exc:
        return f"Redmine error: {exc}"


def _time_entry_create_succeeded(answer: str) -> bool:
    return answer.startswith("Logged ") and "Time entry id:" in answer


def _build_time_entry_draft(
    issue_id_or_keyword: str,
    hours: float,
    spent_on: str = "",
    activity: str = "",
    comments: str = "",
    billable_hours: float | None = None,
    time_entry_comments: str = "",
) -> tuple[dict, str | None]:
    resolved_issue = _resolve_issue_for_time_entry(issue_id_or_keyword)
    if isinstance(resolved_issue, str):
        return {}, resolved_issue
    issue_id, issue_subject = resolved_issue

    activity_id = _resolve_activity_id(activity)
    if isinstance(activity_id, str):
        return {}, activity_id

    activity_name = activity.strip() or "Default"
    if activity.strip():
        global _activity_cache
        if _activity_cache:
            for name in _activity_cache:
                if _activity_cache[name] == activity_id:
                    activity_name = name
                    break

    spent = spent_on.strip() or date.today().isoformat()
    draft = {
        "kind": "time_entry",
        "issue_id": issue_id,
        "issue_subject": issue_subject,
        "hours": hours if hours > 0 else None,
        "spent_on": spent,
        "activity": activity,
        "activity_id": activity_id,
        "activity_name": activity_name.title() if activity_name != "Default" else activity_name,
        "comments": comments.strip(),
        "billable_hours": billable_hours,
        "time_entry_comments": time_entry_comments.strip(),
    }
    return draft, None


@tool
def draft_time_entry(
    issue_id_or_keyword: str,
    hours: float = 0,
    spent_on: str = "",
    activity: str = "",
    comments: str = "",
    billable_hours: float | None = None,
    time_entry_comments: str = "",
) -> str:
    """Draft a time entry for PM review. Does NOT log in Redmine until approved.

    hours is required before logging — if missing or zero, the tool asks how many hours."""
    try:
        draft, error = _build_time_entry_draft(
            issue_id_or_keyword,
            hours,
            spent_on,
            activity,
            comments,
            billable_hours,
            time_entry_comments,
        )
        if error:
            return error

        _store_pending_draft(draft)
        if not isinstance(draft.get("hours"), (int, float)) or draft["hours"] <= 0:
            return (
                f"To log time on #{draft['issue_id']} ({draft['issue_subject']}), "
                "how many hours should I log?"
            )
        return _format_time_draft_message(draft)
    except (requests.RequestException, RuntimeError, KeyError) as exc:
        return f"Redmine error: {exc}"


@tool
def log_time(
    issue_id_or_keyword: str,
    hours: float,
    spent_on: str = "",
    activity: str = "",
    comments: str = "",
    billable_hours: float | None = None,
    time_entry_comments: str = "",
) -> str:
    """Log time on an issue immediately (no draft/approve step). hours is required."""
    if not isinstance(hours, (int, float)) or hours <= 0:
        return (
            "Hours are required to log time. "
            "How many hours should I log? Use draft_time_entry if you want to confirm first."
        )
    try:
        draft, error = _build_time_entry_draft(
            issue_id_or_keyword,
            hours,
            spent_on,
            activity,
            comments,
            billable_hours,
            time_entry_comments,
        )
        if error:
            return error
        return create_time_entry_from_draft(draft)
    except (requests.RequestException, RuntimeError, KeyError) as exc:
        return f"Redmine error: {exc}"


@tool
def update_pending_time_entry(
    hours: float = 0,
    spent_on: str = "",
    activity: str = "",
    comments: str = "",
    billable_hours: float | None = None,
    time_entry_comments: str = "",
) -> str:
    """Update the pending time-entry draft with hours, date, activity, comments, or billable fields."""
    draft = get_pending_draft()
    if not draft or draft.get("kind") != "time_entry":
        return "There is no pending time-entry draft to update."

    if isinstance(hours, (int, float)) and hours > 0:
        draft["hours"] = hours
    if spent_on.strip():
        draft["spent_on"] = spent_on.strip()
    if activity.strip():
        activity_id = _resolve_activity_id(activity)
        if isinstance(activity_id, str):
            return activity_id
        draft["activity"] = activity.strip()
        draft["activity_id"] = activity_id
        draft["activity_name"] = activity.strip().title()
    if comments.strip():
        draft["comments"] = comments.strip()
    if billable_hours is not None:
        draft["billable_hours"] = billable_hours
    if time_entry_comments.strip():
        draft["time_entry_comments"] = time_entry_comments.strip()

    _store_pending_draft(draft)
    return _format_time_draft_message(draft)


@tool
def draft_issue(
    title: str,
    description: str,
    priority: str = "Normal",
    project_identifier_or_id: str = "",
    assign_to_me: bool = True,
) -> str:
    """Draft a new issue for PM review. Does NOT create in Redmine until approved."""
    try:
        project_id, project_name, project_error = _resolve_draft_project(
            project_identifier_or_id
        )
        if project_error:
            return project_error

        draft = {
            "kind": "issue",
            "title": title,
            "description": description,
            "priority": priority,
            "project_id": project_id,
            "project_name": project_name,
            "project_identifier_or_id": project_identifier_or_id.strip()
            or _default_project_identifier(),
            "assign_to_me": assign_to_me,
        }
        _store_pending_draft(draft)
        return _format_draft_message(draft)
    except (requests.RequestException, RuntimeError, KeyError) as exc:
        return f"Redmine error: {exc}"


@tool
def update_pending_issue_project(project_identifier_or_id: str) -> str:
    """Set or change the project on the pending issue draft."""
    draft = get_pending_draft()
    if not draft or draft.get("kind") == "time_entry":
        return "There is no pending issue draft to update."

    resolved = _resolve_project(project_identifier_or_id)
    if isinstance(resolved, str):
        return resolved
    if resolved is None:
        return f"No project found matching '{project_identifier_or_id}'."

    project_id, project_name = resolved
    draft["project_id"] = project_id
    draft["project_name"] = project_name
    _store_pending_draft(draft)
    return (
        f"Updated draft project to {project_name} (#{project_id}). "
        "Ask me to approve it when you want it created."
    )


@tool
def create_issue_in_redmine(
    title: str,
    description: str,
    project_identifier_or_id: str,
    priority: str = "Normal",
    assign_to_me: bool = True,
) -> str:
    """Create a new issue in Redmine immediately (no draft/approve step)."""
    try:
        project_id, project_name, project_error = _resolve_draft_project(
            project_identifier_or_id
        )
        if project_error:
            return project_error
        if not project_id:
            return (
                "Project is required. Pass project_identifier_or_id or set "
                "REDMINE_DEFAULT_PROJECT_ID in .env."
            )

        draft = {
            "title": title,
            "description": description,
            "priority": priority,
            "project_id": project_id,
            "project_name": project_name,
            "assign_to_me": assign_to_me,
        }
        result = create_issue_from_draft(draft)
        clear_pending_draft()
        return result
    except (requests.RequestException, RuntimeError, KeyError) as exc:
        return f"Redmine error: {exc}"


@tool
def approve_pending_draft() -> str:
    """Create/log the currently pending draft after the user approves it."""
    draft = get_pending_draft()
    if draft is None:
        return "There is no pending draft to approve."

    try:
        if draft.get("kind") == "time_entry":
            if not isinstance(draft.get("hours"), (int, float)) or draft["hours"] <= 0:
                return (
                    f"To log time on #{draft['issue_id']}, how many hours should I log?"
                )
            answer = create_time_entry_from_draft(draft)
            if _time_entry_create_succeeded(answer):
                clear_pending_draft()
            return answer

        if not draft.get("project_id"):
            return _approve_without_project_message()
        answer = create_issue_from_draft(draft)
        if _issue_create_succeeded(answer):
            clear_pending_draft()
        return answer
    except (requests.RequestException, RuntimeError, KeyError) as exc:
        return f"Redmine error: {exc}"


ALL_TOOLS = {
    "get_my_profile": get_my_profile,
    "list_my_projects": list_my_projects,
    "list_project_members": list_project_members,
    "list_user_projects": list_user_projects,
    "list_my_issues": list_my_issues,
    "search_issues": search_issues,
    "search_high_priority_issues": search_high_priority_issues,
    "get_issue": get_issue,
    "get_project_status": get_project_status,
    "get_project_manager": get_project_manager,
    "get_my_time_logged": get_my_time_logged,
    "get_user_time_logged": get_user_time_logged,
    "get_project_time_logged": get_project_time_logged,
    "get_project_time_by_member": get_project_time_by_member,
    "get_last_logged_day": get_last_logged_day,
    "draft_time_entry": draft_time_entry,
    "log_time": log_time,
    "update_pending_time_entry": update_pending_time_entry,
    "draft_issue": draft_issue,
    "update_pending_issue_project": update_pending_issue_project,
    "create_issue_in_redmine": create_issue_in_redmine,
    "approve_pending_draft": approve_pending_draft,
}

REDMINE_SYSTEM_PROMPT = """
You are a Redmine PM Assistant. Understand the user's intent and choose the
single best Redmine tool through native tool calling.

Rules:
- Use tools for Redmine-related requests. Do not answer Redmine facts from memory.
- Do not print JSON, XML, or pseudo tool-call text.
- After a tool runs, return the tool result or a short user-facing summary.
- If a request is ambiguous, choose the safest read-only tool or ask for the missing detail.
- Use draft tools for write operations unless the user explicitly asks to create/log immediately.

Routing:
- Own profile/account/who am I -> get_my_profile.
- My projects / projects I am in -> list_my_projects.
- Members/developers/roster on a project -> list_project_members.
- Projects for a named person -> list_user_projects.
- My issues/tasks/open work -> list_my_issues.
- Search by issue keyword/topic/subject -> search_issues; numeric issue refs -> get_issue.
- Urgent/high/blocker/critical issues -> search_high_priority_issues.
- Project health/status/open or closed counts -> get_project_status.
- Who manages/leads/is PM of a project -> get_project_manager.
- My logged hours -> get_my_time_logged.
- A named person's logged hours -> get_user_time_logged.
- Total/team hours on a project -> get_project_time_logged.
- Per-person/member/developer time breakdown on a project -> get_project_time_by_member.
- Most recent date someone logged time -> get_last_logged_day, not a time-summary tool.
- Log/add/record time -> draft_time_entry unless the user explicitly asks to log now and gives hours.
- Create/raise/report an issue -> draft_issue unless the user explicitly asks to create immediately.

Date and entity handling:
- Put dates and ranges in date_range, not project_identifier_or_id.
- Use empty strings for optional filters that were not mentioned.
- If a project name contains a date-looking word such as "all" in "Phun For All", keep the full project name.
- If hours are missing for time logging, call draft_time_entry with hours=0 so the tool asks for hours.

Pending drafts:
- Pending-draft follow-ups are Redmine-related.
- If the context says an issue draft is pending, use update_pending_issue_project for project changes and approve_pending_draft when the user approves creation.
- If the context says a time-entry draft is pending, use update_pending_time_entry for missing or changed details and approve_pending_draft when the user approves logging.
- For pending time entries, map "billable hours" to billable_hours and "time entry comment(s)" to time_entry_comments.
- Do not start a new draft for a short follow-up that clearly refers to the pending draft.

Pending clarifications:
- If the context says a previous tool is missing one argument, treat the user's reply as that missing value unless the user clearly asks a new unrelated question.
- Preserve the previous tool arguments when completing the clarified tool call.
"""


def build_redmine_agent():
    return create_agent(
        model=get_chat_model(),
        tools=list(ALL_TOOLS.values()),
        system_prompt=REDMINE_SYSTEM_PROMPT,
    )


def main() -> None:
    if not _redmine_configured():
        print(_redmine_error())
        return

    base = os.getenv("REDMINE_URL", "").rstrip("/")
    print("Redmine PM Assistant (live data only)")
    print(f"Connected to: {base}")
    print("Type 'quit' to exit.\n")

    agent = build_redmine_agent()

    while True:
        question = input("PM> ").strip()

        if not question or question.lower() in {"quit", "exit", "q"}:
            break

        pending_context_parts = [
            context
            for context in (_pending_draft_context(), _pending_clarification_context())
            if context
        ]
        if pending_context_parts:
            pending_context = "\n\n".join(pending_context_parts)
            question_for_agent = f"{pending_context}\n\nUser reply: {question}"
        else:
            question_for_agent = question

        answer = run_agent_turn(agent, question_for_agent, ALL_TOOLS)
        print(f"\n[Redmine Agent]: {answer}\n")

    print("Bye!")


if __name__ == "__main__":
    main()
