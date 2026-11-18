"""Caption text from two machine transcripts.

Whisper, run on the Pi, times words precisely but mishears shouting.
YouTube's own auto-captions hear better but time words loosely. reconcile()
keeps whisper's timing, uses YouTube's words wherever the two disagree, and
reports every disagreement so the planner can settle any that are still
wrong (for example a line someone keeps repeating).
"""
import difflib
import re


def _norm(word: str) -> str:
    return re.sub(r"[^\w']", "", word.lower().replace("’", "'"))


def youtube_words(yt_words, start_s: float, duration: float) -> list[tuple[float, str]]:
    """YouTube's caption words inside the section, in section time, without
    tags such as [laughter] or [ __ ] and without speaker marks (>>)."""
    out = []
    for t, w in yt_words:
        if not start_s - 1 <= t <= start_s + duration + 1:
            continue
        w = w.replace(">>", "").strip()
        if not w or w.startswith("[") or w.endswith("]") or not _norm(w):
            continue
        out.append((round(t - start_s, 2), w))
    return out


def reconcile(words, yt, max_shift: float = 1.5):
    """words: [[start, end, text]] from whisper; yt: [(start, text)] from YouTube.
    Returns (merged words, disagreements as (start, end, whisper text, youtube text))."""
    if not yt or not words:
        return [list(w) for w in words], []
    a = [_norm(w[2]) for w in words]
    b = [_norm(w) for _, w in yt]
    out, diffs = [], []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag in ("equal", "delete"):  # words both heard, or only whisper heard
            out += [list(w) for w in words[i1:i2]]
            continue
        if tag == "insert":  # only YouTube heard them: no reliable timing, leave out
            continue
        s, e = words[i1][0], words[i2 - 1][1]
        ys, ye = yt[j1][0], yt[j2 - 1][0]
        if ys > e + max_shift or ye < s - max_shift:  # not the same moment after all
            out += [list(w) for w in words[i1:i2]]
            continue
        new = [w for _, w in yt[j1:j2]]
        step = (e - s) / len(new)
        repl = [[round(s + k * step, 3), round(s + (k + 1) * step, 3), w] for k, w in enumerate(new)]
        tail = re.match(r"^.*?([.,!?…]*)$", words[i2 - 1][2]).group(1)
        if tail and not re.search(r"[.,!?…]$", repl[-1][2]):
            repl[-1][2] += tail
        out += repl
        diffs.append((round(s, 2), round(e, 2), " ".join(w[2] for w in words[i1:i2]), " ".join(new)))
    return out, diffs
