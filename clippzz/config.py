"""Settings, read once from the environment."""
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("CLIPPZZ_DATA", "/data"))
# Scratch space. In Docker this is RAM (tmpfs), so frames and temporary
# downloads never touch the SD card.
WORK_DIR = Path(os.environ.get("CLIPPZZ_WORK", "/work"))
DB_PATH = DATA_DIR / "clippzz.db"
SOURCES_DIR = DATA_DIR / "sources"
CLIPS_DIR = DATA_DIR / "clips"
# Speech-to-text model files, downloaded once.
MODELS_DIR = DATA_DIR / "models"
# The owner's meme sounds and background music (SSD network drive, Clippzz/
# sounds and Clippzz/music, mounted read-only into the worker).
LIBRARY_DIR = Path(os.environ.get("CLIPPZZ_LIBRARY", "/library"))
# Synthesised stand-in stings when the sound folder is empty. Off by default:
# the owner found them unfunny ("they take away from the action").
SYNTH_SOUNDS = os.environ.get("CLIPPZZ_SYNTH_SOUNDS", "0") == "1"
EMOJI_FONT = os.environ.get("CLIPPZZ_EMOJI_FONT", "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf")

# Scout reads every minute of the transcript, so it is the cheap model.
SCOUT_MODEL = os.environ.get("CLIPPZZ_SCOUT_MODEL", "claude-haiku-5-5")
# Judge looks at frames for the few best candidates, so it is the strong one.
JUDGE_MODEL = os.environ.get("CLIPPZZ_JUDGE_MODEL", "claude-opus-5-5")
# Planner watches a moment frame by frame and writes the edit, so also strong.
PLANNER_MODEL = os.environ.get("CLIPPZZ_PLANNER_MODEL", "claude-opus-5-5")
# "high" made clip 3, which the owner called perfect, for $0.58. "medium" is
# being tested from November 18, 2026 to cut the cost to about $0.40; each
# clip records the settings it was planned with, so ratings can be compared.
PLANNER_EFFORT = os.environ.get("CLIPPZZ_PLANNER_EFFORT", "medium")

# Captions come from faster-whisper running on the Pi. small.en is the most
# accurate model that still runs in a couple of minutes per clip on a Pi 4.
WHISPER_MODEL = os.environ.get("CLIPPZZ_WHISPER_MODEL", "small.en")
WHISPER_THREADS = int(os.environ.get("CLIPPZZ_WHISPER_THREADS", "3"))
# Extra footage fetched around a judged moment. Lots after it, because the
# owner wants to see how a bit ends, which often happens a minute later.
# The clip accounts (brand guide: Chat Clip That). Used in the Instagram caption.
CHANNEL_HANDLE = os.environ.get("CLIPPZZ_CHANNEL_HANDLE", "chatcl1pthat")

CLIP_PAD_BEFORE_S = 10
CLIP_PAD_AFTER_S = 75

# Dollars per million tokens (input, output), used to track spend.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}

MONTHLY_BUDGET_USD = float(os.environ.get("CLIPPZZ_MONTHLY_BUDGET_USD", "5"))
# How many scouted moments go on to the (more expensive) judge per VOD.
MAX_JUDGED = int(os.environ.get("CLIPPZZ_MAX_JUDGED", "10"))
MAX_ATTEMPTS = 3

SCOUT_CHUNK_S = 600
SCOUT_OVERLAP_S = 45
