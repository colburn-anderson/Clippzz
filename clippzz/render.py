"""Turns an edit plan into a finished vertical clip with ffmpeg.

Every time in a plan is a section time: seconds from the start of the
downloaded section. The renderer keeps only the beats, joins them with hard
cuts, crops each beat to 9:16 around its focus point (or stacks two crops),
then lays the speech captions, stage directions, colour hits, emoji
stickers, sound effects, music and an optional freeze-frame ending on the
joined timeline (output time).
"""
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import emoji, sounds

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
# "win": the frame held green while someone comes out on top (the owner asked
# for the screen to go green when Kai got his plate back). "bw" is its opposite.
HIT_MIX["win"] = HIT_MIX["green"]
HOLDS = {"bw", "win"}
# Tints for the freeze-frame ending ("warm" is the classic sepia look).
TINT_MIX = {
    "warm": "rr=0.393:rg=0.769:rb=0.189:gr=0.349:gg=0.686:gb=0.168:br=0.272:bg=0.534:bb=0.131",
    "bw": HIT_MIX["bw"],
    "red": HIT_MIX["red"],
    "green": HIT_MIX["green"],
}
MUSIC_LEVEL = 0.2          # music bed, before it dips under speech
SOUND_MAX_S = 4.0          # a sound effect never runs longer than this
MAX_CAPTION_S = 1.2        # a speech caption never lingers longer than this
ENDING_EMOJI_POS = (W // 2, 820)

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
    layout: str = "single"          # or "stack": top_box over bottom_box
    top_box: list | None = None     # [left, top, right, bottom], 0 to 1
    bottom_box: list | None = None
    focus_x_end: float | None = None  # where the window has panned to by the beat's end
    focus_y_end: float | None = None

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


@dataclass
class Sticker:
    start: float
    end: float
    char: str
    size: int
    x: int  # top-left corner in the output frame
    y: int


# ---------- timeline ----------

def build_timeline(beats: list[dict]) -> list[Beat]:
    """Beats in plan order, each trimmed to a whole number of frames, placed
    end to end on the output timeline."""
    out, t = [], 0.0
    for b in beats:
        dur = round((b["end_s"] - b["start_s"]) * FPS) / FPS
        if dur < 2 / FPS:
            continue
        stack = b.get("layout") == "stack" and b.get("top_box") and b.get("bottom_box")
        out.append(Beat(b["start_s"], b["start_s"] + dur, round(t, 4),
                        b.get("focus_x", 0.5), b.get("focus_y", 0.45), b.get("zoom", 1.0),
                        "stack" if stack else "single", b.get("top_box"), b.get("bottom_box"),
                        b.get("focus_x_end"), b.get("focus_y_end")))
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
        end = min(end, b.out_e, start + MAX_CAPTION_S)
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
        length = min(max(length, 0.4), 2.5) if kind in HOLDS else min(max(length or 0.15, 0.08), 0.25)
        flashes = [o for o in out if o.kind not in HOLDS]
        if kind not in HOLDS and flashes and t - flashes[-1].start < 0.4:
            continue
        out.append(Hit(round(t, 3), round(min(t + length, end_all), 3), kind))
    return out


def sound_cues(cues: list[dict], beats: list[Beat], names) -> list[Cue]:
    out = []
    for c in sorted(cues, key=lambda c: c["at_s"]):
        if c.get("name") not in names:
            continue
        t = to_out(c["at_s"], beats, snap="next")
        if t is not None and t < total(beats) - 0.1:
            out.append(Cue(round(t, 3), c["name"]))
    return out


# ---------- framing and stickers ----------

def _even(v: float) -> int:
    return max(2, int(v) // 2 * 2)


def crop_box(src_w: int, src_h: int, fx: float, fy: float, zoom: float) -> tuple[int, int, int, int]:
    """A 9:16 window inside the source, centred on the focus point where possible."""
    zoom = min(max(zoom, 1.0), 3.0)
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


def crop_filter(b: Beat, src_w: int, src_h: int) -> str:
    """The crop for a single-layout beat: still, or panning in a straight line
    from the start focus to the end focus over the beat."""
    cw, ch, x0, y0 = crop_box(src_w, src_h, b.focus_x, b.focus_y, b.zoom)
    fx_end = b.focus_x if b.focus_x_end is None else b.focus_x_end
    fy_end = b.focus_y if b.focus_y_end is None else b.focus_y_end
    _, _, x1, y1 = crop_box(src_w, src_h, fx_end, fy_end, b.zoom)
    if abs(x1 - x0) < 4 and abs(y1 - y0) < 4:
        return f"crop={cw}:{ch}:{x0}:{y0}"
    dur = max(b.dur, 0.1)
    # t runs from 0 to the beat's length, so t/dur goes 0 to 1 (no comma needed).
    return f"crop=w={cw}:h={ch}:x='{x0}+{x1 - x0}*t/{dur:.3f}':y='{y0}+{y1 - y0}*t/{dur:.3f}'"


def box_window(src_w: int, src_h: int, box, aspect: float) -> tuple[int, int, int, int]:
    """The smallest window of the given width/height ratio that covers a box
    ([left, top, right, bottom], 0 to 1) with a little margin."""
    l, t, r, b = box
    bw, bh = (r - l) * src_w, (b - t) * src_h
    w = max(bw, bh * aspect) * 1.08
    h = w / aspect
    if h > src_h:
        h, w = src_h, src_h * aspect
    if w > src_w:
        w, h = src_w, src_w / aspect
    w, h = _even(w), _even(h)
    x = min(max((l + r) / 2 * src_w - w / 2, 0), src_w - w)
    y = min(max((t + b) / 2 * src_h - h / 2, 0), src_h - h)
    return w, h, int(x), int(y)


PANEL_H = H // 2


def panels(beat: Beat, src_w: int, src_h: int) -> list[tuple[tuple[int, int, int, int], int, int]]:
    """(crop window, output y, output height) for each picture in the beat."""
    if beat.layout == "stack":
        return [(box_window(src_w, src_h, beat.top_box, W / PANEL_H), 0, PANEL_H),
                (box_window(src_w, src_h, beat.bottom_box, W / PANEL_H), PANEL_H, PANEL_H)]
    return [(crop_box(src_w, src_h, beat.focus_x, beat.focus_y, beat.zoom), 0, H)]


def map_point(beat: Beat, x: float, y: float, src_w: int, src_h: int) -> tuple[int, int]:
    """A point on the source frame (0 to 1) -> pixel in the output frame."""
    sx, sy = x * src_w, y * src_h
    options = panels(beat, src_w, src_h)
    for (cw, ch, cx, cy), oy, oh in options:
        if cx <= sx <= cx + cw and cy <= sy <= cy + ch:
            return int((sx - cx) / cw * W), int(oy + (sy - cy) / ch * oh)
    (cw, ch, cx, cy), oy, oh = options[0]  # outside the crop: keep it on screen
    return (int(min(max((sx - cx) / cw, 0.1), 0.9) * W),
            int(oy + min(max((sy - cy) / ch, 0.1), 0.9) * oh))


def sticker_events(stickers: list[dict], beats: list[Beat], src_w: int, src_h: int) -> list[Sticker]:
    out = []
    for st in sorted(stickers, key=lambda s: s["start_s"]):
        if st.get("emoji") not in emoji.CHOICES:
            continue
        start = to_out(st["start_s"], beats, snap="next")
        if start is None:
            continue
        end = to_out(st["end_s"], beats, snap="prev")
        if end is None or end - start < 0.5:
            end = start + 1.2
        end = min(end, total(beats))
        bi = beat_at(st["start_s"], beats)
        beat = beats[bi] if bi is not None else beats[min(range(len(beats)), key=lambda i: abs(beats[i].out_s - start))]
        size = emoji.SIZES.get(st.get("size"), emoji.SIZES["medium"])
        px, py = map_point(beat, float(st.get("x", 0.5)), float(st.get("y", 0.5)), src_w, src_h)
        x = min(max(px - size // 2, 0), W - size)
        y = min(max(py - size // 2, 0), H - size)
        out.append(Sticker(round(start, 3), round(end, 3), st["emoji"], size, x, y))
    return out


# ---------- ffmpeg ----------

AUDIO_FMT = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"


def source_at(t: float, src) -> tuple[Path, float]:
    """Which file holds time t of the clip's footage, and where in that file.
    src is one file, or [(file, at, dur)] for footage joined from several parts
    of the stream, each starting at "at" on the footage's timeline."""
    if isinstance(src, (str, Path)):
        return Path(src), t
    for path, at, dur in src:
        if t < at + dur - 1e-3:
            return Path(path), max(t - at, 0.0)
    path, at, _ = src[-1]
    return Path(path), max(t - at, 0.0)


def ffmpeg_command(src, beats: list[Beat], hits: list[Hit], cues: list[Cue],
                   sound_files: dict[str, tuple[Path, float]], ass_path: Path, out_path: Path,
                   src_w: int, src_h: int, stickers: list[tuple[Sticker, Path]] = (),
                   freeze_s: float = 0.0, tint: str = "none",
                   music: tuple[Path, float] | None = None) -> list[str]:
    """src: the footage, one file or parts (see source_at); sound_files: name ->
    (file, mix volume); stickers: (sticker, png); music: (file, volume) or None."""
    cmd = ["ffmpeg", "-v", "error", "-y"]
    end_beats = total(beats)
    end_all = end_beats + freeze_s
    for b in beats:
        path, local = source_at(b.src_s, src)
        cmd += ["-ss", f"{local:.3f}", "-t", f"{b.dur + 0.1:.3f}", "-i", str(path)]
    n = len(beats)
    for c in cues:
        cmd += ["-i", str(sound_files[c.name][0])]
    sticker_base = n + len(cues)
    for _, png in stickers:
        cmd += ["-loop", "1", "-framerate", str(FPS), "-t", f"{end_all:.3f}", "-i", str(png)]
    music_input = sticker_base + len(stickers)
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music[0])]

    graph = []
    for i, b in enumerate(beats):
        trim = f"[{i}:v]trim=duration={b.dur:.4f},setpts=PTS-STARTPTS"
        if b.layout == "stack":
            (tw, th, tx, ty), _, _ = panels(b, src_w, src_h)[0]
            (bw, bh, bx, by), _, _ = panels(b, src_w, src_h)[1]
            graph.append(f"{trim},split=2[pt{i}][pb{i}]")
            graph.append(f"[pt{i}]crop={tw}:{th}:{tx}:{ty},scale={W}:{PANEL_H}:flags=bicubic[qt{i}]")
            graph.append(f"[pb{i}]crop={bw}:{bh}:{bx}:{by},scale={W}:{PANEL_H}:flags=bicubic[qb{i}]")
            graph.append(f"[qt{i}][qb{i}]vstack=inputs=2,setsar=1,fps={FPS},format=yuv420p[v{i}]")
        else:
            graph.append(f"{trim},{crop_filter(b, src_w, src_h)},scale={W}:{H}:flags=bicubic,setsar=1,"
                         f"fps={FPS},format=yuv420p[v{i}]")
        graph.append(f"[{i}:a]{AUDIO_FMT},atrim=duration={b.dur:.4f},asetpts=PTS-STARTPTS,"
                     f"apad=whole_dur={b.dur:.4f},afade=t=in:d=0.01,"
                     f"afade=t=out:st={max(b.dur - 0.02, 0):.4f}:d=0.02[a{i}]")
    graph.append("".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[vc][ac]")

    # Video: freeze the last frame, colour hits and the ending tint, stickers, captions.
    looks = []
    if freeze_s > 0:
        looks.append(f"tpad=stop_mode=clone:stop_duration={freeze_s:.3f}")
    looks += [f"colorchannelmixer={HIT_MIX[h.kind]}:enable='between(t,{h.start:.3f},{h.end:.3f})'"
              for h in hits]
    if freeze_s > 0 and tint in TINT_MIX:
        looks.append(f"colorchannelmixer={TINT_MIX[tint]}:enable='gte(t,{end_beats:.3f})'")
    cur = "[vc]"
    if looks:
        graph.append(f"[vc]{','.join(looks)}[vl]")
        cur = "[vl]"
    for j, (st, _) in enumerate(stickers):
        fade_out = max(st.end - 0.12, st.start)
        graph.append(f"[{sticker_base + j}:v]format=rgba,fade=t=in:st={st.start:.3f}:d=0.12:alpha=1,"
                     f"fade=t=out:st={fade_out:.3f}:d=0.12:alpha=1[e{j}]")
        graph.append(f"{cur}[e{j}]overlay=x={st.x}:y={st.y}:"
                     f"enable='between(t,{st.start:.3f},{st.end:.3f})'[o{j}]")
        cur = f"[o{j}]"
    graph.append(f"{cur}ass={ass_path},format=yuv420p[vout]")

    # Audio: stream audio (silent through the freeze), music that dips under
    # it, and the sound effects on top.
    voice = "[ac]"
    if freeze_s > 0:
        graph.append(f"[ac]apad=whole_dur={end_all:.3f}[acp]")
        voice = "[acp]"
    mix = []
    if music:
        graph.append(f"{voice}asplit=2[vx][vk]")
        graph.append(f"[{music_input}:a]{AUDIO_FMT},atrim=duration={end_all:.3f},asetpts=PTS-STARTPTS,"
                     f"afade=t=in:d=0.6,afade=t=out:st={max(end_all - 1.5, 0):.3f}:d=1.5,"
                     f"volume={music[1]:.3f}[mus]")
        graph.append("[mus][vk]sidechaincompress=threshold=0.025:ratio=10:attack=10:release=400[musd]")
        mix += ["[vx]", "[musd]"]
    else:
        mix.append(voice)
    for j, c in enumerate(cues):
        ms = int(round(c.at * 1000))
        # Long files (a theme song sting) play their first few seconds, faded out.
        cap = max(min(SOUND_MAX_S, end_all - c.at), 0.3)
        graph.append(f"[{n + j}:a]{AUDIO_FMT},atrim=duration={cap:.3f},"
                     f"afade=t=out:st={max(cap - 0.3, 0):.3f}:d=0.3,"
                     f"volume={sound_files[c.name][1]:.3f},adelay=delays={ms}:all=1[s{j}]")
        mix.append(f"[s{j}]")
    if len(mix) > 1:
        graph.append("".join(mix) + f"amix=inputs={len(mix)}:normalize=0:duration=first:"
                     "dropout_transition=0[am]")
        final = "[am]"
    else:
        final = mix[0]
    # Loud like the reference Shorts, with a ceiling so nothing clips.
    # The limiter catches the peaks loudnorm lets through when effects stack up.
    graph.append(f"{final}loudnorm=I=-11:TP=-1.5:LRA=11,aresample=48000,"
                 "alimiter=limit=0.84:attack=2:release=50:level=disabled[aout]")

    cmd += ["-filter_complex", ";".join(graph), "-map", "[vout]", "-map", "[aout]",
            "-t", f"{end_all:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
            "-r", str(FPS), "-g", str(FPS * 2), "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart", str(out_path)]
    return cmd




def render(src, plan: dict, words, out_path: Path, src_w: int, src_h: int,
           loud=None, timeout: int = 1800) -> dict:
    """Renders the plan to out_path from src, one file or parts (see
    source_at). Returns what was placed, for the record."""
    beats = build_timeline(plan["beats"])
    if not beats:
        raise RuntimeError("the plan has no usable beats")
    fixes = [(f["heard"], f["write"]) for f in plan.get("caption_fixes", []) if f.get("heard")]
    speech = speech_events(apply_fixes(words, fixes), beats, loud=loud)
    stage = stage_events(plan.get("stage_directions", []), beats)
    events = place(speech, stage)
    hits = hit_events(plan.get("hits", []), beats)
    names = set(sounds.choices())
    cues = sound_cues(plan.get("sounds", []), beats, names)
    work = out_path.parent
    stickers = sticker_events(plan.get("stickers", []), beats, src_w, src_h)

    ending = plan.get("ending") or {}
    freeze = float(ending.get("freeze_s") or 0)
    end_beats = total(beats)
    if freeze > 0:
        if ending.get("emoji") in emoji.CHOICES:
            size = emoji.SIZES["large"]
            stickers.append(Sticker(round(end_beats, 3), round(end_beats + freeze, 3), ending["emoji"], size,
                                    ENDING_EMOJI_POS[0] - size // 2, ENDING_EMOJI_POS[1] - size // 2))
        if ending.get("sound") in names:
            cues.append(Cue(round(end_beats, 3), ending["sound"]))

    ass_path = work / "captions.ass"
    ass_path.write_text(ass_document(events), encoding="utf-8")
    files = sounds.ensure([c.name for c in cues], work / "sounds")
    sound_files = {name: (path, sounds.volume(name, path)) for name, path in files.items()}
    sticker_pngs = [(st, emoji.png(st.char, st.size, work / "emoji")) for st in stickers]

    music, credit = None, ""
    track = sounds.music().get(plan.get("music") or "")
    if track:
        music = (track, round(MUSIC_LEVEL * sounds.volume("music", track) / 10 ** (-4 / 20), 3))
        credit = sounds.note(track)

    cmd = ffmpeg_command(src, beats, hits, cues, sound_files, ass_path, out_path, src_w, src_h,
                         sticker_pngs, freeze, ending.get("tint", "none"), music)
    r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if r.returncode != 0 or not out_path.exists():
        raise RuntimeError("render failed: " + r.stderr.decode(errors="replace")[-600:])
    return {"duration_s": round(end_beats + freeze, 2), "beats": len(beats), "captions": len(speech),
            "stage": len(stage), "hits": len(hits), "sounds": len(cues), "stickers": len(stickers),
            "music": plan.get("music") if track else None, "music_credit": credit}
