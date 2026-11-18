"""Everything that talks to YouTube, through yt-dlp. Nothing here downloads
the full video: metadata, captions, an audio stream piped straight into
ffmpeg, and short low-resolution sections for frames."""
import gzip
import json
import re
import shutil
import subprocess
from pathlib import Path

from . import config

YTDLP = ["yt-dlp", "--no-warnings", "--no-progress", "--no-playlist"]
_ID_RE = re.compile(r"(?:v=|youtu\.be/|/live/|/shorts/|/embed/)([A-Za-z0-9_-]{11})")


class SourceError(RuntimeError):
    pass


def video_id(url: str) -> str | None:
    url = url.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url):
        return url
    m = _ID_RE.search(url)
    return m.group(1) if m else None


def _run(cmd, timeout, **kw):
    try:
        return subprocess.run(cmd, capture_output=True, timeout=timeout, **kw)
    except subprocess.TimeoutExpired as e:
        raise SourceError(f"timed out after {timeout}s: {' '.join(cmd[:3])}") from e


def work_dir(vid: str) -> Path:
    d = config.WORK_DIR / vid
    d.mkdir(parents=True, exist_ok=True)
    return d


def source_dir(vid: str) -> Path:
    d = config.SOURCES_DIR / vid
    d.mkdir(parents=True, exist_ok=True)
    return d


def info_path(vid: str) -> Path:
    return work_dir(vid) / "info.json"


def fetch_info(vid: str, refresh: bool = False) -> dict:
    """Video metadata. Kept in scratch space because its media links expire
    after a few hours; refetched when missing."""
    path = info_path(vid)
    if path.exists() and not refresh:
        return json.loads(path.read_text())
    r = _run([*YTDLP, "--skip-download", "--dump-single-json",
              f"https://www.youtube.com/watch?v={vid}"], timeout=180)
    if r.returncode != 0:
        raise SourceError(r.stderr.decode(errors="replace").strip()[-400:] or "yt-dlp failed")
    path.write_bytes(r.stdout)
    return json.loads(r.stdout)


def fetch_transcript(vid: str) -> dict:
    """Auto-captions as {"lines": [[start_s, text]], "words": [[start_s, word]]}.
    Saved once, compressed, under /data/sources."""
    saved = source_dir(vid) / "transcript.json.gz"
    if saved.exists():
        return json.loads(gzip.decompress(saved.read_bytes()))
    wd = work_dir(vid)
    r = _run([*YTDLP, "--load-info-json", str(info_path(vid)), "--skip-download",
              "--write-subs", "--write-auto-subs", "--sub-langs", "en-orig,en",
              "--sub-format", "json3", "-o", str(wd / "cap")], timeout=300)
    files = sorted(wd.glob("cap*.json3"), key=lambda p: "orig" not in p.name)
    if not files:
        raise SourceError("no English captions on this video: "
                          + r.stderr.decode(errors="replace").strip()[-300:])
    transcript = parse_json3(json.loads(files[0].read_text()))
    for f in files:
        f.unlink()
    if len(transcript["words"]) < 50:
        raise SourceError("captions are nearly empty")
    saved.write_bytes(gzip.compress(json.dumps(transcript).encode()))
    return transcript


def parse_json3(data: dict) -> dict:
    lines, words = [], []
    for ev in data.get("events", []):
        segs = ev.get("segs")
        if not segs:
            continue
        t0 = ev.get("tStartMs", 0) / 1000
        text = "".join(s.get("utf8", "") for s in segs).strip()
        if not text:
            continue
        lines.append([round(t0, 2), re.sub(r"\s+", " ", text)])
        for s in segs:
            w = s.get("utf8", "").strip()
            if w:
                words.append([round(t0 + s.get("tOffsetMs", 0) / 1000, 2), w])
    return {"lines": lines, "words": words}


def heatmap(info: dict) -> list[tuple[float, float, float]]:
    """"Most replayed" as (start_s, end_s, value 0-1). Often empty."""
    return [(p["start_time"], p["end_time"], p["value"]) for p in info.get("heatmap") or []]


def loudness(vid: str) -> list[float]:
    """Loudness (dB RMS) for every second of the VOD. The audio is streamed
    through ffmpeg and never written to disk. Saved compressed afterwards."""
    saved = source_dir(vid) / "loudness.json.gz"
    if saved.exists():
        return json.loads(gzip.decompress(saved.read_bytes()))
    dl = subprocess.Popen(
        [*YTDLP, "-q", "--load-info-json", str(info_path(vid)),
         "-f", "249/250/139/wa[protocol=https]/ba[protocol=https]/wa/ba/w", "-o", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    ff = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", "pipe:0", "-vn", "-af",
         "aresample=8000,asetnsamples=n=8000:p=0,astats=metadata=1:reset=1,"
         "ametadata=mode=print:key=lavfi.astats.Overall.RMS_level:file=-",
         "-f", "null", "-"],
        stdin=dl.stdout, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    dl.stdout.close()
    try:
        out, _ = ff.communicate(timeout=3600)
    except subprocess.TimeoutExpired as e:
        ff.kill()
        dl.kill()
        raise SourceError("loudness scan timed out") from e
    finally:
        dl.wait(timeout=30)
    levels = []
    for line in out.decode(errors="replace").splitlines():
        if "RMS_level=" in line:
            try:
                v = float(line.rsplit("=", 1)[1])
            except ValueError:
                v = -90.0
            levels.append(round(max(v, -90.0), 1) if v == v else -90.0)
    if len(levels) < 30:
        raise SourceError("loudness scan produced no data")
    saved.write_bytes(gzip.compress(json.dumps(levels).encode()))
    return levels


def frames(vid: str, start_s: float, end_s: float, count: int = 8) -> list[tuple[float, bytes]]:
    """Up to `count` small JPEG frames spread across [start_s, end_s], as
    (timestamp, bytes). Downloads only that section at 360p into RAM scratch."""
    wd = work_dir(vid) / f"sec_{int(start_s)}"
    shutil.rmtree(wd, ignore_errors=True)
    wd.mkdir(parents=True)
    try:
        dur = max(end_s - start_s, 2.0)
        r = _run([*YTDLP, "-q", "--load-info-json", str(info_path(vid)),
                  "-f", "134/bv*[height<=360][protocol=https]/bv*[height<=480]/b[height<=480]/wv*/w",
                  "--download-sections", f"*{start_s:.1f}-{end_s:.1f}",
                  "-o", str(wd / "sec.%(ext)s")], timeout=240)
        clips = [p for p in wd.glob("sec.*") if p.suffix not in (".part", ".ytdl")]
        if not clips:
            raise SourceError("section download failed: "
                              + r.stderr.decode(errors="replace").strip()[-300:])
        step = dur / count
        r = _run(["ffmpeg", "-v", "error", "-i", str(clips[0]),
                  "-vf", f"fps=1/{step:.3f}:start_time=0:round=down,scale=512:-2",
                  "-q:v", "7", "-frames:v", str(count), str(wd / "f_%02d.jpg")], timeout=120)
        out = []
        for i, f in enumerate(sorted(wd.glob("f_*.jpg"))):
            out.append((round(start_s + i * step, 1), f.read_bytes()))
        if not out:
            raise SourceError("no frames extracted: " + r.stderr.decode(errors="replace")[-200:])
        return out
    finally:
        shutil.rmtree(wd, ignore_errors=True)
