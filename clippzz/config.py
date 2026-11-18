"""Settings, read once from the environment."""
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("CLIPPZZ_DATA", "/data"))
# Scratch space. In Docker this is RAM (tmpfs), so frames and temporary
# downloads never touch the SD card.
WORK_DIR = Path(os.environ.get("CLIPPZZ_WORK", "/work"))
DB_PATH = DATA_DIR / "clippzz.db"
SOURCES_DIR = DATA_DIR / "sources"

# Scout reads every minute of the transcript, so it is the cheap model.
SCOUT_MODEL = os.environ.get("CLIPPZZ_SCOUT_MODEL", "claude-haiku-5-5")
# Judge looks at frames for the few best candidates, so it is the strong one.
JUDGE_MODEL = os.environ.get("CLIPPZZ_JUDGE_MODEL", "claude-opus-5-5")

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
