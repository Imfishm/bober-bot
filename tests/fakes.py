"""
Shared stand-ins for Discord, API-Football and the clock, so tests never touch the network.
"""

import asyncio
import copy
import itertools
from types import SimpleNamespace

import discord

HOME, AWAY = 10, 20
KICKOFF = 1_800_000_000  # an arbitrary fixed kickoff timestamp

_ids = itertools.count(1)
_real_sleep = asyncio.sleep


def http_error(cls=discord.NotFound, status=404):
    return cls(SimpleNamespace(status=status, reason="fake"), "fake error")


class FakeMessage:
    def __init__(self, content, embed):
        self.id = next(_ids)
        self.content = content
        self.embeds = [embed]

    async def edit(self, embed):
        self.embeds = [embed]


class FakeChannel:
    def __init__(self, channel_id=1, can_post=True, fail_send=False):
        self.id = channel_id
        self.messages = []
        self.can_post = can_post
        self.fail_send = fail_send

    async def send(self, content=None, embed=None, allowed_mentions=None):
        if self.fail_send:
            raise http_error(discord.Forbidden, 403)
        message = FakeMessage(content, embed)
        self.messages.append(message)
        return message

    async def fetch_message(self, message_id):
        return next(m for m in self.messages if m.id == message_id)

    def permissions_for(self, member):
        return SimpleNamespace(send_messages=self.can_post, embed_links=self.can_post)

    def titles(self):
        return [m.embeds[0].title for m in self.messages]


class FakeBot:
    def __init__(self, *channels):
        self.channels = {c.id: c for c in channels}

    def get_channel(self, channel_id):
        return self.channels.get(channel_id)

    async def fetch_channel(self, channel_id):
        raise http_error()


class ScriptedApi:
    """
    Stand-in for FootballApi. fixture() serves `steps` in order (staying on the last one); a step
    can be a dict (returned), an exception (raised) or a callable taking the api (its result is
    used, so a step can also change `remaining`). fixtures_on() serves `fixtures` (or raises it).
    """

    def __init__(self, steps=(), fixtures=(), remaining=100):
        self.steps = list(steps)
        self.fixtures = fixtures
        self.remaining = remaining
        self.fixture_calls = 0
        self.fixtures_on_calls = 0

    def remaining_today(self):
        return self.remaining

    async def fixtures_on(self, utc_date):
        self.fixtures_on_calls += 1
        if isinstance(self.fixtures, BaseException):
            raise self.fixtures
        return copy.deepcopy(list(self.fixtures))

    async def fixture(self, fixture_id):
        step = self.steps[min(self.fixture_calls, len(self.steps) - 1)]
        self.fixture_calls += 1
        if callable(step):
            step = step(self)
        if isinstance(step, BaseException):
            raise step
        return copy.deepcopy(step)


class FakeClock:
    """utc_now() and asyncio.sleep() for the tracker: sleeping advances the clock instantly."""

    def __init__(self, now):
        self.now = now
        self.sleeps = []

    def time(self):
        return self.now

    async def sleep(self, seconds, result=None):
        self.sleeps.append(seconds)
        if len(self.sleeps) > 1000:
            raise RuntimeError("tracker loop looks runaway (1000+ sleeps)")
        self.now += seconds
        await _real_sleep(0)
        return result


class AsyncioWithFakeSleep:
    """Replaces the `asyncio` name inside one module: everything is real except sleep()."""

    def __init__(self, sleep):
        self.sleep = sleep

    def __getattr__(self, name):
        return getattr(asyncio, name)


def fixture_item(
    status="NS",
    elapsed=None,
    home=None,
    away=None,
    events=(),
    fixture_id=1,
    timestamp=KICKOFF,
    home_name="Lyon",
    away_name="Palace",
    league_id=39,
    league_name="Premier League",
):
    """One /fixtures item, shaped like API-Football's response."""
    return {
        "fixture": {
            "id": fixture_id,
            "timestamp": timestamp,
            "status": {"short": status, "long": status, "elapsed": elapsed},
        },
        "league": {"id": league_id, "name": league_name},
        "teams": {
            "home": {"id": HOME, "name": home_name},
            "away": {"id": AWAY, "name": away_name},
        },
        "goals": {"home": home, "away": away},
        "score": {},
        "events": list(events),
    }


def track(storage, fixture=None, user_id=1, channel_id=1, now=KICKOFF - 600):
    """Start tracking `fixture` (default: fixture_item()) the way /bober-track does."""
    storage.start_tracking(
        fixture or fixture_item(),
        channel_id=channel_id,
        user_id=user_id,
        yellows=False,
        subs=False,
        ping_role_id=None,
        now=now,
    )
    return storage.active_match()
