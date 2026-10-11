"""Pictures for a decision model that can see.

The engine finds its candidates from the page's structure; what the structure cannot tell
apart (icon-only buttons, look-alike cards, what is drawn rather than written) a picture
can. A screenshot is taken and each candidate is marked with a numbered box, so the
question "which of these?" can be asked about the picture and the words at once. Only the
candidates in the window are marked; the rest are described in words alone.
"""
import base64
import io

from PIL import Image, ImageDraw, ImageFont

MAX_WIDTH = 1280       # wider screenshots are scaled down: tokens grow with the picture's area


def marked_screenshot(page, locators, limit=12, zoom=False):
    """(PNG data URL, {candidate index: box number}) for the candidates that are on screen
    now, each outlined and numbered 1..n on a screenshot of the window. None when fewer
    than two are on screen: a picture with one box answers nothing. With `zoom`, the
    picture is cut down to the part of the window that holds the candidates and enlarged
    -- a closer look when the first answer was unsure. None if that part is most of the
    window anyway."""
    boxes = {}
    for index, locator in enumerate(locators[:limit]):
        try:
            box = locator.bounding_box(timeout=800)
        except Exception:
            box = None
        if box and box["width"] >= 2 and box["height"] >= 2:
            boxes[index] = box
    if len(boxes) < 2:
        return None, {}
    shot = Image.open(io.BytesIO(page.screenshot(type="png"))).convert("RGB")
    if zoom:
        left = max(0, min(b["x"] for b in boxes.values()) - 40)
        top = max(0, min(b["y"] for b in boxes.values()) - 40)
        right = min(shot.width, max(b["x"] + b["width"] for b in boxes.values()) + 40)
        bottom = min(shot.height, max(b["y"] + b["height"] for b in boxes.values()) + 40)
        if right <= left or bottom <= top or (right - left) * (bottom - top) > 0.6 * shot.width * shot.height:
            return None, {}
        shot = shot.crop((int(left), int(top), int(right), int(bottom)))
        boxes = {i: {"x": b["x"] - left, "y": b["y"] - top, "width": b["width"], "height": b["height"]}
                 for i, b in boxes.items()}
        grow = min(2.0, MAX_WIDTH / shot.width)
        shot = shot.resize((int(shot.width * grow), int(shot.height * grow)))
        scale = grow
    else:
        scale = min(1.0, MAX_WIDTH / shot.width)
        if scale < 1.0:
            shot = shot.resize((int(shot.width * scale), int(shot.height * scale)))
    draw = ImageDraw.Draw(shot)
    try:
        font = ImageFont.load_default(size=16)
    except TypeError:                           # an older Pillow: the default font has one size
        font = ImageFont.load_default()
    numbers = {}
    for number, (index, box) in enumerate(boxes.items(), 1):
        x, y = box["x"] * scale, box["y"] * scale
        w, h = box["width"] * scale, box["height"] * scale
        if x + w < 0 or y + h < 0 or x > shot.width or y > shot.height:
            continue                            # measured, but outside the window
        draw.rectangle([x - 2, y - 2, x + w + 2, y + h + 2], outline=(220, 30, 30), width=3)
        label = str(number)
        tw = draw.textlength(label, font=font) + 8
        tx, ty = max(0, x - 2), max(0, y - 22)
        draw.rectangle([tx, ty, tx + tw, ty + 20], fill=(220, 30, 30))
        draw.text((tx + 4, ty + 1), label, fill=(255, 255, 255), font=font)
        numbers[index] = number
    if len(numbers) < 2:
        return None, {}
    out = io.BytesIO()
    shot.save(out, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode("ascii"), numbers


def sees(cfg):
    """Can this decision profile look at pictures?"""
    return cfg.get("preset") == "liquid" and not str(cfg.get("model", "")).endswith(":free") \
        and cfg.get("vision", True) is not False
