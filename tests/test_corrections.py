"""
Tracker behaviour when the API rewrites or deletes events it already sent, and own-goal attribution.
"""

import asyncio

from fakes import FakeChannel

from bober.config import LINES_PATH, REPLAY_INTERVALS
from bober.embeds import BoberLines
from bober.tracker import OG_META_KEY, Tracker

HOME, AWAY = 10, 20


def goal(minute, team, detail="Normal Goal", player=None):
    return {
        "time": {"elapsed": minute, "extra": None},
        "team": {"id": team, "name": "Lyon" if team == HOME else "Palace"},
        "player": {"id": player, "name": f"Player {player}" if player else None},
        "assist": {"id": None, "name": None},
        "type": "Goal",
        "detail": detail,
        "comments": None,
    }


def poll(events, home, away, status="1H", elapsed=50):
    return {
        "fixture": {
            "id": 1,
            "timestamp": 0,
            "status": {"short": status, "long": "", "elapsed": elapsed},
        },
        "teams": {"home": {"id": HOME, "name": "Lyon"}, "away": {"id": AWAY, "name": "Palace"}},
        "goals": {"home": home, "away": away},
        "score": {},
        "events": events,
    }


def run(storage, polls, convention=None):
    fixture = poll([], 0, 0, status="NS")
    fixture["league"] = {"name": "Test League"}
    storage.start_tracking(
        fixture, channel_id=1, user_id=1, yellows=False, subs=False, ping_role_id=None, now=-60
    )
    if convention is not None:
        storage.set_meta(OG_META_KEY, convention)
    tracker = Tracker(
        bot=None,
        api=None,
        storage=storage,
        lines=BoberLines(LINES_PATH),
        intervals=REPLAY_INTERVALS,
    )
    channel = FakeChannel()

    async def go():
        for data in polls:
            await tracker._process(storage.active_match(), channel, data)

    asyncio.run(go())
    return channel, storage


def goal_messages(channel):
    return [m for m in channel.messages if "GOAL —" in m.embeds[0].title]


def test_minute_correction_edits_instead_of_reposting(storage):
    channel, storage = run(
        storage,
        [
            poll([goal(44, HOME, player=9)], 1, 0),
            poll([goal(45, HOME, player=9)], 1, 0),
        ],
    )
    goals = goal_messages(channel)
    assert len(goals) == 1
    assert goals[0].embeds[0].title.endswith("45'")
    assert [r["minute"] for r in storage.events_of(1, ["goal"])] == ["45'"]


def test_detail_and_minute_correction_retitles(storage):
    channel, _ = run(
        storage,
        [
            poll([goal(30, HOME)], 1, 0),
            poll([goal(31, HOME, "Penalty", player=9)], 1, 0),
        ],
    )
    goals = goal_messages(channel)
    assert len(goals) == 1
    assert goals[0].embeds[0].title.startswith("⚽ PENALTY GOAL — 31'")
    assert "Player 9" in goals[0].embeds[0].fields[0].value


def test_deleted_goal_waits_for_scoreboard_then_struck(storage):
    channel, storage = run(
        storage,
        [
            poll([goal(30, HOME)], 1, 0),
            poll([], 1, 0),  # event gone but score still 1-0: could be a blip, leave it
            poll([], 0, 0),  # scoreboard agrees: it's gone
        ],
    )
    struck = goal_messages(channel)[0].embeds[0].title
    assert struck.startswith("~~") and "TAKEN BACK" in struck
    assert any("GOAL TAKEN BACK" in m.embeds[0].title for m in channel.messages)
    assert storage.events_of(1, ["goal"]) == []


def test_own_goal_convention_learned_and_used(storage):
    # API lists the own goal under AWAY, and the scoreboard says HOME benefited -> under the scorer's team
    channel, storage = run(storage, [poll([goal(20, AWAY, "Own Goal", player=5)], 1, 0)])
    assert storage.get_meta(OG_META_KEY) == "0"
    field = goal_messages(channel)[0].embeds[0].fields[0]
    assert field.name == "Lyon" and "own goal" in field.value
    assert storage.events_of(1, ["own_goal"])[0]["team_id"] == HOME


def test_own_goal_unknown_convention_stays_neutral(storage):
    # Score hasn't caught up, so nothing can be learned yet
    channel, storage = run(storage, [poll([goal(20, AWAY, "Own Goal", player=5)], 0, 0)])
    assert storage.get_meta(OG_META_KEY) is None
    assert goal_messages(channel)[0].embeds[0].fields[0].name == "Own goal"


def test_own_goal_known_convention_beneficiary(storage):
    channel, _ = run(storage, [poll([goal(20, HOME, "Own Goal", player=5)], 0, 0)], convention="1")
    assert goal_messages(channel)[0].embeds[0].fields[0].name == "Lyon"


# --- VAR-cancelled goals


def var_cancel(minute, team):
    return {
        "time": {"elapsed": minute, "extra": None},
        "team": {"id": team, "name": "Lyon" if team == HOME else "Palace"},
        "player": {"id": None, "name": None},
        "assist": {"id": None, "name": None},
        "type": "Var",
        "detail": "Goal cancelled",
        "comments": None,
    }


def test_team_corrected_before_player_known_is_not_reposted(storage):
    channel, _ = run(
        storage,
        [
            poll([goal(30, HOME)], 1, 0),
            poll([goal(30, AWAY)], 0, 1),  # API fixes the team; scorer still unknown
        ],
    )
    assert len(goal_messages(channel)) == 1


def test_correcting_a_var_cancelled_goal_is_not_reposted(storage):
    channel, _ = run(
        storage,
        [
            poll([goal(66, HOME, player=9)], 1, 0),
            poll([goal(66, HOME, player=9), var_cancel(67, HOME)], 0, 0),
            poll([goal(67, HOME, player=9), var_cancel(67, HOME)], 0, 0),  # minute nudged
        ],
    )
    msgs = goal_messages(channel)
    assert len(msgs) == 1
    assert "CANCELLED BY VAR" in msgs[0].embeds[0].title  # still struck through after the rewrite


def test_new_goal_near_a_dropped_cancelled_goal_is_posted(storage):
    channel, _ = run(
        storage,
        [
            poll([goal(67, HOME, player=9)], 1, 0),
            poll([goal(67, HOME, player=9), var_cancel(67, HOME)], 0, 0),
            poll([var_cancel(67, HOME)], 0, 0),  # API drops the cancelled goal for good
            poll([var_cancel(67, HOME), goal(69, HOME)], 1, 0),  # real goal, scorer not known yet
        ],
    )
    assert len(goal_messages(channel)) == 2


def test_same_scorer_again_after_a_dropped_cancelled_goal_is_posted(storage):
    channel, _ = run(
        storage,
        [
            poll([goal(67, HOME, player=9)], 1, 0),
            poll([goal(67, HOME, player=9), var_cancel(67, HOME)], 0, 0),
            poll([var_cancel(67, HOME)], 0, 0),
            poll([var_cancel(67, HOME), goal(69, HOME, player=9)], 1, 0),  # he scores again
        ],
    )
    assert len(goal_messages(channel)) == 2


def test_rewriting_a_cancelled_goal_before_the_scorer_is_known_is_not_reposted(storage):
    channel, _ = run(
        storage,
        [
            poll([goal(66, HOME)], 1, 0),
            poll([goal(66, HOME), var_cancel(67, HOME)], 0, 0),
            poll([goal(67, HOME), var_cancel(67, HOME)], 0, 0),  # minute nudged, still no scorer
        ],
    )
    assert len(goal_messages(channel)) == 1


def test_scorer_filled_into_a_rewritten_cancelled_goal_keeps_it_struck(storage):
    channel, storage = run(
        storage,
        [
            poll([goal(66, HOME)], 1, 0),
            poll([goal(66, HOME), var_cancel(67, HOME)], 0, 0),
            poll([goal(67, HOME), var_cancel(67, HOME)], 0, 0),
            poll([goal(67, HOME, player=9), var_cancel(67, HOME)], 0, 0),  # scorer filled in late
        ],
    )
    (msg,) = goal_messages(channel)
    assert "CANCELLED BY VAR" in msg.embeds[0].title
    assert "Player 9" in msg.embeds[0].fields[0].value
    assert storage.events_of(1, ["goal"]) == []  # still left out of the summaries


def test_var_cancel_and_rewrite_in_one_poll_keeps_the_goal_struck(storage):
    channel, _ = run(
        storage,
        [
            poll([goal(66, HOME, player=9)], 1, 0),
            # VAR listed first, so the goal is cancelled before its rewrite is applied
            poll([var_cancel(67, HOME), goal(67, HOME, player=9)], 0, 0),
        ],
    )
    (msg,) = goal_messages(channel)
    assert "CANCELLED BY VAR" in msg.embeds[0].title


def test_later_var_cancel_skips_a_rewritten_cancelled_goal(storage):
    channel, _ = run(
        storage,
        [
            poll([goal(60, HOME, player=7)], 1, 0),
            poll([goal(60, HOME, player=7), goal(66, HOME, player=9)], 2, 0),
            poll([goal(60, HOME, player=7), goal(66, HOME, player=9), var_cancel(67, HOME)], 1, 0),
            poll([goal(60, HOME, player=7), goal(67, HOME, player=9), var_cancel(67, HOME)], 1, 0),
            poll(  # a second VAR call takes back the 60' goal, not the already-cancelled one
                [
                    goal(60, HOME, player=7),
                    goal(67, HOME, player=9),
                    var_cancel(67, HOME),
                    var_cancel(70, HOME),
                ],
                0,
                0,
            ),
        ],
    )
    first, second = goal_messages(channel)
    assert "CANCELLED BY VAR" in first.embeds[0].title
    assert second.embeds[0].title.count("CANCELLED BY VAR") == 1
