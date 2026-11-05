"""
SQLite persistence: fixture cache, the tracked match, posted events and the daily request budget.
Queries are tiny, so they run directly on the event loop.
"""

import json
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS fixture_days (
    utc_date   TEXT PRIMARY KEY,
    fetched_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS fixtures (
    utc_date   TEXT NOT NULL,
    fixture_id INTEGER NOT NULL,
    data       TEXT NOT NULL,
    PRIMARY KEY (utc_date, fixture_id)
);
CREATE TABLE IF NOT EXISTS tracked (
    fixture_id    INTEGER PRIMARY KEY,
    active        INTEGER NOT NULL DEFAULT 1,
    channel_id    INTEGER NOT NULL,
    user_id       INTEGER NOT NULL,
    yellows       INTEGER NOT NULL,
    subs          INTEGER NOT NULL,
    ping_role_id  INTEGER,
    home_name     TEXT NOT NULL,
    away_name     TEXT NOT NULL,
    league_name   TEXT NOT NULL,
    kickoff_ts    INTEGER NOT NULL,
    started_at    INTEGER NOT NULL,
    last_status   TEXT,
    last_elapsed  INTEGER,
    home_goals    INTEGER,
    away_goals    INTEGER,
    last_poll_at  INTEGER,
    requests_used INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS posted_events (
    fixture_id  INTEGER NOT NULL,
    event_key   TEXT NOT NULL,
    occurrence  INTEGER NOT NULL,
    category    TEXT NOT NULL,
    team_id     INTEGER,
    minute      TEXT,
    player_id   INTEGER,
    player_name TEXT,
    message_id  INTEGER,
    cancelled   INTEGER NOT NULL DEFAULT 0,
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    UNIQUE (fixture_id, event_key, occurrence)
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS budget (
    utc_date         TEXT PRIMARY KEY,
    used             INTEGER NOT NULL DEFAULT 0,
    header_remaining INTEGER
);
"""

TRACKED_SETTABLE = {
    "last_status",
    "last_elapsed",
    "home_goals",
    "away_goals",
    "last_poll_at",
}


class Storage:
    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    # --- fixture cache ---

    def has_fixture_day(self, utc_date):
        row = self.conn.execute(
            "SELECT 1 FROM fixture_days WHERE utc_date = ?", (utc_date,)
        ).fetchone()
        return row is not None

    def save_fixture_day(self, utc_date, fixtures, fetched_at):
        with self.conn:
            self.conn.execute("DELETE FROM fixtures WHERE utc_date = ?", (utc_date,))
            self.conn.executemany(
                "INSERT INTO fixtures (utc_date, fixture_id, data) VALUES (?, ?, ?)",
                [(utc_date, f["fixture"]["id"], json.dumps(f)) for f in fixtures],
            )
            self.conn.execute(
                "INSERT OR REPLACE INTO fixture_days (utc_date, fetched_at) VALUES (?, ?)",
                (utc_date, fetched_at),
            )

    def fixtures_for_day(self, utc_date):
        rows = self.conn.execute(
            "SELECT data FROM fixtures WHERE utc_date = ?", (utc_date,)
        ).fetchall()
        return [json.loads(r["data"]) for r in rows]

    # --- tracked match ---

    def active_match(self):
        return self.conn.execute("SELECT * FROM tracked WHERE active = 1").fetchone()

    def start_tracking(self, fixture, channel_id, user_id, yellows, subs, ping_role_id, now):
        with self.conn:
            # A fixture tracked earlier (then untracked) starts fresh
            self.conn.execute(
                "DELETE FROM tracked WHERE fixture_id = ?", (fixture["fixture"]["id"],)
            )
            self.conn.execute(
                "DELETE FROM posted_events WHERE fixture_id = ?", (fixture["fixture"]["id"],)
            )
            self.conn.execute(
                """INSERT INTO tracked (fixture_id, channel_id, user_id, yellows, subs, ping_role_id,
                                        home_name, away_name, league_name, kickoff_ts, started_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    fixture["fixture"]["id"],
                    channel_id,
                    user_id,
                    int(yellows),
                    int(subs),
                    ping_role_id,
                    fixture["teams"]["home"]["name"],
                    fixture["teams"]["away"]["name"],
                    fixture["league"]["name"],
                    fixture["fixture"]["timestamp"],
                    now,
                ),
            )

    def update_tracked(self, fixture_id, **fields):
        bad = set(fields) - TRACKED_SETTABLE
        if bad:
            raise ValueError(f"Not settable: {bad}")
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self.conn:
            self.conn.execute(
                f"UPDATE tracked SET {cols} WHERE fixture_id = ?", (*fields.values(), fixture_id)
            )

    def count_match_request(self, fixture_id):
        with self.conn:
            self.conn.execute(
                "UPDATE tracked SET requests_used = requests_used + 1 WHERE fixture_id = ?",
                (fixture_id,),
            )

    def stop_tracking(self, fixture_id):
        with self.conn:
            self.conn.execute("UPDATE tracked SET active = 0 WHERE fixture_id = ?", (fixture_id,))

    # --- posted events ---

    def posted_rows(self, fixture_id):
        return self.conn.execute(
            "SELECT * FROM posted_events WHERE fixture_id = ? ORDER BY seq", (fixture_id,)
        ).fetchall()

    def record_event(self, fixture_id, ref, team_id, minute, player_id, player_name, message_id):
        with self.conn:
            self.conn.execute(
                """INSERT OR IGNORE INTO posted_events
                   (fixture_id, event_key, occurrence, category, team_id, minute, player_id, player_name, message_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    fixture_id,
                    ref.key,
                    ref.occurrence,
                    ref.category,
                    team_id,
                    minute,
                    player_id,
                    player_name,
                    message_id,
                ),
            )

    def posted_event(self, fixture_id, ident):
        return self.conn.execute(
            "SELECT * FROM posted_events WHERE fixture_id = ? AND event_key = ? AND occurrence = ?",
            (fixture_id, *ident),
        ).fetchone()

    def fill_player(self, fixture_id, ident, player_id, player_name):
        with self.conn:
            self.conn.execute(
                """UPDATE posted_events SET player_id = ?, player_name = ?
                   WHERE fixture_id = ? AND event_key = ? AND occurrence = ?""",
                (player_id, player_name, fixture_id, *ident),
            )

    def rekey_event(self, seq, ref, team_id, minute, player_id, player_name):
        """Point a posted event at the API's corrected version of it."""
        with self.conn:
            self.conn.execute(
                """UPDATE posted_events SET event_key = ?, occurrence = ?, category = ?, team_id = ?, minute = ?,
                                            player_id = COALESCE(?, player_id),
                                            player_name = COALESCE(?, player_name)
                   WHERE seq = ?""",
                (
                    ref.key,
                    ref.occurrence,
                    ref.category,
                    team_id,
                    minute,
                    player_id,
                    player_name,
                    seq,
                ),
            )

    def latest_live_goal(self, fixture_id, team_id=None):
        """Most recent non-cancelled goal (optionally for one team), for VAR cancellations."""
        sql = """SELECT * FROM posted_events
                 WHERE fixture_id = ? AND category IN ('goal', 'own_goal', 'penalty_goal') AND cancelled = 0"""
        params = [fixture_id]
        if team_id is not None:
            sql += " AND team_id = ?"
            params.append(team_id)
        return self.conn.execute(sql + " ORDER BY seq DESC LIMIT 1", params).fetchone()

    def cancel_event(self, seq):
        with self.conn:
            self.conn.execute("UPDATE posted_events SET cancelled = 1 WHERE seq = ?", (seq,))

    def events_of(self, fixture_id, categories):
        marks = ", ".join("?" for _ in categories)
        return self.conn.execute(
            f"""SELECT * FROM posted_events WHERE fixture_id = ? AND category IN ({marks}) AND cancelled = 0
                ORDER BY seq""",
            (fixture_id, *categories),
        ).fetchall()

    # --- facts learned about the API ---

    def get_meta(self, key):
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key, value):
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value)
            )

    # --- request budget ---

    def count_request(self, utc_date, header_remaining):
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO budget (utc_date) VALUES (?)", (utc_date,))
            self.conn.execute("UPDATE budget SET used = used + 1 WHERE utc_date = ?", (utc_date,))
            if header_remaining is not None:
                self.conn.execute(
                    "UPDATE budget SET header_remaining = ? WHERE utc_date = ?",
                    (header_remaining, utc_date),
                )

    def mark_budget_exhausted(self, utc_date):
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO budget (utc_date) VALUES (?)", (utc_date,))
            self.conn.execute(
                "UPDATE budget SET header_remaining = 0 WHERE utc_date = ?", (utc_date,)
            )

    def budget_row(self, utc_date):
        return self.conn.execute("SELECT * FROM budget WHERE utc_date = ?", (utc_date,)).fetchone()
