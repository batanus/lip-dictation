"""Renders docs/demo.gif: one dictation in Claude's desktop app, with the person mouthing it on camera.

Colors and sizes are measured from screenshots of the app (docs/band.png, docs/settings.png).
Needs macOS (system fonts), Pillow and ffmpeg:
    python docs/render_demo.py /tmp/demo-frames
    cd /tmp/demo-frames && ffmpeg -f concat -safe 0 -i frames.txt \\
      -vf "fps=25,scale=880:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=none" \\
      -loop 0 <repo>/docs/demo.gif
"""

import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
W, H = 1600, 520

PAGE = "#191919"
BAND = "#252424"
BUTTON = "#454545"
BUTTON_DOWN = "#5a5a5a"
TEXT = "#f2f2f2"
DIM = "#8c8c8c"
PROMPT = "#303030"
PROMPT_BORDER = "#414141"
CLAUDE = "#d97757"
RED = "#e5534b"

SF = "/System/Library/Fonts/SFNS.ttf"
font = ImageFont.truetype(SF, 28)
caption_font = ImageFont.truetype(SF, 44)
mono = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 26)
symbols = ImageFont.truetype("/System/Library/Fonts/Apple Symbols.ttf", 30)
emoji_font = ImageFont.truetype("/System/Library/Fonts/Apple Color Emoji.ttc", 160)


def emoji(char: str, size: int) -> Image.Image:
    tile = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    ImageDraw.Draw(tile).text((10, 10), char, font=emoji_font, embedded_color=True)
    return tile.crop(tile.getbbox()).resize((size, size), Image.LANCZOS)


CAMERA = emoji("📹", 28)
BARS = "▁▂▃▄▅▆▇█"

# Where things sit, as in the app's screenshots: band 76 px tall, buttons 54 px, prompt box 87 px.
TOP = 300
BAND_BOX = (30, TOP, W - 30, TOP + 76)
BTN_Y = (TOP + 11, TOP + 65)
PROMPT_BOX = (30, TOP + 94, W - 30, TOP + 181)


def button(d, img, x, label, icon=None, pressed=False):
    """A band button whose left edge is x; returns its right edge."""
    pad = 18
    icon_w = 38 if icon is not None else 0
    w = int(pad * 2 + icon_w + d.textlength(label, font=font))
    d.rounded_rectangle((x, BTN_Y[0], x + w, BTN_Y[1]), radius=10, fill=BUTTON_DOWN if pressed else BUTTON)
    mid = (BTN_Y[0] + BTN_Y[1]) // 2
    if icon is not None:
        img.paste(icon, (x + pad, mid - icon.height // 2), icon)
    d.text((x + pad + icon_w, mid), label, font=font, fill=TEXT, anchor="lm")
    return x + w


def settings_button(d):
    w = int(18 * 2 + 34 + d.textlength("Settings", font=font))
    x = BAND_BOX[2] - 18 - w
    d.rounded_rectangle((x, BTN_Y[0], x + w, BTN_Y[1]), radius=10, fill=BUTTON)
    mid = (BTN_Y[0] + BTN_Y[1]) // 2
    d.text((x + 18, mid), "⚙", font=symbols, fill=TEXT, anchor="lm")
    d.text((x + 18 + 34, mid), "Settings", font=font, fill=TEXT, anchor="lm")


def prompt_icons(d):
    """The return arrow and the microphone at the right of the prompt box."""
    cy = (PROMPT_BOX[1] + PROMPT_BOX[3]) // 2
    x = PROMPT_BOX[2] - 128
    d.line((x + 22, cy - 10, x + 22, cy + 4, x, cy + 4), fill=TEXT, width=3, joint="curve")
    d.line((x, cy + 4, x + 7, cy - 3), fill=TEXT, width=3)
    d.line((x, cy + 4, x + 7, cy + 11), fill=TEXT, width=3)
    m = PROMPT_BOX[2] - 52
    d.rounded_rectangle((m - 7, cy - 16, m + 7, cy + 4), radius=7, outline=TEXT, width=3)
    d.arc((m - 13, cy - 12, m + 13, cy + 12), start=0, end=180, fill=TEXT, width=3)
    d.line((m, cy + 12, m, cy + 18), fill=TEXT, width=3)


def wave(levels) -> str:
    glyphs = [BARS[0] if v < 0.06 else BARS[min(7, 1 + int(v * 7))] for v in levels[-24:]]
    return BARS[0] * (24 - len(glyphs)) + "".join(glyphs)


# ---- the person on camera, drawn 4x larger and scaled down for smooth edges ----

S = 4
CARD_W, CARD_H = 380, 250
SKIN, SKIN_SHADE = (226, 178, 140), (200, 148, 112)
HAIR = (58, 42, 32)
LIP = (184, 98, 92)
MOUTH = (70, 26, 30)
TEETH = (240, 236, 228)
SWEATER = (52, 72, 104)


def card_background() -> Image.Image:
    bg = Image.new("RGB", (CARD_W * S, CARD_H * S))
    d = ImageDraw.Draw(bg)
    for y in range(CARD_H * S):  # a dim room, lighter behind the head
        t = y / (CARD_H * S)
        d.line((0, y, CARD_W * S, y), fill=(int(52 - 18 * t), int(58 - 20 * t), int(66 - 22 * t)))
    return bg


BACKGROUND = card_background()


def face(openness: float, rounding: float, blink: float) -> Image.Image:
    img = BACKGROUND.copy()
    d = ImageDraw.Draw(img)

    def e(box, fill):
        d.ellipse([v * S for v in box], fill=fill)

    cx = CARD_W / 2
    # shoulders and neck
    e((cx - 150, 205, cx + 150, 380), SWEATER)
    d.rectangle([v * S for v in (cx - 26, 170, cx + 26, 222)], fill=SKIN_SHADE)
    d.polygon([v * S for v in (cx - 30, 206, cx + 30, 206, cx, 236)], fill=SKIN_SHADE)
    # ears, then the head
    e((cx - 80, 100, cx - 58, 136), SKIN_SHADE)
    e((cx + 58, 100, cx + 80, 136), SKIN_SHADE)
    e((cx - 68, 34, cx + 68, 202), SKIN)
    # hair: a cap over the head, open over the forehead
    hair = Image.new("L", img.size, 0)
    hd = ImageDraw.Draw(hair)
    hd.ellipse([v * S for v in (cx - 76, 18, cx + 76, 150)], fill=255)
    hd.ellipse([v * S for v in (cx - 62, 64, cx + 66, 240)], fill=0)
    hd.rectangle([v * S for v in (0, 118, CARD_W, CARD_H)], fill=0)
    img.paste(HAIR, (0, 0), hair)
    # eyebrows: soft arches, a little above the eyes
    for side in (-1, 1):
        ex = cx + side * 29
        d.arc([v * S for v in (ex - 15, 84, ex + 15, 104)], start=200, end=340, fill=HAIR, width=4 * S)
    # eyes, closing on a blink
    for side in (-1, 1):
        ex = cx + side * 29
        h = max(1.0, 7 * (1 - blink))
        e((ex - 12, 106 - h, ex + 12, 106 + h), (250, 248, 244))
        if blink < 0.7:
            e((ex - 6, 100, ex + 6, 112), (60, 44, 34))
            e((ex - 2, 103, ex + 1, 106), (255, 255, 255))
        d.arc([v * S for v in (ex - 13, 98, ex + 13, 114)], start=200, end=340, fill=SKIN_SHADE, width=2 * S)
    # nose: a soft shadow on one side and the base
    d.arc([v * S for v in (cx - 9, 128, cx + 9, 146)], start=20, end=160, fill=SKIN_SHADE, width=3 * S)
    d.line([v * S for v in (cx + 3, 116, cx + 6, 132)], fill=SKIN_SHADE, width=2 * S)
    # mouth: lips around a dark opening, teeth showing as it opens
    w = 20 * (1 - 0.3 * rounding)  # silent mouthing: the lips move, the jaw barely drops
    h = 1.5 + 9 * openness
    my = 164
    e((cx - w - 4, my - h - 5, cx + w + 4, my + h + 6), LIP)
    if openness > 0.06:
        inner = Image.new("L", img.size, 0)
        ImageDraw.Draw(inner).ellipse([v * S for v in (cx - w, my - h, cx + w, my + h)], fill=255)
        img.paste(MOUTH, (0, 0), inner)
        teeth = Image.new("L", img.size, 0)
        ImageDraw.Draw(teeth).rectangle([v * S for v in (cx - w, my - h, cx + w, my - h + min(5, h * 0.45))], fill=255)
        img.paste(TEETH, (0, 0), ImageChops.darker(teeth, inner))  # only inside the opening
    else:
        d.line([v * S for v in (cx - w, my, cx + w, my)], fill=MOUTH, width=2 * S)
    # the camera light
    e((CARD_W - 22, 14, CARD_W - 12, 24), (63, 185, 80))
    small = img.resize((CARD_W, CARD_H), Image.LANCZOS)
    mask = Image.new("L", (CARD_W, CARD_H), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, CARD_W - 1, CARD_H - 1), radius=18, fill=255)
    out = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
    out.paste(small, (0, 0), mask)
    return out


def frame(state: dict) -> Image.Image:
    img = Image.new("RGB", (W, H), PAGE)
    d = ImageDraw.Draw(img)

    # caption on the left, the camera on the right
    d.text((40, 145), state["caption"], font=caption_font, fill=TEXT, anchor="lm")
    if state.get("camera"):
        card = face(state.get("mouth", 0.0), state.get("round", 0.0), state.get("blink", 0.0))
        img.paste(card, (W - 30 - CARD_W, 24), card)

    d.rounded_rectangle(BAND_BOX, radius=16, fill=BAND)
    phase = state["phase"]
    x = BAND_BOX[0] + 18
    mid = (BTN_Y[0] + BTN_Y[1]) // 2
    if phase == "idle":
        button(d, img, x, "Lip dictation", CAMERA, pressed=state.get("pressed", False))
        settings_button(d)
    elif phase == "starting":
        right = button(d, img, x, "Cancel")
        d.text((right + 16, mid), "Starting camera…", font=font, fill=DIM, anchor="lm")
    elif phase == "recording":
        right = button(d, img, x, "■ Stop", pressed=state.get("pressed", False))
        d.ellipse((right + 18, mid - 8, right + 34, mid + 8), fill=RED)
        d.text((right + 46, mid), f"0:0{int(state['t'])}", font=font, fill=TEXT, anchor="lm")
        d.text((right + 112, mid + 1), wave(state["levels"]), font=mono, fill=CLAUDE, anchor="lm")
    elif phase == "processing":
        img.paste(CAMERA, (x, mid - CAMERA.height // 2), CAMERA)
        d.text((x + 40, mid), "Reading lips", font=font, fill=TEXT, anchor="lm")
        x0, x1 = x + 230, BAND_BOX[2] - 170
        d.rounded_rectangle((x0, mid - 4, x1, mid + 4), radius=4, fill=BUTTON)
        seg = 160
        start = x0 + (x1 - x0 - seg) * state["p"]
        d.rounded_rectangle((start, mid - 4, start + seg, mid + 4), radius=4, fill=CLAUDE)
        d.text((x1 + 20, mid), "0:03 clip", font=font, fill=DIM, anchor="lm")

    d.rounded_rectangle(PROMPT_BOX, radius=22, fill=PROMPT, outline=PROMPT_BORDER, width=2)
    cy = (PROMPT_BOX[1] + PROMPT_BOX[3]) // 2
    d.line((62, cy, 86, cy), fill=TEXT, width=3)
    d.line((74, cy - 12, 74, cy + 12), fill=TEXT, width=3)
    typed = state.get("typed", "")
    d.text((118, cy), typed, font=font, fill=TEXT, anchor="lm")
    if state.get("cursor", True):
        cx = 118 + d.textlength(typed, font=font) + 3
        d.line((cx, cy - 16, cx, cy + 16), fill=TEXT, width=2)
    prompt_icons(d)
    return img


frames: list[tuple[dict, float]] = []


def hold(state: dict, seconds: float):
    frames.append((state, seconds))


# Mouth openness and rounding while mouthing "run the tests", ten samples a second.
LEVELS = [0.05, 0.3, 0.75, 0.55, 0.2, 0.1, 0.6, 0.85, 0.4, 0.15, 0.7, 0.9, 0.5, 0.25, 0.65, 0.45, 0.15, 0.05, 0.0, 0.0]
ROUND = [0.0, 0.8, 0.9, 0.4, 0.0, 0.0, 0.2, 0.1, 0.0, 0.0, 0.1, 0.0, 0.0, 0.0, 0.2, 0.1, 0.0, 0.0, 0.0, 0.0]

IDLE = "Press Lip dictation above the prompt"
hold({"phase": "idle", "caption": IDLE}, 1.4)
hold({"phase": "idle", "caption": IDLE, "pressed": True}, 0.25)
hold({"phase": "starting", "caption": "The camera turns on", "camera": True}, 0.5)
hold({"phase": "starting", "caption": "The camera turns on", "camera": True, "blink": 1.0}, 0.12)
hold({"phase": "starting", "caption": "The camera turns on", "camera": True}, 0.4)
levels: list[float] = []
for i, (level, rounding) in enumerate(zip(LEVELS, ROUND)):
    levels.append(level)
    hold(
        {
            "phase": "recording",
            "caption": "Mouth a phrase, without a sound",
            "camera": True,
            "mouth": level,
            "round": rounding,
            "blink": 1.0 if i == 16 else 0.0,
            "levels": list(levels),
            "t": 1 + i / 10,
        },
        0.12,
    )
hold({"phase": "recording", "caption": "Press Stop", "camera": True, "levels": list(levels), "t": 3, "pressed": True}, 0.6)
for i in range(10):
    hold({"phase": "processing", "caption": "Your Mac reads your lips", "p": i / 9}, 0.09)
for blink in range(5):
    hold({"phase": "idle", "caption": "The text is in the prompt, ready to send", "typed": "Run the tests", "cursor": blink % 2 == 0}, 0.5)

concat = []
for n, (state, seconds) in enumerate(frames):
    name = f"f{n:03d}.png"
    frame(state).save(OUT / name)
    concat.append(f"file '{name}'\nduration {seconds}")
concat.append(f"file 'f{len(frames) - 1:03d}.png'")  # ffmpeg's concat repeats the last file
(OUT / "frames.txt").write_text("\n".join(concat) + "\n")
print(len(frames), "frames,", round(sum(s for _, s in frames), 1), "seconds")
