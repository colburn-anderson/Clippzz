"""Finds the funny moments. Two passes, funny first:

1. Scout (cheap model) reads the whole transcript in ten-minute stretches and
   proposes moments. Most stretches should yield nothing.
2. Judge (strong model) looks at video frames plus the word-timed transcript
   for the best few proposals and must explain the joke in one line. If it
   cannot, the moment is dropped.
"""
import base64
import json
import logging

import anthropic

from . import config

log = logging.getLogger("clippzz.ranker")

KINDS = ["joke", "fail", "reaction", "rage", "chaos", "wholesome", "story", "awkward", "other"]
FLAGS = ["minors", "nudity", "slurs", "real_injury", "gambling", "private_info",
         "drama_named_people", "leaked_footage", "none"]

SCOUT_SYSTEM = """\
You are the first-pass scout for a clip channel. The channel turns livestream \
recordings and the creators' own highlight videos into 12 to 28 second vertical \
shorts that are genuinely funny or entertaining to people who do not follow the \
streamer. Each short is ONE bit a stranger gets fast, ideally with the sound \
off: a premise, the action, the reaction, and how it ends. A stronger \
reviewer will later watch video frames for the moments you pick, so your job \
is to find every stretch that could plausibly become a great short, without \
padding the list.

You will read one stretch of a stream's auto-generated transcript. Most \
stretches of a stream contain nothing worth clipping: setup, waiting, reading \
chat, menus, sponsor reads, repetitive gameplay. Returning an empty list for \
such a stretch is the normal, correct answer. A weak moment costs the reviewer \
time and money, and a boring clip hurts the channel more than no clip.

What makes a moment worth proposing:
- A real punchline, roast or comeback that lands.
- A fail, or a confident claim immediately followed by the opposite.
- A big, sudden reaction: screaming, rage, panic, disbelief, uncontrollable laughter.
- Chaos or absurdity: something unexpected happens and people lose it.
- A funny back-and-forth between two people, or a wholesome exchange with a funny edge.
- A short story with a clear setup and payoff, and a visible ending (who wins, \
whether he gets away with it).
- Visual comedy on a real camera beats talk over gameplay: something physical \
happens that a viewer can see.

What does not qualify: loudness with nothing behind it, hype with no payoff, \
in-jokes that need long context, plain gameplay commentary, shout-outs, \
sponsor segments, and anything where you cannot say in one sentence what a \
stranger would find funny. In a highlight video most stretches are already \
good, so be stricter there: keep the ones a stranger would send to a friend.

Reading the transcript:
- Each line starts with its time in whole seconds from the start of the video, like [5432].
- ">>" marks a change of speaker. Tags such as [screaming], [laughter], [music] \
and [applause] come from YouTube's captioning.
- A line ending in "!!" was among the loudest parts of the whole stream \
(shouting, a crowd, an impact). Treat it as a hint, not proof.
- Auto-captions mishear words and names. Read through the errors.

For each moment, give start_s and end_s in the same whole-second times. Start \
where a viewer with no context would need to start to get it, including the \
setup, and end just after the payoff or reaction lands. Aim for 15 to 75 \
seconds. Score it from 0 to 100 for how likely a stranger is to laugh or send \
it to a friend: 80 and up means you would bet on it, 50 means it could go \
either way, and below 40 is not worth proposing at all.
"""

SCOUT_SCHEMA = {
    "type": "object",
    "properties": {
        "moments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start_s": {"type": "integer"},
                    "end_s": {"type": "integer"},
                    "kind": {"type": "string", "enum": KINDS},
                    "summary": {"type": "string", "description": "What happens, in one sentence."},
                    "why_funny": {"type": "string", "description": "What a stranger would find funny, in one sentence."},
                    "score": {"type": "integer"},
                },
                "required": ["start_s", "end_s", "kind", "summary", "why_funny", "score"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["moments"],
    "additionalProperties": False,
}

JUDGE_SYSTEM = """\
You are the final reviewer for a clip channel that turns livestream recordings \
and the creators' highlight videos into 12 to 28 second vertical shorts. The owner's bar is high: \
every clip must be genuinely funny or entertaining to someone who does not \
follow the streamer. A boring moment with a good edit is a failure, and the \
channel would rather post nothing than post a weak clip.

A scout that only read the transcript proposed the moment below. You get \
frames from the video and a transcript with timestamps. Decide whether it is \
really worth making into a short.

How the finished clip will look, so you can picture it: one bit, chopped to \
its beats with every pause cut, cropped to a vertical frame (or two cameras \
stacked), speech captioned one or two words at a time, stage directions in \
meme slang (*REGRET*, *AURA LOSS*), colour flashes and classic meme sound \
effects on the hits, and an ending that shows how it resolves. The edit \
amplifies a moment that is already funny. It cannot create one.

How to judge:
- First say what the joke is in one plain sentence, as you would explain it \
to a friend. If you cannot, the verdict is "skip", however loud the moment is.
- Use the frames. Check that what is on screen supports the moment: visible \
reactions, a clear subject, something happening. Say if the moment is funny \
only as audio, or if the frames show something the transcript missed.
- verdict "post": you would bet a stranger laughs or sends it on. "maybe": \
funny to fans or with one fixable weakness. "skip": not funny enough, needs \
too much context, or the visuals do not carry it.
- score 0 to 100 on the same question. Be strict: 85 and up is a clip you \
would be proud of, 70 is solid, 50 is a coin flip.
- Set start_s and end_s for the best cut, to one decimal place, using the \
transcript times. Start exactly where the setup begins (no dead air first) \
and end just after the payoff, the reaction or the resolution lands. The raw \
window may run 15 to 70 seconds; the editor cuts it down. You may move well away from the scout's times if the real moment \
sits a little earlier or later.
- title: a short claim plus one emoji, in the style clip channels use, for \
example "Speed actually lifted a car at the end 🥶". It must be true to what \
happens. No invented quotes.
- edit_ideas: two to four specific beats with times, for example "12.5s: \
punch-in on his face as he realises" or "after 'you didn't even ask': 1s \
cutaway to a disappointed-reaction meme".
- flags: list anything that needs a human look before posting: minors on \
screen, nudity, slurs, a real injury, gambling, private information, drama \
about named people, leaked footage. Use ["none"] if nothing applies.

Auto-captions mishear words and names. Read through the errors.
"""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "joke": {"type": "string", "description": "The joke or appeal in one plain sentence."},
        "visual": {"type": "string", "description": "What the frames show and whether the visuals carry the moment."},
        "verdict": {"type": "string", "enum": ["post", "maybe", "skip"]},
        "score": {"type": "integer"},
        "start_s": {"type": "number"},
        "end_s": {"type": "number"},
        "title": {"type": "string"},
        "mood": {"type": "string", "enum": ["hype", "shock", "laughter", "disbelief", "fail", "rage", "wholesome", "awkward"]},
        "edit_ideas": {"type": "array", "items": {"type": "string"}},
        "flags": {"type": "array", "items": {"type": "string", "enum": FLAGS}},
    },
    "required": ["joke", "visual", "verdict", "score", "start_s", "end_s", "title", "mood", "edit_ideas", "flags"],
    "additionalProperties": False,
}


class RankerError(RuntimeError):
    pass


_client = None


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    return _client


def _parse(response, what: str) -> dict:
    if response.stop_reason == "refusal":
        raise RankerError(f"{what}: the model declined this content")
    if response.stop_reason == "max_tokens":
        raise RankerError(f"{what}: answer was cut off")
    text = next((b.text for b in response.content if b.type == "text"), None)
    if not text:
        raise RankerError(f"{what}: empty answer")
    return json.loads(text)


def _usage(response) -> tuple[int, int]:
    u = response.usage
    inp = (u.input_tokens or 0) + (getattr(u, "cache_read_input_tokens", 0) or 0) \
        + (getattr(u, "cache_creation_input_tokens", 0) or 0)
    return inp, u.output_tokens or 0


# ---------- pass 1: scout ----------

def loud_threshold(levels: list[float], top_fraction: float = 0.08) -> float:
    if not levels:
        return 0.0
    ordered = sorted(levels)
    return ordered[min(len(ordered) - 1, int(len(ordered) * (1 - top_fraction)))]


def chunk_lines(lines, duration_s, size=config.SCOUT_CHUNK_S, overlap=config.SCOUT_OVERLAP_S):
    """Yield (start_s, end_s, lines_in_window). Windows overlap a little so a
    moment on a boundary is seen whole by at least one window."""
    start = 0.0
    while start < duration_s:
        end = start + size
        window = [l for l in lines if start <= l[0] < end + overlap]
        if window:
            yield start, min(end + overlap, duration_s), window
        start = end


def format_lines(lines, levels=None, threshold=0.0, precise=False) -> str:
    out = []
    for i, (t, text) in enumerate(lines):
        loud = ""
        if levels:
            t_next = lines[i + 1][0] if i + 1 < len(lines) else t + 3
            a, b = int(t), min(max(int(t_next), int(t) + 1), len(levels))
            if a < len(levels) and max(levels[a:b] or [-90.0]) > threshold:
                loud = " !!"
        stamp = f"{t:.1f}" if precise else f"{int(t)}"
        out.append(f"[{stamp}] {text}{loud}")
    return "\n".join(out)


def scout_chunk(meta: dict, start_s, end_s, text: str, replay_hint: str = ""):
    """Returns (moments, input_tokens, output_tokens)."""
    user = (
        f"Streamer or channel: {meta.get('channel') or 'unknown'}\n"
        f"Video title: {meta.get('title') or 'unknown'}\n"
        f"This stretch runs from {int(start_s)}s to {int(end_s)}s of a "
        f"{int(meta.get('duration_s') or 0)}s video.{replay_hint}\n\n"
        f"Transcript:\n{text}"
    )
    response = client().messages.create(
        model=config.SCOUT_MODEL,
        max_tokens=8000,
        system=SCOUT_SYSTEM,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCOUT_SCHEMA}},
        messages=[{"role": "user", "content": user}],
    )
    moments = []
    for m in _parse(response, "scout").get("moments", []):
        s, e = float(m["start_s"]), float(m["end_s"])
        # Drop anything the model placed outside the stretch it was shown.
        if e - s < 5 or s < start_s - 5 or e > end_s + 60:
            continue
        m["start_s"], m["end_s"] = s, min(e, s + 120)
        m["score"] = max(0, min(100, int(m["score"])))
        moments.append(m)
    return moments, *_usage(response)


def dedupe(moments: list[dict]) -> list[dict]:
    """Overlapping proposals (from window overlap) collapse to the best one."""
    kept = []
    for m in sorted(moments, key=lambda m: -m["score"]):
        for k in kept:
            inter = min(m["end_s"], k["end_s"]) - max(m["start_s"], k["start_s"])
            shorter = min(m["end_s"] - m["start_s"], k["end_s"] - k["start_s"])
            if inter > 0.5 * shorter:
                break
        else:
            kept.append(m)
    return kept


# ---------- pass 2: judge ----------

def judge_moment(meta: dict, moment: dict, transcript_text: str, frames: list[tuple[float, bytes]]):
    """Returns (judgement, input_tokens, output_tokens)."""
    content = [{
        "type": "text",
        "text": (
            f"Streamer or channel: {meta.get('channel') or 'unknown'}\n"
            f"Video title: {meta.get('title') or 'unknown'}\n\n"
            f"The scout proposed {moment['start_s']:.0f}s to {moment['end_s']:.0f}s "
            f"as a \"{moment.get('scout_kind')}\" moment.\n"
            f"Scout's summary: {moment.get('scout_summary')}\n"
            f"Scout's reason: {moment.get('scout_why')}\n\n"
            f"Transcript around it (times in seconds; \"!!\" marks the loudest "
            f"parts of the stream):\n{transcript_text}\n\n"
            f"Frames from the video, in order:"
        ),
    }]
    for t, jpeg in frames:
        content.append({"type": "text", "text": f"Frame at about {t:.0f}s:"})
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg",
            "data": base64.standard_b64encode(jpeg).decode()}})
    if not frames:
        content.append({"type": "text", "text": "(No frames could be fetched. Judge from the transcript and say so under visual.)"})

    # Server-side fallback: if a safety classifier declines the request, the
    # API retries it on Anthropic's recommended fallback model in the same call.
    response = client().beta.messages.create(
        model=config.JUDGE_MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=JUDGE_SYSTEM,
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": JUDGE_SCHEMA}},
        messages=[{"role": "user", "content": content}],
    )
    j = _parse(response, "judge")
    j["score"] = max(0, min(100, int(j["score"])))
    s, e = float(j["start_s"]), float(j["end_s"])
    # Keep the cut near the proposal and a sane length even if the model drifts.
    if not (moment["start_s"] - 120 <= s < e <= moment["end_s"] + 120) or e - s < 5:
        s, e = moment["start_s"], moment["end_s"]
    j["start_s"], j["end_s"] = round(s, 1), round(min(e, s + 75), 1)
    j["flags"] = [f for f in j.get("flags", []) if f != "none"]
    return j, *_usage(response)
