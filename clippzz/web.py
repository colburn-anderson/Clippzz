"""The dashboard: paste a VOD link, watch progress, rate the moments found."""
import re
import time
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from . import config, db, sources

app = FastAPI(title="Clippzz", docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def clock(seconds) -> str:
    s = int(seconds or 0)
    h, m, s = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def ago(ts) -> str:
    d = time.time() - (ts or 0)
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if d >= size:
            return f"{int(d // size)}{unit} ago"
    return "just now"


templates.env.filters["clock"] = clock
templates.env.filters["ago"] = ago
templates.env.filters["fromjson"] = lambda t: db.loads(t, []) or []
templates.env.filters["fromjsonobj"] = lambda t: db.loads(t, {}) or {}


# Creators named by a hashtag in a title, with their YouTube channels (checked to
# exist) and their Instagram handles where known for sure.
CREATORS = {
    "ishowspeed": ("IShowSpeed", "https://www.youtube.com/@IShowSpeed"),
    "kaicenat": ("Kai Cenat", "https://www.youtube.com/@KaiCenat"),
    "fanum": ("Fanum", "https://www.youtube.com/@Fanum"),
    "stableronaldo": ("Stable Ronaldo", "https://www.youtube.com/@StableRonaldo"),
}
INSTAGRAM = {"ishowspeed": "@ishowspeed", "kaicenat": "@kaicenat"}
# Names to keep capitalised when a capitals-only title is turned into a sentence.
NAMES = ["IShowSpeed", "Speed", "Kai Cenat", "Kai", "Fanum", "Stable Ronaldo", "Ronaldo", "Duke Dennis",
         "Serena Williams", "Serena", "Minecraft", "GTA"]


def sentence_case(title: str) -> str:
    """'SPEED STEALS KAI'S PLATE!!! 😭 #ishowspeed' -> "Speed steals Kai's plate! 😭"."""
    text = re.sub(r"\s*#\w+", "", title).strip()
    text = re.sub(r"!{2,}", "!", text)
    if sum(ch.isupper() for ch in text) > 0.6 * max(1, sum(ch.isalpha() for ch in text)):
        text = text.lower()
        for name in NAMES:
            text = re.sub(rf"\b{re.escape(name.lower())}\b", name, text)
        text = text[:1].upper() + text[1:]
    return text


def _handle(url: str | None) -> str | None:
    m = re.search(r"youtube\.com/(@[\w.-]+)", url or "")
    return m.group(1) if m else None


def post_parts(clip, vod, moment_start=None) -> dict:
    """Title, YouTube description and Instagram/Facebook caption for a clip.
    No links: a new YouTube channel cannot use them until it is verified, so
    creators are credited by @handle and the music by title, artist and licence."""
    title = (clip["title"] or "").strip()
    plan = db.loads(clip["plan"] if "plan" in clip.keys() else None, {}) or {}
    tags = [w for w in title.split() if w.startswith("#")]
    for extra in ("#funny", "#streamer"):
        if extra not in tags:
            tags.append(extra)
    credits = []
    source = next((c for c in CREATORS.values() if c[0].lower() == (vod["channel"] or "").lower()), None)
    credits.append(_handle(source[1]) if source else (_handle(vod["channel_url"]) or vod["channel"] or ""))
    for tag in tags:
        known = CREATORS.get(tag.lstrip("#").lower())
        if known and _handle(known[1]) not in credits:
            credits.append(_handle(known[1]))
    lines = []
    if plan.get("premise"):
        lines += [plan["premise"].strip(), ""]
    lines.append("Credit: " + " ".join(c for c in credits if c))
    if clip["music_credit"]:
        music = re.sub(r"\s*\(?https?://\S+\)?", "", clip["music_credit"]).strip().rstrip(",.")
        lines.append(f"Music: {music}")
    lines += ["", " ".join(tags + ["#shorts"])]
    description = "\n".join(lines)
    return {"title": title, "description": description, "caption": instagram_caption(clip, plan, tags, credits)}


def instagram_caption(clip, plan: dict, tags: list[str], credits: list[str]) -> str:
    """Instagram's own format: a short casual hook (only ~125 characters show
    before "more"), 🎥 credits by Instagram handle, 🎵 music, a follow line,
    and at most five hashtags (Instagram's cap since December 2025)."""
    hook = (plan.get("ig_hook") or "").strip() or sentence_case(clip["title"] or "")
    handles = []
    for tag in tags:
        handle = INSTAGRAM.get(tag.lstrip("#").lower())
        if handle and handle not in handles:
            handles.append(handle)
    if not handles:  # creators without a known Instagram handle: credit by name
        handles = [c.lstrip("@") for c in credits if c]
    lines = [hook, "", "\U0001F3A5 " + " ".join(handles)]
    if clip["music_credit"]:
        music = re.sub(r"\s*\(?https?://\S+\)?", "", clip["music_credit"]).strip().rstrip(",.")
        lines.append("\U0001F3B5 " + music)
    lines += ["", f"Follow @{config.CHANNEL_HANDLE} for a new clip every day", "", " ".join(tags[:5])]
    return "\n".join(lines)


def post_text(clip, vod, moment_start=None) -> str:
    parts = post_parts(clip, vod, moment_start)
    return parts["title"] + "\n\n" + parts["description"]


templates.env.globals["post_parts"] = post_parts


STATE_LABELS = {
    "queued": "Waiting", "fetching": "Fetching", "listening": "Listening",
    "scouting": "Scouting", "judging": "Judging", "done": "Done",
    "failed": "Failed", "paused": "Paused",
}
CLIP_LABELS = {"queued": "Waiting", "working": "Making", "done": "Done", "failed": "Failed"}


@app.get("/healthz")
def healthz():
    return {"ok": True}


# The steps a clip goes through, as the clip maker reports them.
CLIP_STEPS = [("Download", "Downloading"), ("Captions", "Writing captions"), ("Frames", "Looking at"),
              ("Plan", "Planning"), ("Render", "Rendering")]


def clip_step(state: str, detail: str | None) -> int:
    """0 while waiting, 1 to 5 for the step under way, 6 when done."""
    if state == "done":
        return len(CLIP_STEPS) + 1
    for i, (_, prefix) in enumerate(CLIP_STEPS, 1):
        if (detail or "").startswith(prefix):
            return i
    return 0


templates.env.globals["CLIP_STEPS"] = CLIP_STEPS
templates.env.globals["clip_step"] = clip_step

CLIP_JOIN = ("SELECT c.*, m.start_s AS m_start, m.end_s AS m_end, m.title AS m_title, m.vod_id AS vod_id"
             " FROM clips c JOIN moments m ON m.id=c.moment_id")


def _home_data(conn) -> dict:
    vods = conn.execute(
        "SELECT v.*, "
        " (SELECT COUNT(*) FROM moments m WHERE m.vod_id=v.id AND m.judged=1 AND m.verdict!='skip') AS n_good,"
        " (SELECT COUNT(*) FROM moments m WHERE m.vod_id=v.id) AS n_found,"
        " (SELECT COALESCE(SUM(cost_usd),0) FROM usage u WHERE u.vod_id=v.id) AS cost"
        " FROM vods v ORDER BY v.created_at DESC").fetchall()
    # Top contenders: judged worth a look, not skipped, no clip made or being made yet.
    review = conn.execute(
        "SELECT m.*, v.title AS vod_title, v.channel AS vod_channel FROM moments m JOIN vods v ON v.id=m.vod_id"
        " WHERE m.judged=1 AND m.verdict IN ('post','maybe') AND (m.rating IS NULL OR m.rating='good')"
        " AND NOT EXISTS (SELECT 1 FROM clips c WHERE c.moment_id=m.id)"
        " ORDER BY (m.flags IS NOT NULL AND m.flags NOT IN ('[]','')) ,"
        " (m.rating='good') DESC, m.judge_score DESC LIMIT 20").fetchall()
    working = conn.execute(CLIP_JOIN + " WHERE c.state IN ('queued','working')"
                           " ORDER BY c.state='working' DESC, c.created_at").fetchall()
    failed = conn.execute(CLIP_JOIN + " WHERE c.state='failed' ORDER BY c.updated_at DESC LIMIT 5").fetchall()
    # One clip per moment: the newest finished version, unless that moment is already posted.
    ready = conn.execute(CLIP_JOIN + " WHERE c.state='done' AND c.posted_at IS NULL"
                         " AND (c.rating IS NULL OR c.rating!='bad')"
                         " AND c.id=(SELECT MAX(c2.id) FROM clips c2 WHERE c2.moment_id=c.moment_id AND c2.state='done')"
                         " AND NOT EXISTS (SELECT 1 FROM clips c3 WHERE c3.moment_id=c.moment_id AND c3.posted_at IS NOT NULL)"
                         " ORDER BY c.updated_at DESC LIMIT 12").fetchall()
    posted = conn.execute(CLIP_JOIN + " WHERE c.state='done' AND c.posted_at IS NOT NULL"
                          " ORDER BY c.posted_at DESC LIMIT 20").fetchall()
    rated = conn.execute(
        "SELECT rating FROM clips WHERE rating IS NOT NULL ORDER BY rated_at DESC LIMIT 10").fetchall()
    return {
        "vods": vods, "vods_by_id": {v["id"]: v for v in vods}, "review": review, "working": working,
        "failed": failed, "ready": ready, "posted": posted,
        "scanning": [v for v in vods if v["state"] in db.ACTIVE_STATES],
        "spent": db.month_spend(conn),
        "budget": float(db.get_setting(conn, "monthly_budget_usd", config.MONTHLY_BUDGET_USD)),
        "last10_good": sum(1 for r in rated if r["rating"] == "good"), "last10": len(rated),
    }


@app.get("/", response_class=HTMLResponse)
def home(request: Request, error: str = ""):
    with db.session() as conn:
        data = _home_data(conn)
    return templates.TemplateResponse(request, "home.html", {
        **data, "labels": STATE_LABELS, "clip_labels": CLIP_LABELS, "error": error, "busy": False,
        "live": bool(data["working"] or data["scanning"]),
    })


@app.get("/status.json")
def status():
    """What is under way, for the live progress on the home page."""
    with db.session() as conn:
        vods = conn.execute("SELECT id, state, detail FROM vods WHERE state IN (%s)"
                            % ",".join("?" * len(db.ACTIVE_STATES)), db.ACTIVE_STATES).fetchall()
        clips = conn.execute("SELECT id, state, detail, created_at, updated_at FROM clips"
                             " WHERE state IN ('queued','working')"
                             " OR updated_at > ?", (time.time() - 120,)).fetchall()
    return JSONResponse({
        "vods": [{"id": v["id"], "state": v["state"], "detail": v["detail"]} for v in vods],
        "clips": [{"id": c["id"], "state": c["state"], "detail": c["detail"],
                   "step": clip_step(c["state"], c["detail"]), "since": c["updated_at"]} for c in clips],
        "now": time.time(),
    })


@app.post("/moments/{mid}/approve")
def approve(mid: int, note: str = Form("")):
    """The owner watched the moment and wants it made: mark it funny and queue the edit."""
    with db.session() as conn:
        row = conn.execute("SELECT id, note FROM moments WHERE id=?", (mid,)).fetchone()
        if not row:
            raise HTTPException(404, "No such moment")
        conn.execute("UPDATE moments SET rating='good', note=?, rated_at=? WHERE id=?",
                     (note.strip()[:4000] or row["note"], time.time(), mid))
        busy = conn.execute("SELECT 1 FROM clips WHERE moment_id=? AND state IN ('queued','working')",
                            (mid,)).fetchone()
        if not busy:
            db.add_clip(conn, mid)
    return RedirectResponse("/#progress", status_code=303)


@app.post("/moments/{mid}/skip")
def skip(mid: int):
    with db.session() as conn:
        conn.execute("UPDATE moments SET rating='bad', rated_at=? WHERE id=?", (time.time(), mid))
    return RedirectResponse("/#review", status_code=303)


@app.post("/clips/{cid}/retry")
def retry_clip(cid: int):
    with db.session() as conn:
        row = conn.execute("SELECT plan FROM clips WHERE id=? AND state='failed'", (cid,)).fetchone()
        if row and row["plan"]:
            db.queue_rerender(conn, cid)       # the plan was paid for: render it again for free
        elif row:
            db.set_clip(conn, cid, "queued", "Waiting to retry", attempts=0)
    return RedirectResponse("/#progress", status_code=303)


@app.post("/clips/{cid}/posted")
def mark_posted(cid: int, undo: str = Form("")):
    with db.session() as conn:
        conn.execute("UPDATE clips SET posted_at=? WHERE id=?", (None if undo else time.time(), cid))
    return RedirectResponse("/#ready", status_code=303)


@app.post("/vods")
def add_vod(url: str = Form(...)):
    vid = sources.video_id(url)
    if not vid:
        return RedirectResponse("/?error=That+does+not+look+like+a+YouTube+link.", status_code=303)
    with db.session() as conn:
        db.add_vod(conn, vid, f"https://www.youtube.com/watch?v={vid}")
    return RedirectResponse(f"/vod/{vid}", status_code=303)


@app.get("/vod/{vid}", response_class=HTMLResponse)
def vod_page(request: Request, vid: str):
    with db.session() as conn:
        vod = conn.execute("SELECT * FROM vods WHERE id=?", (vid,)).fetchone()
        if not vod:
            raise HTTPException(404, "No such VOD")
        moments = conn.execute(
            "SELECT * FROM moments WHERE vod_id=? ORDER BY judged DESC,"
            " CASE verdict WHEN 'post' THEN 0 WHEN 'maybe' THEN 1 ELSE 2 END,"
            " judge_score DESC, scout_score DESC", (vid,)).fetchall()
        cost = db.vod_spend(conn, vid)
        clips = {}
        for c in _vod_clips(conn, vid):
            clips.setdefault(c["moment_id"], []).append(c)
    return templates.TemplateResponse(request, "vod.html", {
        "vod": vod, "moments": moments, "cost": cost, "labels": STATE_LABELS,
        "signals": db.loads(vod["signals"], {}) or {},
        "busy": vod["state"] in db.ACTIVE_STATES,
        "clips": clips, "clip_labels": CLIP_LABELS,
        "clip_busy": any(c["state"] in db.CLIP_ACTIVE_STATES for cs in clips.values() for c in cs),
    })


def _vod_clips(conn, vid):
    return conn.execute(
        "SELECT c.* FROM clips c JOIN moments m ON m.id=c.moment_id WHERE m.vod_id=?"
        " ORDER BY c.id DESC", (vid,)).fetchall()


@app.get("/vod/{vid}/clips.json")
def vod_clips(vid: str):
    with db.session() as conn:
        rows = _vod_clips(conn, vid)
    return JSONResponse([{"id": c["id"], "state": c["state"], "detail": c["detail"]} for c in rows])


@app.post("/vod/{vid}/retry")
def retry(vid: str):
    with db.session() as conn:
        conn.execute("UPDATE vods SET state='queued', detail='Waiting to retry', attempts=0,"
                     " updated_at=? WHERE id=? AND state IN ('failed','paused')", (time.time(), vid))
    return RedirectResponse(f"/vod/{vid}", status_code=303)


@app.post("/moments/{mid}/rate")
def rate(mid: int, rating: str = Form(...), note: str = Form("")):
    if rating not in ("good", "bad", "clear"):
        raise HTTPException(400, "Unknown rating")
    with db.session() as conn:
        row = conn.execute("SELECT vod_id FROM moments WHERE id=?", (mid,)).fetchone()
        if not row:
            raise HTTPException(404, "No such moment")
        conn.execute("UPDATE moments SET rating=?, note=?, rated_at=? WHERE id=?",
                     (None if rating == "clear" else rating, note.strip()[:4000] or None, time.time(), mid))
    return RedirectResponse(f"/vod/{row['vod_id']}#m{mid}", status_code=303)


@app.post("/moments/{mid}/clip")
def make_clip(mid: int):
    with db.session() as conn:
        row = conn.execute("SELECT vod_id FROM moments WHERE id=?", (mid,)).fetchone()
        if not row:
            raise HTTPException(404, "No such moment")
        busy = conn.execute("SELECT 1 FROM clips WHERE moment_id=? AND state IN ('queued','working')",
                            (mid,)).fetchone()
        if not busy:
            db.add_clip(conn, mid)
    return RedirectResponse(f"/vod/{row['vod_id']}#m{mid}", status_code=303)


@app.get("/clips/{cid}.mp4")
def clip_file(cid: int):
    with db.session() as conn:
        row = conn.execute("SELECT path FROM clips WHERE id=? AND state='done'", (cid,)).fetchone()
    if not row or not row["path"]:
        raise HTTPException(404, "No such clip")
    path = Path(row["path"])
    if not path.is_file() or config.CLIPS_DIR.resolve() not in path.resolve().parents:
        raise HTTPException(404, "Clip file missing")
    return FileResponse(path, media_type="video/mp4", filename=f"clippzz-{cid}.mp4",
                        content_disposition_type="inline")


@app.post("/clips/{cid}/rate")
def rate_clip(cid: int, rating: str = Form(...), note: str = Form("")):
    if rating not in ("good", "bad", "clear"):
        raise HTTPException(400, "Unknown rating")
    with db.session() as conn:
        row = conn.execute("SELECT m.vod_id, c.moment_id FROM clips c JOIN moments m ON m.id=c.moment_id"
                           " WHERE c.id=?", (cid,)).fetchone()
        if not row:
            raise HTTPException(404, "No such clip")
        conn.execute("UPDATE clips SET rating=?, note=?, rated_at=? WHERE id=?",
                     (None if rating == "clear" else rating, note.strip()[:4000] or None, time.time(), cid))
    return RedirectResponse(f"/vod/{row['vod_id']}#c{cid}", status_code=303)


# A reload of a rating or clip form (for example after the Pi restarted mid-submit)
# arrives as GET. Send it back to the page instead of showing an error.
@app.get("/moments/{mid}/rate")
@app.get("/moments/{mid}/clip")
def moment_form_get(mid: int):
    with db.session() as conn:
        row = conn.execute("SELECT vod_id FROM moments WHERE id=?", (mid,)).fetchone()
    return RedirectResponse(f"/vod/{row['vod_id']}#m{mid}" if row else "/", status_code=303)


@app.get("/clips/{cid}/rate")
def clip_rate_get(cid: int):
    with db.session() as conn:
        row = conn.execute("SELECT m.vod_id FROM clips c JOIN moments m ON m.id=c.moment_id"
                           " WHERE c.id=?", (cid,)).fetchone()
    return RedirectResponse(f"/vod/{row['vod_id']}#c{cid}" if row else "/", status_code=303)
