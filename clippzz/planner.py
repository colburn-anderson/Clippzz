"""The edit planner: a Claude model watches one moment frame by frame, with a
word-timed transcript, and writes the edit in the style of the reference
channels (docs/STYLE.md). The renderer then follows the plan exactly."""
import base64
import logging

from . import config, emoji, ranker, sounds

log = logging.getLogger("clippzz.planner")

COLOR_NAMES = ["white", "yellow", "cyan", "pink", "red"]
HIT_KINDS = ["green", "red", "white", "bw", "win"]
TINTS = ["none", "warm", "bw", "red", "green"]

SYSTEM = """\
You edit vertical Shorts for a clip channel modelled on ClippingMania and \
SpeedKaiClip, channels whose Shorts of IShowSpeed, Kai Cenat and friends get \
millions of views (one has 117 million). You receive one moment from a \
livestream: frames (two a second during the moment, one a second around it), \
a word-by-word transcript and the loudest stretches. You return an edit plan. \
A program renders it exactly as written, so every time you give must be right.

The format, measured from those channels' biggest hits:

1. One bit, told in beats. A single premise a stranger gets fast: he shaves \
his own head; the Iron Man mask will not open; a kid calls him trash; he grabs \
Kai's plate and runs. No long story, no context dump.

2. 12 to 28 seconds; long enough to show how it ends, never longer than that.

3. Chopped hard. Keep only the beats that tell the bit: setup, the action \
step by step, the reaction, the payoff. A beat is one continuous stretch of \
the source, usually 0.6 to 2.5 seconds, so there is a cut every 1.5 to 2.5 \
seconds on average. Drop everything else: pauses, walking or travel (a short \
glimpse at most), repeated lines, reading chat, crosstalk that adds nothing. \
Beats stay in the order they happened. The source may switch cameras by \
itself; that is fine inside a beat.

4. Open with the setup. If someone says the setup ("I'm about to run all the \
way to your room to take a bite"), that line is the first beat, even if it is \
only said on a facecam over gameplay: frame the facecam, or stack two cams. \
Viewers must know what is happening and why within two seconds. The owner \
preferred the version that kept the spoken setup over one that opened on the \
action with a stage direction.

5. Show how it ends. Viewers want the resolution: does he get away with it, \
does the other person get it back, what is the last reaction. The section \
runs on for about a minute after the moment for this reason. If the \
resolution comes later, jump to it with a hard cut and end on it. Never stop \
in the middle of the chase.

6. Stage directions tell the story. A beat without speech gets a short \
caption in asterisks that narrates it, often in meme slang; this is where many \
of the jokes are. From the hits: *SHAVING CREAM* -> *APPLIES* -> *SHAVES HEAD* \
-> *REGRET* -> *CRIES*; *RIDING* -> *FALLS* -> *SHOE COOKED*; *AURA LOSS*; \
*SPAM CHAT*; *SEARCH*; *TEXT TO VOICE*; *LAUGH*; *CONFUSED*. One to three \
words, true to what the frames show: exaggerating in slang is good, inventing \
events is not. At least half should be jokes (*REGRET*, *AURA LOSS*, \
*VIOLATED*, *HE KNEW*, *FOOD CRITIC*), not plain descriptions. Colour: white \
for plain actions, pink or red for emotion, shame or loss, yellow or cyan for \
hype. Put them on beats without speech where you can, with a time range \
inside the beats you keep.

7. Speech is captioned automatically, one or two words at a time, from the \
transcript below, so you do not write speech captions. That transcript \
already merges two machine transcripts; where they disagreed it is listed. \
Read every line a viewer will see and fix what is still wrong under \
caption_fixes: "heard" is the exact words as they appear in the transcript, \
"write" is what was said. People on stream repeat themselves, so a line \
shouted five times should be captioned the same way each time unless it \
clearly changed. A couple of small errors is tolerable; a wrong line is not. \
Never put words in anyone's mouth.

8. Colour hits: one or two frames of solid green, red or white tint on a punch \
word, an impact or the cut into a reaction (kind green, red or white, length \
0.1 to 0.2 s); black-and-white held 0.5 to 2 s (kind bw) for a loss or a fail; \
and green held 0.5 to 2 s (kind win) when someone wins, gets it back or comes \
out on top. The owner wants the ending to land hard: put a hold and a sound on \
the payoff. About one effect every 2 seconds, never two flashes within half a \
second.

9. Sound effects: SOUND_LINE

10. Music: MUSIC_LINE

11. Emoji stickers, 0 to 3 per clip, sit on the joke for 0.8 to 2.5 s: 💀 on \
the thing that is funny, 😭 or 😂 on a reaction, 🤭 or 🫢 covering something \
awkward. Place each by x and y on the source frame (0 to 1 across and down); \
the program moves it into the crop. Size small, medium or large.

12. Ending: optionally hold the last frame for 1.5 to 3 s with a tint, a big \
emoji and a sound, the way one 53-million-view Short ends on a warm freeze \
with 💀. Use it when the last moment is a punchline worth holding; otherwise \
freeze_s 0.

13. Framing. The output is 9:16, cut out of the source frame. Layout "single": \
give focus_x and focus_y (0 to 1 across and down the source frame) on what the \
viewer must see, and a zoom: 1.0 shows the full source height (on a 16:9 \
source about a third of its width), 1.2 to 1.6 is a punch-in, up to 3 to fill \
the frame with a small facecam. Layout "stack": two crops, one above the \
other, each half the frame: give top_box and bottom_box as [left, top, right, \
bottom] (0 to 1) around each face or action. Use stack when two people react \
on two cams (both facecams over gameplay), or for a reaction plus what they \
react to.

14. Title in the channels' pattern: the claim in capitals, three exclamation \
marks, one or two emoji, then the creators' hashtags. For example "MARLON \
GOES BALD!!! 😂🤯 #marlon". No invented quotes or events.

Times: every time is in seconds from the start of the section, to two \
decimals. Put beat edges in the gaps between words, using the transcript \
times, and check each beat against the frames. Under fit, say honestly \
whether this moment suits the format (strong, ok or weak) and why; make the \
best plan you can either way. No slurs in stage directions or the title.
"""


def system_prompt(sound_names: list[str], music_names: list[str]) -> str:
    if sound_names:
        sound_line = (
            "the owner's library of classic streamer meme sounds. These short sounds are "
            "spammed in this kind of video, so use several per clip (usually 4 to 8, never "
            "fewer than one), and the same sound may come back when the context fits: "
            f"{'; '.join(sounds.label(n) for n in sound_names)}. Put each exactly where "
            "clip channels would, on a hit, a reaction or a beat change, never over an "
            "important line. Every sound must match what is on screen or what was just "
            "said, the way its description says; the owner noticed one or two sounds "
            "that felt random in an otherwise good clip, so leave a spot empty rather "
            "than force a sound in. They play on top of the stream audio; long ones are "
            "cut to 4 s.")
    else:
        sound_line = ("none available yet; leave sounds empty and use ending sound "
                      "\"none\".")
    if music_names:
        music_line = (
            "one background track from the owner's library plays quietly under the "
            "whole clip and dips under speech. Pick the one that fits the mood, or "
            f"\"none\": {'; '.join(music_names)}.")
    else:
        music_line = "none available yet; choose \"none\"."
    return SYSTEM.replace("SOUND_LINE", sound_line).replace("MUSIC_LINE", music_line)


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props),
            "additionalProperties": False}


def schema(sound_names: list[str], music_names: list[str]) -> dict:
    num, text = {"type": "number"}, {"type": "string"}
    box = {"type": "array", "items": num}
    sound_enum = sound_names or ["none"]
    return _obj({
        "premise": {"type": "string", "description": "The bit in one sentence, as you would pitch it."},
        "fit": {"type": "string", "enum": ["strong", "ok", "weak"]},
        "fit_reason": text,
        "title": text,
        "beats": {"type": "array", "items": _obj({
            "start_s": num, "end_s": num,
            "shows": {"type": "string", "description": "What this beat shows, in a few words."},
            "layout": {"type": "string", "enum": ["single", "stack"]},
            "focus_x": num, "focus_y": num, "zoom": num,
            "top_box": box, "bottom_box": box,
        })},
        "stage_directions": {"type": "array", "items": _obj({
            "start_s": num, "end_s": num, "text": text,
            "color": {"type": "string", "enum": COLOR_NAMES},
        })},
        "hits": {"type": "array", "items": _obj({
            "at_s": num, "kind": {"type": "string", "enum": HIT_KINDS}, "length_s": num,
        })},
        "sounds": {"type": "array", "items": _obj({
            "at_s": num, "name": {"type": "string", "enum": sound_enum},
        })},
        "stickers": {"type": "array", "items": _obj({
            "start_s": num, "end_s": num,
            "emoji": {"type": "string", "enum": emoji.CHOICES},
            "x": num, "y": num,
            "size": {"type": "string", "enum": list(emoji.SIZES)},
        })},
        "ending": _obj({
            "freeze_s": num,
            "tint": {"type": "string", "enum": TINTS},
            "emoji": {"type": "string", "enum": ["none", *emoji.CHOICES]},
            "sound": {"type": "string", "enum": ["none", *sound_names]},
        }),
        "music": {"type": "string", "enum": ["none", *music_names]},
        "caption_fixes": {"type": "array", "items": _obj({"heard": text, "write": text})},
    })


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
              src_w: int, src_h: int, *, moment_span=None, disagreements=(), owner_notes=(),
              sound_names=(), music_names=()):
    """Returns (plan, input_tokens, output_tokens)."""
    sound_names, music_names = list(sound_names), list(music_names)
    span = (f"The reviewer's cut of the moment runs from {moment_span[0]:.1f}s to "
            f"{moment_span[1]:.1f}s; the rest is context before it and the aftermath "
            f"after it.\n" if moment_span else "")
    diffs = "\n".join(f"  {s:.2f}-{e:.2f}s: speech-to-text heard \"{a}\", YouTube heard \"{b}\" "
                      f"(captioned as YouTube's)" for s, e, a, b in disagreements) or "  (none)"
    notes = "\n".join(f"  - {n}" for n in owner_notes) or "  (none yet)"
    intro = (
        f"Streamer or channel: {meta.get('channel') or 'unknown'}\n"
        f"Stream title: {meta.get('title') or 'unknown'}\n\n"
        f"The reviewer's notes on this moment:\n"
        f"Working title: {moment.get('title')}\n"
        f"Joke: {moment.get('joke')}\n"
        f"On screen: {moment.get('visual')}\n\n"
        f"The channel owner's feedback on earlier clips and moments (follow it):\n{notes}\n\n"
        f"This section of the stream is {duration:.1f} seconds long; every time below is "
        f"seconds from its start. The source frame is {src_w}x{src_h}.\n{span}\n"
        f"Transcript, word by word with start times:\n{format_words(words)}\n\n"
        f"Where the two machine transcripts disagreed:\n{diffs}\n\n"
        f"Loudest stretches (shouting, impacts): {format_ranges(loud_times)}\n\n"
        f"Frames:"
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
        system=system_prompt(sound_names, music_names),
        thinking={"type": "adaptive"},
        output_config={"effort": config.PLANNER_EFFORT,
                       "format": {"type": "json_schema", "schema": schema(sound_names, music_names)}},
        messages=[{"role": "user", "content": content}],
    ) as stream:
        response = stream.get_final_message()
    plan = ranker._parse(response, "planner")
    return plan, *ranker._usage(response)


def _clamp(v, lo, hi):
    try:
        return min(max(float(v), lo), hi)
    except (TypeError, ValueError):
        return lo


def _box(b) -> list[float] | None:
    if not isinstance(b, list) or len(b) != 4:
        return None
    l, t, r, bt = (_clamp(v, 0.0, 1.0) for v in b)
    return [l, t, r, bt] if r - l > 0.02 and bt - t > 0.02 else None


def tidy(plan: dict, words, duration: float) -> dict:
    """Keeps beats inside the section, in order, not overlapping, with their
    edges moved out of the middle of words and their framing in range."""
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
        top, bottom = _box(b.get("top_box")), _box(b.get("bottom_box"))
        layout = "stack" if b.get("layout") == "stack" and top and bottom else "single"
        beats.append({**b, "start_s": round(s, 3), "end_s": round(e, 3), "layout": layout,
                      "top_box": top, "bottom_box": bottom,
                      "focus_x": _clamp(b.get("focus_x", 0.5), 0.0, 1.0),
                      "focus_y": _clamp(b.get("focus_y", 0.45), 0.0, 1.0),
                      "zoom": _clamp(b.get("zoom", 1.0), 1.0, 3.0)})
    plan["beats"] = beats
    ending = plan.get("ending") or {}
    plan["ending"] = {"freeze_s": _clamp(ending.get("freeze_s", 0), 0.0, 3.5),
                      "tint": ending.get("tint") if ending.get("tint") in TINTS else "none",
                      "emoji": ending.get("emoji", "none"),
                      "sound": ending.get("sound", "none")}
    return plan
