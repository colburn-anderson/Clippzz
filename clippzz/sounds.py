"""The sound-sting library. Every sound is synthesised by ffmpeg from a
formula, so there is nothing to license and nothing stored on disk: the
files are made in RAM scratch space when a clip is rendered.

These are stand-ins until the user names the meme sounds they want by ear."""
import subprocess
from pathlib import Path

from . import config


def _note(freq: float, dur: float, wobble: bool = False) -> str:
    """One buzzy brass-like note with a soft attack, for the "fail" sting."""
    phase = f"2*PI*{freq}*t" + ("+2.5*sin(2*PI*5*t)" if wobble else "")
    return (f"aevalsrc=exprs='0.3*(sin({phase})+0.5*sin(2*({phase}))+0.3*sin(3*({phase})))"
            f"*(1-exp(-60*t))*exp(-1.6*t)':s=44100:d={dur}")


# name -> (filtergraph ending in [out], what it is for, mix volume)
SOUNDS = {
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

NAMES = list(SOUNDS)


def describe() -> str:
    return "; ".join(f"{name} ({SOUNDS[name][1]})" for name in NAMES)


def volume(name: str) -> float:
    return SOUNDS[name][2]


def ensure(names, folder: Path | None = None) -> dict[str, Path]:
    """Makes the WAV for each name (once per folder) and returns their paths."""
    folder = folder or (config.WORK_DIR / "sounds")
    folder.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name in set(names):
        path = folder / f"{name}.wav"
        if not path.exists():
            r = subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-filter_complex", SOUNDS[name][0],
                 "-map", "[out]", "-ac", "1", "-ar", "44100", str(path)],
                capture_output=True, timeout=60)
            if r.returncode != 0:
                raise RuntimeError(f"could not make sound {name}: {r.stderr.decode(errors='replace')[-300:]}")
        paths[name] = path
    return paths
