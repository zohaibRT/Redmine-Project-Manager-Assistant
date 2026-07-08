"""Tests for draft/approve time entry flow (mocked Redmine POST)."""

from unittest.mock import MagicMock, patch

import pytest

from agent import (
    approve_pending_draft,
    clear_pending_draft,
    create_time_entry_from_draft,
    draft_time_entry,
    get_pending_draft,
    log_time,
    redmine_post,
    update_pending_time_entry,
)

_REDMINE_ENV = {
    "REDMINE_URL": "https://redmine.example.com",
    "REDMINE_API_KEY": "test-key",
}

_AUTOMATE_DBT_ISSUE = {
    "issue": {
        "id": 174022,
        "subject": "Automate dbt flow",
        "status": {"name": "New"},
    }
}


def _mock_response(status_code: int, json_body: object = None, text: str = "") -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.text = text
    response.is_redirect = False
    response.headers = {}
    if json_body is None:
        response.json.side_effect = ValueError("no json")
    else:
        response.json.return_value = json_body
    return response


def test_draft_time_entry_missing_hours_prompts() -> None:
    clear_pending_draft()
    with patch("agent._resolve_issue_for_time_entry", return_value=(174022, "Automate dbt flow")), patch(
        "agent._resolve_activity_id", return_value=9
    ):
        result = draft_time_entry.invoke(
            {
                "issue_id_or_keyword": "174022",
                "hours": 0,
                "spent_on": "",
                "activity": "",
                "comments": "",
            }
        )

    assert "how many hours" in result.lower()
    pending = get_pending_draft()
    assert pending is not None
    assert pending["kind"] == "time_entry"
    assert pending["issue_id"] == 174022
    assert pending["hours"] is None
    clear_pending_draft()


def test_log_time_missing_hours_prompts() -> None:
    result = log_time.invoke(
        {
            "issue_id_or_keyword": "174022",
            "hours": 0,
            "spent_on": "",
            "activity": "",
            "comments": "",
        }
    )
    assert "hours are required" in result.lower()


@patch("agent.redmine_post")
@patch("agent._resolve_activity_id", return_value=9)
@patch("agent._resolve_issue_for_time_entry", return_value=(174022, "Automate dbt flow"))
def test_draft_time_entry_approve_posts(
    _mock_issue: object,
    _mock_activity: object,
    mock_post: object,
) -> None:
    clear_pending_draft()
    mock_post.return_value = {"time_entry": {"id": 555, "hours": 4.0}}

    with patch("agent._resolve_issue_for_time_entry", return_value=(174022, "Automate dbt flow")), patch(
        "agent._resolve_activity_id", return_value=9
    ):
        draft_time_entry.invoke(
            {
                "issue_id_or_keyword": "174022",
                "hours": 4,
                "spent_on": "2026-07-03",
                "activity": "",
                "comments": "dbt work",
            }
        )

    result = approve_pending_draft.invoke({})
    assert result is not None
    assert "Logged 4h on #174022" in result
    assert get_pending_draft() is None
    mock_post.assert_called_once()
    payload = mock_post.call_args[0][1]
    assert payload["time_entry"]["issue_id"] == 174022
    assert payload["time_entry"]["hours"] == 4
    clear_pending_draft()


def test_pending_time_draft_hours_then_approve() -> None:
    clear_pending_draft()
    with patch("agent._resolve_issue_for_time_entry", return_value=(174022, "Automate dbt flow")), patch(
        "agent._resolve_activity_id", return_value=9
    ):
        draft_time_entry.invoke(
            {
                "issue_id_or_keyword": "174022",
                "hours": 0,
                "spent_on": "",
                "activity": "",
                "comments": "",
            }
        )

    hours_result = update_pending_time_entry.invoke({"hours": 8})
    assert hours_result is not None
    assert "DRAFT TIME ENTRY" in hours_result
    assert "8h" in hours_result

    with patch("agent.redmine_post", return_value={"time_entry": {"id": 777}}):
        approve_result = approve_pending_draft.invoke({})

    assert approve_result is not None
    assert "Logged 8h on #174022" in approve_result
    assert get_pending_draft() is None
    clear_pending_draft()


def test_pending_time_draft_survives_rejected_approve() -> None:
    clear_pending_draft()
    with patch(
        "agent._resolve_issue_for_time_entry",
        return_value=(174022, "Automate dbt flow"),
    ), patch("agent._resolve_activity_id", return_value=9):
        draft_time_entry.invoke(
            {
                "issue_id_or_keyword": "174022",
                "hours": 8,
                "spent_on": "",
                "activity": "",
                "comments": "",
            }
        )

    with patch(
        "agent.redmine_post",
        side_effect=RuntimeError(
            "Redmine rejected the request (HTTP 422): Billable Hours cannot be blank"
        ),
    ):
        approve_result = approve_pending_draft.invoke({})

    assert "Billable Hours cannot be blank" in approve_result
    assert get_pending_draft() is not None
    clear_pending_draft()


def test_update_pending_time_entry_sets_required_custom_fields() -> None:
    clear_pending_draft()
    with patch(
        "agent._resolve_issue_for_time_entry",
        return_value=(174022, "Automate dbt flow"),
    ), patch("agent._resolve_activity_id", return_value=9):
        draft_time_entry.invoke(
            {
                "issue_id_or_keyword": "174022",
                "hours": 8,
                "spent_on": "",
                "activity": "",
                "comments": "",
            }
        )

    result = update_pending_time_entry.invoke(
        {
            "billable_hours": 0,
            "time_entry_comments": "Working on the dbt automation",
        }
    )

    assert "Billable Hours: 0" in result
    assert "Time Entry Comments: Working on the dbt automation" in result
    pending = get_pending_draft()
    assert pending is not None
    assert pending["billable_hours"] == 0
    assert pending["time_entry_comments"] == "Working on the dbt automation"
    clear_pending_draft()


def test_time_entry_custom_fields_added_to_payload() -> None:
    draft = {
        "issue_id": 174022,
        "issue_subject": "Automate dbt flow",
        "hours": 8,
        "activity_id": 9,
        "comments": "native note",
        "billable_hours": 0,
        "time_entry_comments": "Working on the dbt automation",
    }

    with patch.dict(
        "os.environ",
        {
            "REDMINE_BILLABLE_HOURS_CUSTOM_FIELD_ID": "30",
            "REDMINE_TIME_ENTRY_COMMENTS_CUSTOM_FIELD_ID": "31",
        },
        clear=False,
    ), patch("agent.redmine_post", return_value={"time_entry": {"id": 777}}) as mock_post:
        result = create_time_entry_from_draft(draft)

    assert "Logged 8h" in result
    payload = mock_post.call_args[0][1]
    assert payload["time_entry"]["custom_fields"] == [
        {"id": 30, "value": "0"},
        {"id": 31, "value": "Working on the dbt automation"},
    ]


def test_create_time_entry_from_draft_requires_hours() -> None:
    result = create_time_entry_from_draft(
        {"issue_id": 174022, "issue_subject": "Automate dbt flow", "hours": None, "activity_id": 9}
    )
    assert "hours are required" in result.lower()


def test_redmine_post_time_entry_success() -> None:
    created = {"time_entry": {"id": 42, "hours": 2.5}}
    with patch.dict("os.environ", _REDMINE_ENV), patch(
        "agent.requests.post", return_value=_mock_response(201, created)
    ):
        assert redmine_post("/time_entries.json", {"time_entry": {}}) == created


def test_redmine_post_time_entry_surfaces_422() -> None:
    body = {"errors": ["Hours cannot be blank"]}
    with patch.dict("os.environ", _REDMINE_ENV), patch(
        "agent.requests.post", return_value=_mock_response(422, body)
    ):
        with pytest.raises(RuntimeError) as excinfo:
            redmine_post("/time_entries.json", {"time_entry": {}})
    assert "Hours cannot be blank" in str(excinfo.value)
