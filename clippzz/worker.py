"""The worker: takes queued VODs through fetch -> listen -> scout -> judge,
and makes the clips queued from the dashboard.

Each step saves its result before the next starts, so the worker can be
killed at any point (power cut, redeploy) and carry on from the same place.
"""
import json
import logging
import shutil
import time

import anthropic

from . import clipper, config, db, ranker, sources
from .db import BudgetReached, check_budget

log = logging.getLogger("clippzz.worker")


def step_fetch(conn, vod):
    vid = vod["id"]
    db.set_state(conn, vid, "fetching", "Reading video details and captions")
    info = sources.fetch_info(vid)
    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
        raise sources.SourceError("this stream has not finished yet")
    db.set_state(conn, vid, "fetching", "Reading captions",
                 title=info.get("title"), channel=info.get("channel"),
                 duration_s=int(info.get("duration") or 0))
    transcript = sources.fetch_transcript(vid)
    return info, transcript


def step_listen(conn, vod, signals):
    vid = vod["id"]
    db.set_state(conn, vid, "listening", "Scanning the audio for loud moments")
    try:
        levels = sources.loudness(vid)
        signals["loudness"] = True
        return levels
    except sources.SourceError as e:
        # Loudness is a helpful hint, not a requirement.
        log.warning("loudness scan failed for %s: %s", vid, e)
        signals["loudness"] = False
        return []


def step_scout(conn, vod, info, transcript, levels):
    vid = vod["id"]
    meta = {"channel": info.get("channel"), "title": info.get("title"),
            "duration_s": int(info.get("duration") or 0)}
    heat = sources.heatmap(info)
    threshold = ranker.loud_threshold(levels)
    chunks = list(ranker.chunk_lines(transcript["lines"], meta["duration_s"] or transcript["lines"][-1][0] + 10))
    found = []
    for i, (start, end, lines) in enumerate(chunks, 1):
        check_budget(conn)
        db.set_state(conn, vid, "scouting", f"Reading the transcript: part {i} of {len(chunks)}")
        hint = ""
        peak = max((v for s, e, v in heat if s < end and e > start), default=0)
        if peak >= 0.6:
            hint = " Viewers replayed part of this stretch much more than the rest of the video."
        text = ranker.format_lines(lines, levels, threshold)
        moments, tin, tout = ranker.scout_chunk(meta, start, end, text, hint)
        db.record_usage(conn, vid, "scout", config.SCOUT_MODEL, tin, tout)
        found.extend(moments)
    kept = ranker.dedupe([m for m in found if m["score"] >= 40])
    conn.execute("DELETE FROM moments WHERE vod_id=?", (vid,))
    conn.executemany(
        "INSERT INTO moments (vod_id, start_s, end_s, scout_score, scout_kind, scout_summary, scout_why)"
        " VALUES (?,?,?,?,?,?,?)",
        [(vid, m["start_s"], m["end_s"], m["score"], m["kind"], m["summary"], m["why_funny"]) for m in kept])
    conn.commit()
    return len(kept)


def step_judge(conn, vod, info, transcript, levels):
    vid = vod["id"]
    meta = {"channel": info.get("channel"), "title": info.get("title")}
    threshold = ranker.loud_threshold(levels)
    rows = conn.execute(
        "SELECT * FROM moments WHERE vod_id=? ORDER BY scout_score DESC, id LIMIT ?",
        (vid, config.MAX_JUDGED)).fetchall()
    todo = [r for r in rows if not r["judged"]]
    for n, row in enumerate(todo, 1):
        check_budget(conn)
        db.set_state(conn, vid, "judging",
                     f"Watching moment {len(rows) - len(todo) + n} of {len(rows)}")
        m = dict(row)
        try:
            frames = sources.frames(vid, max(0, m["start_s"] - 3), m["end_s"] + 3)
        except sources.SourceError as e:
            log.warning("no frames for moment %s: %s", m["id"], e)
            frames = []
        lines = [l for l in transcript["lines"] if m["start_s"] - 60 <= l[0] <= m["end_s"] + 30]
        text = ranker.format_lines(lines, levels, threshold, precise=True)
        j, tin, tout = ranker.judge_moment(meta, m, text, frames)
        db.record_usage(conn, vid, "judge", config.JUDGE_MODEL, tin, tout)
        conn.execute(
            "UPDATE moments SET judged=1, judge_score=?, verdict=?, joke=?, title=?, mood=?,"
            " visual=?, edit_ideas=?, flags=?, start_s=?, end_s=? WHERE id=?",
            (j["score"], j["verdict"], j["joke"], j["title"], j["mood"], j["visual"],
             json.dumps(j["edit_ideas"]), json.dumps(j["flags"]), j["start_s"], j["end_s"], m["id"]))
        conn.commit()


def process(conn, vod):
    vid = vod["id"]
    signals = db.loads(vod["signals"], {}) or {}
    info, transcript = step_fetch(conn, vod)
    signals["captions"] = True
    signals["heatmap"] = bool(sources.heatmap(info))
    levels = step_listen(conn, vod, signals)
    db.set_state(conn, vid, "scouting", "Reading the transcript", signals=json.dumps(signals))

    have = conn.execute("SELECT COUNT(*) FROM moments WHERE vod_id=?", (vid,)).fetchone()[0]
    if not vod["scouted"]:
        have = step_scout(conn, vod, info, transcript, levels)
        conn.execute("UPDATE vods SET scouted=1 WHERE id=?", (vid,))
        conn.commit()

    if have:
        step_judge(conn, vod, info, transcript, levels)
    good = conn.execute("SELECT COUNT(*) FROM moments WHERE vod_id=? AND verdict='post'", (vid,)).fetchone()[0]
    db.set_state(conn, vid, "done",
                 f"{good} worth posting out of {have} found" if have else "Nothing funny enough found")
    shutil.rmtree(config.WORK_DIR / vid, ignore_errors=True)


def run_clip(conn, clip):
    cid = clip["id"]
    log.info("making clip %s (attempt %s)", cid, clip["attempts"] + 1)
    try:
        clipper.make(conn, cid)
    except BudgetReached as e:
        db.set_clip(conn, cid, "failed", str(e))
    except anthropic.AuthenticationError:
        db.set_clip(conn, cid, "failed", "The Anthropic API key was rejected. Check ~/.clippzz.env on the Pi.")
    except Exception as e:  # noqa: BLE001 - recorded on the row and retried
        attempts = clip["attempts"] + 1
        log.exception("clip %s failed (attempt %s)", cid, attempts)
        msg = f"{type(e).__name__}: {e}"[:500]
        if attempts >= config.MAX_ATTEMPTS:
            db.set_clip(conn, cid, "failed", msg, attempts=attempts)
        else:
            db.set_clip(conn, cid, "queued", f"Retrying after error: {msg}", attempts=attempts)
            time.sleep(30 * attempts)


def run_once(conn) -> bool:
    vod = db.next_vod(conn)
    if not vod:
        clip = db.next_clip(conn)
        if not clip:
            return False
        run_clip(conn, clip)
        return True
    vid = vod["id"]
    log.info("processing %s (state %s, attempt %s)", vid, vod["state"], vod["attempts"] + 1)
    try:
        process(conn, vod)
        log.info("done %s", vid)
    except BudgetReached as e:
        db.set_state(conn, vid, "paused", str(e))
    except anthropic.AuthenticationError:
        db.set_state(conn, vid, "paused",
                     "The Anthropic API key was rejected. Check ~/.clippzz.env on the Pi, then press Retry.")
    except Exception as e:  # noqa: BLE001 - any failure is recorded on the row and retried
        attempts = vod["attempts"] + 1
        log.exception("failed %s (attempt %s)", vid, attempts)
        msg = f"{type(e).__name__}: {e}"[:500]
        if attempts >= config.MAX_ATTEMPTS:
            db.set_state(conn, vid, "failed", msg, attempts=attempts)
        else:
            db.set_state(conn, vid, "queued", f"Retrying after error: {msg}", attempts=attempts)
            time.sleep(30 * attempts)
    return True


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    config.WORK_DIR.mkdir(parents=True, exist_ok=True)
    log.info("worker started; scout=%s judge=%s budget=$%.2f/month",
             config.SCOUT_MODEL, config.JUDGE_MODEL, config.MONTHLY_BUDGET_USD)
    conn = db.connect()
    resumed = db.requeue_interrupted_clips(conn)
    if resumed:
        log.info("requeued %s clip(s) interrupted by a restart", resumed)
    while True:
        if not run_once(conn):
            time.sleep(5)


if __name__ == "__main__":
    main()
