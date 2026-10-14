"""Compose the store-listing images (icon, featured image, previews) from the raw captures.

    python tools/make_listing_images.py

Inputs (from tools/listing_assets.py): tools/_listing/raw/{1_build,2_edit,3_image,render}.png
Outputs: docs/store-kit/{icon.png, featured.png, preview_*.png}
"""

import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "tools", "_listing", "raw")
OUT = os.path.join(ROOT, "docs", "store-kit")
os.makedirs(OUT, exist_ok=True)

INK = (24, 24, 28)
ACCENT = (232, 125, 62)      # warm orange
ACCENT2 = (90, 160, 255)     # cool blue
WHITE = (245, 245, 247)


def font(size, bold=False):
    names = ["segoeuib.ttf" if bold else "segoeui.ttf", "arialbd.ttf" if bold else "arial.ttf", "DejaVuSans.ttf"]
    for n in names:
        try:
            return ImageFont.truetype(n, size)
        except OSError:
            continue
    return ImageFont.load_default()


def icon(size=256):
    s = 4  # supersample
    W = size * s
    im = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, W - 1, W - 1], radius=56 * s, fill=INK)
    # Isometric cube
    cx, cy, r = W * 0.44, W * 0.56, W * 0.27
    top = [(cx, cy - r), (cx + r * 0.87, cy - r / 2), (cx, cy), (cx - r * 0.87, cy - r / 2)]
    left = [(cx - r * 0.87, cy - r / 2), (cx, cy), (cx, cy + r), (cx - r * 0.87, cy + r / 2)]
    right = [(cx, cy), (cx + r * 0.87, cy - r / 2), (cx + r * 0.87, cy + r / 2), (cx, cy + r)]
    d.polygon(top, fill=(255, 168, 110))
    d.polygon(left, fill=ACCENT)
    d.polygon(right, fill=(176, 84, 36))
    # Chat bubble
    bx0, by0, bx1, by1 = W * 0.55, W * 0.12, W * 0.90, W * 0.40
    d.rounded_rectangle([bx0, by0, bx1, by1], radius=22 * s, fill=ACCENT2)
    d.polygon([(bx0 + 30 * s, by1 - 2), (bx0 + 8 * s, by1 + 26 * s), (bx0 + 62 * s, by1 - 2)], fill=ACCENT2)
    for i in range(3):
        x = bx0 + (bx1 - bx0) * (0.28 + 0.22 * i)
        y = (by0 + by1) / 2
        d.ellipse([x - 9 * s, y - 9 * s, x + 9 * s, y + 9 * s], fill=WHITE)
    return im.resize((size, size), Image.LANCZOS)


def fit(im, w, h):
    """Cover-crop to w x h."""
    k = max(w / im.width, h / im.height)
    im = im.resize((int(im.width * k + 0.5), int(im.height * k + 0.5)), Image.LANCZOS)
    x, y = (im.width - w) // 2, (im.height - h) // 2
    return im.crop((x, y, x + w, y + h))


def featured():
    W, H = 1920, 1080
    base = fit(Image.open(os.path.join(RAW, "render.png")).convert("RGB"), W, H)
    # Left gradient for legible text
    grad = Image.new("L", (W, 1))
    for x in range(W):
        grad.putpixel((x, 0), int(235 * max(0.0, 1 - x / (W * 0.62)) ** 1.4))
    shade = Image.new("RGB", (W, H), INK)
    base = Image.composite(shade, base, grad.resize((W, H)))
    d = ImageDraw.Draw(base)
    base.paste(icon(150), (120, 250), icon(150))
    d.text((120, 440), "Claude Bridge", font=font(118, True), fill=WHITE)
    d.text((124, 590), "Chat with Claude inside Blender.", font=font(54), fill=WHITE)
    d.text((124, 660), "It builds, edits and checks your scene.", font=font(54), fill=WHITE)
    d.text((124, 780), "Uses your Claude subscription  ·  Works with Claude Code", font=font(36), fill=(255, 190, 150))
    return base


def crop_to_area(shot):
    """Crop a window screenshot to the 3D view + sidebar so the chat text stays legible."""
    import json
    p = os.path.join(RAW, "area.json")
    if not os.path.exists(p):
        return shot
    a = json.load(open(p))
    k = shot.height / a["win_h"]  # screenshot may be at a different scale than window coords
    top = (a["win_h"] - (a["y"] + a["h"])) * k
    return shot.crop((int(a["x"] * k), int(top), int((a["x"] + a["w"]) * k), int(top + a["h"] * k)))


def preview(name, caption):
    shot = crop_to_area(Image.open(os.path.join(RAW, name + ".png")).convert("RGB"))
    W, H = 1920, 1080
    canvas = Image.new("RGB", (W, H), INK)
    cap_h = 120
    shot = shot.resize((int(shot.width * (H - cap_h - 40) / shot.height), H - cap_h - 40), Image.LANCZOS)
    if shot.width > W - 80:
        shot = shot.resize((W - 80, int(shot.height * (W - 80) / shot.width)), Image.LANCZOS)
    x = (W - shot.width) // 2
    shadow = Image.new("RGBA", (shot.width + 40, shot.height + 40), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rectangle([20, 20, shot.width + 20, shot.height + 20], fill=(0, 0, 0, 160))
    canvas.paste(shadow.filter(ImageFilter.GaussianBlur(14)), (x - 20, cap_h - 10), shadow.filter(ImageFilter.GaussianBlur(14)))
    canvas.paste(shot, (x, cap_h + 10))
    d = ImageDraw.Draw(canvas)
    f = font(48, True)
    tw = d.textlength(caption, font=f)
    d.text(((W - tw) / 2, 34), caption, font=f, fill=WHITE)
    return canvas


def save(im, name):
    """Windows image viewers lock open files; fall back to a new name instead of failing."""
    path = os.path.join(OUT, name)
    try:
        im.save(path)
    except OSError:
        path = path.replace(".png", "_new.png")
        im.save(path)
    print(os.path.basename(path))


def main():
    save(icon(256), "icon.png")
    if os.path.exists(os.path.join(RAW, "render.png")):
        save(featured(), "featured.png")
    caps = {"1_build": "Describe a scene: Claude builds it in your viewport",
            "2_edit": "Keep chatting: edits land as single undo steps",
            "3_image": "Attach a screenshot or paste with Ctrl+V"}
    for i, (name, cap) in enumerate(caps.items(), 1):
        if os.path.exists(os.path.join(RAW, name + ".png")):
            save(preview(name, cap), f"preview_{i}.png")


if __name__ == "__main__":
    main()
