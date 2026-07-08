"""Tests for single-agent tool registration (replaces multi-agent routing tests)."""

from unittest.mock import patch

from agent import ALL_TOOLS, build_redmine_agent

EXPECTED_TOOL_NAMES = frozenset(
    {
        "get_my_profile",
        "list_my_projects",
        "list_project_members",
        "list_user_projects",
        "list_my_issues",
        "search_issues",
        "search_high_priority_issues",
        "get_issue",
        "get_project_status",
        "get_project_manager",
        "get_my_time_logged",
        "get_user_time_logged",
        "get_project_time_logged",
        "get_project_time_by_member",
        "get_last_logged_day",
        "draft_time_entry",
        "log_time",
        "update_pending_time_entry",
        "draft_issue",
        "update_pending_issue_project",
        "create_issue_in_redmine",
        "approve_pending_draft",
    }
)


def test_all_tools_registered() -> None:
    assert set(ALL_TOOLS.keys()) == EXPECTED_TOOL_NAMES
    assert len(ALL_TOOLS) == 22


def test_build_redmine_agent() -> None:
    with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=False):
        agent = build_redmine_agent()
    assert agent is not None


def main() -> None:
    test_all_tools_registered()
    print("ALL_TOOLS: 22 tools registered — OK")
    test_build_redmine_agent()
    print("build_redmine_agent: OK")


if __name__ == "__main__":
    main()
