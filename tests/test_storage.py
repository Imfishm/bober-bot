"""
SQLite persistence: what has to survive a restart, the per-day fixture cache, and the budget rows.
"""

from types import SimpleNamespace

import pytest
from fakes import fixture_item, track

from bober.storage import Storage

DAY = "2026-11-04"


def ref(key="10|goal|30", occurrence=1, category="goal"):
    return SimpleNamespace(key=key, occurrence=occurrence, category=category)


def test_tracked_match_and_events_survive_restart(tmp_path):
    path = str(tmp_path / "bober.db")
    s = Storage(path)
    track(s)
    s.update_tracked(1, last_status="1H", home_goals=1, away_goals=0)
    s.count_match_request(1)
    s.record_event(1, ref(), 10, "30'", 9, "Player 9", message_id=555)
    s.set_meta("own_goal_team_is_beneficiary", "1")
    s.close()

    s = Storage(path)
    try:
        match = s.active_match()
        assert match["fixture_id"] == 1
        assert (match["last_status"], match["home_goals"], match["requests_used"]) == ("1H", 1, 1)
        assert s.posted_event(1, ("10|goal|30", 1))["message_id"] == 555
        assert s.get_meta("own_goal_team_is_beneficiary") == "1"
    finally:
        s.close()


def test_stop_tracking_clears_active(storage):
    track(storage)
    storage.stop_tracking(1)
    assert storage.active_match() is None


def test_retracking_a_fixture_starts_fresh(storage):
    track(storage)
    storage.count_match_request(1)
    storage.record_event(1, ref(), 10, "30'", None, None, message_id=1)
    storage.stop_tracking(1)

    match = track(storage)
    assert match["requests_used"] == 0
    assert storage.posted_rows(1) == []


def test_record_event_ignores_duplicates(storage):
    track(storage)
    storage.record_event(1, ref(), 10, "30'", None, None, message_id=1)
    storage.record_event(1, ref(), 10, "30'", None, None, message_id=2)
    rows = storage.posted_rows(1)
    assert len(rows) == 1 and rows[0]["message_id"] == 1


def test_update_tracked_rejects_unknown_columns(storage):
    track(storage)
    with pytest.raises(ValueError):
        storage.update_tracked(1, active=0)


def test_fixture_day_cache(storage):
    assert not storage.has_fixture_day(DAY)
    storage.save_fixture_day(DAY, [fixture_item(fixture_id=1), fixture_item(fixture_id=2)], 0)
    assert storage.has_fixture_day(DAY)
    assert {f["fixture"]["id"] for f in storage.fixtures_for_day(DAY)} == {1, 2}
    assert storage.cached_fixture(DAY, 2)["fixture"]["id"] == 2
    assert storage.cached_fixture("2026-11-05", 2) is None

    # Saving the day again replaces it
    storage.save_fixture_day(DAY, [fixture_item(fixture_id=3)], 0)
    assert [f["fixture"]["id"] for f in storage.fixtures_for_day(DAY)] == [3]


def test_empty_fixture_day_still_counts_as_fetched(storage):
    # A day with no matches in our leagues must not trigger a refetch on every autocomplete
    storage.save_fixture_day(DAY, [], 0)
    assert storage.has_fixture_day(DAY)
    assert storage.fixtures_for_day(DAY) == []


def test_budget_counting(storage):
    assert storage.budget_row(DAY) is None
    storage.count_request(DAY, 97)
    storage.count_request(DAY, None)  # a missing header keeps the last known value
    row = storage.budget_row(DAY)
    assert (row["used"], row["header_remaining"]) == (2, 97)
    storage.count_request(DAY, 90)
    assert storage.budget_row(DAY)["header_remaining"] == 90
    assert storage.budget_row("2026-11-05") is None


def test_mark_budget_exhausted(storage):
    storage.mark_budget_exhausted(DAY)
    row = storage.budget_row(DAY)
    assert (row["used"], row["header_remaining"]) == (0, 0)
