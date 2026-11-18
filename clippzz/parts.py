"""Clips cut from several parts of one stream, such as every time a streamer
called a potato bread.

When the owner's note on an approved moment asks for other times in the
stream, two small API calls find them before anything is downloaded: the
cheap model turns the note into words to search the stream's captions for,
and the planner's model picks which hits belong in the clip. The clip's
footage is then those parts plus the moment's own section, in stream order,
laid end to end on one timeline.
"""
import gzip
import json
import math
import re

from . import config, db, ranker

MAX_EXTRA = 6                           # parts besides the moment's own
EXTRA_MIN_S, EXTRA_MAX_S = 4.0, 20.0
MAIN_BEFORE_S, MAIN_AFTER_S = 20, 40    # the moment's own part, when there are others
MERGE_GAP_S = 15                        # parts closer than this become one
MAX_TOTAL_S = 240                       # all parts together; more costs too much to plan
MAX_HITS = 30                           # caption hits shown to the model
CONTEXT_S = 15                          # caption text either side of a hit

TERMS_SYSTEM = """\
You help edit a short clip of a livestream. The channel owner approved one \
moment and wrote a note for the editor. Decide whether the note asks for \
footage from other times in the same stream, outside the moment itself: for \
example "find more times he calls it bread", "every time he says X", "show \
when it started", "add his other fails". If it does, give words to search the \
stream's auto-captions for: 1 to 4 single words or short phrases, lowercase, \
spelled the way speech-to-text writes them. If the note is only about how to \
edit the moment itself, wants_more is false and terms is empty."""

TERMS_SCHEMA = {"type": "object", "properties": {
    "wants_more": {"type": "boolean"},
    "terms": {"type": "array", "items": {"type": "string"}}},
    "required": ["wants_more", "terms"], "additionalProperties": False}

PICK_SYSTEM = """\
You pick extra footage for a short vertical clip cut from a livestream. The \
clip is built around one moment, and the channel owner's note asks for other \
times from the same stream as well. Below are the places where the stream's \
auto-captions contain the search words, each with its time in seconds from \
the start of the stream and the caption text around it (">>" marks a change \
of speaker).

Choose the hits that belong in this clip: the same running joke or story as \
the moment, with a clear line or reaction that works on screen. Always take \
the first time the joke comes up, because it sets the joke up. A line the \
streamer or a friend says beats one read out from chat or a donation. Skip \
hits that only share a word but are about something else. At most 6; if more \
qualify, keep the strongest. For each, give start_s and end_s in stream \
seconds: start 2 to 3 seconds before the key line so it is not cut off, and \
end after the line and any quick reaction, 5 to 15 seconds in all. Give an \
empty list if none belong."""

PICK_SCHEMA = {"type": "object", "properties": {"parts": {"type": "array", "items": {
    "type": "object", "properties": {
        "start_s": {"type": "number"}, "end_s": {"type": "number"}, "why": {"type": "string"}},
    "required": ["start_s", "end_s", "why"], "additionalProperties": False}}},
    "required": ["parts"], "additionalProperties": False}


def clock(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def load_lines(vid: str) -> list | None:
    """The stream's caption lines, [[start_s, text]], saved when it was scanned."""
    saved = config.SOURCES_DIR / vid / "transcript.json.gz"
    if not saved.exists():
        return None
    return json.loads(gzip.decompress(saved.read_bytes())).get("lines") or None


def search(lines, terms, avoid=None) -> list[dict]:
    """Caption lines containing any of the terms, grouped into hits when they
    are a few seconds apart, each with the caption text around it. avoid is
    (start, end) of the moment's own part, which is in the clip anyway."""
    pats = [re.compile(r"\b" + re.escape(t.strip().lower()), re.I) for t in terms if t and t.strip()]
    if not pats:
        return []
    times = sorted(float(t) for t, text in lines if any(p.search(text) for p in pats))
    if avoid:
        times = [t for t in times if not avoid[0] <= t <= avoid[1]]
    hits = []
    for t in times:
        if hits and t - hits[-1]["end"] <= 12:
            hits[-1]["end"] = t
        else:
            hits.append({"start": t, "end": t})
    for h in hits:
        h["text"] = " ".join(text for t, text in lines
                             if h["start"] - CONTEXT_S <= float(t) <= h["end"] + CONTEXT_S)
    return hits


def nearest(hits: list[dict], t: float, n: int) -> list[dict]:
    """The n hits closest in time to t, back in stream order."""
    return sorted(sorted(hits, key=lambda h: abs(h["start"] - t))[:n], key=lambda h: h["start"])


def clean(picked, main, vod_duration=None) -> list[tuple[float, float]]:
    """The model's picks as (start, end) windows of a sensible length, none
    already inside the moment's own part, nearest the moment first within the
    limits, returned in stream order."""
    out = []
    for p in picked:
        try:
            s, e = float(p["start_s"]), float(p["end_s"])
        except (KeyError, TypeError, ValueError):
            continue
        s, e = min(s, e), max(s, e)
        s = max(0.0, s)
        if vod_duration:
            e = min(e, float(vod_duration))
        e = min(max(e, s + EXTRA_MIN_S), s + EXTRA_MAX_S)
        if s >= main[0] and e <= main[1]:
            continue
        out.append((round(s, 2), round(e, 2)))
    centre = (main[0] + main[1]) / 2
    kept, total = [], main[1] - main[0]
    for s, e in sorted(out, key=lambda w: abs(w[0] - centre))[:MAX_EXTRA]:
        if total + (e - s) <= MAX_TOTAL_S:
            kept.append((s, e))
            total += e - s
    return sorted(kept)


def layout(moment_start: float, moment_end: float, extras, vod_duration=None) -> list[dict]:
    """The clip's parts in stream order: the moment's own (padded) and the
    extras, merged where they nearly touch, each starting on a keyframe
    (YouTube stream recordings have one every 2 seconds)."""
    windows = [(moment_start - MAIN_BEFORE_S, moment_end + MAIN_AFTER_S, True)]
    windows += [(s, e, False) for s, e in extras]
    merged = []
    for s, e, is_main in sorted(windows):
        s = max(0.0, math.floor(s / 2) * 2)
        if vod_duration:
            e = min(e, float(vod_duration))
        if merged and s - merged[-1]["end"] <= MERGE_GAP_S:
            merged[-1]["end"] = round(max(merged[-1]["end"], e), 2)
            merged[-1]["main"] = merged[-1]["main"] or is_main
        else:
            merged.append({"start": s, "end": round(e, 2), "main": is_main})
    return merged


def ask_terms(moment, note: str):
    user = (f"Moment: {moment['title']}\nWhat makes it funny: {moment['joke']}\n"
            f"The owner's note: {note}")
    response = ranker.client().messages.create(
        model=config.SCOUT_MODEL, max_tokens=2000, system=TERMS_SYSTEM,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": TERMS_SCHEMA}},
        messages=[{"role": "user", "content": user}])
    return ranker._parse(response, "parts"), *ranker._usage(response)


def ask_pick(vod, moment, note: str, hits: list[dict], lines, main):
    said = " ".join(text for t, text in lines if main[0] <= float(t) <= main[1])
    listed = "\n".join(f"[{i}] {h['start']:.0f} s ({clock(h['start'])}): {h['text']}"
                       for i, h in enumerate(hits, 1))
    user = (f"Stream: {vod['title'] or 'unknown'}\n"
            f"The moment ({moment['start_s']:.0f} to {moment['end_s']:.0f} s, {clock(moment['start_s'])}): "
            f"{moment['title']}\nWhat makes it funny: {moment['joke']}\n"
            f"The owner's note: {note}\n\n"
            f"What is said around the moment:\n{said[:4000]}\n\n"
            f"Hits:\n{listed}")
    response = ranker.client().messages.create(
        model=config.PLANNER_MODEL, max_tokens=8000, system=PICK_SYSTEM,
        thinking={"type": "adaptive"},
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": PICK_SCHEMA}},
        messages=[{"role": "user", "content": user}])
    return ranker._parse(response, "parts").get("parts", []), *ranker._usage(response)


def find(conn, vod, moment) -> tuple[list[dict], float]:
    """(the clip's parts, what finding them cost). The parts are [] unless the
    owner's note asks for other times in the stream and some are found.
    Costs about 5 cents when the note asks, a fraction of a cent otherwise."""
    note = (moment["note"] or "").strip()
    lines = load_lines(vod["id"]) if note else None
    if not lines:
        return [], 0.0
    db.check_budget(conn)
    want, tin, tout = ask_terms(moment, note)
    cost = db.record_usage(conn, vod["id"], "parts", config.SCOUT_MODEL, tin, tout)
    if not want.get("wants_more") or not want.get("terms"):
        return [], cost
    main = (moment["start_s"] - MAIN_BEFORE_S, moment["end_s"] + MAIN_AFTER_S)
    hits = nearest(search(lines, want["terms"], avoid=main), moment["start_s"], MAX_HITS)
    if not hits:
        return [], cost
    picked, tin, tout = ask_pick(vod, moment, note, hits, lines, main)
    cost += db.record_usage(conn, vod["id"], "parts", config.PLANNER_MODEL, tin, tout)
    extras = clean(picked, main, vod["duration_s"])
    return (layout(moment["start_s"], moment["end_s"], extras, vod["duration_s"]) if extras else []), cost
