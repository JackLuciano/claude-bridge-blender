"""Edit tools/_listing/demo/f*.png into a short promo GIF: docs/demo.gif.

    python tools/make_demo_gif.py [--width 800]

Cut: title card with the prompt typing in -> time-lapse of the build with a caption bar ->
result hold + orbit -> closing card. Crossfades between sections. Black frames (window
minimized while recording) are skipped.
"""

import glob
import json
import os
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_listing_images import ACCENT, INK, WHITE, font, icon  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "tools", "_listing", "demo")
DST = os.path.join(ROOT, "docs", "demo.gif")
PROMPT = "Build a cozy reading nook: armchair, side table, lamp, bookshelf."


def arg(name, default):
    return int(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else default


W = arg("--width", 800)
PAD, BAR = 24, 54
CANVAS = None  # (w, h), set once the first shot is known


def load_shots():
    shots = []
    for f in sorted(glob.glob(os.path.join(SRC, "f*.png"))):
        a = json.load(open(f.replace(".png", ".json")))
        im = Image.open(f).convert("RGB")
        if max(im.convert("L").getextrema()) < 20:
            continue  # window was minimized
        k = im.height / a["win_h"]
        top = (a["win_h"] - (a["y"] + a["h"])) * k
        im = im.crop((int(a["x"] * k), int(top), int((a["x"] + a["w"]) * k), int(top + a["h"] * k)))
        inner = W - 2 * PAD
        shots.append((im.resize((inner, int(im.height * inner / im.width)), Image.LANCZOS), a["orbit"]))
    return shots


def framed(shot, caption, accent=False):
    """Recording inside a rounded frame on the dark canvas, with a caption bar underneath."""
    cw, ch = CANVAS
    c = Image.new("RGB", (cw, ch), INK)
    mask = Image.new("L", shot.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, shot.width - 1, shot.height - 1], radius=14, fill=255)
    c.paste(shot, (PAD, PAD), mask)
    d = ImageDraw.Draw(c)
    f = font(24, True)
    tw = d.textlength(caption, font=f)
    x, y = (cw - tw) / 2, PAD + shot.height + (BAR - 24) / 2 + 2
    if accent:
        d.rounded_rectangle([x - 16, y - 8, x + tw + 16, y + 32], radius=16, fill=ACCENT)
    d.text((x, y), caption, font=f, fill=WHITE)
    return c


def card(lines, sub=None, typed=None):
    cw, ch = CANVAS
    c = Image.new("RGB", (cw, ch), INK)
    d = ImageDraw.Draw(c)
    ic = icon(96)
    c.paste(ic, ((cw - 96) // 2, ch // 2 - 150), ic)
    y = ch // 2 - 30
    for text, size, bold in lines:
        f = font(size, bold)
        d.text(((cw - d.textlength(text, font=f)) / 2, y), text, font=f, fill=WHITE)
        y += size + 14
    if typed is not None:  # prompt box with a typing cursor
        f = font(19)
        box_w = cw - 140
        d.rounded_rectangle([70, y + 10, 70 + box_w, y + 60], radius=12, fill=(40, 40, 46), outline=(70, 70, 80))
        d.text((88, y + 23), typed, font=f, fill=WHITE)
        cx = 88 + d.textlength(typed, font=f) + 3  # drawn cursor: not every font has a block glyph
        d.rectangle([cx, y + 24, cx + 2, y + 46], fill=ACCENT)
    if sub:
        f = font(20)
        d.text(((cw - d.textlength(sub, font=f)) / 2, ch - 70), sub, font=f, fill=(255, 190, 150))
    return c


def fade(a, b, n=4, ms=60):
    return [(Image.blend(a, b, (i + 1) / (n + 1)), ms) for i in range(n)]


def main():
    global CANVAS
    shots = load_shots()
    if not shots:
        sys.exit("no usable frames: run tools/record_demo.py and keep the window visible")
    CANVAS = (W, shots[0][0].height + 2 * PAD + BAR)
    build = [s for s, orbit in shots if not orbit]
    orbit = [s for s, o in shots if o]

    seq = []
    # 1. Title card, then the prompt typing in
    title = [("Claude Bridge", 44, True), ("Chat with Claude inside Blender", 22, False)]
    seq.append((card(title), 1200))
    for n in range(0, len(PROMPT) + 1, 4):
        seq.append((card(title, typed=PROMPT[:n]), 70))
    seq.append((card(title, typed=PROMPT), 900))
    # 2. Time-lapse of the build. Frames were captured every 0.6 s (record_demo.py).
    step = max(1, len(build) // 45)
    speed = round(0.6 * step / 0.16)
    lapse = [framed(s, f"Claude is building it…  ({speed}× speed)") for s in build[::step]]
    seq += fade(seq[-1][0], lapse[0])
    seq += [(f, 160) for f in lapse]
    # 3. Result: hold, then orbit
    done = framed(build[-1], "Done: one message, one undo step", accent=True)
    seq += fade(lapse[-1], done, 3)
    seq.append((done, 1600))
    for s in orbit:
        seq.append((framed(s, "Done: one message, one undo step", accent=True), 90))
    # 4. Closing card
    outro = card([("Claude Bridge", 40, True), ("Free & open source for Blender 4.2+", 22, False)],
                 sub="github.com/JackLuciano/claude-bridge-blender")
    seq += fade(seq[-1][0], outro)
    seq.append((outro, 2600))

    frames = [im.quantize(colors=160, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE) for im, _ in seq]
    os.makedirs(os.path.dirname(DST), exist_ok=True)
    frames[0].save(DST, save_all=True, append_images=frames[1:], duration=[ms for _, ms in seq], loop=0,
                   optimize=True, disposal=1)
    secs = sum(ms for _, ms in seq) / 1000
    print(f"{DST}: {len(frames)} frames, {secs:.0f} s, {os.path.getsize(DST) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
