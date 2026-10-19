#!/usr/bin/env python3
"""Measure the capture texture of realism.py and print it as a markdown table.

Usage (from the repository root):
  python tools/measure.py                      print the tables
  python tools/measure.py --readme             also replace the block between the measure markers in README.md
  python tools/measure.py --script OTHER.py    measure another copy of realism.py (an older version, say)

Everything is deterministic: fixed seeds, synthetic patches and the demo files
in assets/demo. The synthetic measurements use the same patches as the tests
(flat gray and a vertical step, white balance and fringing off, seed 5).
"""
import argparse
import contextlib
import importlib.util
import inspect
import io
import os
import re
import sys
import tempfile
from unittest import mock

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "skill", "snapshot-realism", "scripts", "realism.py")
DEMO = os.path.join(ROOT, "assets", "demo")
START, END = "<!-- measure:start -->", "<!-- measure:end -->"
SEED = 5
STRENGTHS = (0.5, 1, 2, 3)
REC601 = np.array([0.299, 0.587, 0.114])
PHOTOS = (  # (file, origin, preset, extra options): the settings the demo READMEs use
    ("muskoka-wharf/plaza-before.jpg", "camera", "night", []),
    ("muskoka-wharf/sculpture-before.jpg", "camera", "night", []),
    ("candid-male/skill-before-script.png", "generated", "indoor", ["--capture-strength", "1", "--wb-strength", "0.8"]),
)
FEED_WIDTH = 1080


def load(path):
    spec = importlib.util.spec_from_file_location("realism_measured", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(r, img, preset, cs, **kw):
    """process() with white balance and fringing off; returns the output as a float RGB array."""
    kw.setdefault("ca", 0.0)
    out, _ = r.process(img, preset, wb_strength=0.0, seed=SEED, capture_strength=cs, **kw)
    return np.asarray(out.convert("RGB"), np.float64)


def flat(level, size=(256, 256)):
    return Image.new("RGB", size, (level, level, level))


def lag2(out):
    r = out[..., 1] - out[..., 1].mean()
    return float((r[:, :-2] * r[:, 2:]).mean() / (r * r).mean())


def overshoot(r, preset, cs, w=400, h=300):
    """Bright-side overshoot of a 50 to 200 step at the center, green channel averaged over rows."""
    a = np.full((h, w), 50, np.uint8)
    a[:, w // 2:] = 200
    prof = run(r, Image.fromarray(a), preset, cs)[..., 1].mean(axis=0)
    x0 = w // 2
    return float(prof[x0:x0 + 8].max()) - float(np.median(prof[x0 + 15:x0 + 40]))


def effective(r, preset, cs, ca=0.001):
    """The CA k and sharpening amount process() actually passes on, recorded by wrapping the functions."""
    seen = {}

    def spy(name, param):
        orig = getattr(r, name)

        def wrapper(*args, **kwargs):
            seen[name] = inspect.signature(orig).bind(*args, **kwargs).arguments[param]
            return orig(*args, **kwargs)
        return wrapper
    img = Image.fromarray(np.random.default_rng(7).integers(30, 225, size=(72, 96, 3), dtype=np.uint8))
    with mock.patch.object(r, "lateral_ca", spy("lateral_ca", "k")), \
            mock.patch.object(r, "phone_sharpen", spy("phone_sharpen", "amount")):
        r.process(img, preset, wb_strength=0.0, ca=ca, seed=SEED, capture_strength=cs)
    return seen.get("lateral_ca", 0.0), seen.get("phone_sharpen", 0.0)


def texture_rows(r):
    rows = []
    for preset in ("day", "indoor", "night"):
        for cs in STRENGTHS:
            luma = float((run(r, flat(118), preset, cs) @ REC601).std())
            sixty = run(r, flat(60), preset, cs)
            with mock.patch.object(r, "phone_sharpen", lambda s, *a, **k: s):
                soft = run(r, flat(60), preset, cs)
            shifts = [float(run(r, flat(v, (128, 128)), preset, cs).mean()) - v for v in (0, 20, 128)]
            k, amount = effective(r, preset, cs)
            rows.append("| %s | %g | %.2f | %.2f | %.2f / %.2f | %s | %.1f | %.4f | %.2f |" % (
                preset, cs, luma, lag2(sixty), sixty[..., 1].std(), soft[..., 1].std(),
                " / ".join("%+.2f" % d for d in shifts), overshoot(r, preset, cs), k, amount))
    return rows


def photo_rows(r):
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        for name, origin, preset, extra in PHOTOS:
            src = os.path.join(DEMO, *name.split("/"))
            with Image.open(src) as im:
                before, _, _ = r.load_srgb(im)
            out = os.path.join(tmp, "out.jpg")
            argv = [src, "--origin", origin, "--preset", preset, "--seed", "42", "--max-edge",
                    str(max(before.size)), "--overwrite", "-o", out] + extra
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                if r.main(argv) != 0:
                    raise SystemExit("realism.py failed on %s" % name)
            wb = next((x for x in buf.getvalue().splitlines() if x.startswith("white balance:")), "(none)")
            with Image.open(out) as im:
                after = im.convert("RGB")
            native = float(np.abs(np.asarray(after, np.float64) - np.asarray(before, np.float64)).mean())
            size = (FEED_WIDTH, int(round(before.height * FEED_WIDTH / float(before.width))))
            feed = float(np.abs(np.asarray(after.resize(size, Image.LANCZOS), np.float64)
                                - np.asarray(before.resize(size, Image.LANCZOS), np.float64)).mean())
            rows.append("| `%s` (%dx%d) | %s, %s | %.2f | %.2f | %s |" % (
                name.split("/")[-1], before.width, before.height, origin, preset, feed, native,
                wb.replace("|", "/")))
    return rows


def report(r):
    lines = [
        "Measured with `realism.py %s` by `python tools/measure.py` (numpy %s, Pillow %s)."
        % (r.__version__, np.__version__, Image.__version__),
        "",
        "| Preset | Strength | Luma std at 118 (DN) | Lag-2 autocorrelation at 60 | Std at 60, sharpened / not (DN) "
        "| Mean shift at 0 / 20 / 128 (DN) | Edge overshoot (DN) | CA k | Sharpen |",
        "|---|---|---|---|---|---|---|---|---|",
    ] + texture_rows(r) + [
        "",
        "Grain: the luma standard deviation of a flat 256 px gray patch at sRGB 118, and how strongly "
        "each pixel's grain resembles the pixel two to its right (0 is single-pixel speckle; higher is "
        "mottled grain a few pixels wide).",
        "Sharpening: the grain on a flat patch at 60 with sharpening on and with it switched off; equal "
        "numbers mean flat areas are not sharpened.",
        "Black level: how far the average of a flat 128 px patch at 0, 20 and 128 moves; numbers near 0 "
        "mean the noise is zero-mean and does not lift shadows.",
        "Edges: the bright-side overshoot of a 50 to 200 step at the frame center (green channel), and "
        "the color-fringing scale and sharpening amount the script passes on at the default `--ca 0.001`.",
        "",
        "| Input | Origin, preset | Mean change at %d px wide (DN) | Mean change at native size (DN) "
        "| White balance line |" % FEED_WIDTH,
        "|---|---|---|---|---|",
    ] + photo_rows(r) + [
        "",
        "Photos: the average per-channel change between the decoded input and the script output, "
        "with seed 42 and the settings in the demo READMEs, both at the file's own size and with both "
        "resized to %d px wide as a feed shows them (an upscale for inputs narrower than %d px), "
        "plus the white balance line the script printed." % (FEED_WIDTH, FEED_WIDTH),
    ]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Measure realism.py's capture texture as markdown tables.")
    ap.add_argument("--script", default=SCRIPT, help="realism.py to measure (default: the one in this repository)")
    ap.add_argument("--readme", action="store_true", help="replace the measure block in README.md")
    args = ap.parse_args(argv)
    text = report(load(args.script))
    print(text)
    if args.readme:
        path = os.path.join(ROOT, "README.md")
        with open(path, encoding="utf-8", newline="") as f:
            readme = f.read()
        if readme.count(START) != 1 or readme.count(END) != 1:
            raise SystemExit("README.md needs exactly one %s and one %s line" % (START, END))
        eol = "\r\n" if "\r\n" in readme else "\n"  # keep the checkout's line endings
        block = "\n".join((START, text, END)).replace("\n", eol)
        readme = re.compile(re.escape(START) + ".*?" + re.escape(END), re.S).sub(lambda m: block, readme)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(readme)
    return 0


if __name__ == "__main__":
    sys.exit(main())
