"""
Runs the scripted replay through the real Tracker processing with a fake Discord channel.
"""

import asyncio
import json

from fakes import FakeChannel

from bober.config import DATA_DIR, LINES_PATH, REPLAY_INTERVALS
from bober.embeds import BoberLines
from bober.tracker import Tracker


def run_replay(storage, yellows=False, subs=False, ping_role_id=None):
    with open(DATA_DIR / "replays" / "sample_match.json", encoding="utf-8") as f:
        data = json.load(f)
    fixture = data["fixtures"][0]
    storage.start_tracking(
        fixture,
        channel_id=1,
        user_id=1,
        yellows=yellows,
        subs=subs,
        ping_role_id=ping_role_id,
        now=fixture["fixture"]["timestamp"] - 60,
    )
    tracker = Tracker(
        bot=None,
        api=None,
        storage=storage,
        lines=BoberLines(LINES_PATH),
        intervals=REPLAY_INTERVALS,
    )
    channel = FakeChannel()

    async def go():
        for step in data["timeline"]:
            if "error" in step or step["fixture"]["status"]["short"] == "NS":
                continue
            if await tracker._process(storage.active_match(), channel, step):
                return

    asyncio.run(go())
    return channel, storage


def titles(channel):
    return [m.embeds[0].title for m in channel.messages]


def test_replay_default_flags(storage):
    channel, _ = run_replay(storage)
    t = titles(channel)
    assert t[0] == "🟢 KICKOFF"
    assert t[-1] == "🏁 FULL TIME"
    assert "⏸️ HALF-TIME" in t and "▶️ SECOND HALF" in t
    assert not any("YELLOW" in x or "SUBSTITUTION" in x for x in t)
    # 6 goals posted (incl. the one VAR later cancels), 1 red, 1 missed penalty, 1 VAR
    assert sum(1 for x in t if "GOAL —" in x and not x.startswith("📺")) == 6
    assert sum(1 for x in t if "RED CARD" in x) == 1
    assert sum(1 for x in t if "MISSED PENALTY" in x) == 1
    assert sum(1 for x in t if x.startswith("📺 VAR: GOAL CANCELLED")) == 1
    # No event posted twice
    assert len(t) == len(set(t))


def test_replay_late_filled_scorer_is_edited(storage):
    channel, _ = run_replay(storage)
    first_goal = next(m for m in channel.messages if m.embeds[0].title.startswith("⚽ GOAL — 8'"))
    assert "Corentin Tolisso" in first_goal.embeds[0].fields[0].value


def test_replay_var_strikes_cancelled_goal_and_fulltime_omits_it(storage):
    channel, _ = run_replay(storage)
    cancelled = next(m for m in channel.messages if "66'" in m.embeds[0].title)
    assert (
        cancelled.embeds[0].title.startswith("~~")
        and "CANCELLED BY VAR" in cancelled.embeds[0].title
    )
    fulltime = channel.messages[-1].embeds[0]
    assert "3–2" in fulltime.description
    goals = fulltime.fields[0].value
    assert "Kamada" not in goals
    assert goals.count("\n") == 4  # five goals


def test_replay_flags_and_pings(storage):
    channel, storage = run_replay(storage, yellows=True, subs=True, ping_role_id=42)
    t = titles(channel)
    assert sum(1 for x in t if "YELLOW CARD" in x) == 3
    assert sum(1 for x in t if "SUBSTITUTION" in x) == 4
    pinged = [m.embeds[0].title for m in channel.messages if m.content == "<@&42>"]
    assert len(pinged) == 7  # 6 goals (incl. the VAR-cancelled one) + full time
    assert storage.active_match()["last_status"] == "FT"
