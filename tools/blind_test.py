"""Blind A/B test kit: does the processed image pass as the phone photo?

Make the sheets, give them to people who have not seen the images, ask for
each sheet "which one is the photo taken on a phone, left or right?", then
score the answers against the key.

  python tools/blind_test.py make OUTDIR A1 B1 [A2 B2 ...] [--seed N]
      Each pair is ORIGINAL then PROCESSED (for example photo.png and
      photo_real.jpg). Writes OUTDIR/sheet-01.png and so on, one sheet per
      pair, with the two images side by side at the same height, the sides
      and the sheet order shuffled. Also writes OUTDIR/key.csv (which side
      holds the processed image; keep it away from the raters) and
      OUTDIR/answers.csv, a blank form with one row per sheet.

  python tools/blind_test.py score KEY ANSWERS [ANSWERS ...]
      ANSWERS has one line per sheet: the sheet (01, 1 or sheet-01.png) and
      the pick (L or R, left or right), separated by a comma or spaces. Blank
      picks are left out. Prints how often the processed image was picked as
      the phone photo, and the one-sided binomial p-value against guessing
      (50%). A small p-value means raters picked the processed image more
      often than chance; about 0.5 or more means they could not tell or
      preferred the original.

Needs Python 3.9+ and Pillow. The same inputs and --seed give the same files.
"""
import argparse
import csv
import math
import os
import random
import re
import sys

from PIL import Image, ImageDraw, ImageFont, ImageOps

GUTTER = 16  # pixels between and around the two images
HEADER = 48  # label strip above the images
BACKGROUND = (255, 255, 255)
INK = (40, 40, 40)
KEY_FIELDS = ["sheet", "processed_side", "original", "processed"]


def binom_sf(k, n):
    """P(X >= k) for X ~ Binomial(n, 0.5), exact, pure Python."""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    return sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n


def load_rgb(path):
    """The image upright (EXIF orientation applied) as RGB, alpha flattened onto white."""
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
            im = im.convert("RGBA")
            flat = Image.new("RGB", im.size, BACKGROUND)
            flat.paste(im, mask=im.getchannel("A"))
            return flat
        return im.convert("RGB")


def fit_height(im, height):
    if im.height == height:
        return im
    width = max(1, int(round(im.width * height / im.height)))
    return im.resize((width, height), Image.LANCZOS)


def label_font(size):
    for name in ("arial.ttf", "DejaVuSans.ttf", "Helvetica.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return None


def label_mask(text, size):
    """The text as an L-mode mask about size pixels tall; scales Pillow's small built-in font up if no TrueType font exists."""
    font = label_font(size)
    if font is not None:
        w, h = font.getbbox(text)[2:]
        tile = Image.new("L", (max(1, w), max(1, h)), 0)
        ImageDraw.Draw(tile).text((0, 0), text, fill=255, font=font)
        return tile
    small = ImageFont.load_default()
    draw = ImageDraw.Draw(Image.new("L", (1, 1)))
    if hasattr(draw, "textsize"):  # Pillow < 10
        w, h = draw.textsize(text, font=small)
    else:
        w, h = draw.textbbox((0, 0), text, font=small)[2:]
    tile = Image.new("L", (max(1, w), max(1, h)), 0)
    ImageDraw.Draw(tile).text((0, 0), text, fill=255, font=small)
    scale = max(1, size // max(1, h))
    return tile.resize((tile.width * scale, tile.height * scale), Image.NEAREST)


def draw_label(sheet, x, top, text, size, right_align=False):
    mask = label_mask(text, size)
    if right_align:
        x -= mask.width
    sheet.paste(INK, (x, top, x + mask.width, top + mask.height), mask)


def build_sheet(left, right, title, max_height):
    """Both images at one height (the smaller of the two, capped at max_height) side by side.

    L and R label the images; the sheet number sits apart at the top right.
    """
    height = min(left.height, right.height, max_height)
    left, right = fit_height(left, height), fit_height(right, height)
    width = left.width + right.width + 3 * GUTTER
    sheet = Image.new("RGB", (width, HEADER + height + GUTTER), BACKGROUND)
    sheet.paste(left, (GUTTER, HEADER))
    sheet.paste(right, (2 * GUTTER + left.width, HEADER))
    size = HEADER * 3 // 5
    top = (HEADER - size) // 2
    draw_label(sheet, GUTTER, top, "L", size)
    draw_label(sheet, 2 * GUTTER + left.width, top, "R", size)
    draw_label(sheet, width - GUTTER, top + size // 4, "sheet %s" % title, size // 2, right_align=True)
    return sheet


def make(outdir, paths, seed=None, max_height=1600, fmt="png"):
    """Write the sheets, key.csv and answers.csv; returns the key rows."""
    if len(paths) < 2 or len(paths) % 2:
        raise ValueError("give the images in pairs: ORIGINAL PROCESSED [ORIGINAL PROCESSED ...]")
    if max_height < 16:
        raise ValueError("--max-height must be at least 16")
    pairs = [(paths[i], paths[i + 1]) for i in range(0, len(paths), 2)]
    for path in paths:
        if not os.path.isfile(path):
            raise ValueError("not found: %s" % path)
    rng = random.Random(seed)
    order = list(range(len(pairs)))
    rng.shuffle(order)
    sides = [rng.choice("LR") for _ in pairs]
    os.makedirs(outdir, exist_ok=True)
    digits = max(2, len(str(len(pairs))))
    ext = "jpg" if fmt == "jpg" else "png"
    rows = []
    for n, (index, side) in enumerate(zip(order, sides), 1):
        original, processed = pairs[index]
        sheet_id = str(n).zfill(digits)
        a, b = load_rgb(original), load_rgb(processed)
        left, right = (b, a) if side == "L" else (a, b)
        sheet = build_sheet(left, right, sheet_id, max_height)
        name = os.path.join(outdir, "sheet-%s.%s" % (sheet_id, ext))
        if ext == "jpg":
            sheet.save(name, quality=95, subsampling=0)
        else:
            sheet.save(name)
        rows.append({"sheet": sheet_id, "processed_side": side,
                     "original": os.path.abspath(original), "processed": os.path.abspath(processed)})
    with open(os.path.join(outdir, "key.csv"), "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=KEY_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    with open(os.path.join(outdir, "answers.csv"), "w", newline="", encoding="utf-8") as f:
        f.write("sheet,pick\n")
        for row in rows:
            f.write("%s,\n" % row["sheet"])
    return rows


def sheet_number(text):
    m = re.search(r"\d+", text)
    return int(m.group(0)) if m else None


def read_key(path):
    key = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            side = (row.get("processed_side") or "").strip().upper()
            number = sheet_number(row.get("sheet") or "")
            if number is None or side not in ("L", "R"):
                raise ValueError("%s: bad key row %r" % (path, row))
            key[number] = side
    if not key:
        raise ValueError("%s: no sheets in the key" % path)
    return key


PICKS = {"l": "L", "left": "L", "r": "R", "right": "R"}


def read_answers(path, key):
    """{sheet number: 'L' or 'R'} from one answers file; blank picks are left out."""
    answers = {}
    with open(path, encoding="utf-8-sig") as f:
        for n, line in enumerate(f, 1):
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            parts = [p.strip() for p in re.split(r"[,;\t ]+", line)]
            if parts and parts[-1] == "":
                parts.pop()
            number = sheet_number(parts[0])
            if number is None:
                if n == 1 or parts[0].lower() == "sheet":
                    continue  # header row
                raise ValueError("%s line %d: no sheet number in %r" % (path, n, line))
            if number not in key:
                raise ValueError("%s line %d: sheet %d is not in the key" % (path, n, number))
            if number in answers:
                raise ValueError("%s line %d: sheet %d answered twice" % (path, n, number))
            if len(parts) < 2:
                continue  # blank pick
            pick = PICKS.get(parts[1].lower())
            if pick is None:
                raise ValueError("%s line %d: pick %r is not L or R" % (path, n, parts[1]))
            answers[number] = pick
    return answers


def tally(key, answers):
    """(times the processed image was picked, sheets answered)."""
    hits = sum(1 for number, pick in answers.items() if key[number] == pick)
    return hits, len(answers)


def summary(label, hits, n, sheets):
    if n == 0:
        return "%s: no answers (0 of %d sheets answered)" % (label, sheets)
    return ("%s: processed image picked as the phone photo %d of %d times (%.0f%%), "
            "%d of %d sheets answered; one-sided binomial p = %.4g"
            % (label, hits, n, 100.0 * hits / n, n, sheets, binom_sf(hits, n)))


def score(key_path, answer_paths, out=None):
    out = out or sys.stdout
    key = read_key(key_path)
    total_hits = total_n = 0
    for path in answer_paths:
        hits, n = tally(key, read_answers(path, key))
        total_hits += hits
        total_n += n
        print(summary(os.path.basename(path), hits, n, len(key)), file=out)
    if len(answer_paths) > 1:
        print(summary("all raters", total_hits, total_n, len(key) * len(answer_paths)), file=out)
        print("note: the pooled p-value treats every answer as independent; "
              "raters who share a bias make it look stronger than it is", file=out)
    print("p is the chance of picking the processed image at least this often by guessing (50%).", file=out)
    return total_hits, total_n


def main(argv=None):
    parser = argparse.ArgumentParser(prog="blind_test.py", description="Blind A/B test kit for snapshot-realism.")
    sub = parser.add_subparsers(dest="command")
    p_make = sub.add_parser("make", help="write shuffled side-by-side sheets, key.csv and answers.csv")
    p_make.add_argument("outdir")
    p_make.add_argument("images", nargs="+", help="pairs: ORIGINAL PROCESSED [ORIGINAL PROCESSED ...]")
    p_make.add_argument("--seed", type=int, default=None, help="shuffle seed; the same seed gives the same sheets")
    p_make.add_argument("--max-height", type=int, default=1600, help="tallest image height on a sheet (default 1600)")
    p_make.add_argument("--format", choices=("png", "jpg"), default="png",
                        help="png (default, lossless) or jpg (quality 95, no chroma subsampling)")
    p_score = sub.add_parser("score", help="score answers against key.csv")
    p_score.add_argument("key")
    p_score.add_argument("answers", nargs="+")
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2
    try:
        if args.command == "make":
            seed = args.seed
            if seed is None:
                seed = random.SystemRandom().randrange(2 ** 31)
            rows = make(args.outdir, args.images, seed=seed, max_height=args.max_height, fmt=args.format)
            print("wrote %d sheets, key.csv and answers.csv to %s (seed %d)" % (len(rows), args.outdir, seed))
            print("keep key.csv away from the raters")
        else:
            score(args.key, args.answers)
    except (OSError, ValueError) as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
