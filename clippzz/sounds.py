"""Sound stings and background music.

The real library lives on the SSD network drive, in Clippzz/sounds and
Clippzz/music, so the owner can drop files in from the Mac. A file's name is
what the planner sees and picks from ("vine boom.mp3" -> "vine boom"), so name
each file after the meme. Inside the containers the folder is /library.

A text file next to a sound with the same name ("vine boom.txt") says what
it is for; the planner reads it. Next to a music track it holds the credit
line the track's licence asks for.

Stand-ins: a few synthesised stings exist for an empty folder, switched off
by default because the owner found them unfunny.
"""
import re
import subprocess
from functools import lru_cache
from pathlib import Path

from . import config

AUDIO_EXT = (".mp3", ".wav", ".m4a", ".ogg", ".aac", ".flac", ".opus")


def _note(freq: float, dur: float, wobble: bool = False) -> str:
    """One buzzy brass-like note with a soft attack, for the "fail" sting."""
    phase = f"2*PI*{freq}*t" + ("+2.5*sin(2*PI*5*t)" if wobble else "")
    return (f"aevalsrc=exprs='0.3*(sin({phase})+0.5*sin(2*({phase}))+0.3*sin(3*({phase})))"
            f"*(1-exp(-60*t))*exp(-1.6*t)':s=44100:d={dur}")


# name -> (filtergraph ending in [out], what it is for, mix volume)
SYNTH = {
    "boom": (
        "aevalsrc=exprs='0.95*sin(2*PI*(58*t-16*t*t))*exp(-3.2*t)"
        "+0.35*sin(2*PI*116*t)*exp(-9*t)+0.25*(2*random(0)-1)*exp(-30*t)':s=44100:d=1.1[out]",
        "payoff, reveal or big impact", 0.9),
    "hit": (
        "aevalsrc=exprs='0.8*sin(2*PI*(150*t-70*t*t))*exp(-16*t)"
        "+0.5*(2*random(0)-1)*exp(-45*t)':s=44100:d=0.35[out]",
        "punch word, slap or shove", 0.75),
    "whoosh": (
        "anoisesrc=d=0.5:c=pink:r=44100:a=0.7,highpass=f=500,lowpass=f=4500,"
        "afade=t=in:d=0.3:curve=exp,afade=t=out:st=0.3:d=0.2[out]",
        "fast movement or a cut from one place to another", 0.6),
    "ding": (
        "aevalsrc=exprs='0.55*sin(2*PI*1568*t)*exp(-4.5*t)+0.25*sin(2*PI*3136*t)*exp(-7*t)"
        "+0.12*sin(2*PI*4704*t)*exp(-11*t)':s=44100:d=1.3[out]",
        "realisation or idea", 0.5),
    "fail": (
        f"{_note(311.1, 0.3)}[a];{_note(293.7, 0.3)}[b];{_note(277.2, 0.3)}[c];"
        f"{_note(261.6, 0.9, wobble=True)}[d];[a][b][c][d]concat=n=4:v=0:a=1[out]",
        "loss or defeat (sad trombone)", 0.6),
}


def _folder(kind: str) -> dict[str, Path]:
    try:
        files = sorted(p for p in (config.LIBRARY_DIR / kind).iterdir()
                       if p.is_file() and p.suffix.lower() in AUDIO_EXT and not p.name.startswith("."))
    except OSError:
        return {}
    return {re.sub(r"\s+", " ", p.stem).strip(): p for p in files}


def library() -> dict[str, Path]:
    """The owner's meme sounds, by name."""
    return _folder("sounds")


def music() -> dict[str, Path]:
    """The owner's background tracks, by name."""
    return _folder("music")


def app_songs() -> list[dict]:
    """Commercial songs the owner adds inside Instagram or YouTube at posting
    time, where they are licensed (music/app songs.txt: song | when | part)."""
    try:
        text = (config.LIBRARY_DIR / "music" / "app songs.txt").read_text(encoding="utf-8")
    except OSError:
        return []
    songs = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        songs.append({"name": parts[0], "when": parts[1] if len(parts) > 1 else "",
                      "part": parts[2] if len(parts) > 2 else ""})
    return songs


def choices() -> list[str]:
    """Sound names the planner may use: the owner's library, else (if switched
    on) the synthesised stand-ins, else none."""
    return list(library()) or (list(SYNTH) if config.SYNTH_SOUNDS else [])


def note(path: Path) -> str:
    """The text file saved next to a sound or track, if any."""
    try:
        return path.with_suffix(".txt").read_text(encoding="utf-8").strip()[:300]
    except OSError:
        return ""


def label(name: str) -> str:
    """"vine boom (Deep boom: a stare, a reveal...)" for the planner."""
    path = library().get(name)
    text = note(path) if path else SYNTH.get(name, ("", ""))[1]
    return f"{name} ({text})" if text else name


@lru_cache(maxsize=256)
def _peak_db(path: str) -> float:
    r = subprocess.run(["ffmpeg", "-v", "info", "-nostats", "-i", path, "-af", "volumedetect",
                        "-f", "null", "-"], capture_output=True, timeout=60)
    m = re.search(r"max_volume: (-?[\d.]+) dB", r.stderr.decode(errors="replace"))
    return float(m.group(1)) if m else 0.0


def volume(name: str, path: Path | None = None) -> float:
    """Mix level: stand-ins have fixed levels; library files are evened out
    so each one peaks around -4 dBFS, whatever level it was saved at."""
    if path is None or path.name.startswith("synth-"):
        return SYNTH[name][2]
    gain_db = min(max(-4.0 - _peak_db(str(path)), -20.0), 12.0)
    return round(10 ** (gain_db / 20), 3)


def ensure(names, folder: Path | None = None) -> dict[str, Path]:
    """A file for each name: the library file, or a stand-in made once per folder."""
    lib = library()
    folder = folder or (config.WORK_DIR / "sounds")
    paths = {}
    for name in set(names):
        if name in lib:
            paths[name] = lib[name]
            continue
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"synth-{name}.wav"
        if not path.exists():
            r = subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-filter_complex", SYNTH[name][0],
                 "-map", "[out]", "-ac", "1", "-ar", "44100", str(path)],
                capture_output=True, timeout=60)
            if r.returncode != 0:
                raise RuntimeError(f"could not make sound {name}: {r.stderr.decode(errors='replace')[-300:]}")
        paths[name] = path
    return paths
