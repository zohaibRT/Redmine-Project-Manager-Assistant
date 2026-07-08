"""Helpers for the Redmine PM Assistant."""

import re
from datetime import date, datetime, timedelta

DATE_RANGE_KEYWORDS = frozenset(
    {
        "today",
        "yesterday",
        "this week",
        "last week",
        "this month",
        "last month",
        "weekend",
        "this weekend",
        "last weekend",
    }
)

MONTH_NAMES: dict[str, int] = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}

NO_DATE_FILTER_PHRASES = frozenset({"all", "all time"})

DATE_RANGE_HINT = (
    'natural language or YYYY-MM-DD / YYYY-MM-DD..YYYY-MM-DD; "" or "all" for no filter'
)

# Obvious aliases → canonical phrases handled by parse_date_range (keep small).
_DATE_ALIASES: dict[str, str] = {
    "last day": "yesterday",
    "past day": "yesterday",
    "previous day": "yesterday",
    "the last day": "yesterday",
    "the past day": "yesterday",
}

_DASH_DATE_RE = re.compile(r"^(\d{1,2})-(\d{1,2})-(\d{4})$")

_SINGLE_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d-%m-%Y",
    "%m-%d-%Y",
    "%B %d, %Y",
    "%b %d, %Y",
    "%B %d %Y",
    "%b %d %Y",
    "%d %B %Y",
    "%d %b %Y",
)

_MONTH_PATTERN = "|".join(
    re.escape(name) for name in sorted(MONTH_NAMES, key=len, reverse=True)
)
_MONTH_ONLY_RE = re.compile(rf"^({_MONTH_PATTERN})$")
_MONTH_YEAR_RE = re.compile(rf"^({_MONTH_PATTERN})\s+(\d{{4}})$")
_LAST_MONTH_RE = re.compile(rf"^last\s+({_MONTH_PATTERN})$")

_PERSON_NAME_RE = re.compile(r"^[a-z][a-z'-]*(?:\s+[a-z][a-z'-]*){0,3}$", re.IGNORECASE)

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_RANGE_RE = re.compile(
    r"^(?P<start>\d{4}-\d{2}-\d{2})\s*(?:\.\.|to|-)\s*(?P<end>\d{4}-\d{2}-\d{2})$"
)
_FROM_ISO_TILL_TODAY_RE = re.compile(
    r"^(?:from\s+)?(?P<start>\d{4}-\d{2}-\d{2})\s+(?:till|until|to)\s+today$",
    re.IGNORECASE,
)


def is_person_name_token(value: str) -> bool:
    """True when value looks like a person name rather than a project identifier."""
    if not value or not value.strip():
        return False
    if is_date_range_token(value):
        return False
    raw = value.strip()
    if raw.isdigit():
        return False
    if "_" in raw or "-" in raw:
        return False
    return bool(_PERSON_NAME_RE.match(raw))


def _canonicalize_date_phrase(normalized: str) -> str:
    """Map small alias set to canonical phrases; leave others unchanged."""
    if normalized in _DATE_ALIASES:
        return _DATE_ALIASES[normalized]
    return normalized


def _parse_dash_separated_date(value: str) -> date | None:
    """Parse D-M-Y or M-D-Y dash dates; prefer DD-MM-YYYY when ambiguous (e.g. Pakistan)."""
    match = _DASH_DATE_RE.match(value.strip())
    if not match:
        return None

    part_a = int(match.group(1))
    part_b = int(match.group(2))
    year = int(match.group(3))

    candidates: list[tuple[int, int]] = []
    if part_a > 12 and part_b <= 12:
        candidates.append((part_a, part_b))
    elif part_b > 12 and part_a <= 12:
        candidates.append((part_b, part_a))
    elif part_a <= 12 and part_b <= 12:
        candidates.append((part_a, part_b))
        if part_a != part_b:
            candidates.append((part_b, part_a))
    else:
        candidates.append((part_a, part_b))
        if part_a != part_b:
            candidates.append((part_b, part_a))

    seen: set[tuple[int, int]] = set()
    for day, month in candidates:
        key = (day, month)
        if key in seen:
            continue
        seen.add(key)
        try:
            return date(year, month, day)
        except ValueError:
            continue
    return None


def _try_parse_single_date(value: str) -> tuple[str, str, str] | None:
    """Best-effort single-date parse via strptime patterns."""
    stripped = value.strip()

    dash_parsed = _parse_dash_separated_date(stripped)
    if dash_parsed is not None:
        iso = _format_iso(dash_parsed)
        return iso, iso, iso

    for fmt in _SINGLE_DATE_FORMATS:
        if fmt in {"%d-%m-%Y", "%m-%d-%Y"}:
            continue
        try:
            parsed = datetime.strptime(stripped, fmt).date()
        except ValueError:
            continue
        iso = _format_iso(parsed)
        return iso, iso, iso
    return None


def _matches_month_date_range(normalized: str) -> bool:
    return bool(
        _MONTH_ONLY_RE.match(normalized)
        or _MONTH_YEAR_RE.match(normalized)
        or _LAST_MONTH_RE.match(normalized)
    )


def _resolve_month_year(month: int, today: date, *, last: bool = False) -> int:
    """Pick the calendar year for a bare or 'last <month>' phrase."""
    if last:
        if today.month > month:
            return today.year
        return today.year - 1
    if today.month >= month:
        return today.year
    return today.year - 1


def _parse_month_phrase(
    normalized: str, today: date
) -> tuple[int, int, str] | None:
    """Return (year, month, label) for month-name phrases, or None."""
    last_match = _LAST_MONTH_RE.match(normalized)
    if last_match:
        month = MONTH_NAMES[last_match.group(1)]
        year = _resolve_month_year(month, today, last=True)
        month_name = last_match.group(1)
        return year, month, f"last {month_name} {year}"

    year_match = _MONTH_YEAR_RE.match(normalized)
    if year_match:
        month = MONTH_NAMES[year_match.group(1)]
        year = int(year_match.group(2))
        month_name = year_match.group(1)
        return year, month, f"{month_name} {year}"

    month_match = _MONTH_ONLY_RE.match(normalized)
    if month_match:
        month = MONTH_NAMES[month_match.group(1)]
        year = _resolve_month_year(month, today)
        month_name = month_match.group(1)
        return year, month, f"{month_name} {year}"

    return None


def is_date_range_token(value: str) -> bool:
    """True when value is a date/range phrase, not a project name."""
    if not value or not value.strip():
        return False

    normalized = _canonicalize_date_phrase(value.strip().lower())
    if normalized in NO_DATE_FILTER_PHRASES:
        return True
    if normalized in DATE_RANGE_KEYWORDS:
        return True
    if value.strip().lower() in _DATE_ALIASES:
        return True
    if _matches_month_date_range(normalized):
        return True
    if _ISO_DATE_RE.match(normalized):
        return True
    if _ISO_RANGE_RE.match(normalized):
        return True
    if _FROM_ISO_TILL_TODAY_RE.match(normalized):
        return True
    if _DASH_DATE_RE.match(value.strip()):
        return True
    return False


def _format_iso(d: date) -> str:
    return d.isoformat()


def _week_bounds(d: date) -> tuple[date, date]:
    """Monday–Sunday week containing d."""
    start = d - timedelta(days=d.weekday())
    return start, start + timedelta(days=6)


def _weekend_bounds(d: date) -> tuple[date, date]:
    """Saturday–Sunday of the calendar week containing d."""
    week_start, _ = _week_bounds(d)
    return week_start + timedelta(days=5), week_start + timedelta(days=6)


def _last_weekend_bounds(d: date) -> tuple[date, date]:
    """Saturday–Sunday of the calendar week before the one containing d."""
    week_start, _ = _week_bounds(d)
    last_week_start = week_start - timedelta(days=7)
    return last_week_start + timedelta(days=5), last_week_start + timedelta(days=6)


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    return date(year, month, 1), next_month - timedelta(days=1)


def parse_date_range(
    date_range: str,
    *,
    reference: date | None = None,
) -> tuple[str, str, str] | None | str:
    """Parse a date phrase into (from_date, to_date, label) as YYYY-MM-DD strings.

    Returns None when no date filter should be applied (empty, "all", "all time",
    or unrecognized natural language — interpreted generously as no filter).
    Returns an error string only for invalid ISO ranges (start after end).
    """
    if not date_range or not date_range.strip():
        return None

    raw = date_range.strip()
    normalized = _canonicalize_date_phrase(raw.lower())
    if normalized in NO_DATE_FILTER_PHRASES:
        return None

    today = reference or date.today()

    if normalized == "today":
        return _format_iso(today), _format_iso(today), "today"
    if normalized == "yesterday":
        day = today - timedelta(days=1)
        return _format_iso(day), _format_iso(day), "yesterday"
    if normalized == "this week":
        start, end = _week_bounds(today)
        return _format_iso(start), _format_iso(end), "this week"
    if normalized == "last week":
        start, _ = _week_bounds(today)
        last_start = start - timedelta(days=7)
        last_end = last_start + timedelta(days=6)
        return _format_iso(last_start), _format_iso(last_end), "last week"
    if normalized == "this month":
        start, end = _month_bounds(today.year, today.month)
        return _format_iso(start), _format_iso(end), "this month"
    if normalized == "last month":
        first_of_month = date(today.year, today.month, 1)
        prev_month_end = first_of_month - timedelta(days=1)
        start, end = _month_bounds(prev_month_end.year, prev_month_end.month)
        return _format_iso(start), _format_iso(end), "last month"
    if normalized in {"weekend", "this weekend"}:
        start, end = _weekend_bounds(today)
        return _format_iso(start), _format_iso(end), "this weekend"
    if normalized == "last weekend":
        start, end = _last_weekend_bounds(today)
        return _format_iso(start), _format_iso(end), "last weekend"

    month_parsed = _parse_month_phrase(normalized, today)
    if month_parsed is not None:
        year, month, label = month_parsed
        start, end = _month_bounds(year, month)
        return _format_iso(start), _format_iso(end), label

    if _ISO_DATE_RE.match(normalized):
        return normalized, normalized, normalized

    range_match = _ISO_RANGE_RE.match(normalized)
    if range_match:
        start = range_match.group("start")
        end = range_match.group("end")
        if start > end:
            return "Invalid date range: start date must be on or before end date."
        label = f"{start} to {end}"
        return start, end, label

    till_today_match = _FROM_ISO_TILL_TODAY_RE.match(normalized)
    if till_today_match:
        start = till_today_match.group("start")
        end = _format_iso(today)
        if start > end:
            return "Invalid date range: start date must be on or before end date."
        label = f"{start} to today"
        return start, end, label

    single_parsed = _try_parse_single_date(raw)
    if single_parsed is not None:
        return single_parsed

    # Unrecognized: no date filter rather than blocking the tool call.
    return None


def run_agent_turn(agent, question: str, allowed_tools: dict) -> str:
    """Let the LangChain agent choose and execute tools, then return its answer."""
    _ = allowed_tools

    try:
        result = agent.invoke(
            {
                "messages": [
                    {"role": "user", "content": question}
                ]
            }
        )
    except Exception as exc:
        return f"Agent execution error: {exc}"

    last_message = result["messages"][-1]
    content = last_message.content or ""

    if isinstance(content, str):
        return content
    return str(content)
