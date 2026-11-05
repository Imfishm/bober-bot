"""
Slash command callbacks with fake interactions: every exit path, permissions, the untrack
confirmation view, /bober-status and match autocomplete.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fakes import KICKOFF, FakeBot, FakeChannel, ScriptedApi, fixture_item, track

from bober.cogs import tracking
from bober.config import BUDGET_FLOOR, LIVE_INTERVALS
from bober.football_api import ApiError

NOW = KICKOFF - 3600  # an hour before the default fixture's kickoff (07:00 UTC)
MIDNIGHT = NOW - NOW % tracking.DAY  # the UTC midnight starting NOW's day
OWNER, STRANGER = 100, 200


class FakeTracker:
    def __init__(self):
        self.started = []
        self.stops = 0

    def start(self, fixture_id, resumed=False):
        self.started.append(fixture_id)

    def stop(self):
        self.stops += 1


def interaction(channel, user_id=OWNER, manage_guild=False):
    return SimpleNamespace(
        channel=channel,
        user=SimpleNamespace(
            id=user_id,
            mention=f"<@{user_id}>",
            guild_permissions=SimpleNamespace(manage_guild=manage_guild),
        ),
        guild=SimpleNamespace(me=object()),
        response=AsyncMock(),
        followup=AsyncMock(),
        edit_original_response=AsyncMock(),
    )


@pytest.fixture
def channel():
    return FakeChannel()


@pytest.fixture
def cog(monkeypatch, storage, channel):
    monkeypatch.setattr(tracking, "API_FOOTBALL_KEY", "test-key")
    monkeypatch.setattr(tracking, "REPLAY_PATH", None)
    monkeypatch.setattr(tracking, "INTERVALS", LIVE_INTERVALS)
    monkeypatch.setattr(tracking, "utc_now", lambda: NOW)
    # Built by hand instead of cog_load, so there's no real HTTP session or Tracker
    c = tracking.Tracking(FakeBot(channel))
    c.storage = storage
    c.api = ScriptedApi(fixtures=[fixture_item(fixture_id=1)])
    c.tracker = FakeTracker()
    return c


def followup_text(inter):
    """The one followup a deferred command sent (asserting there was exactly one, ephemeral)."""
    inter.followup.send.assert_awaited_once()
    args, kwargs = inter.followup.send.await_args
    assert kwargs.get("ephemeral") is True
    return args[0]


def reply(inter):
    inter.response.send_message.assert_awaited_once()
    args, kwargs = inter.response.send_message.await_args
    assert kwargs.get("ephemeral") is True
    return (args[0] if args else None), kwargs


# --- /bober-track ---


def no_api(cog, monkeypatch, channel):
    monkeypatch.setattr(tracking, "API_FOOTBALL_KEY", None)


def already_tracking(cog, monkeypatch, channel):
    track(cog.storage, fixture_item(fixture_id=2, home_name="Ajax", away_name="PSV"))


def fixtures_fail(cog, monkeypatch, channel):
    cog.api.fixtures = ApiError("boom")


def match_over(cog, monkeypatch, channel):
    cog.api.fixtures = [fixture_item("FT", 90, fixture_id=1)]


def no_permissions(cog, monkeypatch, channel):
    channel.can_post = False


def at_budget_floor(cog, monkeypatch, channel):
    cog.api.remaining = BUDGET_FLOOR


def low_budget(cog, monkeypatch, channel):
    cog.api.remaining = 20


def announce_fails(cog, monkeypatch, channel):
    channel.fail_send = True


def nothing(cog, monkeypatch, channel):
    pass


TRACK_CASES = [
    # (id, setup, match value, expected followup fragment, tracking starts?)
    ("no-api-key", no_api, "1", "no API key", False),
    ("already-tracking", already_tracking, "1", "already tracking **Ajax vs PSV**", False),
    ("not-a-number", nothing, "Lyon", "Pick a match", False),
    ("fixtures-fail", fixtures_fail, "1", "couldn't load the match list: boom", False),
    ("not-listed", nothing, "999", "isn't a match Bober can track right now", False),
    ("already-over", match_over, "1", "already over", False),
    ("no-permissions", no_permissions, "1", "can't post embeds", False),
    ("budget-floor", at_budget_floor, "1", "out of API requests", False),
    ("low-budget-warns", low_budget, "1", "⚠️ Only ~20 API requests left", True),
    ("announce-fails", announce_fails, "1", "Bober is on it", True),
    ("success", nothing, "1", "Bober is on it", True),
]


@pytest.mark.parametrize(
    ("setup", "match", "expected", "starts"),
    [case[1:] for case in TRACK_CASES],
    ids=[case[0] for case in TRACK_CASES],
)
async def test_track_exit_paths(cog, monkeypatch, channel, setup, match, expected, starts):
    setup(cog, monkeypatch, channel)
    before = cog.storage.active_match()
    inter = interaction(channel)

    await cog.bober_track.callback(cog, inter, match)

    inter.response.defer.assert_awaited_once_with(ephemeral=True)
    text = followup_text(inter)
    assert expected in text
    if starts:
        assert cog.storage.active_match()["fixture_id"] == 1
        assert cog.tracker.started == [1]
        assert channel.titles() == ([] if channel.fail_send else ["👀 BOBER IS WATCHING"])
    else:
        assert cog.tracker.started == []
        assert channel.messages == []
        assert cog.storage.active_match() == before
    if starts and "⚠️" not in expected:
        assert "⚠️" not in text


async def test_track_saves_options(cog, channel):
    role = SimpleNamespace(id=42, mention="<@&42>")
    await cog.bober_track.callback(
        cog, interaction(channel), "1", yellows=True, subs=True, ping_role=role
    )
    match = cog.storage.active_match()
    assert (match["yellows"], match["subs"], match["ping_role_id"]) == (1, 1, 42)
    assert match["channel_id"] == channel.id and match["user_id"] == OWNER


async def test_track_kicked_off_match_uses_in_play_estimate(cog, channel, monkeypatch):
    # 60 min after kickoff a stale "NS" from the morning cache is treated as 2H: ~28 requests,
    # which fits in 50 (a not-started estimate would be ~72 and warn).
    monkeypatch.setattr(tracking, "utc_now", lambda: KICKOFF + 3600)
    cog.api.remaining = 50
    inter = interaction(channel)
    await cog.bober_track.callback(cog, inter, "1")
    assert "⚠️" not in followup_text(inter)


async def test_track_fetches_fixture_list_once_per_day(cog, channel):
    await cog.bober_track.callback(cog, interaction(channel), "999")
    await cog.bober_track.callback(cog, interaction(channel), "999")
    assert cog.api.fixtures_on_calls == 1


# --- /bober-untrack ---


async def test_untrack_nothing_tracked(cog, channel):
    inter = interaction(channel)
    await cog.bober_untrack.callback(cog, inter)
    assert "isn't tracking anything" in reply(inter)[0]


async def test_untrack_needs_owner_or_manage_server(cog, channel):
    track(cog.storage, user_id=OWNER)
    inter = interaction(channel, user_id=STRANGER)
    await cog.bober_untrack.callback(cog, inter)
    assert f"Only <@{OWNER}>" in reply(inter)[0]
    assert cog.storage.active_match() is not None
    assert cog.tracker.stops == 0


@pytest.mark.parametrize(
    ("user_id", "manage_guild"), [(OWNER, False), (STRANGER, True)], ids=["owner", "manager"]
)
async def test_untrack_before_kickoff_stops_immediately(cog, channel, user_id, manage_guild):
    track(cog.storage, user_id=OWNER)
    inter = interaction(channel, user_id=user_id, manage_guild=manage_guild)
    await cog.bober_untrack.callback(cog, inter)

    assert reply(inter)[0] == "✅ Stopped tracking."
    assert cog.storage.active_match() is None
    assert cog.tracker.stops == 1
    assert channel.titles() == ["🛑 BOBER STOPPED TRACKING"]


async def untrack_after_kickoff(cog, channel):
    track(cog.storage, fixture_item(timestamp=NOW - 60), user_id=OWNER)
    inter = interaction(channel)
    await cog.bober_untrack.callback(cog, inter)
    text, kwargs = reply(inter)
    assert "API requests already spent" in text
    assert cog.storage.active_match() is not None  # nothing happens until confirmed
    return kwargs["view"]


async def test_untrack_after_kickoff_asks_for_confirmation(cog, channel):
    view = await untrack_after_kickoff(cog, channel)
    assert isinstance(view, tracking.ConfirmUntrackView)
    assert cog.tracker.stops == 0


async def test_confirm_button_stops_tracking(cog, channel):
    view = await untrack_after_kickoff(cog, channel)
    click = interaction(channel)
    await view.confirm.callback(click)

    click.response.edit_message.assert_awaited_once_with(content="✅ Stopped tracking.", view=None)
    assert cog.storage.active_match() is None
    assert cog.tracker.stops == 1
    assert channel.titles() == ["🛑 BOBER STOPPED TRACKING"]


async def test_confirm_button_for_a_match_no_longer_tracked(cog, channel):
    view = await untrack_after_kickoff(cog, channel)
    cog.storage.stop_tracking(1)
    track(cog.storage, fixture_item(fixture_id=2))
    click = interaction(channel)
    await view.confirm.callback(click)

    assert "isn't being tracked anymore" in click.response.edit_message.await_args.kwargs["content"]
    assert cog.storage.active_match()["fixture_id"] == 2
    assert cog.tracker.stops == 0


async def test_cancel_button_keeps_tracking(cog, channel):
    view = await untrack_after_kickoff(cog, channel)
    click = interaction(channel)
    await view.cancel.callback(click)
    assert "keeps watching" in click.response.edit_message.await_args.kwargs["content"]
    assert cog.storage.active_match() is not None
    assert cog.tracker.stops == 0


async def test_only_the_requester_can_press_the_buttons(cog, channel):
    view = await untrack_after_kickoff(cog, channel)
    assert await view.interaction_check(interaction(channel, user_id=OWNER))
    assert not await view.interaction_check(interaction(channel, user_id=STRANGER))


async def test_confirmation_expires(cog, channel):
    view = await untrack_after_kickoff(cog, channel)
    await view.on_timeout()
    content = view.message.edit_original_response.await_args.kwargs["content"]
    assert "expired" in content
    assert cog.storage.active_match() is not None


# --- /bober-status ---


async def test_status_idle(cog, channel):
    cog.api.remaining = 77
    inter = interaction(channel)
    await cog.bober_status.callback(cog, inter)
    assert "isn't tracking anything. **77**" in reply(inter)[0]


async def test_status_tracking(cog, channel):
    track(cog.storage)
    inter = interaction(channel)
    await cog.bober_status.callback(cog, inter)
    embed = reply(inter)[1]["embed"]
    assert embed.title == "👀 BOBER STATUS"
    fields = {f.name: f.value for f in embed.fields}
    assert fields["Match status"] == "Waiting for kickoff"
    assert fields["Last checked"] == "Not yet"
    assert "100 left today" in fields["API requests"]


# --- autocomplete ---


async def test_autocomplete_filters_sorts_and_hides(cog, channel):
    cog.api.fixtures = [
        fixture_item(fixture_id=1, timestamp=NOW + 7200, home_name="Lyon"),
        fixture_item(fixture_id=2, timestamp=NOW + 3600, away_name="Lyon"),
        fixture_item("FT", fixture_id=3, home_name="Lyon"),  # finished
        fixture_item("PST", fixture_id=4, home_name="Lyon"),  # called off
        fixture_item(fixture_id=5, timestamp=NOW - LIVE_INTERVALS.give_up_after - 1),  # stale
        fixture_item(fixture_id=6, home_name="Ajax", away_name="PSV"),  # doesn't match
        fixture_item(fixture_id=7, home_name="Lyon", league_id=999),  # league we don't follow
    ]
    choices = await cog.match_autocomplete(interaction(channel), "  LYON ")
    assert [c.value for c in choices] == ["2", "1"]
    assert all(len(c.name) <= 100 for c in choices)


async def test_autocomplete_matches_league_and_caps_at_25(cog, channel):
    cog.api.fixtures = [fixture_item(fixture_id=i, timestamp=NOW + i) for i in range(1, 31)]
    choices = await cog.match_autocomplete(interaction(channel), "premier")
    assert len(choices) == 25
    assert choices[0].value == "1"


async def test_autocomplete_api_error_returns_nothing(cog, channel):
    cog.api.fixtures = ApiError("boom")
    assert await cog.match_autocomplete(interaction(channel), "") == []


async def test_autocomplete_without_api_key_makes_no_request(cog, channel, monkeypatch):
    monkeypatch.setattr(tracking, "API_FOOTBALL_KEY", None)
    assert await cog.match_autocomplete(interaction(channel), "") == []
    assert cog.api.fixtures_on_calls == 0


# --- matches around midnight UTC (#16) ---

YESTERDAY = tracking.utc_date(MIDNIGHT - 1)
TODAY = tracking.utc_date(MIDNIGHT)
TOMORROW = tracking.utc_date(MIDNIGHT + tracking.DAY)


async def choice_ids(cog, channel):
    return [c.value for c in await cog.match_autocomplete(interaction(channel), "")]


async def test_evening_lists_tomorrows_early_kickoffs(cog, channel, monkeypatch):
    tomorrow = MIDNIGHT + tracking.DAY
    monkeypatch.setattr(tracking, "utc_now", lambda: tomorrow - 3600)  # 23:00 UTC
    cog.api.fixtures = {
        TODAY: [fixture_item(fixture_id=1, timestamp=tomorrow - 1800)],
        TOMORROW: [
            fixture_item(fixture_id=2, timestamp=tomorrow + 1800),  # 00:30, evening in the Americas
            fixture_item(fixture_id=3, timestamp=tomorrow + 12 * 3600),  # tomorrow's lunchtime
        ],
    }
    assert await choice_ids(cog, channel) == ["1", "2"]

    inter = interaction(channel)
    await cog.bober_track.callback(cog, inter, "2")
    assert "Bober is on it" in followup_text(inter)
    assert cog.api.fixtures_on_calls == 2  # each day fetched once


async def test_after_midnight_lists_yesterdays_running_matches(cog, channel, monkeypatch):
    monkeypatch.setattr(tracking, "utc_now", lambda: MIDNIGHT + 3600)  # 01:00 UTC
    cog.api.fixtures = {
        YESTERDAY: [
            fixture_item(fixture_id=1, timestamp=MIDNIGHT - 3600),  # still running
            fixture_item(fixture_id=2, timestamp=MIDNIGHT - 5 * 3600),  # must be over
        ],
        TODAY: [fixture_item(fixture_id=3, timestamp=MIDNIGHT + 7200)],
    }
    assert await choice_ids(cog, channel) == ["1", "3"]

    inter = interaction(channel)
    await cog.bober_track.callback(cog, inter, "1")
    assert "Bober is on it" in followup_text(inter)
    assert cog.api.fixtures_on_calls == 2


async def test_midday_fetches_only_today(cog, channel, monkeypatch):
    monkeypatch.setattr(tracking, "utc_now", lambda: MIDNIGHT + 12 * 3600)
    cog.api.fixtures = {TODAY: [fixture_item(fixture_id=1, timestamp=MIDNIGHT + 13 * 3600)]}
    assert await choice_ids(cog, channel) == ["1"]
    assert cog.api.fixtures_on_calls == 1


async def test_adjacent_day_failure_still_lists_today(cog, channel, monkeypatch):
    tomorrow = MIDNIGHT + tracking.DAY
    monkeypatch.setattr(tracking, "utc_now", lambda: tomorrow - 3600)
    cog.api.fixtures = {
        TODAY: [fixture_item(fixture_id=1, timestamp=tomorrow - 1800)],
        TOMORROW: ApiError("boom"),
    }
    assert await choice_ids(cog, channel) == ["1"]


async def test_failed_fetch_is_not_retried_on_every_keystroke(cog, channel, monkeypatch):
    tomorrow = MIDNIGHT + tracking.DAY
    now = [tomorrow - 3600]
    monkeypatch.setattr(tracking, "utc_now", lambda: now[0])
    cog.api.fixtures = {TODAY: [], TOMORROW: ApiError("boom")}
    await choice_ids(cog, channel)
    await choice_ids(cog, channel)
    assert cog.api.fixtures_on_calls == 2  # today once, tomorrow once

    now[0] += tracking.FETCH_RETRY
    await choice_ids(cog, channel)
    assert cog.api.fixtures_on_calls == 3  # tomorrow retried after the cooldown


async def test_after_midnight_kickoff_uses_tomorrows_budget(cog, channel, monkeypatch):
    tomorrow = MIDNIGHT + tracking.DAY
    monkeypatch.setattr(tracking, "utc_now", lambda: tomorrow - 3600)
    cog.api.fixtures = {TOMORROW: [fixture_item(fixture_id=2, timestamp=tomorrow + 1800)]}
    cog.api.remaining = BUDGET_FLOOR  # today's budget is spent
    inter = interaction(channel)
    await cog.bober_track.callback(cog, inter, "2")
    text = followup_text(inter)
    assert "Bober is on it" in text and "⚠️" not in text


async def test_same_fixture_on_two_days_is_listed_once(cog, channel, monkeypatch):
    # Replay mode serves its one match for every date
    tomorrow = MIDNIGHT + tracking.DAY
    monkeypatch.setattr(tracking, "utc_now", lambda: tomorrow - 3600)
    cog.api.fixtures = [fixture_item(fixture_id=1, timestamp=tomorrow - 1800)]
    assert await choice_ids(cog, channel) == ["1"]
