"""Tests for new session tools: members, user projects, per-member time, billable."""

from unittest.mock import patch

from agent import (
    _aggregate_billable_hours,
    _format_project_time_by_member,
    _format_time_summary,
    _fuzzy_user_score,
    _resolve_user_fuzzy,
    get_project_time_by_member,
    list_project_members,
    list_user_projects,
)


def test_aggregate_billable_hours() -> None:
    entries = [
        {
            "hours": 4.0,
            "custom_fields": [{"name": "Billable", "value": "1"}],
        },
        {
            "hours": 2.0,
            "custom_fields": [{"name": "Billable", "value": "0"}],
        },
        {"hours": 1.0},
    ]
    billable, non_billable, seen = _aggregate_billable_hours(entries)
    assert seen is True
    assert billable == 4.0
    assert non_billable == 2.0


def test_format_time_summary_shows_billable_breakdown() -> None:
    entries = [
        {
            "hours": 3.0,
            "spent_on": "2026-07-03",
            "activity": {"name": "Dev"},
            "custom_fields": [{"name": "Billable", "value": "Yes"}],
        }
    ]
    summary = _format_time_summary(entries, scope_label="Rauf")
    assert "By billable: 3.00h billable" in summary


def test_format_time_summary_no_billable_field_message() -> None:
    entries = [
        {
            "hours": 3.0,
            "spent_on": "2026-07-03",
            "activity": {"name": "Dev"},
        }
    ]
    summary = _format_time_summary(entries, scope_label="Rauf")
    assert "Billable breakdown: not available" in summary


def test_fuzzy_user_last_name_typo() -> None:
    user = {
        "id": 10,
        "firstname": "Mehboob",
        "lastname": "Destagir",
        "login": "mehboob.destagir",
    }
    score = _fuzzy_user_score(user, "mehboob destgir")
    assert score >= 0.8
    result = _resolve_user_fuzzy([user], "mehboob destgir", "mehboob destgir")
    assert result == (10, "Mehboob Destagir")


@patch("agent._fetch_project_memberships")
@patch("agent._resolve_project", return_value=(1576, "Association Analytics"))
def test_list_project_members(mock_resolve: object, mock_memberships: object) -> None:
    mock_memberships.return_value = [
        {
            "user": {
                "id": 1,
                "firstname": "Ali",
                "lastname": "Dev",
                "login": "ali.dev",
            },
            "roles": [{"name": "Developer"}],
        }
    ]
    result = list_project_members.invoke(
        {"project_identifier_or_id": "Association Analytics"}
    )
    assert "Project: Association Analytics (#1576)" in result
    assert "Ali Dev" in result
    assert "Developer" in result


@patch(
    "agent._fetch_user_project_memberships",
    return_value=[
        {
            "project": {"id": 1576, "name": "Association Analytics"},
            "roles": [{"name": "Developer"}],
        }
    ],
)
@patch("agent._resolve_user", return_value=(99, "Uzair Aziz"))
def test_list_user_projects(mock_user: object, mock_memberships: object) -> None:
    result = list_user_projects.invoke({"user_name": "uzair aziz"})
    assert "Projects for Uzair Aziz" in result
    assert "#1576: Association Analytics" in result


@patch("agent._fetch_time_entries")
@patch("agent._resolve_project", return_value=(1576, "Association Analytics"))
def test_get_project_time_by_member(mock_resolve: object, mock_entries: object) -> None:
    mock_entries.return_value = [
        {
            "hours": 8.0,
            "user": {"firstname": "Zahid", "lastname": "Abbas"},
        },
        {
            "hours": 4.0,
            "user": {"firstname": "Rauf", "lastname": "Khan"},
        },
        {
            "hours": 4.0,
            "user": {"firstname": "Rauf", "lastname": "Khan"},
        },
    ]
    result = get_project_time_by_member.invoke(
        {"project_identifier_or_id": "Association Analytics", "date_range": ""}
    )
    assert "Project: Association Analytics (#1576)" in result
    assert "Developer | Hours | Entries" in result
    assert "Zahid Abbas | 8.00 | 1" in result
    assert "Rauf Khan | 8.00 | 2" in result
    assert "Total hours (all members): 16.00" in result


def test_format_project_time_by_member_table() -> None:
    entries = [
        {"hours": 5.5, "user": {"firstname": "A", "lastname": "One"}},
        {"hours": 2.0, "user": {"firstname": "B", "lastname": "Two"}},
    ]
    result = _format_project_time_by_member(
        entries,
        project_name="Association Analytics",
        project_id=1576,
    )
    assert "A One | 5.50 | 1" in result
    assert "B Two | 2.00 | 1" in result
