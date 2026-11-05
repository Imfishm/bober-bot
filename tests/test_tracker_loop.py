"""
Tracker._run/_loop scheduling with a fake clock: when it polls, how long it sleeps, and when it stops.
Uses the real LIVE_INTERVALS (poll 90 s, half-time 13 min, ...).
"""

import asyncio

import pytest
from fakes import KICKOFF, FakeBot, FakeChannel, ScriptedApi, fixture_item, track

from bober.config import BUDGET_FLOOR, LINES_PATH, LIVE_INTERVALS
from bober.embeds import BoberLines
from bober.football_api import ApiError, BudgetExhausted
from bober.tracker import Tracker

IV = LIVE_INTERVALS


def live(status="1H", elapsed=10, home=0, away=0):
    return fixture_item(status, elapsed, home, away)


FT = fixture_item("FT", 90, 0, 0)


@pytest.fixture
def make(storage, clock):
    def _make(steps, remaining=100, channel_exists=True):
        track(storage, now=clock.now)
        api = ScriptedApi(steps, remaining=remaining)
        channel = FakeChannel()
        bot = FakeBot(channel) if channel_exists else FakeBot()
        tracker = Tracker(bot, api, storage, BoberLines(LINES_PATH), IV)
        return tracker, api, channel

    return _make


def requests_used(storage):
    return storage.conn.execute("SELECT requests_used FROM tracked").fetchone()[0]


async def test_sleeps_until_kickoff_then_polls_every_interval(make, clock, storage):
    tracker, api, channel = make([live(elapsed=1), live(elapsed=3), FT])
    await tracker._run(1, resumed=False)

    assert clock.sleeps == [600, IV.poll, IV.poll]
    assert api.fixture_calls == 3 == requests_used(storage)
    assert channel.titles()[0] == "🟢 KICKOFF" and channel.titles()[-1] == "🏁 FULL TIME"
    assert storage.active_match() is None


async def test_halftime_uses_the_long_pause(make, clock):
    tracker, _, channel = make([live(), live("HT", 45), live("2H", 46), FT])
    await tracker._run(1, resumed=False)
    assert clock.sleeps == [600, IV.poll, IV.halftime_pause, IV.poll]
    assert "⏸️ HALF-TIME" in channel.titles() and "▶️ SECOND HALF" in channel.titles()


async def test_not_started_yet_is_rechecked(make, clock):
    tracker, _, _ = make([fixture_item("NS"), fixture_item("NS"), live(), FT])
    await tracker._run(1, resumed=False)
    assert clock.sleeps == [600, IV.prestart_recheck, IV.prestart_recheck, IV.poll]


async def test_no_kickoff_gives_up_after_max_wait(make, clock, storage):
    tracker, api, channel = make([fixture_item("NS")])
    await tracker._run(1, resumed=False)

    assert channel.titles() == ["🤷 NO KICKOFF"]
    assert set(clock.sleeps[1:]) == {IV.prestart_recheck}
    assert clock.now - KICKOFF > IV.prestart_max_wait
    assert api.fixture_calls == IV.prestart_max_wait // IV.prestart_recheck + 2
    assert storage.active_match() is None


async def test_gives_up_on_a_match_that_never_ends(make, clock, storage):
    tracker, api, channel = make([live()])  # stuck "in play" forever
    await tracker._run(1, resumed=False)

    assert channel.titles()[-1] == "😴 BOBER GAVE UP"
    assert clock.now - KICKOFF > IV.give_up_after
    assert api.fixture_calls == IV.give_up_after // IV.poll + 1
    assert storage.active_match() is None


@pytest.mark.parametrize(("remaining", "polls"), [(BUDGET_FLOOR, 0), (BUDGET_FLOOR + 1, 2)])
async def test_budget_floor_checked_before_each_request(make, storage, remaining, polls):
    tracker, api, channel = make([live(), FT], remaining=remaining)
    await tracker._run(1, resumed=False)

    assert api.fixture_calls == polls
    out_of_budget = "💸 BOBER IS OUT OF API REQUESTS" in channel.titles()
    assert out_of_budget == (polls == 0)
    assert storage.active_match() is None


async def test_budget_running_out_mid_match_stops_polling(make):
    def spend_it_all(api):
        api.remaining = BUDGET_FLOOR
        return live(elapsed=2)

    tracker, api, channel = make([live(), spend_it_all, FT])
    await tracker._run(1, resumed=False)
    assert api.fixture_calls == 2
    assert channel.titles()[-1] == "💸 BOBER IS OUT OF API REQUESTS"


async def test_budget_exhausted_error_stops_polling(make, storage):
    tracker, api, channel = make([live(), BudgetExhausted("limit"), FT])
    await tracker._run(1, resumed=False)
    assert api.fixture_calls == 2
    assert channel.titles()[-1] == "💸 BOBER IS OUT OF API REQUESTS"
    assert storage.active_match() is None


async def test_failed_polls_wait_a_full_interval_and_alert_once(make, clock):
    failures = IV.failure_alert + 2
    tracker, api, channel = make([ApiError("boom")] * failures + [live(), FT])
    await tracker._run(1, resumed=False)

    assert clock.sleeps[1:] == [IV.poll] * (failures + 1)  # never retried immediately
    titles = channel.titles()
    assert titles.count("📡 BOBER LOST THE SIGNAL") == 1
    assert titles.count("📡 SIGNAL IS BACK") == 1
    assert titles.index("📡 BOBER LOST THE SIGNAL") < titles.index("📡 SIGNAL IS BACK")
    assert api.fixture_calls == failures + 2


async def test_a_few_failed_polls_stay_quiet(make):
    tracker, _, channel = make([ApiError("boom")] * (IV.failure_alert - 1) + [live(), FT])
    await tracker._run(1, resumed=False)
    assert not any("SIGNAL" in t for t in channel.titles())


async def test_resume_announces_once_with_current_score(make, clock, storage):
    tracker, _, channel = make([live(elapsed=30, home=1), live(elapsed=31, home=1), FT])
    storage.update_tracked(1, last_status="1H", home_goals=1, away_goals=0)
    clock.now = KICKOFF + 30 * 60

    tracker.resume()
    await tracker.task

    titles = channel.titles()
    assert titles.count("🔌 BOBER IS BACK") == 1 and titles[0] == "🔌 BOBER IS BACK"
    assert "1–0" in channel.messages[0].embeds[0].description
    assert "🟢 KICKOFF" not in titles  # already announced before the restart
    assert clock.sleeps[0] == IV.poll  # kickoff is in the past: no initial sleep


async def test_resume_with_nothing_tracked_does_nothing(make, storage):
    tracker, _, _ = make([FT])
    storage.stop_tracking(1)
    tracker.resume()
    assert tracker.task is None


async def test_unexpected_error_posts_tripped_and_stops(make, storage):
    tracker, _, channel = make([{"not": "a fixture"}])
    await tracker._run(1, resumed=False)
    assert channel.titles() == ["💥 BOBER TRIPPED"]
    assert storage.active_match() is None


async def test_missing_channel_drops_the_match_quietly(make, storage):
    tracker, api, _ = make([live()], channel_exists=False)
    await tracker._run(1, resumed=False)
    assert api.fixture_calls == 0
    assert storage.active_match() is None


async def test_untracked_during_sleep_stops_without_polling_again(make, storage):
    def untrack_after_this_poll(api):
        storage.stop_tracking(1)
        return live()

    tracker, api, channel = make([untrack_after_this_poll, live(), FT])
    await tracker._run(1, resumed=False)
    assert api.fixture_calls == 1
    assert "🏁 FULL TIME" not in channel.titles()


async def test_start_replaces_the_running_task(make):
    tracker, _, _ = make([live()])
    tracker.start(1)
    first = tracker.task
    tracker.start(1)
    tracker.stop()
    results = await asyncio.gather(first, tracker.task, return_exceptions=True)
    assert all(isinstance(r, asyncio.CancelledError) for r in results)
    assert not tracker.is_running()
