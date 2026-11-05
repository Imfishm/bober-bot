import itertools
import json

from bober.config import LINES_PATH
from bober.embeds import FALLBACK_LINE, BoberLines
from bober.events import (
    categorize,
    diff_events,
    estimate_requests,
    event_key,
    find_correction,
    own_goal_convention,
    wanted,
)


def ev(elapsed, team_id, typ, detail, player_id=None, comments=None, extra=None):
    return {
        "time": {"elapsed": elapsed, "extra": extra},
        "team": {"id": team_id, "name": f"Team {team_id}"},
        "player": {"id": player_id, "name": f"Player {player_id}" if player_id else None},
        "assist": {"id": None, "name": None},
        "type": typ,
        "detail": detail,
        "comments": comments,
    }


def test_categorize():
    assert categorize(ev(1, 1, "Goal", "Normal Goal")) == "goal"
    assert categorize(ev(1, 1, "Goal", "Own Goal")) == "own_goal"
    assert categorize(ev(1, 1, "Goal", "Penalty")) == "penalty_goal"
    assert categorize(ev(1, 1, "Goal", "Missed Penalty")) == "missed_penalty"
    assert categorize(ev(1, 1, "Card", "Yellow Card")) == "yellow_card"
    assert categorize(ev(1, 1, "Card", "Second Yellow card")) == "red_card"
    assert categorize(ev(1, 1, "Card", "Red Card")) == "red_card"
    assert categorize(ev(1, 1, "subst", "Substitution 1")) == "substitution"
    assert categorize(ev(1, 1, "Var", "Goal cancelled")) == "var_goal_cancelled"
    assert categorize(ev(1, 1, "Var", "Penalty confirmed")) == "var_other"
    assert categorize(ev(1, 1, "Weird", "???")) is None


def test_goal_detail_correction_keeps_same_key():
    normal = ev(30, 1, "Goal", "Normal Goal", 9)
    penalty = ev(30, 1, "Goal", "Penalty", 9)
    assert event_key(normal, categorize(normal)) == event_key(penalty, categorize(penalty))


def test_diff_new_and_occurrences():
    events = [ev(30, 1, "Goal", "Normal Goal", 9), ev(30, 1, "Goal", "Normal Goal", 10)]
    new, filled, _ = diff_events(events, {})
    assert [r.ident for r in new] == [("1|goal|30", 1), ("1|goal|30", 2)]
    assert filled == []


def test_diff_already_posted_is_skipped():
    events = [ev(30, 1, "Goal", "Normal Goal", 9)]
    new, filled, _ = diff_events(events, {("1|goal|30", 1): 9})
    assert new == [] and filled == []


def test_diff_late_filled_player():
    events = [ev(30, 1, "Goal", "Normal Goal", 9)]
    new, filled, _ = diff_events(events, {("1|goal|30", 1): None})
    assert new == []
    assert [r.ident for r in filled] == [("1|goal|30", 1)]


def test_diff_skips_shootout_and_unknown():
    events = [ev(120, 1, "Goal", "Penalty", 9, comments="Penalty Shootout"), ev(5, 1, "Weird", "x")]
    assert diff_events(events, {}) == ([], [], [])


def test_wanted_flags():
    assert wanted("goal", False, False)
    assert not wanted("yellow_card", False, True)
    assert wanted("yellow_card", True, False)
    assert not wanted("substitution", True, False)
    assert wanted("substitution", False, True)


def test_estimate_requests():
    full = estimate_requests("NS", None)
    assert 65 <= full <= 80
    assert estimate_requests("FT", 90) == 0
    assert estimate_requests("PST", None) == 0
    assert estimate_requests("2H", 80) < estimate_requests("1H", 20) < full


def write_lines(tmp_path, data):
    path = tmp_path / "lines.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return BoberLines(str(path))


def test_lines_skip_player_lines_when_player_unknown(tmp_path):
    lines = write_lines(tmp_path, {"goal": ["{player} SCORED!", "{team} SCORED!"]})
    for _ in range(10):
        assert lines.pick("goal", {"team": "Lyon", "player": None}) == "LYON SCORED!"


def test_lines_fall_back_to_default(tmp_path):
    lines = write_lines(tmp_path, {"default": ["OKAY!"]})
    assert lines.pick("red_card", {}) == "OKAY!"


def test_lines_no_back_to_back_repeat(tmp_path):
    lines = write_lines(tmp_path, {"goal": ["A", "B"]})
    picks = [lines.pick("goal", {}) for _ in range(20)]
    assert all(a != b for a, b in itertools.pairwise(picks))


def test_lines_missing_file():
    lines = BoberLines("definitely_missing.json")
    assert lines.pick("goal", {}) == FALLBACK_LINE


def test_real_lines_file_is_valid():
    lines = BoberLines(LINES_PATH)
    keys = [
        "goal",
        "own_goal",
        "penalty_goal",
        "missed_penalty",
        "yellow_card",
        "red_card",
        "substitution",
        "var_goal_cancelled",
        "var_other",
        "kickoff",
        "halftime",
        "second_half",
        "fulltime",
        "default",
    ]
    ctx = {"team": "T", "opponent": "O", "player": "P", "minute": "1'", "score": "0–0"}
    for key in keys:
        assert len(lines.lines[key]) == 10, key
        for line in lines.lines[key]:
            assert line.format(**ctx)


def test_diff_reports_vanished():
    events = [ev(31, 1, "Goal", "Normal Goal", 9)]
    _, _, vanished = diff_events(events, {("1|goal|30", 1): 9})
    assert vanished == [("1|goal|30", 1)]


def row(key, player=None):
    return {"event_key": key, "occurrence": 1, "player_id": player}


def ref_for(e):
    new, _, _ = diff_events([e], {})
    return new[0]


def test_correction_minute_nudge_same_team():
    orphan = row("1|goal|44")
    assert find_correction(ref_for(ev(45, 1, "Goal", "Normal Goal")), [orphan]) is orphan


def test_correction_team_fix_needs_same_player():
    orphan = row("1|goal|30", player=9)
    assert find_correction(ref_for(ev(30, 2, "Goal", "Normal Goal", 9)), [orphan]) is orphan
    assert find_correction(ref_for(ev(30, 2, "Goal", "Normal Goal", 10)), [orphan]) is None
    assert find_correction(ref_for(ev(30, 2, "Goal", "Normal Goal")), [orphan]) is None


def test_correction_rejects_far_minute_and_other_family():
    assert find_correction(ref_for(ev(40, 1, "Goal", "Normal Goal")), [row("1|goal|30")]) is None
    assert find_correction(ref_for(ev(30, 1, "Card", "Red Card")), [row("1|goal|30")]) is None


def test_correction_picks_closest():
    near, far = row("1|goal|33"), row("1|goal|31")
    assert find_correction(ref_for(ev(34, 1, "Goal", "Normal Goal")), [far, near]) is near


def test_own_goal_convention():
    # Home (1) scores normally; an away player (2) puts one in his own net -> 2-0 home
    og_under_home = [ev(10, 1, "Goal", "Normal Goal"), ev(20, 1, "Goal", "Own Goal")]
    og_under_away = [ev(10, 1, "Goal", "Normal Goal"), ev(20, 2, "Goal", "Own Goal")]
    assert own_goal_convention(og_under_home, 1, 2, 2, 0) is True
    assert own_goal_convention(og_under_away, 1, 2, 2, 0) is False
    # Score not updated yet / no own goals / symmetric -> can't tell
    assert own_goal_convention(og_under_home, 1, 2, 1, 0) is None
    assert own_goal_convention([ev(10, 1, "Goal", "Normal Goal")], 1, 2, 1, 0) is None
    assert (
        own_goal_convention(
            [ev(5, 1, "Goal", "Own Goal"), ev(9, 2, "Goal", "Own Goal")], 1, 2, 1, 1
        )
        is None
    )
