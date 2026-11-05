"""
Match tracking slash commands for Bober (˶ᵔ ᵕ ᵔ˶)
"""

import asyncio
import contextlib
import os
from datetime import datetime, timezone

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from .. import embeds
from ..config import (
    API_FOOTBALL_KEY,
    BUDGET_FLOOR,
    DB_PATH,
    GUILD_ID,
    INTERVALS,
    LEAGUES,
    LINES_PATH,
    REPLAY_PATH,
)
from ..events import CALLED_OFF, FINISHED, NOT_STARTED, estimate_requests
from ..football_api import ApiError, FootballApi, ReplayApi, utc_now, utc_today
from ..storage import Storage
from ..tracker import Tracker

HIDDEN_STATUSES = FINISHED | CALLED_OFF


def fixture_label(f):
    home, away = f["teams"]["home"]["name"], f["teams"]["away"]["name"]
    kickoff = datetime.fromtimestamp(f["fixture"]["timestamp"], timezone.utc).strftime("%H:%M")
    started = " (started)" if f["fixture"]["timestamp"] <= utc_now() else ""
    return f"{home} vs {away} — {kickoff} UTC{started} · {f['league']['name']}"[:100]


class ConfirmUntrackView(discord.ui.View):
    def __init__(self, cog, fixture_id, user_id):
        super().__init__(timeout=60)
        self.cog = cog
        self.fixture_id = fixture_id
        self.user_id = user_id
        self.message = None  # the interaction whose ephemeral response holds this view

    async def interaction_check(self, interaction):
        return interaction.user.id == self.user_id

    async def on_timeout(self):
        if self.message is not None:
            with contextlib.suppress(discord.HTTPException):
                await self.message.edit_original_response(
                    content="⌛ Untrack request expired. Still tracking.", view=None
                )

    @discord.ui.button(label="Stop tracking", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        self.stop()
        match = self.cog.storage.active_match()
        if match is None or match["fixture_id"] != self.fixture_id:
            await interaction.response.edit_message(
                content="That match isn't being tracked anymore.", view=None
            )
            return
        await interaction.response.edit_message(content="✅ Stopped tracking.", view=None)
        await self.cog.untrack(match, interaction.user)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        self.stop()
        await interaction.response.edit_message(content="Okay, Bober keeps watching.", view=None)


class Tracking(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.storage = None
        self.session = None
        self.api = None
        self.tracker = None
        self.fetch_lock = asyncio.Lock()

    async def cog_load(self):
        if REPLAY_PATH and os.path.exists(DB_PATH):  # noqa: ASYNC240 - once, at startup
            os.remove(DB_PATH)  # every replay starts from a clean slate
        self.storage = Storage(DB_PATH)
        if REPLAY_PATH:
            print(f"REPLAY MODE: serving {REPLAY_PATH}, no real API calls")
            self.api = ReplayApi(REPLAY_PATH)
        else:
            if not API_FOOTBALL_KEY:
                print("WARNING: API_FOOTBALL_KEY is not set; /bober-track will not work")
            self.session = aiohttp.ClientSession()
            self.api = FootballApi(self.session, API_FOOTBALL_KEY, self.storage)
        self.tracker = Tracker(
            self.bot, self.api, self.storage, embeds.BoberLines(LINES_PATH), INTERVALS
        )
        self.resume_task = asyncio.create_task(self._resume_when_ready())

    async def cog_unload(self):
        self.tracker.stop()
        if self.session:
            await self.session.close()
        self.storage.close()

    async def _resume_when_ready(self):
        await self.bot.wait_until_ready()
        self.tracker.resume()

    def has_api(self):
        return bool(REPLAY_PATH or API_FOOTBALL_KEY)

    async def todays_fixtures(self):
        """Today's (timezone.utc) fixtures in our leagues, fetched at most once a day."""
        today = utc_today()
        async with self.fetch_lock:
            if not self.storage.has_fixture_day(today):
                fixtures = await self.api.fixtures_on(today)
                ours = [f for f in fixtures if f["league"]["id"] in LEAGUES]
                self.storage.save_fixture_day(today, ours, utc_now())
        return self.storage.fixtures_for_day(today)

    @staticmethod
    def trackable(f):
        if f["fixture"]["status"]["short"] in HIDDEN_STATUSES:
            return False
        # The cached status can be hours old, so also hide anything that must be over by now
        return utc_now() < f["fixture"]["timestamp"] + INTERVALS.give_up_after

    async def untrack(self, match, user):
        self.tracker.stop()
        self.storage.stop_tracking(match["fixture_id"])
        channel = self.bot.get_channel(match["channel_id"])
        if channel is not None:
            with contextlib.suppress(discord.HTTPException):
                await channel.send(
                    embed=embeds.notice_embed(
                        "🛑 BOBER STOPPED TRACKING",
                        f"{match['home_name']} vs {match['away_name']} (stopped by {user.mention})",
                    ),
                    allowed_mentions=discord.AllowedMentions.none(),
                )

    # --- commands ---

    async def match_autocomplete(self, interaction, current):
        if not self.has_api():
            return []
        try:
            fixtures = await self.todays_fixtures()
        except ApiError as e:
            print(f"Autocomplete fixture fetch failed: {e}")
            return []
        needle = current.lower().strip()
        matches = [
            f
            for f in fixtures
            if self.trackable(f)
            and needle
            in " ".join(
                (f["teams"]["home"]["name"], f["teams"]["away"]["name"], f["league"]["name"])
            ).lower()
        ]
        matches.sort(key=lambda f: f["fixture"]["timestamp"])
        return [
            app_commands.Choice(name=fixture_label(f), value=str(f["fixture"]["id"]))
            for f in matches[:25]
        ]

    @app_commands.guilds(GUILD_ID)
    @app_commands.command(
        name="bober-track", description="Bober watches a match and shouts about it"
    )
    @app_commands.describe(
        match="Today's match (start typing a team name)",
        yellows="Also post yellow cards",
        subs="Also post substitutions",
        ping_role="Role to ping on goals and full time",
    )
    @app_commands.autocomplete(match=match_autocomplete)
    async def bober_track(
        self,
        interaction: discord.Interaction,
        match: str,
        yellows: bool = False,
        subs: bool = False,
        ping_role: discord.Role | None = None,
    ):
        """Start tracking one of today's matches in this channel."""
        await interaction.response.defer(ephemeral=True)

        async def fail(text):
            await interaction.followup.send(text, ephemeral=True)

        if not self.has_api():
            return await fail(
                "Bober has no API key! Ask whoever runs Bober to set `API_FOOTBALL_KEY`."
            )

        active = self.storage.active_match()
        if active is not None:
            return await fail(
                f"Bober is already tracking **{active['home_name']} vs {active['away_name']}**. "
                "One match at a time!"
            )

        if not match.isdigit():
            return await fail("Pick a match from the list Bober suggests.")
        try:
            await self.todays_fixtures()
        except ApiError as e:
            return await fail(f"Bober couldn't load today's matches: {e}")
        fixture = self.storage.cached_fixture(utc_today(), int(match))
        if fixture is None:
            return await fail(
                "That isn't one of today's matches. Pick one from the list Bober suggests."
            )
        if not self.trackable(fixture):
            return await fail("That match is already over (or called off).")

        channel = interaction.channel
        perms = channel.permissions_for(interaction.guild.me)
        if not (perms.send_messages and perms.embed_links):
            return await fail("Bober can't post embeds in this channel. Check his permissions.")

        remaining = self.api.remaining_today()
        if remaining <= BUDGET_FLOOR:
            return await fail(
                f"Bober is out of API requests for today ({remaining} left). "
                "Try again after 00:00 UTC."
            )

        status = fixture["fixture"]["status"]
        # The cached status is from the morning; if kickoff has passed, assume it's in play
        if status["short"] in NOT_STARTED and fixture["fixture"]["timestamp"] <= utc_now():
            minutes_in = (utc_now() - fixture["fixture"]["timestamp"]) // 60
            needed = estimate_requests(
                "2H" if minutes_in > 45 else "1H", minutes_in, INTERVALS.poll
            )
        else:
            needed = estimate_requests(status["short"], status.get("elapsed"), INTERVALS.poll)
        warning = None
        if needed > remaining - BUDGET_FLOOR:
            warning = (
                f"Only ~{remaining} API requests left today and this match needs ~{needed}. "
                "Bober will probably go quiet before full time."
            )

        self.storage.start_tracking(
            fixture,
            channel.id,
            interaction.user.id,
            yellows,
            subs,
            ping_role.id if ping_role else None,
            utc_now(),
        )
        tracked = self.storage.active_match()
        try:
            await channel.send(
                embed=embeds.tracking_embed(tracked, yellows, subs, ping_role, warning),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException as e:
            print(f"Failed to announce tracking: {e}")
        self.tracker.start(tracked["fixture_id"])
        await interaction.followup.send(
            "✅ Bober is on it!" + (f"\n⚠️ {warning}" if warning else ""), ephemeral=True
        )

    @app_commands.guilds(GUILD_ID)
    @app_commands.command(
        name="bober-untrack", description="Bober stops watching the current match"
    )
    async def bober_untrack(self, interaction: discord.Interaction):
        """Stop tracking (confirmation required once the match has kicked off)."""
        match = self.storage.active_match()
        if match is None:
            return await interaction.response.send_message(
                "Bober isn't tracking anything.", ephemeral=True
            )

        is_owner = interaction.user.id == match["user_id"]
        if not (is_owner or interaction.user.guild_permissions.manage_guild):
            return await interaction.response.send_message(
                f"Only <@{match['user_id']}> or someone with Manage Server can stop this.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        started = match["kickoff_ts"] <= utc_now() or (
            match["last_status"] not in (None, *NOT_STARTED)
        )
        if not started:
            await interaction.response.send_message("✅ Stopped tracking.", ephemeral=True)
            return await self.untrack(match, interaction.user)

        remaining = self.api.remaining_today()
        view = ConfirmUntrackView(self, match["fixture_id"], interaction.user.id)
        await interaction.response.send_message(
            f"⚠️ ~{match['requests_used']} API requests already spent on this match, ~{remaining} left today. "
            "Another match tracked now may not make it to full time.",
            view=view,
            ephemeral=True,
        )
        view.message = interaction

    @app_commands.guilds(GUILD_ID)
    @app_commands.command(name="bober-status", description="What is Bober watching?")
    async def bober_status(self, interaction: discord.Interaction):
        """Show the tracked match and remaining API budget (costs no requests)."""
        remaining = self.api.remaining_today()
        match = self.storage.active_match()
        if match is None:
            return await interaction.response.send_message(
                f"Bober isn't tracking anything. **{remaining}** API requests left today.",
                ephemeral=True,
            )

        embed = discord.Embed(
            title="👀 BOBER STATUS",
            description=f"**{embeds.score_line(match, match['home_goals'], match['away_goals'])}**\n"
            f"{match['league_name']}",
            color=embeds.COLORS["status"],
        )
        embed.add_field(name="Match status", value=match["last_status"] or "Waiting for kickoff")
        embed.add_field(name="Kickoff", value=f"<t:{match['kickoff_ts']}:t>")
        embed.add_field(
            name="Last checked",
            value=f"<t:{match['last_poll_at']}:R>" if match["last_poll_at"] else "Not yet",
        )
        embed.add_field(name="Channel", value=f"<#{match['channel_id']}>")
        embed.add_field(name="Started by", value=f"<@{match['user_id']}>")
        embed.add_field(
            name="API requests",
            value=f"{match['requests_used']} used on this match, {remaining} left today",
        )
        await interaction.response.send_message(
            embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )


async def setup(bot):
    await bot.add_cog(Tracking(bot))
