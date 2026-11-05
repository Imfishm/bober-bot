"""
Shared configuration for Bober (˶ᵔ ᵕ ᵔ˶)
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

load_dotenv(ROOT / ".env")

DISCORD_TOKEN = os.getenv("SUPER_SECRET_DISCORD_TOKEN")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
# Path to a replay JSON file; when set, no real API calls are made
REPLAY_PATH = os.getenv("BOBER_REPLAY")
if REPLAY_PATH:
    REPLAY_PATH = str(ROOT / REPLAY_PATH)  # relative paths are relative to the repo root

GUILD_ID = 1446565791452364935

API_BASE = "https://v3.football.api-sports.io"
DAILY_REQUEST_LIMIT = 100  # free tier, resets at 00:00 UTC
BUDGET_FLOOR = 5  # stop polling when this many requests (or fewer) remain

# league id -> display name
LEAGUES = {
    39: "Premier League",
    140: "La Liga",
    61: "Ligue 1",
    78: "Bundesliga",
    1: "World Cup",
}

DB_PATH = str(ROOT / ("bober_replay.db" if REPLAY_PATH else "bober.db"))
LINES_PATH = str(DATA_DIR / "bober_lines.json")


@dataclass(frozen=True)
class Intervals:
    poll: int  # seconds between polls while live
    halftime_pause: int  # sleep after seeing HT (half-time is ~15 min; wake a bit early)
    prestart_recheck: int  # recheck interval when kickoff time passed but match not live yet
    prestart_max_wait: int  # give up if still not live this long after scheduled kickoff
    give_up_after: int  # stop tracking this long after scheduled kickoff no matter what
    failure_alert: int  # consecutive failed polls before posting "lost signal"


LIVE_INTERVALS = Intervals(
    poll=90,
    halftime_pause=13 * 60,
    prestart_recheck=120,
    prestart_max_wait=20 * 60,
    give_up_after=3 * 60 * 60,
    failure_alert=5,
)

REPLAY_INTERVALS = Intervals(
    poll=5,
    halftime_pause=5,
    prestart_recheck=5,
    prestart_max_wait=60,
    give_up_after=3 * 60 * 60,
    failure_alert=5,
)

INTERVALS = REPLAY_INTERVALS if REPLAY_PATH else LIVE_INTERVALS
