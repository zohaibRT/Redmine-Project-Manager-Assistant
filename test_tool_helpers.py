"""Tests for date parsing and project-name disambiguation."""

from datetime import date
from unittest.mock import patch

from agent import _resolve_project
from tool_helpers import is_date_range_token, parse_date_range


def test_parse_date_range_all_returns_none() -> None:
    assert parse_date_range("all") is None
    assert parse_date_range("ALL") is None


def test_parse_date_range_all_time_returns_none() -> None:
    assert parse_date_range("all time") is None
    assert parse_date_range("  All Time  ") is None


def test_parse_date_range_empty_returns_none() -> None:
    assert parse_date_range("") is None
    assert parse_date_range("   ") is None


def test_parse_date_range_today() -> None:
    ref = date(2026, 7, 3)
    result = parse_date_range("today", reference=ref)
    assert result == ("2026-07-03", "2026-07-03", "today")


def test_parse_date_range_this_weekend() -> None:
    ref = date(2026, 7, 3)  # Friday
    result = parse_date_range("this weekend", reference=ref)
    assert result == ("2026-07-04", "2026-07-05", "this weekend")


def test_parse_date_range_weekend_alias() -> None:
    ref = date(2026, 7, 3)
    result = parse_date_range("weekend", reference=ref)
    assert result == ("2026-07-04", "2026-07-05", "this weekend")


def test_parse_date_range_last_weekend() -> None:
    ref = date(2026, 7, 3)  # Friday; last weekend was Jun 27–28
    result = parse_date_range("last weekend", reference=ref)
    assert result == ("2026-06-27", "2026-06-28", "last weekend")


def test_parse_date_range_last_day() -> None:
    ref = date(2026, 7, 3)
    result = parse_date_range("last day", reference=ref)
    assert result == ("2026-07-02", "2026-07-02", "yesterday")


def test_parse_date_range_unrecognized_returns_none() -> None:
    assert parse_date_range("sometime soon") is None


def test_parse_date_range_from_iso_till_today() -> None:
    ref = date(2026, 7, 3)
    result = parse_date_range("from 2026-06-08 till today", reference=ref)
    assert result == ("2026-06-08", "2026-07-03", "2026-06-08 to today")


def test_parse_date_range_iso_till_today_without_from() -> None:
    ref = date(2026, 7, 3)
    result = parse_date_range("2026-06-08 till today", reference=ref)
    assert result == ("2026-06-08", "2026-07-03", "2026-06-08 to today")


def test_is_date_range_token_from_till_today() -> None:
    assert is_date_range_token("from 2026-06-08 till today") is True
    assert is_date_range_token("2026-06-08 till today") is True


def test_is_date_range_token_last_day() -> None:
    assert is_date_range_token("last day") is True


def test_is_date_range_token_weekend() -> None:
    assert is_date_range_token("this weekend") is True
    assert is_date_range_token("last weekend") is True
    assert is_date_range_token("weekend") is True


def test_is_date_range_token_all() -> None:
    assert is_date_range_token("all") is True
    assert is_date_range_token("all time") is True


def test_is_date_range_token_phun_for_all_is_not_date() -> None:
    assert is_date_range_token("phun for all") is False
    assert is_date_range_token("Phun For All") is False


def test_parse_date_range_dd_mm_yyyy() -> None:
    result = parse_date_range("03-07-2026")
    assert result == ("2026-07-03", "2026-07-03", "2026-07-03")


def test_is_date_range_token_dd_mm_yyyy() -> None:
    assert is_date_range_token("03-07-2026") is True


def test_resolve_project_association_analytics() -> None:
    projects = [
        {"id": 1, "name": "Web Analytics Dashboard", "identifier": "web-analytics"},
        {"id": 1576, "name": "Association Analytics", "identifier": "association-analytics"},
        {"id": 99, "name": "Other Project", "identifier": "other"},
    ]
    with patch("agent._fetch_all_projects", return_value=projects):
        result = _resolve_project("Association analytics")
    assert result == (1576, "Association Analytics")


def test_resolve_project_phun_for_all_substring() -> None:
    projects = [
        {
            "id": 1325,
            "name": "Phun For All - Sean Burns",
            "identifier": "phun-for-all-sean-burns",
        },
        {"id": 99, "name": "Other Project", "identifier": "other"},
    ]
    with patch("agent._fetch_all_projects", return_value=projects):
        result = _resolve_project("phun for all")
    assert result == (1325, "Phun For All - Sean Burns")


def main() -> None:
    test_parse_date_range_all_returns_none()
    print("parse_date_range('all') -> None — OK")
    test_parse_date_range_all_time_returns_none()
    print("parse_date_range('all time') -> None — OK")
    test_parse_date_range_empty_returns_none()
    print("parse_date_range('') -> None — OK")
    test_parse_date_range_today()
    print("parse_date_range('today') — OK")
    test_parse_date_range_this_weekend()
    print("parse_date_range('this weekend') — OK")
    test_parse_date_range_weekend_alias()
    print("parse_date_range('weekend') — OK")
    test_parse_date_range_last_weekend()
    print("parse_date_range('last weekend') — OK")
    test_parse_date_range_last_day()
    print("parse_date_range('last day') — OK")
    test_parse_date_range_unrecognized_returns_none()
    print("parse_date_range unrecognized -> None — OK")
    test_parse_date_range_dd_mm_yyyy()
    print("parse_date_range('03-07-2026') -> 2026-07-03 — OK")
    test_is_date_range_token_dd_mm_yyyy()
    print("is_date_range_token('03-07-2026') — OK")
    test_is_date_range_token_last_day()
    print("is_date_range_token('last day') — OK")
    test_is_date_range_token_weekend()
    print("is_date_range_token weekend phrases — OK")
    test_is_date_range_token_all()
    print("is_date_range_token('all') — OK")
    test_is_date_range_token_phun_for_all_is_not_date()
    print("'phun for all' is not a date token — OK")
    test_resolve_project_association_analytics()
    print("_resolve_project('Association analytics') -> #1576 — OK")
    test_resolve_project_phun_for_all_substring()
    print("_resolve_project('phun for all') -> #1325 — OK")


if __name__ == "__main__":
    main()
