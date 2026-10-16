"""
The poll loop: waits for kickoff, polls the tracked fixture, and posts what changed.
"""
import asyncio
import traceback

import discord

from . import embeds
from .config import BUDGET_FLOOR
from .events import (CALLED_OFF, FINISHED, GOAL_CATEGORIES, INTERRUPTED, NOT_STARTED, diff_events,
                    find_correction, minute_label, own_goal_convention, player_id, wanted)
from .football_api import ApiError, BudgetExhausted, utc_now

CATCHUP_GRACE = 120  # tracking started more than this long after kickoff -> summarize instead of replaying
OG_META_KEY = "own_goal_team_is_beneficiary"  # "1"/"0" once learned from a poll, see own_goal_convention


class Tracker:
    def __init__(self, bot, api, storage, lines, intervals):
        self.bot = bot
        self.api = api
        self.storage = storage
        self.lines = lines
        self.intervals = intervals
        self.task = None

    def is_running(self):
        return self.task is not None and not self.task.done()

    def start(self, fixture_id, resumed=False):
        self.stop()
        self.task = asyncio.create_task(self._run(fixture_id, resumed))

    def stop(self):
        if self.is_running():
            self.task.cancel()

    def resume(self):
        """Pick a still-active match back up after a restart."""
        match = self.storage.active_match()
        if match and not self.is_running():
            print(f"Resuming tracking of fixture {match['fixture_id']}")
            self.start(match["fixture_id"], resumed=True)

    # --- helpers ---

    async def _channel(self, channel_id):
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except discord.HTTPException:
                return None
        return channel

    async def _send(self, channel, embed, ping_role_id=None):
        try:
            if ping_role_id:
                return await channel.send(content=f"<@&{ping_role_id}>", embed=embed,
                                          allowed_mentions=discord.AllowedMentions(roles=True))
            return await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as e:
            print(f"Failed to send message: {e}")
            return None

    async def _edit(self, channel, message_id, change):
        """Fetch a posted message, apply change(embed), and save it."""
        try:
            message = await channel.fetch_message(message_id)
            if message.embeds:
                await message.edit(embed=change(message.embeds[0]))
        except discord.HTTPException as e:
            print(f"Failed to edit message {message_id}: {e}")

    async def _notice(self, channel, title, text, warning=False):
        await self._send(channel, embeds.notice_embed(title, text, warning))

    # --- main loop ---

    async def _run(self, fixture_id, resumed):
        channel = None
        try:
            match = self.storage.active_match()
            if match is None or match["fixture_id"] != fixture_id:
                return
            channel = await self._channel(match["channel_id"])
            if channel is None:
                print(f"Channel {match['channel_id']} is gone; dropping fixture {fixture_id}")
            else:
                await self._loop(fixture_id, channel, resumed)
            self.storage.stop_tracking(fixture_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            traceback.print_exc()
            self.storage.stop_tracking(fixture_id)
            if channel is not None:
                await self._notice(channel, "💥 BOBER TRIPPED",
                                   "Something broke and Bober stopped tracking. Sorry!", warning=True)

    async def _loop(self, fixture_id, channel, resumed):
        iv = self.intervals
        kickoff = self.storage.active_match()["kickoff_ts"]
        if kickoff > utc_now():
            await asyncio.sleep(kickoff - utc_now())

        failures = 0
        announced_back = not resumed
        while True:
            match = self.storage.active_match()
            if match is None or match["fixture_id"] != fixture_id:
                return
            now = utc_now()
            if now > kickoff + iv.give_up_after:
                await self._notice(channel, "😴 BOBER GAVE UP",
                                   "This match has gone on way too long. Bober stopped tracking.")
                return
            if self.api.remaining_today() <= BUDGET_FLOOR:
                await self._out_of_budget(channel)
                return

            self.storage.count_match_request(fixture_id)
            try:
                data = await self.api.fixture(fixture_id)
            except BudgetExhausted:
                await self._out_of_budget(channel)
                return
            except ApiError as e:
                failures += 1
                print(f"Poll failed ({failures} in a row): {e}")
                if failures == iv.failure_alert:
                    await self._notice(channel, "📡 BOBER LOST THE SIGNAL",
                                       "Bober can't reach the football API right now. Still trying...",
                                       warning=True)
                await asyncio.sleep(iv.poll)
                continue

            if failures >= iv.failure_alert:
                await self._notice(channel, "📡 SIGNAL IS BACK", "Bober can see again! Catching up...")
            failures = 0

            status = data["fixture"]["status"]["short"]
            if status in NOT_STARTED:
                if now > kickoff + iv.prestart_max_wait:
                    await self._notice(channel, "🤷 NO KICKOFF",
                                       "The match still hasn't started. Bober stopped waiting.", warning=True)
                    return
                await asyncio.sleep(iv.prestart_recheck)
                continue

            if not announced_back:
                goals = data.get("goals") or {}
                await self._notice(channel, "🔌 BOBER IS BACK",
                                   f"Current score: **{embeds.score_line(match, goals.get('home'), goals.get('away'))}**")
                announced_back = True

            if await self._process(match, channel, data):
                return
            await asyncio.sleep(iv.halftime_pause if status == "HT" else iv.poll)

    async def _out_of_budget(self, channel):
        await self._notice(channel, "💸 BOBER IS OUT OF API REQUESTS",
                           "No more updates today, so the final score will be missing. Sorry!", warning=True)

    async def _process(self, match, channel, data):
        """Post status changes and new events for one poll. Returns True when the match is over."""
        fixture_id = match["fixture_id"]
        status_info = data["fixture"]["status"]
        status, elapsed = status_info["short"], status_info.get("elapsed")
        goals = data.get("goals") or {}
        hg, ag = goals.get("home"), goals.get("away")
        home_id, away_id = data["teams"]["home"]["id"], data["teams"]["away"]["id"]
        teams = {home_id: match["home_name"], away_id: match["away_name"]}
        prev = match["last_status"]
        ping = match["ping_role_id"]

        base_ctx = {
            "team": match["home_name"],
            "opponent": match["away_name"],
            "score": f"{hg}–{ag}",
            "minute": f"{elapsed}'" if elapsed is not None else None,
            "player": None,
        }

        def status_post(kind, key):
            return self._send(channel, embeds.status_embed(kind, match, hg, ag, self.lines.pick(key, base_ctx)))

        catchup = prev is None and match["started_at"] > match["kickoff_ts"] + CATCHUP_GRACE
        events = data.get("events") or []
        posted = {(r["event_key"], r["occurrence"]): r for r in self.storage.posted_rows(fixture_id)}
        new, filled, vanished = diff_events(events, {ident: r["player_id"] for ident, r in posted.items()})
        orphans = [posted[ident] for ident in vanished if not posted[ident]["cancelled"]]
        self._learn_own_goal_convention(events, home_id, away_id, hg, ag)

        # Status changes that come before this poll's events
        if not catchup:
            if (prev is None or prev in NOT_STARTED) and status not in FINISHED | CALLED_OFF:
                await status_post("kickoff", "kickoff")
            if prev == "HT" and status == "2H":
                await status_post("second_half", "second_half")
            if status == "ET" and prev != "ET":
                await status_post("extra_time", "extra_time")
            if status == "P" and prev != "P":
                await status_post("penalties", "penalties")

        for ref in filled:
            await self._fill_player(channel, fixture_id, ref)
        for ref in new:
            row = find_correction(ref, orphans)
            if row is not None:
                orphans.remove(row)
                await self._correct_event(channel, fixture_id, row, ref, teams)
            else:
                await self._handle_event(channel, match, ref, teams, hg, ag, silent=catchup)
        await self._drop_deleted_goals(channel, match, orphans, hg, ag, silent=catchup)

        if catchup and status not in FINISHED | CALLED_OFF:
            await self._send(channel, embeds.catchup_embed(
                match, f"{elapsed}'", hg, ag,
                self.storage.events_of(fixture_id, sorted(GOAL_CATEGORIES)),
                self.storage.events_of(fixture_id, ["red_card"]),
                self.lines.pick("default", base_ctx),
            ))

        # Status changes that come after this poll's events
        done = False
        if status in FINISHED:
            penalty = (data.get("score") or {}).get("penalty")
            embed = embeds.fulltime_embed(
                match, hg, ag, penalty,
                self.storage.events_of(fixture_id, sorted(GOAL_CATEGORIES)),
                self.storage.events_of(fixture_id, ["red_card"]),
                self.lines.pick("fulltime", base_ctx),
            )
            await self._send(channel, embed, ping)
            done = True
        elif status in CALLED_OFF:
            await self._notice(channel, "🚫 MATCH CALLED OFF",
                               f"{match['home_name']} vs {match['away_name']}: {status_info.get('long', status)}",
                               warning=True)
            done = True
        elif not catchup:
            if status == "HT" and prev != "HT":
                await status_post("halftime", "halftime")
            elif status in INTERRUPTED and prev != status:
                await status_post("interrupted", "interrupted")

        self.storage.update_tracked(fixture_id, last_status=status, last_elapsed=elapsed,
                                    home_goals=hg, away_goals=ag, last_poll_at=utc_now())
        return done

    def _learn_own_goal_convention(self, events, home_id, away_id, hg, ag):
        if self.storage.get_meta(OG_META_KEY) is not None:
            return
        learned = own_goal_convention(events, home_id, away_id, hg, ag)
        if learned is not None:
            side = "the team that benefits" if learned else "the scorer's own team"
            print(f"Learned: API-Football lists own goals under {side}")
            self.storage.set_meta(OG_META_KEY, "1" if learned else "0")

    def _credited_team(self, ev, category, teams):
        """Team the event counts for. None for an own goal until we know the API's convention."""
        team_id = (ev.get("team") or {}).get("id")
        if category != "own_goal":
            return team_id
        convention = self.storage.get_meta(OG_META_KEY)
        if convention is None:
            return None
        if convention == "1":
            return team_id
        return next((t for t in teams if t != team_id), None)

    @staticmethod
    def _team_label(category, credited, teams):
        if category == "own_goal" and credited is None:
            return "Own goal"
        return teams.get(credited, "?")

    async def _correct_event(self, channel, fixture_id, row, ref, teams):
        """The API rewrote an event we already posted: re-key it and fix the message instead of reposting."""
        ev, category = ref.event, ref.category
        credited = self._credited_team(ev, category, teams)
        print(f"Event corrected by API: {row['event_key']}#{row['occurrence']} -> {ref.key}#{ref.occurrence}")
        self.storage.rekey_event(row["seq"], ref, credited if credited is not None else row["team_id"],
                                 minute_label(ev), player_id(ev), embeds.name_of(ev.get("player")))
        if row["message_id"]:
            label = self._team_label(category, credited, teams)
            await self._edit(channel, row["message_id"],
                             lambda e: embeds.correct_event(e, category, ev, minute_label(ev), label))

    async def _drop_deleted_goals(self, channel, match, orphans, hg, ag, silent):
        """
        A goal the API deleted without a VAR event: once the scoreboard agrees there are fewer goals
        than we posted, strike the newest vanished ones through.
        """
        if hg is None or ag is None:
            return
        fixture_id = match["fixture_id"]
        live = self.storage.events_of(fixture_id, sorted(GOAL_CATEGORIES))
        extra = len(live) - (hg + ag)
        live_seqs = {r["seq"] for r in live}
        gone = sorted((r for r in orphans if r["seq"] in live_seqs), key=lambda r: r["seq"], reverse=True)
        for row in gone[:max(extra, 0)]:
            self.storage.cancel_event(row["seq"])
            if silent:
                continue
            if row["message_id"]:
                await self._edit(channel, row["message_id"], lambda e: embeds.cancel_goal(e, "TAKEN BACK"))
            await self._notice(channel, "↩️ GOAL TAKEN BACK",
                               f"The {row['minute']} goal disappeared from the match data.\n"
                               f"**{embeds.score_line(match, hg, ag)}**")

    async def _handle_event(self, channel, match, ref, teams, hg, ag, silent):
        ev, category = ref.event, ref.category
        team = ev.get("team") or {}
        credited = self._credited_team(ev, category, teams)
        player_name = embeds.name_of(ev.get("player"))
        message_id = None

        if not silent and wanted(category, bool(match["yellows"]), bool(match["subs"])):
            ctx = {
                "team": teams.get(credited),
                "opponent": next((name for t, name in teams.items() if t != credited), None)
                            if credited is not None else None,
                # For subs the interesting player is the one coming on
                "player": embeds.name_of(ev.get("assist")) if category == "substitution" else player_name,
                "minute": minute_label(ev),
                "score": f"{hg}–{ag}",
            }
            embed = embeds.event_embed(category, ev, minute_label(ev), match, hg, ag,
                                       self.lines.pick(category, ctx),
                                       self._team_label(category, credited, teams))
            ping = match["ping_role_id"] if category in GOAL_CATEGORIES else None
            message = await self._send(channel, embed, ping)
            message_id = message.id if message else None

        if category == "var_goal_cancelled":
            await self._cancel_goal(channel, match["fixture_id"], team.get("id"), silent)

        self.storage.record_event(match["fixture_id"], ref, credited if credited is not None else team.get("id"),
                                  minute_label(ev),
                                  player_id(ev), player_name, message_id)

    async def _fill_player(self, channel, fixture_id, ref):
        row = self.storage.posted_event(fixture_id, ref.ident)
        self.storage.fill_player(fixture_id, ref.ident, player_id(ref.event), embeds.name_of(ref.event.get("player")))
        if row and row["message_id"]:
            await self._edit(channel, row["message_id"], lambda e: embeds.fill_player(e, ref.category, ref.event))

    async def _cancel_goal(self, channel, fixture_id, team_id, silent):
        row = self.storage.latest_live_goal(fixture_id, team_id) or self.storage.latest_live_goal(fixture_id)
        if row is None:
            return
        self.storage.cancel_event(row["seq"])
        if row["message_id"] and not silent:
            await self._edit(channel, row["message_id"], embeds.cancel_goal)
