"""Tests for user-session bug fixes: me resolution, date ranges, issue keyword filter."""

from datetime import date
from unittest.mock import patch

from agent import (
    _filter_entries_by_issue_keyword,
    _resolve_issue_ids_by_keyword,
    _resolve_user,
    _search_issues_by_keyword,
    _user_matches_name,
    get_my_time_logged,
    search_issues,
)
from tool_helpers import is_date_range_token, parse_date_range

_REFERENCE = date(2026, 7, 3)

_CURRENT_USER = {
    "id": 42,
    "firstname": "Zohaib",
    "lastname": "Hussain",
    "login": "zohaib.hussain",
}

_FALSE_ME_MATCHES = [
    {"id": 1, "firstname": "Omer", "lastname": "Mansoor", "login": "omer.m"},
    {"id": 2, "firstname": "Asad", "lastname": "Mehmood", "login": "asad.m"},
    {"id": 3, "firstname": "Asim", "lastname": "Hameed Khan", "login": "asim.h"},
]


def test_resolve_user_me_returns_current_user() -> None:
    with patch("agent.redmine_get", return_value={"user": _CURRENT_USER}):
        result = _resolve_user("me")
    assert result == (42, "Zohaib Hussain")


def test_resolve_user_myself_returns_current_user() -> None:
    with patch("agent.redmine_get", return_value={"user": _CURRENT_USER}):
        result = _resolve_user("MYSELF")
    assert result == (42, "Zohaib Hussain")


def test_resolve_user_current_user_alias() -> None:
    with patch("agent.redmine_get", return_value={"user": _CURRENT_USER}):
        result = _resolve_user("current user")
    assert result == (42, "Zohaib Hussain")


def test_user_matches_name_rejects_me_substring() -> None:
    for user in _FALSE_ME_MATCHES:
        assert _user_matches_name(user, "me") is False


def test_parse_date_range_from_iso_till_today() -> None:
    result = parse_date_range("from 2026-06-08 till today", reference=_REFERENCE)
    assert result == ("2026-06-08", "2026-07-03", "2026-06-08 to today")


def test_parse_date_range_iso_till_today_without_from() -> None:
    result = parse_date_range("2026-06-08 till today", reference=_REFERENCE)
    assert result == ("2026-06-08", "2026-07-03", "2026-06-08 to today")


def test_parse_date_range_from_iso_to_today() -> None:
    result = parse_date_range("from 2026-06-08 to today", reference=_REFERENCE)
    assert result == ("2026-06-08", "2026-07-03", "2026-06-08 to today")


def test_is_date_range_token_from_till_today() -> None:
    assert is_date_range_token("from 2026-06-08 till today") is True
    assert is_date_range_token("2026-06-08 till today") is True


def test_resolve_issue_ids_by_keyword_or_token_match() -> None:
    issues = [
        {"id": 100, "subject": "dbt skills automation"},
        {"id": 101, "subject": "dbt pipeline"},
        {"id": 102, "subject": "skills training"},
    ]

    def fake_redmine_get(path: str) -> dict:
        token = "dbt" if "subject=~dbt" in path else "skills"
        assert f"subject=~{token}" in path
        assert "status_id=*" in path
        matched = [i for i in issues if token in i["subject"].lower()]
        return {"issues": matched, "total_count": len(matched)}

    with patch("agent.redmine_get", side_effect=fake_redmine_get):
        result = _resolve_issue_ids_by_keyword("dbt skills")
    assert result == {100, 101, 102}


def test_resolve_issue_ids_dbt_automatic_flow() -> None:
    issues = [{"id": 174022, "subject": "Automate dbt flow"}]

    def fake_redmine_get(path: str) -> dict:
        if "/issues/174022.json" in path:
            return {"issue": issues[0]}
        return {"issues": issues, "total_count": 1}

    with patch("agent.redmine_get", side_effect=fake_redmine_get):
        result = _resolve_issue_ids_by_keyword("dbt automatic flow")
    assert result == {174022}


def test_resolve_issue_ids_numeric_id() -> None:
    with patch(
        "agent.redmine_get",
        return_value={"issue": {"id": 174022, "subject": "Automate dbt flow"}},
    ):
        result = _resolve_issue_ids_by_keyword("174022")
    assert result == {174022}

    with patch(
        "agent.redmine_get",
        return_value={"issue": {"id": 174022, "subject": "Automate dbt flow"}},
    ):
        result = _resolve_issue_ids_by_keyword("#174022")
    assert result == {174022}


def test_search_issues_finds_automate_dbt_flow() -> None:
    issue = {
        "id": 174022,
        "subject": "Automate dbt flow",
        "status": {"name": "New"},
        "priority": {"name": "Normal"},
        "project": {"name": "Association Analytics"},
        "assigned_to": {"name": "Zohaib Hussain"},
    }

    with patch(
        "agent._search_issues_by_keyword",
        return_value=[(issue, 1.0)],
    ):
        result = search_issues.invoke({"query": "dbt automatic flow"})

    assert "#174022" in result
    assert "Automate dbt flow" in result
    assert "[New]" in result


def test_search_issues_by_keyword_ranks_best_match() -> None:
    issues = [
        {"id": 174022, "subject": "Automate dbt flow"},
        {"id": 100, "subject": "dbt pipeline only"},
    ]

    def fake_redmine_get(path: str) -> dict:
        return {"issues": issues, "total_count": len(issues)}

    with patch("agent.redmine_get", side_effect=fake_redmine_get):
        ranked = _search_issues_by_keyword("dbt flow")
    assert ranked[0][0]["id"] == 174022
    assert ranked[0][1] >= ranked[1][1]


def test_resolve_issue_ids_by_keyword_no_match() -> None:
    with patch(
        "agent.redmine_get",
        return_value={"issues": [{"id": 1, "subject": "other"}], "total_count": 1},
    ):
        result = _resolve_issue_ids_by_keyword("dbt skills")
    assert result == "No issues found matching 'dbt skills'."


def test_filter_entries_by_issue_keyword() -> None:
    entries = [
        {"hours": 2.0, "issue": {"id": 100}},
        {"hours": 3.0, "issue": {"id": 200}},
    ]
    with patch("agent._resolve_issue_ids_by_keyword", return_value={100}):
        filtered, error = _filter_entries_by_issue_keyword(entries, "dbt skills")
    assert error is None
    assert filtered == [{"hours": 2.0, "issue": {"id": 100}}]


def test_get_my_time_logged_with_numeric_issue_keyword() -> None:
    time_entries = [
        {
            "hours": 6.0,
            "spent_on": "2026-06-08",
            "activity": {"name": "Dev"},
            "issue": {"id": 174022},
        },
        {
            "hours": 1.0,
            "spent_on": "2026-06-09",
            "activity": {"name": "Dev"},
            "issue": {"id": 999},
        },
    ]

    with patch("agent._fetch_time_entries", return_value=time_entries), patch(
        "agent.redmine_get",
        return_value={"issue": {"id": 174022, "subject": "Automate dbt flow"}},
    ):
        result = get_my_time_logged.invoke(
            {
                "project_identifier_or_id": "",
                "date_range": "from 2026-06-08 till today",
                "issue_keyword": "174022",
            }
        )

    assert "Total hours (you (2026-06-08 to today)): 6.00" in result
    assert "#174022" in result


def test_get_my_time_logged_with_issue_keyword_and_date_range() -> None:
    time_entries = [
        {
            "hours": 4.0,
            "spent_on": "2026-06-08",
            "activity": {"name": "Dev"},
            "issue": {"id": 170796},
        },
        {
            "hours": 1.0,
            "spent_on": "2026-06-09",
            "activity": {"name": "Dev"},
            "issue": {"id": 999},
        },
    ]

    with patch("agent._fetch_time_entries", return_value=time_entries), patch(
        "agent._resolve_issue_ids_by_keyword", return_value={170796}
    ):
        result = get_my_time_logged.invoke(
            {
                "project_identifier_or_id": "",
                "date_range": "from 2026-06-08 till today",
                "issue_keyword": "dbt skills",
            }
        )

    assert "Total hours (you (2026-06-08 to today)): 4.00" in result
    assert "Issue filter: dbt skills" in result
    assert f"Date range: 2026-06-08 to {date.today().isoformat()}" in result
    assert "#170796" in result
