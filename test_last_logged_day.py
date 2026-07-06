"""Tests for get_last_logged_day (most recent spent_on date)."""

from unittest.mock import patch

from agent import (
    _fetch_last_logged_entry,
    _format_last_logged_day,
    get_last_logged_day,
)

_ENTRY_OLD = {
    "id": 1,
    "spent_on": "2026-06-15",
    "hours": 2.0,
    "issue": {"id": 100},
}
_ENTRY_NEW = {
    "id": 2,
    "spent_on": "2026-07-02",
    "hours": 3.5,
    "issue": {"id": 12345},
}


def test_format_last_logged_day_current_user() -> None:
    msg = _format_last_logged_day(_ENTRY_NEW)
    assert msg == "Your most recent time log was on 2026-07-02 (3.5h on #12345)."


def test_format_last_logged_day_named_user() -> None:
    msg = _format_last_logged_day(_ENTRY_NEW, display_name="Abdul Rauf")
    assert msg == "Abdul Rauf's most recent time log was on 2026-07-02 (3.5h on #12345)."


def test_fetch_last_logged_entry_uses_sort() -> None:
    def fake_redmine_get(path: str) -> dict:
        if "sort=spent_on:desc" in path:
            return {"time_entries": [_ENTRY_NEW], "total_count": 1}
        raise AssertionError(f"unexpected path: {path}")

    with patch("agent.redmine_get", side_effect=fake_redmine_get):
        entry = _fetch_last_logged_entry("me")
    assert entry == _ENTRY_NEW


def test_fetch_last_logged_entry_fallback_client_max() -> None:
    calls: list[str] = []

    def fake_redmine_get(path: str) -> dict:
        calls.append(path)
        if "sort=spent_on:desc" in path:
            return {"time_entries": [_ENTRY_OLD], "total_count": 3}
        if "sort=spent_on:asc" in path:
            return {"time_entries": [_ENTRY_NEW]}
        raise AssertionError(f"unexpected path: {path}")

    def fake_fetch(query: str) -> list[dict]:
        assert query == "user_id=me"
        return [_ENTRY_OLD, _ENTRY_NEW, {"spent_on": "2026-05-01", "hours": 1.0}]

    with patch("agent.redmine_get", side_effect=fake_redmine_get), patch(
        "agent._fetch_time_entries", side_effect=fake_fetch
    ):
        entry = _fetch_last_logged_entry("me")

    assert entry == _ENTRY_NEW
    assert any("sort=spent_on:desc" in c for c in calls)
    assert any("sort=spent_on:asc" in c for c in calls)


def test_get_last_logged_day_no_entries() -> None:
    with patch("agent._fetch_last_logged_entry", return_value=None):
        result = get_last_logged_day.invoke({"user_name": ""})
    assert result == "You have no time entries in Redmine."


def test_get_last_logged_day_returns_latest() -> None:
    with patch("agent._fetch_last_logged_entry", return_value=_ENTRY_NEW):
        result = get_last_logged_day.invoke({"user_name": ""})
    assert "2026-07-02" in result
    assert "3.5h on #12345" in result


def main() -> None:
    test_format_last_logged_day_current_user()
    test_format_last_logged_day_named_user()
    test_fetch_last_logged_entry_uses_sort()
    test_fetch_last_logged_entry_fallback_client_max()
    test_get_last_logged_day_no_entries()
    test_get_last_logged_day_returns_latest()
    print("test_last_logged_day: all OK")


if __name__ == "__main__":
    main()
