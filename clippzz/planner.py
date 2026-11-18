"""The edit planner: a Claude model watches one moment frame by frame, with a
word-timed transcript, and writes the edit in the ClippingMania format
(docs/STYLE.md). The renderer then follows the plan exactly."""
import base64
import logging

from . import config, ranker, sounds

log = logging.getLogger("clippzz.planner")

COLOR_NAMES = ["white", "yellow", "cyan", "pink", "red"]
HIT_KINDS = ["green", "red", "white", "bw"]

SYSTEM = f"""\
You edit vertical Shorts for a clip channel that copies the format of \
ClippingMania, a channel whose Shorts of Kai Cenat, IShowSpeed and friends \
get 5 to 24 million views. You receive one moment from a livestream: frames \
twice a second, a word-by-word transcript and the loudest half-seconds. You \
return an edit plan. A program renders it exactly as written, so every time \
you give must be right.

The format, measured from the channel's biggest hits:

1. One bit, told in beats. A single premise that a stranger gets with the \
sound off: he shaves his own head; the Iron Man mask will not open; a kid on \
a scooter calls him trash; he grabs the plate and runs. No long story, no \
context, no lead-in.

2. 12 to 25 seconds in total; 14 to 20 is the sweet spot. Never over 26.

3. Chopped hard. Keep only the beats that tell the bit: setup, the action \
step by step, the reaction, the payoff. A beat is one continuous stretch of \
the source, usually 0.6 to 2.5 seconds, so there is a cut every 1.5 to 2.5 \
seconds on average. Everything between beats is dropped: pauses, walking or \
travel (keep a short glimpse of it at most), repeated lines, reading chat, \
crosstalk that adds nothing. Beats stay in the order they happened. The source \
may switch cameras by itself; that is fine inside a beat.

4. Open on the hook: the first beat is the setup line or the action already \
under way, inside the first second, on real camera footage. Never open on a \
gameplay screen with a small facecam: if the setup is only said there, start \
on the first real-camera beat and let a stage direction carry the setup \
instead. End on the payoff or the biggest reaction, then stop. No wind-down.

5. Stage directions tell the story. A beat without speech gets a short \
caption in asterisks that narrates it, often in meme slang; this is where most \
of the jokes are. From the hits: *SHAVING CREAM* -> *APPLIES* -> *SHAVES HEAD* \
-> *REGRET* -> *CRIES*; *RIDING* -> *FALLS* -> *SHOE COOKED*; *AURA LOSS* each \
time the mask fails to open; *GETS FAT*, then *SKINNY* / *FAT* as he flips \
back and forth; *LOOKS FOR PHONE*. One to three words, true to what the frames \
show: exaggerating in slang is good, inventing events is not. At least half \
of them should be jokes, a reaction or verdict in meme slang (*REGRET*, \
*AURA LOSS*, *SHOE COOKED*, *VIOLATED*, *HE KNEW*), not plain descriptions \
like *WALKS* or *EATING*. Colour: white for \
plain actions, pink or red for emotion, shame or loss, yellow or cyan for hype. \
Put them on beats without speech where you can. Give each a time range inside \
the beats you keep.

6. Speech is captioned automatically from the speech-to-text transcript, one \
or two words at a time, so you do not write speech captions. Under \
caption_fixes, correct words or phrases it misheard: "heard" is the exact \
words as they appear in that transcript, "write" is what was said (for \
example "you're my food bank" -> "give me my food back", or "Chai" -> "Kai"). \
YouTube's own captions are given as a second opinion; fix only where the two \
transcripts or the context make the right words clear. Never put words in \
anyone's mouth.

7. Colour hits: one or two frames of solid green, red or white tint on a punch \
word, an impact or the cut into a reaction (kind green, red or white, length \
0.1 to 0.2 s), and black-and-white (kind bw, 0.5 to 2 s) for a loss, a fail or \
an *AURA LOSS* beat. About one every 2 seconds, never two flashes within half a \
second.

8. Sound stings, 3 to 6 per clip, placed on hits, from this library only: \
{sounds.describe()}. They play over the stream audio.

9. Framing. The output is 9:16, cut out of the source frame. For each beat give \
focus_x and focus_y (0 to 1 across and down the source frame) on what the \
viewer must see in that beat, usually faces or the action, and a zoom: 1.0 \
shows the full source height (on a 16:9 source the window is about a third of \
the frame's width), 1.2 to 1.6 is a punch-in for a reaction or a detail. Aim \
the window where the action is in each beat. Gameplay screens with small \
facecams look poor at 9:16, so prefer real camera footage, and use a gameplay \
beat only if the bit cannot be told without it.

10. Title in the channel's pattern: the claim in capitals, three exclamation \
marks, one or two emoji, then the creators' hashtags. For example "MARLON \
GOES BALD!!! 😂🤯 #marlon" or "DUKE DENNIS GETS CALLED TRASH BY A LITTLE KID AT \
THE SKATE PARK!!! 😂 #dukedennis". No invented quotes or events.

Times: every time is in seconds from the start of the section, to two \
decimals. Put beat edges in the gaps between words, not mid-word, using the \
transcript times, and check each beat against the frames so it shows what you \
say it shows. Under fit, say honestly whether this moment suits the format \
(strong, ok or weak) and why; make the best plan you can either way. No slurs \
in stage directions or the title.
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "premise": {"type": "string", "description": "The bit in one sentence, as you would pitch it."},
        "fit": {"type": "string", "enum": ["strong", "ok", "weak"]},
        "fit_reason": {"type": "string"},
        "title": {"type": "string"},
        "beats": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "start_s": {"type": "number"},
                "end_s": {"type": "number"},
                "shows": {"type": "string", "description": "What this beat shows, in a few words."},
                "focus_x": {"type": "number"},
                "focus_y": {"type": "number"},
                "zoom": {"type": "number"},
            },
            "required": ["start_s", "end_s", "shows", "focus_x", "focus_y", "zoom"],
            "additionalProperties": False}},
        "stage_directions": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "start_s": {"type": "number"},
                "end_s": {"type": "number"},
                "text": {"type": "string"},
                "color": {"type": "string", "enum": COLOR_NAMES},
            },
            "required": ["start_s", "end_s", "text", "color"],
            "additionalProperties": False}},
        "hits": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "at_s": {"type": "number"},
                "kind": {"type": "string", "enum": HIT_KINDS},
                "length_s": {"type": "number"},
            },
            "required": ["at_s", "kind", "length_s"],
            "additionalProperties": False}},
        "sounds": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "at_s": {"type": "number"},
                "name": {"type": "string", "enum": sounds.NAMES},
            },
            "required": ["at_s", "name"],
            "additionalProperties": False}},
        "caption_fixes": {"type": "array", "items": {
            "type": "object",
            "properties": {"heard": {"type": "string"}, "write": {"type": "string"}},
            "required": ["heard", "write"],
            "additionalProperties": False}},
    },
    "required": ["premise", "fit", "fit_reason", "title", "beats", "stage_directions",
                 "hits", "sounds", "caption_fixes"],
    "additionalProperties": False,
}


def format_words(words) -> str:
    """Transcript lines like "[12.30-14.05] I(12.30) am(12.51) fat.(12.80)"."""
    lines, cur = [], []
    for ws, we, w in words:
        if cur and (ws - cur[-1][1] > 0.6 or len(cur) >= 12):
            lines.append(cur)
            cur = []
        cur.append((ws, we, w))
    if cur:
        lines.append(cur)
    return "\n".join(f"[{l[0][0]:.2f}-{l[-1][1]:.2f}] " + " ".join(f"{w}({ws:.2f})" for ws, _, w in l)
                     for l in lines) or "(no speech found)"


def format_ranges(times: list[float], step: float = 0.5) -> str:
    """[1.0, 1.5, 2.0, 7.5] -> "1.0-2.5, 7.5-8.0"."""
    out, start, prev = [], None, None
    for t in sorted(times):
        if start is None:
            start = prev = t
        elif t - prev <= step + 1e-6:
            prev = t
        else:
            out.append(f"{start:.1f}-{prev + step:.1f}")
            start = prev = t
    if start is not None:
        out.append(f"{start:.1f}-{prev + step:.1f}")
    return ", ".join(out) or "(none stand out)"


def plan_clip(meta: dict, moment: dict, frames, words, loud_times, duration: float,
              src_w: int, src_h: int, yt_lines=None):
    """Returns (plan, input_tokens, output_tokens)."""
    intro = (
        f"Streamer or channel: {meta.get('channel') or 'unknown'}\n"
        f"Stream title: {meta.get('title') or 'unknown'}\n\n"
        f"The reviewer's notes on this moment:\n"
        f"Working title: {moment.get('title')}\n"
        f"Joke: {moment.get('joke')}\n"
        f"On screen: {moment.get('visual')}\n\n"
        f"This section of the stream is {duration:.1f} seconds long; every time below is "
        f"seconds from its start. The source frame is {src_w}x{src_h}.\n\n"
        f"Transcript, word by word with start times (speech-to-text, may mishear names):\n"
        f"{format_words(words)}\n\n"
        f"YouTube's own auto-captions for the same stretch (another machine transcript, "
        f"times approximate):\n"
        + ("\n".join(f"[{t:.1f}] {text}" for t, text in (yt_lines or [])) or "(none)") + "\n\n"
        f"Loudest stretches (shouting, impacts): {format_ranges(loud_times)}\n\n"
        f"Frames, two per second:"
    )
    content = [{"type": "text", "text": intro}]
    for t, jpeg in frames:
        content.append({"type": "text", "text": f"t={t:.1f}"})
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg",
            "data": base64.standard_b64encode(jpeg).decode()}})
    content.append({"type": "text", "text": "Write the edit plan."})

    # Streaming, because thinking plus a long plan can take a few minutes.
    # Thinking counts toward max_tokens, so the limit leaves it plenty of room.
    with ranker.client().beta.messages.stream(
        model=config.PLANNER_MODEL,
        max_tokens=64000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=SYSTEM,
        thinking={"type": "adaptive"},
        output_config={"effort": config.PLANNER_EFFORT,
                       "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": content}],
    ) as stream:
        response = stream.get_final_message()
    plan = ranker._parse(response, "planner")
    return plan, *ranker._usage(response)


def _clamp(v, lo, hi):
    return min(max(float(v), lo), hi)


def tidy(plan: dict, words, duration: float) -> dict:
    """Keeps beats inside the section, in order, not overlapping, and with
    their edges moved out of the middle of words."""
    beats = []
    for b in sorted(plan.get("beats", []), key=lambda b: b["start_s"]):
        s = _clamp(b["start_s"], 0.0, duration)
        e = _clamp(b["end_s"], 0.0, duration)
        for ws, we, _ in words:
            if ws < s < we:  # starts mid-word: take the whole word, or none of it
                s = max(ws - 0.03, 0.0) if s - ws < we - s else we + 0.01
            if ws < e < we:  # ends mid-word
                e = min(we + 0.05, duration) if we - e < e - ws else ws - 0.01
        if beats and s < beats[-1]["end_s"]:
            s = beats[-1]["end_s"]
        if e - s < 0.25:
            continue
        beats.append({**b, "start_s": round(s, 3), "end_s": round(e, 3),
                      "focus_x": _clamp(b.get("focus_x", 0.5), 0.0, 1.0),
                      "focus_y": _clamp(b.get("focus_y", 0.45), 0.0, 1.0),
                      "zoom": _clamp(b.get("zoom", 1.0), 1.0, 1.8)})
    plan["beats"] = beats
    return plan
