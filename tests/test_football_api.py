"""
FootballApi against a fake aiohttp session: budget accounting and API-Football's error conventions.
"""

import asyncio
import json

import aiohttp
import pytest

from bober.config import DAILY_REQUEST_LIMIT
from bober.football_api import ApiError, BudgetExhausted, FootballApi, utc_today


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None, raw=None):
        self.status = status
        self.body = {"errors": [], "response": []} if body is None else body
        self.headers = headers or {}
        self.raw = raw  # when set, json() parses this text (so invalid JSON can be simulated)

    async def json(self, content_type=None):
        return json.loads(self.raw) if self.raw is not None else self.body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers})
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def ok(response, remaining_header=None):
    headers = (
        {} if remaining_header is None else {"x-ratelimit-requests-remaining": remaining_header}
    )
    return FakeResponse(body={"errors": [], "response": response}, headers=headers)


def api_with(storage, *responses):
    session = FakeSession(*responses)
    return FootballApi(session, "test-key", storage), session


def used(storage):
    row = storage.budget_row(utc_today())
    return row["used"] if row else 0


async def test_success_returns_response_and_counts(storage):
    api, session = api_with(storage, ok([{"a": 1}], "97"))
    assert await api.fixtures_on("2026-11-04") == [{"a": 1}]

    call = session.calls[0]
    assert call["url"].endswith("/fixtures")
    assert call["params"] == {"date": "2026-11-04"}
    assert call["headers"] == {"x-apisports-key": "test-key"}
    row = storage.budget_row(utc_today())
    assert (row["used"], row["header_remaining"]) == (1, 97)


@pytest.mark.parametrize("header", [None, "", "abc"])
async def test_missing_or_garbage_header_is_ignored(storage, header):
    api, _ = api_with(storage, ok([], header))
    await api.fixtures_on("2026-11-04")
    assert storage.budget_row(utc_today())["header_remaining"] is None
    assert api.remaining_today() == DAILY_REQUEST_LIMIT - 1


def test_remaining_is_lower_of_own_count_and_header(storage):
    api, _ = api_with(storage)
    assert api.remaining_today() == DAILY_REQUEST_LIMIT  # nothing used yet today
    storage.count_request(utc_today(), 50)  # header lags behind: trust it, it's lower
    assert api.remaining_today() == 50
    storage.count_request(utc_today(), 99)  # header says more than we know we've used
    assert api.remaining_today() == DAILY_REQUEST_LIMIT - 2


def test_remaining_never_negative(storage):
    api, _ = api_with(storage)
    for _ in range(DAILY_REQUEST_LIMIT + 5):
        storage.count_request(utc_today(), None)
    assert api.remaining_today() == 0


async def test_exhausted_budget_raises_before_any_request(storage):
    storage.mark_budget_exhausted(utc_today())
    api, session = api_with(storage, ok([]))
    with pytest.raises(BudgetExhausted):
        await api.fixture(1)
    assert session.calls == []


async def test_requests_error_marks_budget_exhausted(storage):
    body = {"errors": {"requests": "You have reached the request limit for the day"}}
    api, _ = api_with(storage, FakeResponse(body=body))
    with pytest.raises(BudgetExhausted):
        await api.fixture(1)
    assert api.remaining_today() == 0


@pytest.mark.parametrize("errors", [{"token": "Error/Missing application key"}, ["oops"]])
async def test_other_api_errors_raise_api_error(storage, errors):
    api, _ = api_with(storage, FakeResponse(body={"errors": errors, "response": []}))
    with pytest.raises(ApiError) as info:
        await api.fixture(1)
    assert not isinstance(info.value, BudgetExhausted)
    assert used(storage) == 1


@pytest.mark.parametrize("status", [429, 500])
async def test_http_error_still_counts_the_request(storage, status):
    api, _ = api_with(storage, FakeResponse(status=status))
    with pytest.raises(ApiError, match=f"HTTP {status}"):
        await api.fixture(1)
    assert used(storage) == 1


@pytest.mark.parametrize("error", [aiohttp.ClientConnectionError("down"), asyncio.TimeoutError()])
async def test_transport_errors_become_api_error(storage, error):
    api, _ = api_with(storage, error)
    with pytest.raises(ApiError):
        await api.fixture(1)


async def test_invalid_json_becomes_api_error(storage):
    api, _ = api_with(storage, FakeResponse(raw="<html>not json</html>"))
    with pytest.raises(ApiError):
        await api.fixture(1)
    assert used(storage) == 1


@pytest.mark.parametrize("body", [{"errors": []}, {"errors": [], "response": {"x": 1}}])
async def test_bad_response_shape_raises(storage, body):
    api, _ = api_with(storage, FakeResponse(body=body))
    with pytest.raises(ApiError, match="response"):
        await api.fixture(1)


async def test_fixture_returns_first_item_and_uses_id_param(storage):
    api, session = api_with(storage, ok([{"fixture": {"id": 5}}]))
    assert await api.fixture(5) == {"fixture": {"id": 5}}
    # The free plan rejects `ids` (multi-fixture lookups)
    assert session.calls[0]["params"] == {"id": 5}


async def test_fixture_not_found(storage):
    api, _ = api_with(storage, ok([]))
    with pytest.raises(ApiError, match="not found"):
        await api.fixture(5)
