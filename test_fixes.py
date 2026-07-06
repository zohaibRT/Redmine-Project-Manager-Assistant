"""Unit tests for the bug fixes: date parsing, date display, and fuzzy project matching."""

from datetime import date

from agent import (
    _format_time_summary,
    _normalize_project_key,
    _resolve_project_fuzzy,
)
from tool_helpers import parse_date_range

_REFERENCE = date(2026, 7, 3)

_PROJECTS = [
    {"id": 1411, "name": "Refocus AI", "identifier": "refocus-ai"},
    {"id": 1576, "name": "Association Analytics", "identifier": "association-analytics"},
    {"id": 42, "name": "Timesheet", "identifier": "tyf"},
]


def test_may_is_full_month() -> None:
    from_date, to_date, label = parse_date_range("may", reference=_REFERENCE)
    assert from_date == "2026-05-01"
    assert to_date == "2026-05-31"
    assert label == "may 2026"


def test_may_2026_is_full_month() -> None:
    from_date, to_date, _ = parse_date_range("may 2026", reference=_REFERENCE)
    assert (from_date, to_date) == ("2026-05-01", "2026-05-31")


def test_normalize_project_key() -> None:
    assert _normalize_project_key("Refocus AI") == "refocusai"
    assert _normalize_project_key("RefocusAI") == "refocusai"
    assert _normalize_project_key("association-analytics") == "associationanalytics"


def test_fuzzy_matches_refocusai_to_refocus_ai() -> None:
    result = _resolve_project_fuzzy(_PROJECTS, "RefocusAI", "refocusai")
    assert result == (1411, "Refocus AI")


def test_fuzzy_no_match_returns_none() -> None:
    assert _resolve_project_fuzzy(_PROJECTS, "Zzz Nonexistent", "zzznonexistent") is None


def test_time_summary_omits_bogus_range_without_filter() -> None:
    entries = [
        {"hours": 2.0, "spent_on": "2020-03-31", "activity": {"name": "Dev"}},
        {"hours": 3.0, "spent_on": "2020-05-28", "activity": {"name": "Dev"}},
    ]
    summary = _format_time_summary(entries, scope_label="you")
    assert "Date range: all time" in summary
    assert "2020-05-28" not in summary


def test_time_summary_shows_filter_range() -> None:
    entries = [
        {"hours": 2.0, "spent_on": "2026-05-26", "activity": {"name": "Dev"}},
    ]
    summary = _format_time_summary(
        entries, scope_label="you (may 2026)", date_from="2026-05-01", date_to="2026-05-31"
    )
    assert "Date range: 2026-05-01 to 2026-05-31" in summary


def main() -> None:
    test_may_is_full_month()
    test_may_2026_is_full_month()
    test_normalize_project_key()
    test_fuzzy_matches_refocusai_to_refocus_ai()
    test_fuzzy_no_match_returns_none()
    test_time_summary_omits_bogus_range_without_filter()
    test_time_summary_shows_filter_range()
    print("test_fixes: all OK")


if __name__ == "__main__":
    main()
