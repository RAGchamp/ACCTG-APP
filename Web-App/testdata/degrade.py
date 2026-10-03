"""The "scan" look and handwriting (plan §15.2 step 3).

Difficulty levels:
  clean        the digital PDF as is (text layer)
  scanned      a good scan: 200 dpi, slight skew, a little noise
  messy        faded, skewed, blurred, speckled, sometimes a coffee stain or a crease
  adversarial  messy and worse; an illegible digit is really covered (the app must
               mark it [?] and ask, never guess)

Handwriting (cash sheets, checks) is drawn with open-licence fonts bundled in
testdata\\fonts (Caveat, Reenie Beanie, Shadows Into Light - SIL OFL), with
jittered letter position and size. Everything is seeded: same seed, same bytes.
"""

import io
import random

import numpy as np
import pymupdf
from PIL import Image, ImageDraw, ImageFilter, ImageFont

import config
from testdata import render

FONTS_DIR = config.TESTDATA_DIR / "fonts"
HAND_FONTS = ["Caveat.ttf", "ReenieBeanie.ttf", "ShadowsIntoLight.ttf"]
PRINT_FONTS = [r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\consola.ttf"]

LEVELS = {
    "scanned": dict(dpi=200, angle=0.8, blur=0.3, noise=6, contrast=0.95, bright=0, stain=0.0, crease=0.0,
                    speckles=40, jpeg=85),
    "messy": dict(dpi=170, angle=2.5, blur=0.8, noise=12, contrast=0.72, bright=18, stain=0.5, crease=0.5,
                  speckles=300, jpeg=60),
    "adversarial": dict(dpi=150, angle=3.5, blur=1.05, noise=16, contrast=0.58, bright=30, stain=0.8, crease=0.7,
                        speckles=600, jpeg=50),
    "photo": dict(dpi=110, angle=4.0, blur=1.2, noise=14, contrast=0.7, bright=10, stain=0.3, crease=0.0,
                  speckles=100, jpeg=45),
}


def _font(path, size):
    try:
        return ImageFont.truetype(str(path), size)
    except OSError:
        return ImageFont.load_default()


def print_font(size, mono=False):
    for p in (PRINT_FONTS[1] if mono else PRINT_FONTS[0], PRINT_FONTS[0]):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


# ------------------------------------------------------------------ obscuring a digit in a PDF

def obscure_digit(pdf_bytes, value, pos, rng):
    """Cover the digit at raw index `pos` of amount `value` wherever it is printed."""
    printed_forms = [render.money(value), render.money(value, dollar=False)]
    raw = str(value)
    k = sum(1 for ch in raw[:pos + 1] if ch.isdigit())          # the k-th digit
    doc = pymupdf.open("pdf", pdf_bytes)
    for page in doc:
        done = set()
        for printed in printed_forms:
            for rect in page.search_for(printed):
                key = (round(rect.x0), round(rect.y0))
                if key in done:
                    continue
                done.add(key)
                idx = [i for i, ch in enumerate(printed) if ch.isdigit()][k - 1]
                cw = rect.width / len(printed)
                x0 = rect.x0 + idx * cw
                blot = pymupdf.Rect(x0 - cw * 0.15, rect.y0 + rect.height * 0.12, x0 + cw * 1.15, rect.y1 - rect.height * 0.08)
                shade = 0.25 + rng.random() * 0.15
                page.draw_oval(blot, color=None, fill=(shade, shade, shade))
    out = doc.tobytes()
    doc.close()
    return out


# ------------------------------------------------------------------ raster effects

def rasterize(pdf_bytes, dpi):
    doc = pymupdf.open("pdf", pdf_bytes)
    images = []
    for page in doc:
        pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
        images.append(Image.frombytes("L", (pix.width, pix.height), pix.samples))
    doc.close()
    return images


def effects(img, level, rng, faded=False, rotate=0.0, upside_down=False):
    p = LEVELS[level]
    arr = np.asarray(img, dtype=np.float32)
    contrast = p["contrast"] * (0.6 if faded else 1.0)
    bright = p["bright"] + (35 if faded else 0)
    arr = 255 - (255 - arr) * contrast + bright
    nprng = np.random.default_rng(rng.randint(0, 2**31))
    arr += nprng.normal(0, p["noise"], arr.shape)
    h, w = arr.shape
    if rng.random() < p["stain"]:
        cy, cx, r = rng.uniform(0.2, 0.8) * h, rng.uniform(0.2, 0.8) * w, rng.uniform(0.06, 0.14) * min(h, w)
        yy, xx = np.ogrid[:h, :w]
        d = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
        ring = np.exp(-((d - r) ** 2) / (2 * (r * 0.08) ** 2)) * 60 + (d < r) * 18
        arr -= ring
    if rng.random() < p["crease"]:
        y = int(rng.uniform(0.3, 0.7) * h)
        arr[max(0, y - 2):y + 2, :] -= 45
        arr[y + 2:y + 6, :] += 20
    for _ in range(p["speckles"]):
        y, x = rng.randrange(h), rng.randrange(w)
        arr[y:y + rng.randint(1, 3), x:x + rng.randint(1, 3)] = rng.randint(0, 90)
    arr = np.clip(arr, 0, 255).astype(np.uint8)
    img = Image.fromarray(arr, "L")
    if p["blur"]:
        img = img.filter(ImageFilter.GaussianBlur(p["blur"]))
    angle = rng.uniform(-p["angle"], p["angle"]) + rotate
    if upside_down:
        angle += 180
    img = img.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=228)
    return img


def images_to_pdf(images, dpi, jpeg, title=""):
    doc = pymupdf.open()
    for img in images:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=jpeg, optimize=False)
        wpt, hpt = img.width * 72 / dpi, img.height * 72 / dpi
        page = doc.new_page(width=wpt, height=hpt)
        page.insert_image(page.rect, stream=buf.getvalue())
    data = doc.tobytes()
    doc.close()
    return render.finish(data, title, footer=False)


def scan(pdf_bytes, level, seed, faded=False, rotate=0.0, upside_down=False, title=""):
    rng = random.Random(seed)
    p = LEVELS[level]
    pages = [effects(img, level, rng, faded, rotate, upside_down) for img in rasterize(pdf_bytes, p["dpi"])]
    return images_to_pdf(pages, p["dpi"], p["jpeg"], title)


# ------------------------------------------------------------------ handwriting

def hand(draw, xy, text, font_path, size, rng, ink=40, jitter=2.0, blot_at=None):
    """Draw text letter by letter with a slightly unsteady hand. blot_at = index of a
    character to scribble over (illegible). Returns the end x."""
    x, y = xy
    for i, ch in enumerate(text):
        f = _font(font_path, int(size * rng.uniform(0.93, 1.07)))
        dy = rng.uniform(-jitter, jitter)
        shade = max(0, min(120, ink + rng.randint(-15, 15)))
        draw.text((x, y + dy), ch, font=f, fill=shade)
        wch = draw.textlength(ch, font=f)
        if blot_at is not None and i == blot_at:
            for _ in range(14):
                x1, y1 = x + rng.uniform(-2, wch + 2), y + rng.uniform(size * 0.15, size * 0.95)
                x2, y2 = x + rng.uniform(-2, wch + 2), y + rng.uniform(size * 0.15, size * 0.95)
                draw.line((x1, y1, x2, y2), fill=shade, width=3)
        x += wch + rng.uniform(-0.5, 1.2)
    return x


def cash_log_image(spec, seed):
    rng = random.Random(seed)
    ext = spec["extraction"]
    font = FONTS_DIR / rng.choice(HAND_FONTS)
    W, H = 1700, 2200
    img = Image.new("L", (W, H), 250)
    d = ImageDraw.Draw(img)
    for y in range(260, H - 150, 90):
        d.line((80, y, W - 80, y), fill=205, width=2)
    d.line((1200, 200, 1200, H - 150), fill=215, width=2)
    d.text((80, 90), "DAILY CASH SHEET", font=print_font(46), fill=60)
    d.text((1000, 100), "Date:", font=print_font(34), fill=70)
    hand(d, (1110, 70), f"{int(ext['date'][5:7])}/{int(ext['date'][8:10])}/{ext['date'][2:4]}", font, 70, rng)
    ill = spec["render"].get("illegible")
    y = 280
    for n, row in enumerate(ext["entries"]):
        hand(d, (100, y - 70), row["description"], font, 64, rng)
        amt = row["amount"]
        blot = None
        if ill and ill["field"] == f"entries.{n}.amount":
            blot = ill["pos"]
        if spec["render"].get("crossed_out") and n == 0:
            wrong = f"{int(float(amt)) + 10}.00"
            xe = hand(d, (1240, y - 70), wrong, font, 64, rng)
            d.line((1230, y - 35, xe + 5, y - 45), fill=50, width=5)
            hand(d, (xe + 30, y - 70), amt, font, 64, rng, blot_at=blot)
        else:
            hand(d, (1240, y - 70), amt, font, 64, rng, blot_at=blot)
        y += 90
    if ext.get("total_stated"):
        y += 40
        hand(d, (100, y - 70), "Total cash in", font, 64, rng)
        d.line((1230, y - 80, W - 120, y - 80), fill=60, width=3)
        hand(d, (1240, y - 70), ext["total_stated"], font, 64, rng)
    hand(d, (100, H - 250), rng.choice(["- Sam", "closed by M.", "SR"]), font, 56, rng)
    d.text((80, H - 60), render.SYNTHETIC_MARK, font=print_font(22), fill=150)
    return img


def check_image(spec, seed):
    rng = random.Random(seed)
    ext = spec["extraction"]
    font = FONTS_DIR / rng.choice(HAND_FONTS)
    W, H = 1650, 720
    img = Image.new("L", (W, H), 244)
    d = ImageDraw.Draw(img)
    d.rectangle((6, 6, W - 6, H - 6), outline=120, width=3)
    outgoing = ext["direction"] == "outgoing"
    printed_payer = ext["payer"] if outgoing else ext["payer"]
    d.text((40, 30), printed_payer, font=print_font(32), fill=40)
    d.text((40, 70), "412 Franklin Ave, Hartford, CT 06114" if outgoing else "West Hartford, CT", font=print_font(22), fill=70)
    d.text((W - 180, 30), ext["check_no"], font=print_font(34, mono=True), fill=40)
    d.text((W - 560, 120), "DATE", font=print_font(24), fill=70)
    d.line((W - 480, 160, W - 120, 160), fill=90, width=2)
    hand(d, (W - 460, 95), f"{int(ext['date'][5:7])}/{int(ext['date'][8:10])}/{ext['date'][:4]}", font, 60, rng)
    d.text((40, 215), "PAY TO THE", font=print_font(20), fill=70)
    d.text((40, 240), "ORDER OF", font=print_font(20), fill=70)
    d.line((170, 265, W - 420, 265), fill=90, width=2)
    hand(d, (190, 195), ext["payee"], font, 62, rng)
    d.rectangle((W - 380, 200, W - 60, 275), outline=90, width=2)
    d.text((W - 410, 215), "$", font=print_font(40), fill=50)
    ill = spec["render"].get("illegible")
    hand(d, (W - 360, 205), ext["amount_numeric"], font, 62, rng,
         blot_at=ill["pos"] if ill and ill["field"] == "amount_numeric" else None)
    d.line((40, 360, W - 200, 360), fill=90, width=2)
    hand(d, (60, 290), ext["amount_words"], font, 58, rng)
    d.text((W - 190, 330), "DOLLARS", font=print_font(22), fill=70)
    d.text((40, 400), "Charter Oak Community Bank" if outgoing else "Farmington Bank", font=print_font(26), fill=60)
    d.text((40, 480), "MEMO", font=print_font(20), fill=70)
    d.line((120, 505, 700, 505), fill=90, width=2)
    hand(d, (130, 440), ext.get("memo") or "", font, 52, rng)
    d.line((W - 700, 505, W - 80, 505), fill=90, width=2)
    x = W - 660
    for _ in range(3):         # signature scribble
        pts = [(x + i * 18, 470 + rng.randint(-25, 20)) for i in range(18)]
        d.line(pts, fill=40, width=3)
        x += 40
    d.text((120, 600), f"C021000089C  A{'0931' if outgoing else '7702'}A  {ext['check_no']}",
           font=print_font(40, mono=True), fill=30)
    if ext.get("void"):
        d.text((W // 2 - 260, 160), "VOID", font=print_font(260), fill=95)
    d.text((40, H - 40), render.SYNTHETIC_MARK, font=print_font(18), fill=150)
    return img


def handwritten_pdf(spec, level, seed):
    maker = cash_log_image if spec["doc_type"] == "cash_log" else check_image
    img = maker(spec, seed)
    rng = random.Random(seed + 7)
    lvl = level if level in LEVELS else "scanned"
    img = effects(img, lvl, rng, rotate=spec["render"].get("rotate", 0.0),
                  upside_down=spec["render"].get("upside_down", False))
    return images_to_pdf([img], 200, LEVELS[lvl]["jpeg"], spec["doc_type"])
