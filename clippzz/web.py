"""The dashboard: paste a VOD link, watch progress, rate the moments found."""
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

STATE_LABELS = {
    "queued": "Waiting", "fetching": "Fetching", "listening": "Listening",
    "scouting": "Scouting", "judging": "Judging", "done": "Done",
    "failed": "Failed", "paused": "Paused",
}
CLIP_LABELS = {"queued": "Waiting", "working": "Making", "done": "Done", "failed": "Failed"}


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def home(request: Request, error: str = ""):
    with db.session() as conn:
        vods = conn.execute(
            "SELECT v.*, "
            " (SELECT COUNT(*) FROM moments m WHERE m.vod_id=v.id AND m.verdict='post') AS n_post,"
            " (SELECT COUNT(*) FROM moments m WHERE m.vod_id=v.id) AS n_found,"
            " (SELECT COALESCE(SUM(cost_usd),0) FROM usage u WHERE u.vod_id=v.id) AS cost"
            " FROM vods v ORDER BY v.created_at DESC").fetchall()
        spent = db.month_spend(conn)
        # The week 1 gate counts finished clips, not moments.
        rated = conn.execute(
            "SELECT rating FROM clips WHERE rating IS NOT NULL ORDER BY rated_at DESC LIMIT 10").fetchall()
    return templates.TemplateResponse(request, "home.html", {
        "vods": vods, "spent": spent, "budget": config.MONTHLY_BUDGET_USD,
        "labels": STATE_LABELS, "error": error,
        "last10_good": sum(1 for r in rated if r["rating"] == "good"), "last10": len(rated),
        "busy": any(v["state"] in db.ACTIVE_STATES for v in vods),
    })


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
                     (None if rating == "clear" else rating, note.strip()[:500] or None, time.time(), mid))
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
                     (None if rating == "clear" else rating, note.strip()[:500] or None, time.time(), cid))
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
