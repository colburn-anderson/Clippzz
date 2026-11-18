"""The dashboard: paste a VOD link, watch progress, rate the moments found."""
import time
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
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

STATE_LABELS = {
    "queued": "Waiting", "fetching": "Fetching", "listening": "Listening",
    "scouting": "Scouting", "judging": "Judging", "done": "Done",
    "failed": "Failed", "paused": "Paused",
}


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
        rated = conn.execute(
            "SELECT rating FROM moments WHERE rating IS NOT NULL ORDER BY rated_at DESC LIMIT 10").fetchall()
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
    return templates.TemplateResponse(request, "vod.html", {
        "vod": vod, "moments": moments, "cost": cost, "labels": STATE_LABELS,
        "signals": db.loads(vod["signals"], {}) or {},
        "busy": vod["state"] in db.ACTIVE_STATES,
    })


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
