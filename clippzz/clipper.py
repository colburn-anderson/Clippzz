"""Makes one finished clip from a judged moment:

download the moment in HD (RAM scratch) -> speech-to-text with word timings
-> frames twice a second -> the planner writes the edit -> the renderer
follows it -> the MP4 is saved under /data/clips.

The footage is a list of parts of the stream laid end to end on one
timeline. Usually that is one part, the moment with some time either side.
When the owner's note asks for other times in the stream ("find more times
he calls it bread"), parts.py finds them first and they become extra parts.

Run by the worker for clips queued from the dashboard. By hand:
    python -m clippzz.clipper --clip 3 --rerender   queue clip 3 to render again from its saved plan
    python -m clippzz.clipper --moment 10 --here    make a clip right here (stop the worker first)
"""
import argparse
import gzip
import json
import logging
import math
import shutil
import subprocess
import time
from pathlib import Path

from . import captions, config, db, parts, planner, render, sounds, sources, transcribe

log = logging.getLogger("clippzz.clipper")

# Best 1080p with H.264 (cheapest to decode on the Pi), else the best up to 1080p.
SECTION_FORMAT = ("137+140/299+140/bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]"
                  "/bv*[height<=1080]+ba/b[height<=1080]/b")


def fetch_section(vid: str, start_s: float, end_s: float, wd: Path, name: str = "src",
                  tries: int = 3) -> Path:
    """Downloads start_s to end_s in HD. YouTube now and then drops one request
    (ffmpeg exits with code 8), so a failed download is tried again before the
    whole clip, and every part already downloaded, has to start over."""
    src = wd / f"{name}.mp4"
    for attempt in range(1, tries + 1):
        r = sources._run([*sources.YTDLP, "-q", "-f", SECTION_FORMAT,
                          "--download-sections", f"*{start_s:.2f}-{end_s:.2f}",
                          "--merge-output-format", "mp4", "-o", str(wd / f"{name}.%(ext)s"),
                          f"https://www.youtube.com/watch?v={vid}"], timeout=900)
        if r.returncode == 0 and src.exists():
            return src
        error = r.stderr.decode(errors="replace").strip()[-300:]
        if attempt < tries:
            log.info("download of %.0f-%.0f s failed (try %d), trying again: %s",
                     start_s, end_s, attempt, error[-120:])
            for leftover in wd.glob(f"{name}.*"):
                leftover.unlink(missing_ok=True)
            time.sleep(10 * attempt)
    raise sources.SourceError("HD section download failed: " + error)


def probe(path: Path) -> dict:
    """Frame size, length, and how far each stream starts after the earliest one."""
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "stream=codec_type,width,height,start_time:format=duration",
                        "-of", "json", str(path)], capture_output=True, timeout=60)
    d = json.loads(r.stdout or b"{}")
    streams = d.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not v or not a:
        raise sources.SourceError("the downloaded section is missing its video or audio")
    vs, as_ = float(v.get("start_time") or 0), float(a.get("start_time") or 0)
    base = min(vs, as_)
    return {"width": int(v["width"]), "height": int(v["height"]),
            "duration": float(d["format"]["duration"]), "v_off": vs - base, "a_off": as_ - base}


def joined_wav(pieces: list[dict], wd: Path) -> Path:
    """One 16 kHz mono WAV on the footage's timeline: each part's audio moved
    to where its earliest stream starts, cut or padded to the part's length,
    and the parts end to end."""
    wav = wd / "audio.wav"
    cmd = ["ffmpeg", "-v", "error", "-y"]
    for p in pieces:
        cmd += ["-i", str(p["file"])]
    graph = []
    for i, p in enumerate(pieces):
        delay = int(round(p["info"]["a_off"] * 1000))
        graph.append(f"[{i}:a]aformat=sample_fmts=s16:sample_rates=16000:channel_layouts=mono,"
                     f"adelay=delays={delay}:all=1,apad=whole_dur={p['dur']:.3f},"
                     f"atrim=duration={p['dur']:.3f}[a{i}]")
    graph.append("".join(f"[a{i}]" for i in range(len(pieces))) + f"concat=n={len(pieces)}:v=0:a=1[out]")
    cmd += ["-filter_complex", ";".join(graph), "-map", "[out]", "-c:a", "pcm_s16le", str(wav)]
    r = subprocess.run(cmd, capture_output=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError("audio extract failed: " + r.stderr.decode(errors="replace")[-300:])
    return wav


def half_second_levels(wav: Path) -> list[float]:
    """Loudness (dB RMS) of each half second of the 16 kHz WAV."""
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(wav), "-af",
         "asetnsamples=n=8000:p=0,astats=metadata=1:reset=1,"
         "ametadata=mode=print:key=lavfi.astats.Overall.RMS_level:file=-",
         "-f", "null", "-"], capture_output=True, timeout=300)
    levels = []
    for line in r.stdout.decode(errors="replace").splitlines():
        if "RMS_level=" in line:
            try:
                v = float(line.rsplit("=", 1)[1])
            except ValueError:
                v = -90.0
            levels.append(max(v, -90.0) if v == v else -90.0)
    return levels


def extract_frames(src: Path, wd: Path, v_off: float, fps: int = 2, width: int = 576):
    fd = wd / "frames"
    shutil.rmtree(fd, ignore_errors=True)
    fd.mkdir()
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(src), "-vf", f"fps={fps},scale={width}:-2",
                        "-q:v", "5", str(fd / "f_%04d.jpg")], capture_output=True, timeout=600)
    files = sorted(fd.glob("f_*.jpg"))
    if not files:
        raise RuntimeError("frame extract failed: " + r.stderr.decode(errors="replace")[-300:])
    frames = [(round(v_off + i / fps, 2), f.read_bytes()) for i, f in enumerate(files)]
    shutil.rmtree(fd, ignore_errors=True)
    return frames


def youtube_words(vid: str, start_s: float, duration: float) -> list[tuple[float, str]]:
    """YouTube's auto-caption words for the section, in section time."""
    saved = config.SOURCES_DIR / vid / "transcript.json.gz"
    if not saved.exists():
        return []
    words = json.loads(gzip.decompress(saved.read_bytes())).get("words", [])
    return captions.youtube_words(words, start_s, duration)


def owner_notes(conn, moment_id: int, limit: int = 8) -> list[str]:
    """The owner's notes on this moment and on recent clips, newest first."""
    notes = []
    row = conn.execute("SELECT note FROM moments WHERE id=? AND note IS NOT NULL", (moment_id,)).fetchone()
    if row:
        notes.append(f"On this moment: {row['note']}")
    for r in conn.execute(
            "SELECT c.title, c.rating, c.note FROM clips c WHERE c.note IS NOT NULL"
            " ORDER BY c.rated_at DESC LIMIT ?", (limit,)):
        verdict = {"good": "rated post it", "bad": "rated not good enough"}.get(r["rating"], "not rated")
        notes.append(f"On the clip \"{r['title']}\" ({verdict}): {r['note']}")
    return notes


def thin_frames(frames, keep):
    """Every frame inside the keep ranges [(from, to)] (the moment, and any
    extra parts of the stream), every other frame (one a second) elsewhere."""
    return [f for i, f in enumerate(frames) if i % 2 == 0 or any(a <= f[0] <= b for a, b in keep)]


def _top(levels: list[float], fraction: float) -> float:
    if not levels:
        return 0.0
    ordered = sorted(levels)
    return ordered[min(len(ordered) - 1, int(len(ordered) * (1 - fraction)))]


def footage_layout(clip, m, rerender: bool) -> list[dict]:
    """The parts of the stream the clip is cut from: the stored ones, or the
    moment with time either side of it."""
    stored = db.loads(clip["parts"], [])
    if stored:
        return stored
    if rerender and clip["section_start_s"] is not None:
        start = clip["section_start_s"]
    else:
        # YouTube stream recordings have a keyframe every 2 seconds.
        start = max(0.0, math.floor((m["start_s"] - config.CLIP_PAD_BEFORE_S) / 2) * 2)
    return [{"start": start, "end": m["end_s"] + config.CLIP_PAD_AFTER_S, "main": True}]


def fetch_parts(vid: str, layout: list[dict], wd: Path, step, first_fields=None) -> list[dict]:
    """Downloads each part once and measures it. Each part gets "at", where it
    starts on the footage's timeline; a stored one is kept, so a saved plan's
    times still fit when the clip is rendered again."""
    out, at = [], 0.0
    for i, p in enumerate(layout):
        path = wd / f"part{i}.mp4"
        if not path.exists():
            what = "the moment" if len(layout) == 1 else f"part {i + 1} of {len(layout)}"
            step(f"Downloading {what} in HD", **(first_fields if i == 0 and first_fields else {}))
            fetch_section(vid, p["start"], p["end"], wd, name=f"part{i}")
        info = probe(path)
        start_at = float(p.get("at", at))
        dur = float(p.get("dur", info["duration"]))
        out.append({**p, "file": path, "at": start_at, "dur": dur, "info": info})
        at = start_at + dur
    return out


def part_frames(pieces: list[dict], wd: Path) -> list:
    """Frames twice a second from every part, in footage time."""
    frames = []
    for p in pieces:
        frames += [(round(p["at"] + t, 2), jpeg)
                   for t, jpeg in extract_frames(p["file"], wd, p["info"]["v_off"]) if t < p["dur"]]
    return frames


def part_youtube_words(vid: str, pieces: list[dict]) -> list[tuple[float, str]]:
    out = []
    for p in pieces:
        out += [(round(p["at"] + t, 2), w) for t, w in youtube_words(vid, p["start"], p["dur"])
                if 0 <= t < p["dur"]]
    return out


def parts_note(pieces: list[dict]) -> str:
    """What the planner needs to know when the footage joins several parts."""
    if len(pieces) < 2:
        return ""
    rows = "\n".join(f"  part {i}{' (the moment)' if p.get('main') else ''}: {p['at']:.1f} to "
                     f"{p['at'] + p['dur']:.1f} s, from {parts.clock(p['start'])} in the stream"
                     for i, p in enumerate(pieces, 1))
    return (f"This footage joins {len(pieces)} parts of the stream, in stream order, because the owner "
            f"asked for other times as well:\n{rows}\n"
            "Each part is a different time, so a beat stays inside one part, and going from one part to "
            "the next is a jump in time: use it like a montage cut, and mark a long jump with a stage "
            "direction such as *10 minutes later* when it helps. Leave out a part that doesn't land.\n")


def frame_pass(conn, clip_id, vod_id, plan, frames, words, info, bounds=()) -> tuple[dict, float]:
    """The framing pass: split beats at camera cuts and aim or pan the crop at
    the subject. Returns the updated plan and what it cost."""
    beats, tin, tout = planner.reframe(plan, frames, info["width"], info["height"])
    cost = db.record_usage(conn, vod_id, "reframe", config.PLANNER_MODEL, tin, tout)
    log.info("clip %s: framing pass cost $%.3f, %d beats -> %d shots", clip_id, cost,
             len(plan.get("beats", [])), len(beats))
    plan = planner.tidy({**plan, "beats": beats}, words, info["duration"], bounds)
    return plan, cost


def make(conn, clip_id: int, keep: bool = False, rerender: bool = False, reframe: bool = False) -> dict:
    clip = conn.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()
    if not clip:
        raise ValueError(f"no clip {clip_id}")
    m = conn.execute("SELECT * FROM moments WHERE id=?", (clip["moment_id"],)).fetchone()
    vod = conn.execute("SELECT * FROM vods WHERE id=?", (m["vod_id"],)).fetchone()
    if rerender and not (clip["plan"] and clip["words"]):
        raise ValueError(f"clip {clip_id} has no saved plan to render again")
    wd = config.WORK_DIR / f"clip-{clip_id}"
    wd.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    def step(detail, **fields):
        log.info("clip %s: %s (%.0fs in)", clip_id, detail, time.time() - t0)
        db.set_clip(conn, clip_id, "working", detail, **fields)

    try:
        layout, find_cost = None, 0.0
        if not rerender and clip["parts"] is None and (m["note"] or "").strip():
            # Once per clip: a retry reuses what was found ("[]" when nothing).
            step("Finding the other times in the stream")
            layout, find_cost = parts.find(conn, vod, m)
            db.set_clip(conn, clip_id, "working", "Finding the other times in the stream",
                        parts=json.dumps(layout))
            if layout:
                log.info("clip %s: footage from %d parts of the stream, starting %s (cost $%.3f)", clip_id,
                         len(layout), ", ".join(parts.clock(p["start"]) for p in layout), find_cost)
        layout = layout or footage_layout(clip, m, rerender)
        multi = len(layout) > 1
        pieces = fetch_parts(vod["id"], layout, wd, step,
                             None if multi else {"section_start_s": layout[0]["start"]})
        timeline = [{k: p[k] for k in ("start", "end", "main", "at", "dur")} for p in pieces]
        info = {"width": pieces[0]["info"]["width"], "height": pieces[0]["info"]["height"],
                "duration": round(sum(p["dur"] for p in pieces), 3)}
        bounds = [p["at"] for p in pieces[1:]]
        main = next((p for p in pieces if p.get("main")), pieces[0])
        span = (main["at"] + m["start_s"] - main["start"], main["at"] + m["end_s"] - main["start"])
        wav = joined_wav(pieces, wd)
        levels = half_second_levels(wav)

        def level_at(t):
            i = int(t / 0.5)
            return levels[i] if 0 <= i < len(levels) else -90.0

        shout = _top(levels, 0.35)

        if rerender:
            plan, words = json.loads(clip["plan"]), json.loads(clip["words"])
            if reframe:
                db.check_budget(conn)
                step("Looking at the frames to fix the framing")
                plan, fcost = frame_pass(conn, clip_id, vod["id"], plan, part_frames(pieces, wd),
                                         words, info, bounds)
                db.set_clip(conn, clip_id, "working", "Rendering", plan=json.dumps(plan),
                            cost_usd=round((clip["cost_usd"] or 0) + fcost, 4))
        else:
            # The timeline is stored with the words, so a re-render lines up with them.
            step("Writing captions from the audio", **({"parts": json.dumps(timeline)} if multi else {}))
            # The reviewer's notes name people and quote the key lines, which
            # steers speech-to-text towards the right words.
            prompt = f"{vod['channel'] or ''}. {m['title'] or ''}. {m['joke'] or ''}"
            heard = [[ws, we, w] for ws, we, w in transcribe.words(wav, prompt[:600])]
            words, diffs = captions.reconcile(heard, part_youtube_words(vod["id"], pieces))
            log.info("clip %s: %d words, %d places where YouTube's captions disagreed",
                     clip_id, len(words), len(diffs))
            db.set_clip(conn, clip_id, "working", "Looking at the frames", words=json.dumps(words))
            all_frames = part_frames(pieces, wd)
            keep_all = [(span[0] - 1, span[1] + 1)] + [(p["at"], p["at"] + p["dur"])
                                                       for p in pieces if p is not main]
            frames = thin_frames(all_frames, keep_all)
            loud_cut = _top(levels, 0.15)
            loud_times = [round(i * 0.5, 1) for i, v in enumerate(levels) if v >= loud_cut]
            db.check_budget(conn)
            step(f"Planning the edit from {len(frames)} frames")
            meta = {"channel": vod["channel"], "title": vod["title"]}
            plan, tin, tout = planner.plan_clip(
                meta, dict(m), frames, words, loud_times, info["duration"], info["width"], info["height"],
                moment_span=span, disagreements=diffs, owner_notes=owner_notes(conn, m["id"]),
                sound_names=sounds.choices(), music_names=list(sounds.music()), app_songs=sounds.app_songs(),
                parts_note=parts_note(pieces))
            cost = db.record_usage(conn, vod["id"], "plan", config.PLANNER_MODEL, tin, tout)
            plan = planner.tidy(plan, words, info["duration"], bounds)
            step("Planning the edit: fixing the framing")
            plan, fcost = frame_pass(conn, clip_id, vod["id"], plan, all_frames, words, info, bounds)
            cost += fcost
            if str(plan.get("music", "")).startswith("app: "):  # the owner adds it in the app
                name = plan["music"][5:]
                song = next((s for s in sounds.app_songs() if s["name"] == name), {"name": name, "part": ""})
                plan["_app_song"] = {"name": song["name"], "part": song.get("part", "")}
            plan["_settings"] = {"model": config.PLANNER_MODEL, "effort": config.PLANNER_EFFORT,
                                 "frames": len(frames), "cost_usd": round(cost, 3)}
            db.set_clip(conn, clip_id, "working", "Rendering", plan=json.dumps(plan),
                        title=plan.get("title"), cost_usd=round(cost + find_cost, 4))
            log.info("clip %s: plan cost $%.3f (%s in, %s out tokens)", clip_id, cost, tin, tout)

        step("Rendering")
        out = wd / "clip.mp4"
        stats = render.render([(p["file"], p["at"], p["dur"]) for p in pieces], plan, words, out,
                              info["width"], info["height"], loud=lambda t: level_at(t) >= shout)
        config.CLIPS_DIR.mkdir(parents=True, exist_ok=True)
        final = config.CLIPS_DIR / f"{clip_id}.mp4"
        shutil.move(str(out), final)
        stats["seconds"] = round(time.time() - t0)
        detail = (f"{stats['duration_s']:.1f} s, {stats['beats']} beats, "
                  f"made in {stats['seconds'] // 60} min {stats['seconds'] % 60} s")
        if stats.get("music_credit"):
            detail += f". Music credit for the description: {stats['music_credit']}"
        elif stats.get("music"):
            detail += f". Music: {stats['music']}"
        db.set_clip(conn, clip_id, "done", detail, path=str(final), duration_s=stats["duration_s"], rerender=0,
                    music_credit=stats.get("music_credit") or None)
        log.info("clip %s: done %s", clip_id, stats)
        return stats
    finally:
        if not keep:
            shutil.rmtree(wd, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description="Make a clip from a judged moment.")
    ap.add_argument("--moment", type=int, help="moment id: make a new clip from it")
    ap.add_argument("--clip", type=int, help="clip id: run this clip again")
    ap.add_argument("--rerender", action="store_true", help="reuse the clip's saved plan and words")
    ap.add_argument("--reframe", action="store_true", help="with --rerender: fix the framing first (a few cents)")
    ap.add_argument("--keep", action="store_true", help="keep the scratch files (for re-renders)")
    ap.add_argument("--here", action="store_true",
                    help="run in this process instead of the worker's queue (stop the worker first)")
    args = ap.parse_args()
    if not (args.moment or args.clip):
        ap.error("give --moment or --clip")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if not args.here:
        # The worker does the work, so two processes never touch the same clip.
        with db.session() as conn:
            if args.clip and args.rerender:
                db.queue_rerender(conn, args.clip, reframe=args.reframe)
                print(f"clip {args.clip} queued to render again; watch: docker compose logs -f worker")
            elif args.moment:
                cid = db.add_clip(conn, args.moment)
                print(f"clip {cid} queued; watch: docker compose logs -f worker")
            else:
                ap.error("to run clip --clip again with a new plan, use the dashboard's Make a clip button")
        return
    with db.session() as conn:
        clip_id = args.clip or db.add_clip(conn, args.moment)
        try:
            stats = make(conn, clip_id, keep=args.keep, rerender=args.rerender)
        except Exception as e:
            db.set_clip(conn, clip_id, "failed", f"{type(e).__name__}: {e}"[:500])
            raise
        print(json.dumps({"clip": clip_id, **stats}))


if __name__ == "__main__":
    main()
