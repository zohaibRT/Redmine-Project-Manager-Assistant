"""Tests for draft/approve issue creation flow (mocked Redmine POST)."""

from unittest.mock import MagicMock, patch

import pytest

import agent
from agent import (
    approve_pending_draft,
    clear_pending_draft,
    create_issue,
    create_issue_from_draft,
    draft_issue,
    get_pending_draft,
    redmine_post,
    update_pending_issue_project,
)

_REDMINE_ENV = {
    "REDMINE_URL": "https://redmine.example.com",
    "REDMINE_API_KEY": "test-key",
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


def test_approval_is_handled_by_pending_draft_tool() -> None:
    clear_pending_draft()
    assert "no pending draft" in approve_pending_draft.invoke({}).lower()


def test_draft_issue_stores_pending_draft() -> None:
    clear_pending_draft()
    with patch(
        "agent._resolve_draft_project",
        return_value=(1576, "Association Analytics", None),
    ):
        result = draft_issue.invoke(
            {
                "title": "Automate dbt flow",
                "description": "Build skills for agent dbt layers",
                "priority": "Normal",
                "project_identifier_or_id": "Association Analytics",
                "assign_to_me": True,
            }
    )

    assert "DRAFT ISSUE" in result
    assert "Tell me when to create it in Redmine" in result
    pending = get_pending_draft()
    assert pending is not None
    assert pending["title"] == "Automate dbt flow"
    assert pending["project_id"] == 1576
    assert pending["assign_to_me"] is True
    clear_pending_draft()


def test_create_issue_from_draft_without_project() -> None:
    result = create_issue_from_draft(
        {
            "title": "Test",
            "description": "Body",
            "priority": "Normal",
            "project_id": None,
            "assign_to_me": True,
        }
    )
    assert "Cannot create issue: no project specified" in result


@patch("agent.create_issue")
@patch("agent._get_current_user_id", return_value=878)
@patch("agent._resolve_priority_id", return_value=2)
def test_create_issue_from_draft_posts_to_redmine(
    _mock_priority: object,
    _mock_user: object,
    mock_create: object,
) -> None:
    mock_create.return_value = {
        "issue": {
            "id": 9999,
            "subject": "Automate dbt flow",
            "priority": {"name": "Normal"},
            "project": {"name": "Association Analytics"},
            "assigned_to": {"name": "Test User"},
        }
    }

    with patch.dict("os.environ", {"REDMINE_URL": "https://redmine.example.com"}):
        result = create_issue_from_draft(
            {
                "title": "Automate dbt flow",
                "description": "Build skills for agent dbt layers",
                "priority": "Normal",
                "project_id": 1576,
                "project_name": "Association Analytics",
                "assign_to_me": True,
            }
        )

    assert "Created issue #9999" in result
    assert "https://redmine.example.com/issues/9999" in result
    mock_create.assert_called_once_with(
        1576,
        "Automate dbt flow",
        "Build skills for agent dbt layers",
        assigned_to_id=878,
        priority_id=2,
    )


def test_draft_issue_notes_missing_project() -> None:
    clear_pending_draft()
    with patch("agent._resolve_draft_project", return_value=(None, None, None)):
        result = draft_issue.invoke(
            {
                "title": "New task",
                "description": "Details",
                "priority": "Normal",
                "project_identifier_or_id": "",
                "assign_to_me": True,
            }
        )

    assert "Project: (not specified" in result
    pending = get_pending_draft()
    assert pending is not None
    assert pending["project_id"] is None
    clear_pending_draft()


def test_pending_draft_use_project_updates_project() -> None:
    clear_pending_draft()
    with patch(
        "agent._resolve_draft_project",
        return_value=(None, None, None),
    ):
        draft_issue.invoke(
            {
                "title": "Automate dbt flow",
                "description": "Build skills for agent dbt layers",
                "priority": "Normal",
                "project_identifier_or_id": "",
                "assign_to_me": True,
            }
        )

    with patch(
        "agent._resolve_project",
        return_value=(1576, "Association Analytics"),
    ):
        result = update_pending_issue_project.invoke(
            {"project_identifier_or_id": "Association Analytics"}
        )

    assert "Updated draft project to Association Analytics (#1576)" in result
    pending = get_pending_draft()
    assert pending is not None
    assert pending["project_id"] == 1576
    assert pending["project_name"] == "Association Analytics"
    clear_pending_draft()


@patch("agent.create_issue")
@patch("agent._get_current_user_id", return_value=878)
@patch("agent._resolve_priority_id", return_value=2)
@patch("agent._resolve_project", return_value=(1576, "Association Analytics"))
def test_pending_draft_use_project_and_approved_creates_issue(
    _mock_resolve: object,
    _mock_priority: object,
    _mock_user: object,
    mock_create: object,
) -> None:
    clear_pending_draft()
    with patch("agent._resolve_draft_project", return_value=(None, None, None)):
        draft_issue.invoke(
            {
                "title": "Automate dbt flow",
                "description": "Build skills for agent dbt layers",
                "priority": "Normal",
                "project_identifier_or_id": "",
                "assign_to_me": True,
            }
        )

    mock_create.return_value = {
        "issue": {
            "id": 9999,
            "subject": "Automate dbt flow",
            "priority": {"name": "Normal"},
            "project": {"name": "Association Analytics"},
            "assigned_to": {"name": "Test User"},
        }
    }

    with patch.dict("os.environ", {"REDMINE_URL": "https://redmine.example.com"}):
        update_pending_issue_project.invoke(
            {"project_identifier_or_id": "Association Analytics"}
        )
        result = approve_pending_draft.invoke({})

    assert "Created issue #9999" in result
    assert get_pending_draft() is None
    mock_create.assert_called_once_with(
        1576,
        "Automate dbt flow",
        "Build skills for agent dbt layers",
        assigned_to_id=878,
        priority_id=2,
    )
    clear_pending_draft()


def test_redmine_post_returns_json_on_success() -> None:
    created = {"issue": {"id": 12345, "subject": "Hello"}}
    with patch.dict("os.environ", _REDMINE_ENV), patch(
        "agent.requests.post", return_value=_mock_response(201, created)
    ):
        assert redmine_post("/issues.json", {"issue": {}}) == created


def test_redmine_post_surfaces_422_errors() -> None:
    body = {"errors": ["Tracker cannot be blank", "Subject cannot be blank"]}
    with patch.dict("os.environ", _REDMINE_ENV), patch(
        "agent.requests.post", return_value=_mock_response(422, body)
    ):
        with pytest.raises(RuntimeError) as excinfo:
            redmine_post("/issues.json", {"issue": {}})
    message = str(excinfo.value)
    assert "422" in message
    assert "Tracker cannot be blank" in message
    assert "Subject cannot be blank" in message


def test_create_issue_from_draft_surfaces_validation_error() -> None:
    with patch("agent._resolve_priority_id", return_value=2), patch(
        "agent._get_current_user_id", return_value=878
    ), patch(
        "agent.create_issue",
        side_effect=RuntimeError(
            "Redmine rejected the request (HTTP 422): Tracker cannot be blank"
        ),
    ):
        result = create_issue_from_draft(
            {
                "title": "T",
                "description": "D",
                "priority": "Normal",
                "project_id": 1576,
                "assign_to_me": True,
            }
        )
    assert "Redmine error:" in result
    assert "Tracker cannot be blank" in result


def test_create_issue_from_draft_handles_missing_issue_key() -> None:
    with patch("agent._resolve_priority_id", return_value=2), patch(
        "agent._get_current_user_id", return_value=878
    ), patch("agent.create_issue", return_value={"issues": []}):
        result = create_issue_from_draft(
            {
                "title": "T",
                "description": "D",
                "priority": "Normal",
                "project_id": 1576,
                "assign_to_me": True,
            }
        )
    assert "did not return a created issue" in result
    assert "'issue'" not in result


def test_create_issue_includes_tracker_id_from_env() -> None:
    agent._tracker_cache = None
    agent._tracker_lookup_done = False
    captured: dict = {}

    def _fake_post(path: str, payload: dict) -> dict:
        captured["path"] = path
        captured["payload"] = payload
        return {"issue": {"id": 1, "subject": "T"}}

    with patch.dict("os.environ", {"REDMINE_DEFAULT_TRACKER_ID": "3"}), patch(
        "agent.redmine_post", side_effect=_fake_post
    ):
        create_issue(1576, "T", "D", assigned_to_id=878, priority_id=2)

    assert captured["payload"]["issue"]["tracker_id"] == 3
    assert captured["payload"]["issue"]["project_id"] == 1576


def main() -> None:
    test_approval_is_handled_by_pending_draft_tool()
    print("approve_pending_draft — OK")
    test_draft_issue_stores_pending_draft()
    print("draft_issue stores pending draft — OK")
    test_create_issue_from_draft_without_project()
    print("create_issue_from_draft without project — OK")
    test_create_issue_from_draft_posts_to_redmine()
    print("create_issue_from_draft POST — OK")
    test_draft_issue_notes_missing_project()
    print("draft_issue missing project note — OK")
    test_pending_draft_use_project_updates_project()
    print("pending draft + use project — OK")
    test_pending_draft_use_project_and_approved_creates_issue()
    print("pending draft + use project + approved — OK")
    test_redmine_post_returns_json_on_success()
    print("redmine_post 201 success — OK")
    test_redmine_post_surfaces_422_errors()
    print("redmine_post 422 readable error — OK")
    test_create_issue_from_draft_surfaces_validation_error()
    print("create_issue_from_draft surfaces validation error — OK")
    test_create_issue_from_draft_handles_missing_issue_key()
    print("create_issue_from_draft missing 'issue' key — OK")
    test_create_issue_includes_tracker_id_from_env()
    print("create_issue includes tracker_id — OK")


if __name__ == "__main__":
    main()
