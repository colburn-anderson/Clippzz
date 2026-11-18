"""Turns an edit plan into a finished vertical clip with ffmpeg.

Every time in a plan is a section time: seconds from the start of the
downloaded section. The renderer keeps only the beats, joins them with hard
cuts, crops each beat to 9:16 around its focus point, then lays the speech
captions, stage directions, colour hits and sound stings on the joined
timeline (output time).
"""
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import sounds

W, H, FPS = 1080, 1920, 30
CAPTION_Y = 1130      # caption centre: a little below the middle, under the faces
STAGE_Y_ABOVE = 985   # a stage direction moves up here when speech shares the screen
FONT = "Montserrat Black"
# Sized to match the reference channel: capitals about 4 percent of frame height.
SPEECH_SIZE, STAGE_SIZE, MIN_SIZE = 150, 132, 80
CHAR_WIDTH = 0.47  # measured: Montserrat Black capitals advance about 0.47 x font size

# Caption colours in ASS order (blue, green, red): yellow is #FFE61F.
COLOURS = {
    "white": "FFFFFF", "yellow": "1FE6FF", "cyan": "FFE53A",
    "pink": "B464FF", "red": "3C3CFF",
}
SPEECH_CYCLE = ("yellow", "white", "cyan")

# Full-frame tints for colour hits, as colorchannelmixer settings. The tints
# map brightness into one colour, like the green and red frames on the
# reference channel; "white" washes the frame out; "bw" drops the colour.
HIT_MIX = {
    "green": "rr=0.09:rg=0.18:rb=0.03:gr=0.42:gg=0.83:gb=0.15:br=0.09:bg=0.18:bb=0.03",
    "red": "rr=0.45:rg=0.89:rb=0.17:gr=0.07:gg=0.15:gb=0.03:br=0.07:bg=0.15:bb=0.03",
    "white": "rr=1.25:rg=0.2:rb=0.2:gr=0.2:gg=1.25:gb=0.2:br=0.2:bg=0.2:bb=1.25",
    "bw": "rr=0.3:rg=0.59:rb=0.11:gr=0.3:gg=0.59:gb=0.11:br=0.3:bg=0.59:bb=0.11",
}

SWEARS = {
    "fuck": "F*CK", "fucking": "F*CKING", "fucked": "F*CKED", "fucker": "F*CKER",
    "motherfucker": "MOTHERF*CKER", "shit": "SH*T", "shitty": "SH*TTY", "bullshit": "BULLSH*T",
    "bitch": "B*TCH", "bitches": "B*TCHES", "dick": "D*CK", "pussy": "P*SSY",
}
# Slurs are never captioned at all.
SLUR_RE = re.compile(r"^(nigg|fag)", re.I)


@dataclass
class Beat:
    src_s: float
    src_e: float
    out_s: float = 0.0
    focus_x: float = 0.5
    focus_y: float = 0.45
    zoom: float = 1.0

    @property
    def dur(self) -> float:
        return self.src_e - self.src_s

    @property
    def out_e(self) -> float:
        return self.out_s + self.dur


@dataclass
class Event:
    start: float
    end: float
    text: str
    colour: str
    kind: str = "speech"  # speech or stage
    y: int = CAPTION_Y


@dataclass
class Hit:
    start: float
    end: float
    kind: str


@dataclass
class Cue:
    at: float
    name: str


# ---------- timeline ----------

def build_timeline(beats: list[dict]) -> list[Beat]:
    """Beats in plan order, each trimmed to a whole number of frames, placed
    end to end on the output timeline."""
    out, t = [], 0.0
    for b in beats:
        dur = round((b["end_s"] - b["start_s"]) * FPS) / FPS
        if dur < 2 / FPS:
            continue
        out.append(Beat(b["start_s"], b["start_s"] + dur, round(t, 4),
                        b.get("focus_x", 0.5), b.get("focus_y", 0.45), b.get("zoom", 1.0)))
        t += dur
    return out


def total(beats: list[Beat]) -> float:
    return beats[-1].out_e if beats else 0.0


def beat_at(t: float, beats: list[Beat]) -> int | None:
    for i, b in enumerate(beats):
        if b.src_s - 1e-6 <= t <= b.src_e + 1e-6:
            return i
    return None


def to_out(t: float, beats: list[Beat], snap: str | None = None) -> float | None:
    """Section time -> output time. A time that falls in a cut gap maps to the
    start of the next beat (snap="next"), the end of the previous one
    (snap="prev"), or nowhere."""
    for i, b in enumerate(beats):
        if b.src_s - 1e-6 <= t <= b.src_e + 1e-6:
            return b.out_s + min(max(t, b.src_s), b.src_e) - b.src_s
        if t < b.src_s:
            if snap == "next":
                return b.out_s
            if snap == "prev" and i:
                return beats[i - 1].out_e
            return None
    if snap == "prev" and beats:
        return beats[-1].out_e
    return None


# ---------- captions ----------

def clean_word(raw: str, fixes: dict[str, str]) -> tuple[str, str]:
    """Caption text for one word (upper case, censored) and its trailing punctuation."""
    m = re.match(r"^(.*?)([.,!?…]*)$", raw.strip())
    base, punct = m.group(1).strip("\"“”()[]-—"), m.group(2)
    if not base or SLUR_RE.match(fixes.get(base.lower(), base)):
        return "", punct
    base = fixes.get(base.lower(), base)
    base = SWEARS.get(base.lower(), base).upper().replace("’", "'")
    mark = "!" if "!" in punct else "?" if "?" in punct else ""
    return base + mark, punct


def _norm(word: str) -> str:
    return re.sub(r"[^\w']", "", word.lower().replace("’", "'"))


def apply_fixes(words, fixes) -> list[list]:
    """Replaces misheard phrases. Each (heard, write) pair matches consecutive
    words (case and punctuation ignored); the written words share the heard
    words' time span."""
    out = [list(w) for w in words]
    for heard, write in fixes:
        h = [_norm(x) for x in heard.split() if _norm(x)]
        new = write.split()
        if not h or not new:
            continue
        i = 0
        while i <= len(out) - len(h):
            if [_norm(w[2]) for w in out[i:i + len(h)]] != h:
                i += 1
                continue
            s, e = out[i][0], out[i + len(h) - 1][1]
            tail = re.match(r"^.*?([.,!?…]*)$", out[i + len(h) - 1][2]).group(1)
            step = (e - s) / len(new)
            repl = [[round(s + k * step, 3), round(s + (k + 1) * step, 3), w] for k, w in enumerate(new)]
            repl[-1][2] += tail
            out[i:i + len(h)] = repl
            i += len(new)
    return out


def speech_events(words, beats: list[Beat], fixes: dict[str, str] | None = None,
                  loud=None) -> list[Event]:
    """One or two words at a time, timed to the word, colour changing by phrase.
    `words` are [start, end, text] in section time. `loud(t)` says whether the
    audio is loud at t; a loud phrase-final word gets an exclamation mark."""
    fixes = {k.lower(): v for k, v in (fixes or {}).items()}
    items = []
    for ws, we, raw in words:
        bi = beat_at((ws + we) / 2, beats)
        if bi is None:
            continue
        text, punct = clean_word(raw, fixes)
        if text:
            items.append({"s": max(ws, beats[bi].src_s), "e": min(we, beats[bi].src_e),
                          "text": text, "ends": any(c in punct for c in ".!?"),
                          "pause": bool(punct), "bi": bi})

    chunks, colour, prev = [], -1, None
    i = 0
    while i < len(items):
        it = dict(items[i])
        if prev is None or prev["ends"] or it["bi"] != prev["bi"] or it["s"] - prev["e"] > 0.45:
            colour += 1
        if i + 1 < len(items):
            nx = items[i + 1]
            # One word at a time, except a quick pair with a tiny word ("LOOK AT", "MY PHONE").
            joinable = (nx["bi"] == it["bi"] and not it["pause"] and nx["s"] - it["e"] < 0.15
                        and (len(it["text"]) <= 2 or len(nx["text"]) <= 2)
                        and len(it["text"]) + 1 + len(nx["text"]) <= 9)
            if joinable:
                it.update(text=f"{it['text']} {nx['text']}", e=nx["e"], ends=nx["ends"],
                          pause=nx["pause"])
                i += 1
        it["colour"] = SPEECH_CYCLE[colour % len(SPEECH_CYCLE)]
        chunks.append(it)
        prev = it
        i += 1

    # Fast talk: a chunk that would be on screen under 0.22 s takes the next word too.
    merged = []
    for c in chunks:
        if merged:
            p = merged[-1]
            if (p["colour"] == c["colour"] and p["bi"] == c["bi"] and c["s"] - p["s"] < 0.22
                    and len(p["text"]) + 1 + len(c["text"]) <= 12):
                p.update(text=f"{p['text']} {c['text']}", e=c["e"], ends=c["ends"])
                continue
        merged.append(c)
    chunks = merged

    # Exclamation on loud phrase endings, the way the reference captions shout.
    if loud:
        for k, c in enumerate(chunks):
            last = k + 1 == len(chunks) or chunks[k + 1]["colour"] != c["colour"]
            if last and c["text"][-1] not in "!?" and loud(c["s"]):
                c["text"] += "!"

    events = []
    for k, c in enumerate(chunks):
        b = beats[c["bi"]]
        start = to_out(c["s"], beats)
        end = to_out(c["e"], beats) + 0.3
        if k + 1 < len(chunks):
            nxt = to_out(chunks[k + 1]["s"], beats)
            if nxt - end < 0.6:
                end = nxt
        end = min(end, b.out_e)
        if end - start >= 0.08:
            events.append(Event(round(start, 3), round(end, 3), c["text"], c["colour"]))
    return events


def stage_events(directions: list[dict], beats: list[Beat]) -> list[Event]:
    events = []
    for d in sorted(directions, key=lambda d: d["start_s"]):
        text = d["text"].strip().strip("*").strip().upper()
        if not text:
            continue
        start = to_out(d["start_s"], beats, snap="next")
        end = to_out(d["end_s"], beats, snap="prev")
        if start is None:
            continue
        if end is None or end - start < 0.4:
            end = start + 0.8
        if events and events[-1].end > start:  # one stage direction at a time
            events[-1].end = round(start, 3)
            if events[-1].end - events[-1].start < 0.2:
                events.pop()
        end = min(end, total(beats))
        if end - start < 0.6:  # e.g. a closing line placed at the very end: start it earlier
            earliest = max(events[-1].end if events else 0.0, end - 0.6)
            start = min(start, earliest)
        if end - start < 0.2:
            continue
        events.append(Event(round(start, 3), round(end, 3), f"*{text}*",
                            d.get("color", "white") if d.get("color") in COLOURS else "white",
                            kind="stage"))
    return events


def place(speech: list[Event], stage: list[Event]) -> list[Event]:
    """A stage direction that shares the screen with speech moves up."""
    for s in stage:
        if any(e.start < s.end and s.start < e.end for e in speech):
            s.y = STAGE_Y_ABOVE
    return speech + stage


def _ass_time(t: float) -> str:
    cs = int(round(max(t, 0) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _size(text: str, base: int) -> int:
    """Font size that keeps one line inside about 960 px."""
    return max(MIN_SIZE, min(base, int(960 / (CHAR_WIDTH * max(len(text), 1)))))


def ass_document(events: list[Event]) -> str:
    head = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {W}\nPlayResY: {H}\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Cap,{FONT},{SPEECH_SIZE},&H00FFFFFF,&H00FFFFFF,&H00000000,&H78000000,"
        "0,0,0,0,100,100,1,0,1,10,4,5,50,50,0,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = []
    for e in sorted(events, key=lambda e: (e.start, e.kind)):
        text = e.text.replace("\\", "/").replace("{", "(").replace("}", ")")
        size = _size(text, SPEECH_SIZE if e.kind == "speech" else STAGE_SIZE)
        # Pop in: start small, overshoot, settle.
        tags = (f"{{\\an5\\pos({W // 2},{e.y})\\fs{size}\\c&H{COLOURS[e.colour]}&"
                "\\fscx70\\fscy70\\t(0,80,\\fscx106\\fscy106)\\t(80,140,\\fscx100\\fscy100)}")
        layer = 1 if e.kind == "stage" else 0
        lines.append(f"Dialogue: {layer},{_ass_time(e.start)},{_ass_time(e.end)},Cap,,0,0,0,,{tags}{text}")
    return head + "\n".join(lines) + "\n"


# ---------- hits and sounds ----------

def hit_events(hits: list[dict], beats: list[Beat]) -> list[Hit]:
    out = []
    end_all = total(beats)
    for h in sorted(hits, key=lambda h: h["at_s"]):
        kind = h.get("kind")
        if kind not in HIT_MIX:
            continue
        t = to_out(h["at_s"], beats, snap="next")
        if t is None or t >= end_all:
            continue
        length = float(h.get("length_s") or 0)
        length = min(max(length, 0.4), 2.5) if kind == "bw" else min(max(length or 0.15, 0.08), 0.25)
        flashes = [o for o in out if o.kind != "bw"]
        if kind != "bw" and flashes and t - flashes[-1].start < 0.4:
            continue
        out.append(Hit(round(t, 3), round(min(t + length, end_all), 3), kind))
    return out


def sound_cues(cues: list[dict], beats: list[Beat]) -> list[Cue]:
    out = []
    for c in sorted(cues, key=lambda c: c["at_s"]):
        if c.get("name") not in sounds.SOUNDS:
            continue
        t = to_out(c["at_s"], beats, snap="next")
        if t is not None and t < total(beats) - 0.1:
            out.append(Cue(round(t, 3), c["name"]))
    return out


# ---------- ffmpeg ----------

def _even(v: float) -> int:
    return max(2, int(v) // 2 * 2)


def crop_box(src_w: int, src_h: int, fx: float, fy: float, zoom: float) -> tuple[int, int, int, int]:
    """A 9:16 window inside the source, centred on the focus point where possible."""
    zoom = min(max(zoom, 1.0), 2.0)
    if src_w / src_h > W / H:
        ch = src_h / zoom
        cw = ch * W / H
    else:
        cw = src_w / zoom
        ch = cw * H / W
    cw, ch = _even(cw), _even(ch)
    x = min(max(fx * src_w - cw / 2, 0), src_w - cw)
    y = min(max(fy * src_h - ch / 2, 0), src_h - ch)
    return cw, ch, int(x), int(y)


def ffmpeg_command(src: Path, beats: list[Beat], hits: list[Hit], cues: list[Cue],
                   sound_files: dict[str, Path], ass_path: Path, out_path: Path,
                   src_w: int, src_h: int) -> list[str]:
    cmd = ["ffmpeg", "-v", "error", "-y"]
    for b in beats:
        cmd += ["-ss", f"{b.src_s:.3f}", "-t", f"{b.dur + 0.1:.3f}", "-i", str(src)]
    for c in cues:
        cmd += ["-i", str(sound_files[c.name])]

    graph = []
    audio_fmt = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
    for i, b in enumerate(beats):
        cw, ch, x, y = crop_box(src_w, src_h, b.focus_x, b.focus_y, b.zoom)
        graph.append(f"[{i}:v]trim=duration={b.dur:.4f},setpts=PTS-STARTPTS,"
                     f"crop={cw}:{ch}:{x}:{y},scale={W}:{H}:flags=bicubic,setsar=1,"
                     f"fps={FPS},format=yuv420p[v{i}]")
        graph.append(f"[{i}:a]{audio_fmt},atrim=duration={b.dur:.4f},asetpts=PTS-STARTPTS,"
                     f"apad=whole_dur={b.dur:.4f},afade=t=in:d=0.01,"
                     f"afade=t=out:st={max(b.dur - 0.02, 0):.4f}:d=0.02[a{i}]")
    n = len(beats)
    graph.append("".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[vc][ac]")

    video = [f"colorchannelmixer={HIT_MIX[h.kind]}:enable='between(t,{h.start:.3f},{h.end:.3f})'"
             for h in hits]
    video += [f"ass={ass_path}", "format=yuv420p"]
    graph.append("[vc]" + ",".join(video) + "[vout]")

    mix = ["[ac]"]
    for j, c in enumerate(cues):
        ms = int(round(c.at * 1000))
        graph.append(f"[{n + j}:a]{audio_fmt},volume={sounds.volume(c.name):.2f},"
                     f"adelay=delays={ms}:all=1[s{j}]")
        mix.append(f"[s{j}]")
    if cues:
        graph.append("".join(mix) + f"amix=inputs={len(mix)}:normalize=0:duration=first:"
                     "dropout_transition=0[am]")
    # Loud like the reference Shorts, with a ceiling so nothing clips.
    graph.append(f"{'[am]' if cues else '[ac]'}loudnorm=I=-11:TP=-1.0:LRA=11,aresample=48000[aout]")

    cmd += ["-filter_complex", ";".join(graph), "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
            "-r", str(FPS), "-g", str(FPS * 2), "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart", str(out_path)]
    return cmd


def render(src: Path, plan: dict, words, out_path: Path, src_w: int, src_h: int,
           loud=None, timeout: int = 1800) -> dict:
    """Renders the plan to out_path. Returns what was placed, for the record."""
    beats = build_timeline(plan["beats"])
    if not beats:
        raise RuntimeError("the plan has no usable beats")
    fixes = [(f["heard"], f["write"]) for f in plan.get("caption_fixes", []) if f.get("heard")]
    speech = speech_events(apply_fixes(words, fixes), beats, loud=loud)
    stage = stage_events(plan.get("stage_directions", []), beats)
    events = place(speech, stage)
    hits = hit_events(plan.get("hits", []), beats)
    cues = sound_cues(plan.get("sounds", []), beats)

    work = out_path.parent
    ass_path = work / "captions.ass"
    ass_path.write_text(ass_document(events), encoding="utf-8")
    files = sounds.ensure([c.name for c in cues], work / "sounds")
    cmd = ffmpeg_command(src, beats, hits, cues, files, ass_path, out_path, src_w, src_h)
    r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if r.returncode != 0 or not out_path.exists():
        raise RuntimeError("render failed: " + r.stderr.decode(errors="replace")[-600:])
    return {"duration_s": round(total(beats), 2), "beats": len(beats), "captions": len(speech),
            "stage": len(stage), "hits": len(hits), "sounds": len(cues)}
