"""
API-Football client (and a replay stand-in for testing without spending requests).
"""

import asyncio
import copy
import json
import time
from datetime import datetime, timezone

import aiohttp

from .config import API_BASE, DAILY_REQUEST_LIMIT


class ApiError(Exception):
    pass


class BudgetExhausted(ApiError):
    pass


def utc_now():
    return int(time.time())


def utc_today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class FootballApi:
    def __init__(self, session, key, storage):
        self.session = session
        self.key = key
        self.storage = storage

    def remaining_today(self):
        """Requests left today: the lower of our own count and the API's (sometimes lagging) header."""
        row = self.storage.budget_row(utc_today())
        if row is None:
            return DAILY_REQUEST_LIMIT
        remaining = DAILY_REQUEST_LIMIT - row["used"]
        if row["header_remaining"] is not None:
            remaining = min(remaining, row["header_remaining"])
        return max(remaining, 0)

    async def _get(self, path, params):
        today = utc_today()
        if self.remaining_today() <= 0:
            raise BudgetExhausted("Daily request budget used up")
        try:
            async with self.session.get(
                f"{API_BASE}{path}",
                params=params,
                headers={"x-apisports-key": self.key},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                header = resp.headers.get("x-ratelimit-requests-remaining", "")
                self.storage.count_request(today, int(header) if header.isdigit() else None)
                if resp.status != 200:
                    raise ApiError(f"HTTP {resp.status}")
                body = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            raise ApiError(f"{type(e).__name__}: {e}") from e

        # Errors come back as HTTP 200 with a non-empty "errors" (a dict); success has errors == []
        errors = body.get("errors")
        if errors:
            if isinstance(errors, dict) and "requests" in errors:
                self.storage.mark_budget_exhausted(today)
                raise BudgetExhausted(str(errors["requests"]))
            raise ApiError(json.dumps(errors))
        response = body.get("response")
        if not isinstance(response, list):
            raise ApiError("Response missing 'response' list")
        return response

    async def fixtures_on(self, utc_date):
        return await self._get("/fixtures", {"date": utc_date})

    async def fixture(self, fixture_id):
        response = await self._get("/fixtures", {"id": fixture_id})
        if not response:
            raise ApiError(f"Fixture {fixture_id} not found")
        return response[0]


class ReplayApi:
    """
    Serves a scripted match from a JSON file instead of the network:
      {"fixtures": [<items like /fixtures?date=>], "timeline": [<items like /fixtures?id=>, ...]}
    Each fixture() call advances one timeline step (staying on the last). A step of
    {"error": "..."} simulates a failed poll. Kickoff is shifted to ~60 seconds after startup.
    """

    def __init__(self, path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        offset = utc_now() + 60 - data["fixtures"][0]["fixture"]["timestamp"]
        self.fixtures = [self._shift(f, offset) for f in data["fixtures"]]
        self.timeline = [
            step if "error" in step else self._shift(step, offset) for step in data["timeline"]
        ]
        self.step = 0

    @staticmethod
    def _shift(item, offset):
        item = copy.deepcopy(item)
        fx = item["fixture"]
        fx["timestamp"] += offset
        fx["date"] = datetime.fromtimestamp(fx["timestamp"], timezone.utc).isoformat()
        return item

    def remaining_today(self):
        return DAILY_REQUEST_LIMIT

    async def fixtures_on(self, utc_date):
        return copy.deepcopy(self.fixtures)

    async def fixture(self, fixture_id):
        step = self.timeline[min(self.step, len(self.timeline) - 1)]
        self.step += 1
        if "error" in step:
            raise ApiError(step["error"])
        return copy.deepcopy(step)
