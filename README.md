# Bober (˶ᵔ ᵕ ᵔ˶)

A Discord bot that live-tracks a soccer match and shouts about it. Pick one of today's matches, and Bober posts goals, red cards, VAR decisions, kickoff, half-time and full time in the channel, each with a random Bober line.

Covered leagues: Premier League, La Liga, Ligue 1, Bundesliga and the World Cup.

## Commands

| Command | What it does |
| --- | --- |
| `/bober-track match [yellows] [subs] [ping_role]` | Track one of today's matches in this channel. Start typing a team name to pick it. `yellows` and `subs` also post yellow cards and substitutions; `ping_role` is pinged on goals and full time. |
| `/bober-untrack` | Stop tracking (the person who started it, or anyone with Manage Server). Asks for confirmation once the match has kicked off. |
| `/bober-status` | The tracked match, its score, and how many API requests are left today. |

Bober tracks one match at a time and keeps tracking through restarts.

## Setup

Requires Python 3.10+.

```
pip install -r requirements.txt
cp .env.example .env    # then fill in your tokens
python main.py
```

- Create a bot in the [Discord Developer Portal](https://discord.com/developers/applications), invite it with the `bot` and `applications.commands` scopes, and give it Send Messages and Embed Links.
- Slash commands are registered to one server: set `GUILD_ID` in `bober/config.py` to your server's ID.
- Get a free key from [API-Football](https://www.api-football.com/).

## API budget

The API-Football free tier allows **100 requests per day** (resets at 00:00 UTC). Bober polls the tracked match about every 90 seconds, which is ~75 requests for a full match, so realistically one match a day. `/bober-track` warns when there isn't enough budget left, and Bober stops polling before the limit runs out.

## Testing without the API

Replay mode plays a scripted match from a file instead of calling the API:

1. Add `BOBER_REPLAY=data/replays/sample_match.json` to `.env`.
2. Run `python main.py`, then within about a minute run `/bober-track` and pick the "(replay)" match.

The whole match plays out in about 2 minutes. Remove the line to go live.

Tests (fully offline: no Discord token or API key needed, and they run in CI on every PR on Python 3.10 and 3.13):

```
pip install -r requirements-dev.txt
python -m pytest
```

They cover the event logic, the API client, storage, the poll loop (with a fake clock), every slash command path, and a smoke test that boots the bot without connecting. Leaked resources and un-awaited coroutines fail the run. Known bugs are kept as `xfail(strict=True)` tests.

Linting and formatting use [Ruff](https://docs.astral.sh/ruff/) (config in `ruff.toml`, checked in CI on every PR):

```
ruff check --fix
ruff format
```

## Project layout

```
main.py              entry point
bober/               the bot (config, slash commands, poll loop, API client, storage, embeds)
data/                Bober's lines and replay files
tests/               pytest tests
```

Changes are listed in [CHANGELOG.md](CHANGELOG.md).
