import os

# Must run before anything imports bober.config: load_dotenv never overrides a variable that is
# already set, so a developer's .env can't switch the test run into replay mode (which would
# also delete bober_replay.db when the cog loads).
os.environ["BOBER_REPLAY"] = ""

import pytest
from fakes import KICKOFF, AsyncioWithFakeSleep, FakeClock

from bober import tracker as tracker_module
from bober.storage import Storage


@pytest.fixture
def storage(tmp_path):
    s = Storage(str(tmp_path / "test.db"))
    yield s
    s.close()


@pytest.fixture
def clock(monkeypatch):
    """Fake time for bober.tracker, starting 10 minutes before KICKOFF."""
    c = FakeClock(KICKOFF - 600)
    monkeypatch.setattr(tracker_module, "utc_now", c.time)
    monkeypatch.setattr(tracker_module, "asyncio", AsyncioWithFakeSleep(c.sleep))
    return c
