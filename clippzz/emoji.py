"""Emoji stickers (💀 on the joke, 🤭 over something that needs covering),
drawn from Google's Noto Color Emoji font (open licence) into PNGs."""
from pathlib import Path

from . import config

# The planner picks from this list, so every one is known to draw.
CHOICES = ["💀", "😭", "😂", "🤣", "🔥", "😳", "🤯", "😱", "🗿", "🤡", "👀", "🤭", "🫢",
           "😤", "🥶", "💯", "❤️", "💔", "😈", "🙏", "💪", "🍽️", "🏃", "🚨"]
SIZES = {"small": 170, "medium": 270, "large": 420}


def png(char: str, size_px: int, folder: Path) -> Path:
    """A transparent PNG of one emoji, size_px wide."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{'-'.join(f'{ord(c):x}' for c in char)}-{size_px}.png"
    if path.exists():
        return path
    from PIL import Image, ImageDraw, ImageFont  # only needed when rendering

    font = ImageFont.truetype(config.EMOJI_FONT, 109)  # the font's only bitmap size
    canvas = Image.new("RGBA", (220, 220), (0, 0, 0, 0))
    ImageDraw.Draw(canvas).text((110, 110), char, font=font, embedded_color=True, anchor="mm")
    box = canvas.getbbox()
    if not box:
        raise ValueError(f"the emoji font cannot draw {char!r}")
    art = canvas.crop(box)
    scale = size_px / max(art.size)
    art = art.resize((max(1, round(art.width * scale)), max(1, round(art.height * scale))), Image.LANCZOS)
    art.save(path)
    return path
