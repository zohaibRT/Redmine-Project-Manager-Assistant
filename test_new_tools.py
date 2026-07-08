"""Tests for new session tools: members, user projects, per-member time, billable."""

from unittest.mock import patch

from agent import (
    AmbiguousUserMessage,
    _aggregate_billable_hours,
    _format_project_time_by_member,
    _format_time_summary,
    _fuzzy_user_score,
    _pending_clarification_context,
    _resolve_user,
    _resolve_user_fuzzy,
    clear_pending_clarification,
    get_project_time_by_member,
    get_pending_clarification,
    get_user_time_logged,
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
    billable, non_billable, unclassified, seen = _aggregate_billable_hours(entries)
    assert seen is True
    assert billable == 4.0
    assert non_billable == 2.0
    assert unclassified == 1.0


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
    assert "Billable breakdown for classified entries: 3.00h billable" in summary


def test_format_time_summary_shows_unclassified_billable_hours() -> None:
    entries = [
        {
            "hours": 19.0,
            "spent_on": "2026-07-03",
            "activity": {"name": "Dev"},
            "custom_fields": [{"name": "Billable", "value": "Yes"}],
        },
        {
            "hours": 41.0,
            "spent_on": "2026-07-04",
            "activity": {"name": "Dev"},
            "custom_fields": [{"name": "Billable", "value": "No"}],
        },
        {
            "hours": 586.75,
            "spent_on": "2026-07-05",
            "activity": {"name": "Dev"},
        },
    ]
    summary = _format_time_summary(
        entries,
        scope_label="Zohaib Hussain",
        project_name="Association Analytics",
    )
    assert "Total hours (Zohaib Hussain): 646.75" in summary
    assert "19.00h billable, 41.00h non-billable" in summary
    assert "Unclassified hours: 586.75h" in summary


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


def test_resolve_user_url_encodes_search_query() -> None:
    captured: list[str] = []

    def fake_redmine_get(path: str) -> dict:
        captured.append(path)
        return {
            "users": [
                {
                    "id": 99,
                    "firstname": "Omar",
                    "lastname": "Ali Khan",
                    "login": "omar.ali",
                }
            ]
        }

    with patch("agent._users_api_search_allowed", None), patch(
        "agent.redmine_get", side_effect=fake_redmine_get
    ):
        result = _resolve_user("Omar Ali Khan")

    assert result == (99, "Omar Ali Khan")
    assert captured[0].startswith("/users.json?name=~Omar%20Ali%20Khan")


def test_get_user_time_logged_stores_ambiguous_user_clarification() -> None:
    clear_pending_clarification()
    with patch(
        "agent._resolve_user",
        return_value=AmbiguousUserMessage(
            "Multiple users match 'zohaib': Zohaib Hussain, zohaib Ali. Please be more specific."
        ),
    ):
        result = get_user_time_logged.invoke(
            {
                "user_name": "zohaib",
                "project_identifier_or_id": "Association Analytics",
                "date_range": "",
                "issue_keyword": "",
            }
        )

    assert "Multiple users match" in result
    pending = get_pending_clarification()
    assert pending is not None
    assert pending["tool"] == "get_user_time_logged"
    assert pending["missing_argument"] == "user_name"
    assert pending["arguments"]["project_identifier_or_id"] == "Association Analytics"
    assert "Previous arguments" in _pending_clarification_context()
    clear_pending_clarification()


def test_get_user_time_logged_success_clears_user_clarification() -> None:
    clear_pending_clarification()
    with patch(
        "agent._resolve_user",
        return_value=AmbiguousUserMessage(
            "Multiple users match 'zohaib': Zohaib Hussain, zohaib Ali. Please be more specific."
        ),
    ):
        get_user_time_logged.invoke(
            {
                "user_name": "zohaib",
                "project_identifier_or_id": "Association Analytics",
                "date_range": "",
                "issue_keyword": "",
            }
        )

    with patch("agent._resolve_user", return_value=(99, "Zohaib Hussain")), patch(
        "agent._resolve_project", return_value=(1576, "Association Analytics")
    ), patch("agent._fetch_time_entries", return_value=[]):
        result = get_user_time_logged.invoke(
            {
                "user_name": "Zohaib Hussain",
                "project_identifier_or_id": "Association Analytics",
                "date_range": "",
                "issue_keyword": "",
            }
        )

    assert "Zohaib Hussain" in result
    assert get_pending_clarification() is None


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
