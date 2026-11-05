"""
Boots the real bot from main.py without connecting to Discord: the cog loads, the command tree is
what Discord will receive on sync, and unloading leaves nothing running or open.
"""

import asyncio
import sqlite3

import discord
import pytest

from bober import config
from bober.config import GUILD_ID

COMMANDS = ["bober-status", "bober-track", "bober-untrack"]


async def test_bot_boots_offline(monkeypatch, tmp_path):
    # load_extension executes a *fresh copy* of bober.cogs.tracking (not the imported module),
    # which re-imports these names from bober.config, so patch them at the source.
    db = tmp_path / "smoke.db"
    monkeypatch.setattr(config, "DB_PATH", str(db))
    monkeypatch.setattr(config, "API_FOOTBALL_KEY", "test-key")
    monkeypatch.setattr(config, "REPLAY_PATH", None)

    import main

    bot = main.bot
    async with bot:  # runs discord.py's async setup, as main() does; never logs in
        await main.load_extensions()
        cog = bot.get_cog("Tracking")
        assert db.exists()

        guild = discord.Object(id=GUILD_ID)
        commands = bot.tree.get_commands(guild=guild)
        assert sorted(c.name for c in commands) == COMMANDS
        assert bot.tree.get_commands() == []  # every command must carry @app_commands.guilds

        # Serialize exactly what tree.sync() would send, so payload problems show up here
        payloads = {c.name: c.to_dict(bot.tree) for c in commands}
        track_options = {o["name"]: o for o in payloads["bober-track"]["options"]}
        assert track_options["match"]["autocomplete"] is True
        assert set(track_options) == {"match", "yellows", "subs", "ping_role"}

        await bot.unload_extension("bober.cogs.tracking")
        await asyncio.sleep(0)  # let cancellations land

        assert cog.session.closed
        assert cog.resume_task.done()
        with pytest.raises(sqlite3.ProgrammingError):
            cog.storage.active_match()  # connection closed
