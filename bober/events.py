"""
Pure match/event logic (no Discord, no network) so it can be unit tested.
"""
import math
from collections import Counter
from dataclasses import dataclass

NOT_STARTED = {"TBD", "NS"}
FINISHED = {"FT", "AET", "PEN"}
CALLED_OFF = {"PST", "CANC", "ABD", "AWD", "WO"}
INTERRUPTED = {"SUSP", "INT"}

GOAL_CATEGORIES = {"goal", "own_goal", "penalty_goal"}


def categorize(ev):
    """Map an API-Football event to one of our event categories, or None to ignore it."""
    etype = (ev.get("type") or "").lower()
    detail = (ev.get("detail") or "").lower()
    if etype == "goal":
        if "missed" in detail:
            return "missed_penalty"
        if "own" in detail:
            return "own_goal"
        if "penalty" in detail:
            return "penalty_goal"
        return "goal"
    if etype == "card":
        if "red" in detail or "second yellow" in detail:
            return "red_card"
        return "yellow_card"
    if etype == "subst":
        return "substitution"
    if etype == "var":
        if "goal cancelled" in detail or "goal disallowed" in detail:
            return "var_goal_cancelled"
        return "var_other"
    return None


def family(category):
    """Goal variants share a family so a detail correction (Normal Goal -> Penalty) isn't a new event."""
    return "goal" if category in GOAL_CATEGORIES else category


def is_shootout(ev):
    return "shootout" in (ev.get("comments") or "").lower()


def event_key(ev, category):
    # Player is deliberately left out: the API often fills the scorer in a poll or two later
    team_id = (ev.get("team") or {}).get("id")
    elapsed = (ev.get("time") or {}).get("elapsed")
    return f"{team_id}|{family(category)}|{elapsed}"


def player_id(ev):
    return (ev.get("player") or {}).get("id")


def minute_label(ev):
    t = ev.get("time") or {}
    elapsed, extra = t.get("elapsed"), t.get("extra")
    if elapsed is None:
        return "?'"
    return f"{elapsed}+{extra}'" if extra else f"{elapsed}'"


@dataclass
class EventRef:
    key: str
    occurrence: int
    category: str
    event: dict

    @property
    def ident(self):
        return (self.key, self.occurrence)


def diff_events(events, posted):
    """
    Compare the API's full event list against what we've already recorded.

    posted: {(key, occurrence): player_id or None}
    Returns (new, filled, vanished): events never seen before, seen events whose
    player was missing before but is present now, and idents we recorded that the
    API no longer lists (corrected or deleted).
    """
    counts = Counter()
    new, filled, seen = [], [], set()
    for ev in events:
        if is_shootout(ev):
            continue
        category = categorize(ev)
        if category is None:
            continue
        key = event_key(ev, category)
        counts[key] += 1
        ref = EventRef(key, counts[key], category, ev)
        seen.add(ref.ident)
        if ref.ident not in posted:
            new.append(ref)
        elif posted[ref.ident] is None and player_id(ev) is not None:
            filled.append(ref)
    vanished = [ident for ident in posted if ident not in seen]
    return new, filled, vanished


CORRECTION_WINDOW = 3  # minutes the API may shift an event's time when it corrects it


def _key_parts(key):
    team, fam, elapsed = key.split("|")
    return team, fam, int(elapsed) if elapsed.lstrip("-").isdigit() else None


def find_correction(ref, orphans):
    """
    The API sometimes rewrites an event it already sent (minute nudged, team fixed). That changes
    the dedup key, so it looks new. Return the vanished posted row (a dict with event_key and
    player_id) it most likely replaces, or None if it really is new.
    """
    ref_team, ref_fam, ref_elapsed = _key_parts(ref.key)
    pid = player_id(ref.event)
    best, best_gap = None, None
    for row in orphans:
        team, fam, elapsed = _key_parts(row["event_key"])
        if fam != ref_fam or elapsed is None or ref_elapsed is None:
            continue
        gap = abs(elapsed - ref_elapsed)
        if gap > CORRECTION_WINDOW:
            continue
        if pid is not None and row["player_id"] is not None:
            if pid != row["player_id"]:
                continue
        elif team != ref_team:
            continue  # without both players known, only trust a same-team minute nudge
        if best_gap is None or gap < best_gap:
            best, best_gap = row, gap
    return best


def own_goal_convention(events, home_id, away_id, home_goals, away_goals):
    """
    API-Football doesn't document which team an own goal is listed under. Check the event list
    against the scoreboard: True if own goals sit under the team that benefits, False if under
    the scorer's own team, None if this poll can't tell (no own goals, lagging score, VAR...).
    """
    if home_goals is None or away_goals is None:
        return None
    regular, own = Counter(), Counter()
    for ev in events:
        if is_shootout(ev):
            continue
        category = categorize(ev)
        team_id = (ev.get("team") or {}).get("id")
        if category in ("goal", "penalty_goal"):
            regular[team_id] += 1
        elif category == "own_goal":
            own[team_id] += 1
    if not own:
        return None
    for_beneficiary = (home_goals == regular[home_id] + own[home_id]
                       and away_goals == regular[away_id] + own[away_id])
    for_scorer = (home_goals == regular[home_id] + own[away_id]
                  and away_goals == regular[away_id] + own[home_id])
    if for_beneficiary == for_scorer:
        return None
    return for_beneficiary


def wanted(category, yellows, subs):
    if category == "yellow_card":
        return yellows
    if category == "substitution":
        return subs
    return True


def estimate_requests(status, elapsed, poll_interval=90):
    """Rough number of requests needed to follow a match from its current state to full time."""
    if status in FINISHED or status in CALLED_OFF:
        return 0
    if status is None or status in NOT_STARTED:
        playing, overhead = 100, 5  # ~90 min + stoppage, plus prestart rechecks and the HT wake-up
    else:
        playing = max(5, 100 - (elapsed or 0))
        overhead = 3 if status == "1H" else 1
    return math.ceil(playing * 60 / poll_interval) + overhead
