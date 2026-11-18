"""SQLite storage. Every VOD and moment is a row with a state, so a power cut
loses nothing: the worker picks up where the rows say it stopped."""
import json
import sqlite3
import time
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS vods (
    id TEXT PRIMARY KEY,            -- YouTube video id
    url TEXT NOT NULL,
    title TEXT, channel TEXT, duration_s INTEGER,
    state TEXT NOT NULL DEFAULT 'queued',
    detail TEXT,                    -- human-readable progress or error
    attempts INTEGER NOT NULL DEFAULT 0,
    signals TEXT,                   -- JSON: which signals were available
    scouted INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS moments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vod_id TEXT NOT NULL REFERENCES vods(id),
    start_s REAL NOT NULL, end_s REAL NOT NULL,
    scout_score INTEGER, scout_kind TEXT, scout_summary TEXT, scout_why TEXT,
    judged INTEGER NOT NULL DEFAULT 0,
    judge_score INTEGER, verdict TEXT, joke TEXT, title TEXT, mood TEXT,
    visual TEXT, edit_ideas TEXT, flags TEXT,
    rating TEXT, note TEXT, rated_at REAL
);
CREATE INDEX IF NOT EXISTS moments_vod ON moments(vod_id);
CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vod_id TEXT, step TEXT, model TEXT,
    input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL, at REAL
);
CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    moment_id INTEGER NOT NULL REFERENCES moments(id),
    state TEXT NOT NULL DEFAULT 'queued',   -- queued, working, done, failed
    detail TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    section_start_s REAL,           -- where the downloaded section starts in the VOD
    plan TEXT,                      -- JSON edit plan (times are section times)
    words TEXT,                     -- JSON speech-to-text words, kept for re-renders
    title TEXT, duration_s REAL, cost_usd REAL, path TEXT,
    rating TEXT, note TEXT, rated_at REAL,
    rerender INTEGER NOT NULL DEFAULT 0,   -- 1: render again from the saved plan
    created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS clips_moment ON clips(moment_id);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""

# States a VOD moves through, in order.
ACTIVE_STATES = ("queued", "fetching", "listening", "scouting", "judging")
CLIP_ACTIVE_STATES = ("queued", "working")


def connect() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    # WAL plus NORMAL sync keeps writes few and small (kinder to an SD card)
    # while staying safe against a crash.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    # Columns added after the first release: add them to older databases.
    for table, column in (("clips", "rerender INTEGER NOT NULL DEFAULT 0"),
                          ("clips", "music_credit TEXT"), ("vods", "channel_url TEXT")):
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column}")
        except sqlite3.OperationalError:
            pass
    return conn


@contextmanager
def session():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def add_vod(conn, video_id: str, url: str) -> bool:
    now = time.time()
    cur = conn.execute(
        "INSERT OR IGNORE INTO vods (id, url, created_at, updated_at) VALUES (?,?,?,?)",
        (video_id, url, now, now),
    )
    return cur.rowcount == 1


def set_state(conn, video_id: str, state: str, detail: str | None = None, **fields):
    cols = {"state": state, "detail": detail, "updated_at": time.time(), **fields}
    sets = ", ".join(f"{k}=?" for k in cols)
    conn.execute(f"UPDATE vods SET {sets} WHERE id=?", (*cols.values(), video_id))
    conn.commit()


def next_vod(conn):
    marks = ",".join("?" * len(ACTIVE_STATES))
    return conn.execute(
        f"SELECT * FROM vods WHERE state IN ({marks}) ORDER BY created_at LIMIT 1",
        ACTIVE_STATES,
    ).fetchone()


def add_clip(conn, moment_id: int) -> int:
    now = time.time()
    cur = conn.execute(
        "INSERT INTO clips (moment_id, created_at, updated_at) VALUES (?,?,?)",
        (moment_id, now, now))
    conn.commit()
    return cur.lastrowid


def set_clip(conn, clip_id: int, state: str, detail: str | None = None, **fields):
    cols = {"state": state, "detail": detail, "updated_at": time.time(), **fields}
    sets = ", ".join(f"{k}=?" for k in cols)
    conn.execute(f"UPDATE clips SET {sets} WHERE id=?", (*cols.values(), clip_id))
    conn.commit()


def next_clip(conn):
    """Only queued clips. A clip someone is making by hand (python -m
    clippzz.clipper) is "working" and must be left alone."""
    return conn.execute(
        "SELECT * FROM clips WHERE state='queued' ORDER BY created_at LIMIT 1").fetchone()


def set_setting(conn, key: str, value):
    """Facts the worker publishes for the dashboard, such as the spending cap
    it enforces (the dashboard never reads the worker's private settings file)."""
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)"
                 " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    conn.commit()


def get_setting(conn, key: str, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def queue_rerender(conn, clip_id: int):
    """Ask the worker to render a clip again from its saved plan (no API cost).
    Going through the worker means only one process ever touches a clip."""
    conn.execute("UPDATE clips SET state='queued', rerender=1, attempts=0,"
                 " detail='Waiting to render again', updated_at=? WHERE id=? AND plan IS NOT NULL",
                 (time.time(), clip_id))
    conn.commit()


def requeue_interrupted_clips(conn) -> int:
    """At worker start: clips left "working" by a crash or power cut go back in the queue."""
    cur = conn.execute("UPDATE clips SET state='queued', detail='Resuming after a restart',"
                       " updated_at=? WHERE state='working'", (time.time(),))
    conn.commit()
    return cur.rowcount


def record_usage(conn, vod_id, step, model, input_tokens, output_tokens):
    price_in, price_out = config.PRICES.get(model, (0.0, 0.0))
    cost = (input_tokens * price_in + output_tokens * price_out) / 1_000_000
    conn.execute(
        "INSERT INTO usage (vod_id, step, model, input_tokens, output_tokens, cost_usd, at)"
        " VALUES (?,?,?,?,?,?,?)",
        (vod_id, step, model, input_tokens, output_tokens, cost, time.time()),
    )
    conn.commit()
    return cost


class BudgetReached(RuntimeError):
    pass


def check_budget(conn):
    spent = month_spend(conn)
    if spent >= config.MONTHLY_BUDGET_USD:
        raise BudgetReached(
            f"Monthly budget reached (${spent:.2f} of ${config.MONTHLY_BUDGET_USD:.2f}). "
            "Raise CLIPPZZ_MONTHLY_BUDGET_USD or wait for next month, then press Retry.")


def month_spend(conn) -> float:
    start = time.mktime(time.strptime(time.strftime("%Y-%m-01"), "%Y-%m-%d"))
    row = conn.execute("SELECT COALESCE(SUM(cost_usd),0) FROM usage WHERE at>=?", (start,)).fetchone()
    return float(row[0])


def vod_spend(conn, vod_id) -> float:
    row = conn.execute("SELECT COALESCE(SUM(cost_usd),0) FROM usage WHERE vod_id=?", (vod_id,)).fetchone()
    return float(row[0])


def loads(text, default=None):
    try:
        return json.loads(text) if text else default
    except ValueError:
        return default
