"""Renders docs/demo.gif: the plugin's band through one dictation, drawn like Claude's desktop app.

An illustration of the interface, not a recording. Needs macOS (system fonts), Pillow and ffmpeg:
    python docs/render_demo.py /tmp/demo-frames
    cd /tmp/demo-frames && ffmpeg -f concat -safe 0 -i frames.txt \
      -vf "fps=25,scale=800:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=64[p];[b][p]paletteuse=dither=none" \
      -loop 0 <repo>/docs/demo.gif
"""

import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
W, H = 1600, 560
FPS = 12

BG = "#1b1b1b"
BAND = "#262626"
BUTTON = "#3a3a3a"
BUTTON_DOWN = "#505050"
PRIMARY = "#d97757"
TEXT = "#ececec"
DIM = "#9a9a9a"
PROMPT = "#2c2c2c"
PROMPT_BORDER = "#474747"
RED = "#e5534b"
FACE = "#3b3b3b"

SF = "/System/Library/Fonts/SFNS.ttf"
font = ImageFont.truetype(SF, 42)
caption_font = ImageFont.truetype(SF, 46)
small = ImageFont.truetype(SF, 36)
mono = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 40)
symbols = ImageFont.truetype("/System/Library/Fonts/Apple Symbols.ttf", 44)
emoji_font = ImageFont.truetype("/System/Library/Fonts/Apple Color Emoji.ttc", 160)


def emoji(char: str, size: int) -> Image.Image:
    tile = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    ImageDraw.Draw(tile).text((10, 10), char, font=emoji_font, embedded_color=True)
    return tile.crop(tile.getbbox()).resize((size, size), Image.LANCZOS)


CAMERA = emoji("📹", 44)
BARS = "▁▂▃▄▅▆▇█"
# Mouth openness while mouthing "run the tests", ~10 samples a second.
LEVELS = [0.0, 0.1, 0.55, 0.8, 0.4, 0.15, 0.6, 0.9, 0.5, 0.2, 0.7, 0.85, 0.45, 0.3, 0.75, 0.6, 0.2, 0.05, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


def button(d: ImageDraw.ImageDraw, x: int, label: str, icon=None, pressed=False, primary=False, img=None) -> int:
    """Draws a band button at x; returns its right edge."""
    pad, h, y = 26, 84, 38
    text_w = d.textlength(label, font=font)
    icon_w = 56 if icon is not None else 0
    w = int(pad * 2 + icon_w + text_w)
    fill = BUTTON_DOWN if pressed else BUTTON
    d.rounded_rectangle((x, y, x + w, y + h), radius=16, fill=fill)
    color = PRIMARY if primary else TEXT
    if icon is not None:
        img.paste(icon, (x + pad, y + 20), icon)
    d.text((x + pad + icon_w, y + h / 2), label, font=font, fill=color, anchor="lm")
    return x + w


def wave(levels) -> str:
    glyphs = [BARS[0] if l < 0.06 else BARS[min(7, 1 + int(l * 7))] for l in levels[-14:]]
    return BARS[0] * (14 - len(glyphs)) + "".join(glyphs)


def frame(state: dict) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    top = 200  # band and prompt live below the caption area
    band = Image.new("RGB", (W - 40, 160), BG)
    bd = ImageDraw.Draw(band)
    bd.rounded_rectangle((0, 0, W - 41, 159), radius=26, fill=BAND)
    phase = state["phase"]
    if phase == "idle":
        button(bd, 24, "Lip dictation", CAMERA, pressed=state.get("pressed", False), img=band)
        gear_w = bd.textlength("Settings", font=font) + 26 * 2 + 50
        x = W - 41 - 24 - gear_w
        bd.rounded_rectangle((x, 38, x + gear_w, 122), radius=16, fill=BUTTON)
        bd.text((x + 26, 80), "⚙", font=symbols, fill=TEXT, anchor="lm")
        bd.text((x + 26 + 50, 80), "Settings", font=font, fill=TEXT, anchor="lm")
    elif phase == "starting":
        right = button(bd, 24, "Cancel")
        bd.text((right + 28, 80), "Starting camera…", font=font, fill=DIM, anchor="lm")
    elif phase == "recording":
        right = button(bd, 24, "■ Stop", primary=True, pressed=state.get("pressed", False))
        bd.ellipse((right + 30, 68, right + 54, 92), fill=RED)
        seconds = state["t"]
        bd.text((right + 72, 80), f"0:0{int(seconds)}", font=font, fill=TEXT, anchor="lm")
        bd.text((right + 170, 82), wave(state["levels"]), font=mono, fill=PRIMARY, anchor="lm")
    elif phase == "processing":
        band.paste(CAMERA, (28, 58), CAMERA)
        bd.text((96, 80), "Reading lips", font=font, fill=TEXT, anchor="lm")
        x0, x1 = 380, 1160
        bd.rounded_rectangle((x0, 72, x1, 88), radius=8, fill=BUTTON)
        p = state["p"]
        seg = 180
        start = x0 + (x1 - x0 - seg) * p
        bd.rounded_rectangle((start, 72, start + seg, 88), radius=8, fill=PRIMARY)
        bd.text((x1 + 30, 80), "0:03 clip", font=font, fill=DIM, anchor="lm")
    img.paste(band, (20, top))

    # The prompt box, as in the screenshots.
    py = top + 190
    d.rounded_rectangle((20, py, W - 20, py + 140), radius=30, fill=PROMPT, outline=PROMPT_BORDER, width=2)
    d.text((70, py + 70), "+", font=ImageFont.truetype(SF, 56), fill=TEXT, anchor="mm")
    typed = state.get("typed", "")
    d.text((120, py + 70), typed, font=font, fill=TEXT, anchor="lm")
    if state.get("cursor", True):
        cx = 120 + d.textlength(typed, font=font) + 4
        d.line((cx, py + 44, cx, py + 96), fill=TEXT, width=3)
    d.text((W - 160, py + 70), "↵", font=symbols, fill=TEXT, anchor="mm")

    # Caption on the left, the camera's view on the right.
    d.text((40, 90), state["caption"], font=caption_font, fill=TEXT, anchor="lm")
    if state.get("face"):
        cx, cy = W - 150, 95
        d.rounded_rectangle((cx - 110, cy - 80, cx + 110, cy + 80), radius=20, fill="#222222", outline="#3a3a3a", width=2)
        d.ellipse((cx - 50, cy - 62, cx + 50, cy + 62), fill=FACE)
        d.ellipse((cx - 24, cy - 24, cx - 12, cy - 12), fill=DIM)
        d.ellipse((cx + 12, cy - 24, cx + 24, cy - 12), fill=DIM)
        open_ = state.get("mouth", 0.0)
        d.ellipse((cx - 22, cy + 22 - 4 - 14 * open_, cx + 22, cy + 22 + 4 + 14 * open_), fill=PRIMARY)
        d.ellipse((cx + 84, cy - 70, cx + 96, cy - 58), fill="#3fb950")  # camera light
    return img


frames: list[tuple[dict, float]] = []  # (state, seconds)


def hold(state: dict, seconds: float):
    frames.append((state, seconds))


IDLE = "Press Lip dictation above the prompt"
hold({"phase": "idle", "caption": IDLE}, 1.4)
hold({"phase": "idle", "caption": IDLE, "pressed": True}, 0.25)
hold({"phase": "starting", "caption": "The camera turns on", "face": True}, 0.9)
levels: list[float] = []
for i, level in enumerate(LEVELS):
    levels.append(level)
    hold(
        {
            "phase": "recording",
            "caption": "Mouth a phrase, without a sound",
            "face": True,
            "mouth": level,
            "levels": list(levels),
            "t": 1 + i / 10,
        },
        0.12,
    )
hold({"phase": "recording", "caption": "Press Stop", "face": True, "levels": list(levels), "t": 3.4, "pressed": True}, 0.6)
for i in range(10):
    hold({"phase": "processing", "caption": "The model reads your lips, on your Mac", "p": i / 9}, 0.09)
phrase = "Run the tests"
for n in range(1, len(phrase) + 1):
    hold({"phase": "idle", "caption": "The text lands in the prompt", "typed": phrase[:n]}, 0.04)
for blink in range(4):
    hold({"phase": "idle", "caption": "The text lands in the prompt, ready to send", "typed": phrase, "cursor": blink % 2 == 0}, 0.5)

concat = []
for n, (state, seconds) in enumerate(frames):
    name = f"f{n:03d}.png"
    frame(state).save(OUT / name)
    concat.append(f"file '{name}'\nduration {seconds}")
concat.append(f"file 'f{len(frames) - 1:03d}.png'")  # ffmpeg's concat needs the last file twice
(OUT / "frames.txt").write_text("\n".join(concat) + "\n")
print(len(frames), "frames,", round(sum(s for _, s in frames), 1), "seconds")
