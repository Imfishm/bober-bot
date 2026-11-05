"""
Embed builders and Bober's line picker (˶ᵔ ᵕ ᵔ˶)
"""

import json
import random
import string

import discord

FALLBACK_LINE = "BOBER HAS NO WORDS!"

COLORS = {
    "goal": 0x2ECC71,
    "own_goal": 0x2ECC71,
    "penalty_goal": 0x2ECC71,
    "missed_penalty": 0xE67E22,
    "yellow_card": 0xF1C40F,
    "red_card": 0xE74C3C,
    "substitution": 0x3498DB,
    "var_goal_cancelled": 0x9B59B6,
    "var_other": 0x9B59B6,
    "status": 0x1ABC9C,
    "fulltime": 0x34495E,
    "notice": 0x7F8C8D,
    "warning": 0xE67E22,
}

TITLES = {
    "goal": "⚽ GOAL",
    "own_goal": "⚽ OWN GOAL",
    "penalty_goal": "⚽ PENALTY GOAL",
    "missed_penalty": "❌ MISSED PENALTY",
    "yellow_card": "🟨 YELLOW CARD",
    "red_card": "🟥 RED CARD",
    "substitution": "🔄 SUBSTITUTION",
    "var_goal_cancelled": "📺 VAR: GOAL CANCELLED",
    "var_other": "📺 VAR",
}

STATUS_TITLES = {
    "kickoff": "🟢 KICKOFF",
    "halftime": "⏸️ HALF-TIME",
    "second_half": "▶️ SECOND HALF",
    "extra_time": "⏱️ EXTRA TIME",
    "penalties": "🎯 PENALTY SHOOTOUT",
    "interrupted": "⚠️ MATCH INTERRUPTED",
}

UNKNOWN_PLAYER = "*Bober is still squinting at who it was...*"


class BoberLines:
    """Random shouty lines per event key, loaded from bober_lines.json."""

    def __init__(self, path):
        try:
            with open(path, encoding="utf-8") as f:
                self.lines = json.load(f)
        except (OSError, ValueError) as e:
            print(f"Could not load {path}: {e}")
            self.lines = {}
        self._last = {}

    @staticmethod
    def _usable(line, ctx):
        try:
            fields = {name for _, name, _, _ in string.Formatter().parse(line) if name}
        except ValueError:
            return False
        return all(ctx.get(name) is not None for name in fields)

    def pick(self, key, ctx):
        candidates = [line for line in self.lines.get(key) or [] if self._usable(line, ctx)]
        if not candidates:
            candidates = [
                line for line in self.lines.get("default") or [] if self._usable(line, ctx)
            ]
        if not candidates:
            return FALLBACK_LINE
        if len(candidates) > 1 and self._last.get(key) in candidates:
            candidates.remove(self._last[key])
        line = random.choice(candidates)
        self._last[key] = line
        try:
            # Bober shouts everything, names included
            return line.format(
                **{k: v.upper() if isinstance(v, str) else v for k, v in ctx.items()}
            )
        except (KeyError, IndexError, ValueError):
            return FALLBACK_LINE


def score_line(match, home_goals, away_goals):
    hg = "?" if home_goals is None else home_goals
    ag = "?" if away_goals is None else away_goals
    return f"{match['home_name']} {hg}–{ag} {match['away_name']}"


def _with_line(embed, line):
    embed.add_field(name="​", value=f"> *{line}*", inline=False)
    return embed


def name_of(obj):
    return (obj or {}).get("name")


def who_text(category, ev):
    player, assist = name_of(ev.get("player")), name_of(ev.get("assist"))
    detail, comments = ev.get("detail") or "", ev.get("comments")
    if category == "substitution":
        # API-Football puts the player coming off in "player" and the one coming on in "assist"
        return f"⬆️ {assist or '?'}\n⬇️ {player or '?'}"
    if category == "var_goal_cancelled" or category == "var_other":
        return f"{detail}" + (f" ({player})" if player else "")
    if not player:
        return UNKNOWN_PLAYER
    if category == "own_goal":
        return f"{player} (own goal)"
    if category == "goal" and assist:
        return f"{player}\n🅰️ {assist}"
    if category == "red_card" and "second" in detail.lower():
        return f"{player} (second yellow)"
    if category in ("yellow_card", "red_card") and comments:
        return f"{player} — {comments}"
    return player


def event_embed(category, ev, minute, match, home_goals, away_goals, line, team_name):
    embed = discord.Embed(
        title=f"{TITLES[category]} — {minute}",
        description=f"**{score_line(match, home_goals, away_goals)}**",
        color=COLORS[category],
    )
    embed.add_field(name=team_name or "?", value=who_text(category, ev), inline=False)
    return _with_line(embed, line)


def correct_event(embed, category, ev, minute, team_name):
    """Rewrite an already-posted event embed after the API corrected its minute/team/detail."""
    embed.title = f"{TITLES[category]} — {minute}"
    embed.color = COLORS[category]
    embed.set_field_at(0, name=team_name or "?", value=who_text(category, ev), inline=False)
    return embed


def fill_player(embed, category, ev):
    """Rewrite the player field of an already-posted event embed once the API knows who it was."""
    embed.set_field_at(0, name=embed.fields[0].name, value=who_text(category, ev), inline=False)
    return embed


def cancel_goal(embed, reason="CANCELLED BY VAR"):
    """Strike through a goal that VAR (or a silent API correction) took back."""
    embed.title = f"~~{embed.title}~~ ({reason})"
    embed.description = f"~~{embed.description}~~"
    embed.color = COLORS["notice"]
    return embed


def status_embed(kind, match, home_goals, away_goals, line):
    embed = discord.Embed(
        title=STATUS_TITLES[kind],
        description=f"**{score_line(match, home_goals, away_goals)}**",
        color=COLORS["status"],
    )
    return _with_line(embed, line)


def _goal_list(goals):
    return (
        "\n".join(
            f"{g['minute']} {g['player_name'] or '?'}"
            + (" (OG)" if g["category"] == "own_goal" else "")
            for g in goals
        )
        or "None"
    )


def fulltime_embed(match, home_goals, away_goals, penalty, goals, reds, line):
    desc = f"**{score_line(match, home_goals, away_goals)}**"
    if penalty and penalty.get("home") is not None:
        desc += f"\n(Penalties: {penalty['home']}–{penalty['away']})"
    embed = discord.Embed(title="🏁 FULL TIME", description=desc, color=COLORS["fulltime"])
    embed.add_field(name="Goals", value=_goal_list(goals), inline=False)
    if reds:
        embed.add_field(
            name="Red cards",
            value="\n".join(f"{r['minute']} {r['player_name'] or '?'}" for r in reds),
            inline=False,
        )
    return _with_line(embed, line)


def catchup_embed(match, minute, home_goals, away_goals, goals, reds, line):
    embed = discord.Embed(
        title=f"📋 CATCHING UP — {minute}",
        description=f"**{score_line(match, home_goals, away_goals)}**",
        color=COLORS["status"],
    )
    embed.add_field(name="Goals so far", value=_goal_list(goals), inline=False)
    if reds:
        embed.add_field(
            name="Red cards so far",
            value="\n".join(f"{r['minute']} {r['player_name'] or '?'}" for r in reds),
            inline=False,
        )
    return _with_line(embed, line)


def notice_embed(title, text, warning=False):
    return discord.Embed(
        title=title, description=text, color=COLORS["warning" if warning else "notice"]
    )


def tracking_embed(match, yellows, subs, ping_role, warning):
    ts = match["kickoff_ts"]
    embed = discord.Embed(
        title="👀 BOBER IS WATCHING",
        description=f"**{match['home_name']} vs {match['away_name']}**\n{match['league_name']}",
        color=COLORS["status"],
    )
    embed.add_field(name="Kickoff", value=f"<t:{ts}:t> (<t:{ts}:R>)")
    extras = [name for name, on in (("yellow cards", yellows), ("substitutions", subs)) if on]
    embed.add_field(name="Also posting", value=", ".join(extras) or "major events only")
    if ping_role:
        embed.add_field(name="Pinging", value=f"{ping_role.mention} on goals and full time")
    if warning:
        embed.add_field(name="⚠️ Budget", value=warning, inline=False)
    return embed
