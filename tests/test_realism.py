"""Tests for scripts/realism.py. Run: python -m unittest discover -s tests"""
import contextlib
import hashlib
import io
import os
import struct
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zlib

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "skill", "snapshot-realism", "scripts"))

import realism  # noqa: E402

DEMO = os.path.join(ROOT, "assets", "demo")
OPENAI_PNG = os.path.join(DEMO, "candid-male", "skill-before-script.png")  # ChatGPT output with its real C2PA manifest
WARM = np.array([1.18, 1.0, 0.78], dtype=np.float32)  # GPT-style yellow-orange cast, linear gains


def scene(h=240, w=320):
    """Synthetic night-portrait stand-in (linear light): dark sky, gray surfaces at
    several levels, a large skin-tone area, saturated stage-light colors."""
    lin = np.zeros((h, w, 3), dtype=np.float32)
    lin[:] = (0.004, 0.005, 0.009)  # dark navy sky, below the estimator's floor
    for i, g in enumerate((0.05, 0.12, 0.25, 0.45, 0.7)):
        lin[h // 2:, i * w // 10:(i + 1) * w // 10] = g  # neutral patches
    lin[h // 4:, w // 2:w * 3 // 4] = (0.42, 0.27, 0.19)  # skin
    lin[: h // 4, w // 2:] = (0.05, 0.08, 0.6)  # blue stage light
    lin[: h // 4, : w // 4] = (0.6, 0.1, 0.5)  # magenta light
    return lin


def textured(rgb, h=160, w=160, amp=0.04, seed=0):
    """A colored surface with mild texture (linear light): no neutral anywhere."""
    rng = np.random.default_rng(seed)
    base = realism.srgb_to_linear(np.array(rgb, np.float32) / 255.0)
    tex = 1.0 + amp * rng.standard_normal((h, w, 1)).astype(np.float32)
    return np.clip(base * tex, 0, 1).astype(np.float32)


def to_image(lin):
    return Image.fromarray((realism.linear_to_srgb(lin) * 255 + 0.5).astype(np.uint8))


def gray_rb(lin):
    """Mean R/B ratio over the neutral patches."""
    h, w, _ = lin.shape
    patch = lin[h // 2:, : w // 2]
    return float(patch[..., 0].mean() / patch[..., 2].mean())


def lab(rgb8):
    """sRGB 0-255 array (..., 3) to CIE Lab (D65)."""
    lin = realism.srgb_to_linear(np.asarray(rgb8, np.float32) / 255.0).astype(np.float64)
    m = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = lin @ m.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def _s15(v):
    return struct.pack(">i", int(round(v * 65536)))


def _assemble_icc(space, tags):
    """ICC v2 display profile bytes from (signature, body) tags, D50 PCS, a valid fixed date."""
    d50 = (0.96422, 1.0, 0.82521)
    off, table, data = 128 + 4 + 12 * len(tags), b"", b""
    for sig, body in tags:
        while (off + len(data)) % 4:
            data += b"\0"
        table += sig + struct.pack(">II", off + len(data), len(body))
        data += body
    body = struct.pack(">I", len(tags)) + table + data
    hdr = (struct.pack(">I", 128 + len(body)) + b"lcms" + bytes([2, 0x10, 0, 0]) + b"mntr" + space + b"XYZ "
           + struct.pack(">6H", 2026, 1, 1, 0, 0, 0) + b"acsp" + b"MSFT" + b"\0" * 24
           + b"".join(_s15(c) for c in d50) + b"\0" * 48)
    assert len(hdr) == 128
    return hdr + body


def _desc(name):
    name = name.encode("ascii") + b"\0"
    return b"desc" + b"\0" * 4 + struct.pack(">I", len(name)) + name + b"\0" * 8 + b"\0" * 70


def linear_gray_profile():
    """A gray profile with a linear (gamma 1.0) tone curve: code 128 is half the light, sRGB 188."""
    wtpt = b"XYZ " + b"\0" * 4 + b"".join(_s15(c) for c in (0.96422, 1.0, 0.82521))
    identity = b"curv" + b"\0" * 4 + struct.pack(">I", 0)  # no entries: the identity curve
    return _assemble_icc(b"GRAY", [(b"desc", _desc("Linear gray (test)")), (b"cprt", b"text" + b"\0" * 4 + b"none\0"),
                                   (b"wtpt", wtpt), (b"kTRC", identity)])


def p3_profile(name="Display P3 (test)"):
    """A minimal ICC v2 Display P3 matrix/TRC profile, built here so no profile file ships."""
    def rgb_to_xyz(prim, white):
        xy = np.array(prim)
        m = np.stack([xy[:, 0] / xy[:, 1], np.ones(3), (1 - xy[:, 0] - xy[:, 1]) / xy[:, 1]], 1).T
        wv = np.array([white[0] / white[1], 1, (1 - white[0] - white[1]) / white[1]])
        return m * np.linalg.solve(m, wv)

    brad = np.array([[0.8951, 0.2664, -0.1614], [-0.7502, 1.7135, 0.0367], [0.0389, -0.0685, 1.0296]])
    d65, d50 = np.array([0.95047, 1.0, 1.08883]), np.array([0.96422, 1.0, 0.82521])
    adapt = np.linalg.inv(brad) @ np.diag((brad @ d50) / (brad @ d65)) @ brad
    m = adapt @ rgb_to_xyz([(0.680, 0.320), (0.265, 0.690), (0.150, 0.060)], (0.3127, 0.3290))

    def s15(v):
        return struct.pack(">i", int(round(v * 65536)))

    def xyz(v):
        return b"XYZ " + b"\0" * 4 + b"".join(s15(c) for c in v)

    x = np.linspace(0, 1, 1024)
    y = np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)
    curve = b"curv" + b"\0" * 4 + struct.pack(">I", 1024) + b"".join(struct.pack(">H", int(round(v * 65535))) for v in y)
    tags = [(b"desc", _desc(name)), (b"cprt", b"text" + b"\0" * 4 + b"none\0"), (b"wtpt", xyz(d50)),
            (b"rXYZ", xyz(m[:, 0])), (b"gXYZ", xyz(m[:, 1])), (b"bXYZ", xyz(m[:, 2])),
            (b"rTRC", curve), (b"gTRC", curve), (b"bTRC", curve)]
    return _assemble_icc(b"RGB ", tags)


def png_with_chunk(img, ctype, payload):
    """PNG bytes with one extra chunk inserted before IEND."""
    buf = io.BytesIO()
    img.save(buf, "PNG")
    data = buf.getvalue()
    chunk = struct.pack(">I", len(payload)) + ctype + payload + struct.pack(">I", zlib.crc32(ctype + payload))
    return data[:-12] + chunk + data[-12:]


def jpeg_with(img, *segments):
    """JPEG bytes with extra marker segments right after SOI (works on any Pillow)."""
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    data = buf.getvalue()
    return data[:2] + b"".join(segments) + data[2:]


def cbor(text):
    """A CBOR text string, as C2PA manifests store keys and values."""
    b = text.encode("utf-8")
    return (bytes([0x60 + len(b)]) if len(b) < 24 else b"\x78" + bytes([len(b)])) + b


def segment(marker, payload):
    return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload


def exif_user_comment(comment):
    """APP1 Exif segment holding only an Exif IFD with a UserComment (0x9286, UNDEFINED)."""
    ifd0 = struct.pack("<HHHII", 1, 0x8769, 4, 1, 26) + struct.pack("<I", 0)  # Exif IFD pointer: offset 26
    exif_ifd = struct.pack("<HHHII", 1, 0x9286, 7, len(comment), 44) + struct.pack("<I", 0)  # data at 44
    return segment(0xE1, b"Exif\x00\x00" + b"II*\x00" + struct.pack("<I", 8) + ifd0 + exif_ifd + comment)


def read(path):
    with open(path, "rb") as f:
        return f.read()


def md5(path):
    return hashlib.md5(read(path)).hexdigest()


def run(argv):
    """main() with stdout and stderr captured: (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = realism.main(argv)
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


class WhiteBalance(unittest.TestCase):
    def test_warm_cast_on_gray_patches_detected_and_removed(self):
        lin = scene() * WARM
        cast, conf, _ = realism.estimate_cast(lin)
        self.assertGreater(cast[0] - cast[2], 0.2)
        self.assertEqual(conf, 1.0)
        fixed = realism.white_balance(lin, cast, strength=1.0)
        self.assertAlmostEqual(gray_rb(fixed), 1.0, delta=0.06)

    def test_default_strength_removes_most_of_the_cast(self):
        lin = scene() * WARM
        cast, conf, _ = realism.estimate_cast(lin)
        before = abs(np.log(gray_rb(lin)))
        after = abs(np.log(gray_rb(realism.white_balance(lin, cast, 0.8 * conf))))
        self.assertLess(after, 0.3 * before)

    def test_neutral_scene_left_alone(self):
        cast, _, _ = realism.estimate_cast(scene())
        self.assertLess(float(np.linalg.norm(cast)), 0.03)

    def test_dark_blue_sky_does_not_warm_the_image(self):
        lin = scene()
        lin[: lin.shape[0] // 2] = (0.008, 0.009, 0.012)  # half the frame: dull blue-gray night sky
        cast, _, _ = realism.estimate_cast(lin)
        self.assertLess(abs(float(cast[0] - cast[2])), 0.03)

    def test_scenes_without_a_neutral_surface_are_not_corrected(self):
        # mixed frames: the surface fills 70%, with denim and a red top beside it,
        # so the gate (not the monochrome check) has to make the call
        cases = {
            "peach wall": (235, 189, 160),
            "terracotta wall": (190, 110, 80),
            "foliage": (70, 120, 50),
            "skin close-up": (214, 160, 130),
            "golden hour": (240, 170, 90),
            "blue hour": (80, 100, 150),
        }
        for name, rgb in cases.items():
            with self.subTest(name):
                lin = textured(rgb, 200, 200)
                lin[:, 140:] = textured((60, 80, 120), 200, 60, seed=1)  # jeans
                lin[:60, :140] = textured((180, 30, 40), 60, 140, seed=2)  # red top
                cast, conf, reason = realism.estimate_cast(lin)
                self.assertEqual(conf, 0.0, reason)
                self.assertNotIn("monochrome", reason)

    def test_toned_monochrome_is_not_corrected(self):
        cast, conf, reason = realism.estimate_cast(textured((120, 109, 99)))
        self.assertEqual(conf, 0.0)
        self.assertEqual(reason, "toned monochrome image")

    def test_skin_survives_alongside_a_cast_gray_wall(self):
        lin = textured((150, 150, 150), 160, 160)
        lin[:, 80:] = textured((214, 160, 130), 160, 80)
        lin = lin * WARM
        cast, conf, _ = realism.estimate_cast(lin)
        self.assertGreater(conf, 0.0)
        fixed = realism.white_balance(lin, cast, 0.8 * conf)
        wall = lab(realism.linear_to_srgb(fixed[:, :80]).mean(axis=(0, 1)) * 255)
        self.assertLess(abs(wall[2]), 4.0)  # the gray wall ends close to neutral

    def test_manual_warmth_cools(self):
        lin = scene()
        cooler = realism.white_balance(lin, np.zeros(3, np.float32), warmth=-0.2)
        self.assertLess(gray_rb(cooler), 0.9)

    def test_brightness_preserved(self):
        lin = scene() * WARM
        cast, _, _ = realism.estimate_cast(lin)
        fixed = realism.white_balance(lin, cast, strength=1.0)
        y0 = float((lin @ realism.REC709).mean())
        y1 = float((fixed @ realism.REC709).mean())
        self.assertAlmostEqual(y1 / y0, 1.0, delta=0.03)

    def test_white_stays_white(self):
        lin = scene() * WARM
        lin[:20, :20] = 1.0  # highlight the input had already clipped
        lin[20:40, :20] = 0.7 * WARM  # bright white under the same cast
        cast, _, _ = realism.estimate_cast(lin)
        fixed = realism.white_balance(lin, cast, strength=1.0)
        self.assertTrue(np.all(fixed[:20, :20] == 1.0))  # not tinted cyan by per-channel clipping
        bright = fixed[20:40, :20].reshape(-1, 3).mean(axis=0)
        self.assertLess(float(bright.max() - bright.min()), 0.03)  # corrected to neutral

    def test_bright_unclipped_whites_are_corrected_all_the_way_up(self):
        lin = scene() * WARM
        ramp = np.linspace(0.55, 0.95, lin.shape[1], dtype=np.float32)[None, :, None]
        lin[:60] = ramp * WARM / WARM.max()  # warm-cast white ramp, brightest channel up to 0.95
        cast, conf, _ = realism.estimate_cast(lin)
        fixed = realism.white_balance(lin, cast, 0.8 * conf)
        b = lab(realism.linear_to_srgb(fixed[10:50]).mean(axis=0) * 255)[:, 2]
        self.assertLess(float(np.abs(b).max()), 4.0)  # no yellow band at the bright end

    def test_blown_lamp_goes_white_and_neon_stays_red(self):
        lin = scene() * WARM
        lin[:20, :20] = (1.0, 1.0, 0.83)  # a lamp clipped in red and green
        lin[20:40, :20] = (1.0, 0.2, 0.1)  # a red neon sign, clipped in red
        cast, conf, _ = realism.estimate_cast(lin)
        fixed = realism.white_balance(lin, cast, 0.8 * conf)
        lamp = fixed[:20, :20].reshape(-1, 3).mean(axis=0)
        self.assertLess(float(lamp.max() - lamp.min()), 0.05)  # neutral, not cyan or blue
        self.assertGreater(float(lamp.min()), 0.9)
        neon = fixed[20:40, :20].reshape(-1, 3).mean(axis=0)
        self.assertGreater(float(neon[0] - neon[2]), 0.5)  # still red

    def test_pixels_pushed_past_white_are_scaled_not_clipped(self):
        lin = np.full((64, 64, 3), 0.2, np.float32)
        lin[:8, :8] = (0.80, 0.86, 0.6)  # bright, unclipped, warm
        out = realism.white_balance(lin, np.array([0.3, 0.0, -0.3], np.float32), strength=1.0, warmth=-0.5)
        px = out[:8, :8].reshape(-1, 3).mean(axis=0)
        self.assertLessEqual(float(px.max()), 1.0)
        self.assertGreater(float(px.max() - px.min()), 0.05)  # hue kept, not flattened to white
        self.assertLess(float(px[0] / px[2]), float(lin[0, 0, 0] / lin[0, 0, 2]))  # still cooled

    def test_demo_whites_and_skin_keep_their_color(self):
        # The generated portrait supplies both near-white shirt pixels and skin.
        for name in ("skill-before-script.png",):
            with self.subTest(name), Image.open(os.path.join(DEMO, "candid-male", name)) as src:
                out, _ = realism.process(src, "indoor", 0.8, seed=0, max_edge=4096)
                a = lab(np.asarray(src.convert("RGB"), np.float32))
                b = lab(np.asarray(out, np.float32))
                whites = (a[..., 0] > 85) & (np.hypot(a[..., 1], a[..., 2]) < 6)
                self.assertGreater(int(whites.sum()), 1000)
                shift = b[whites].mean(axis=0) - a[whites].mean(axis=0)
                self.assertLess(abs(float(shift[2])), 2.0)  # whites do not go blue
                skin = (a[..., 1] > 8) & (a[..., 2] > 10) & (a[..., 0] > 40) & (a[..., 0] < 80)
                chroma = lambda x: float(np.hypot(x[skin, 1], x[skin, 2]).mean())  # noqa: E731
                self.assertGreater(chroma(b) / chroma(a), 0.9)


class Color(unittest.TestCase):
    def test_display_p3_input_is_converted_to_srgb(self):
        from PIL import ImageCms
        # the second name claims sRGB; the primaries are still P3, so it must be converted too
        for name in ("Display P3 (test)", "P3 / sRGB TRC"):
            with self.subTest(name):
                icc = p3_profile(name)
                px = Image.new("RGB", (64, 64), (200, 120, 80))
                ref = ImageCms.profileToProfile(px, ImageCms.ImageCmsProfile(io.BytesIO(icc)),
                                                ImageCms.createProfile("sRGB"), renderingIntent=1, outputMode="RGB")
                buf = io.BytesIO()
                px.save(buf, "PNG", icc_profile=icc)
                with Image.open(buf) as tagged:
                    rgb, mono, notes = realism.load_srgb(tagged)
                got = np.asarray(rgb, np.int16)[0, 0]
                self.assertLessEqual(int(np.abs(got - np.asarray(ref, np.int16)[0, 0]).max()), 1)
                self.assertGreater(int(np.abs(got - np.array([200, 120, 80])).max()), 5)
                # the note only stays quiet for a profile that calls itself sRGB
                self.assertEqual("converted the embedded color profile to sRGB" in notes, "sRGB" not in name)

    def test_grayscale_profile_is_applied(self):
        from PIL import ImageCms
        icc = linear_gray_profile()
        px = Image.new("L", (32, 32), 128)
        ref = ImageCms.profileToProfile(px, ImageCms.ImageCmsProfile(io.BytesIO(icc)),
                                        ImageCms.createProfile("sRGB"), renderingIntent=1, outputMode="RGB")
        for mode in ("L", "LA"):
            with self.subTest(mode):
                buf = io.BytesIO()
                px.convert(mode).save(buf, "PNG", icc_profile=icc)
                with Image.open(buf) as tagged:
                    rgb, mono, notes = realism.load_srgb(tagged)
                self.assertTrue(mono)
                self.assertAlmostEqual(rgb.getpixel((3, 3))[0], ref.getpixel((3, 3))[0], delta=1)
                self.assertAlmostEqual(rgb.getpixel((3, 3))[0], 188, delta=2)  # linear 0.5 in sRGB

    def test_outputs_embed_a_valid_srgb_profile(self):
        from PIL import ImageCms
        data = realism.encode_jpeg(Image.new("RGB", (32, 32), (90, 100, 110)), 88, "camera")
        with Image.open(io.BytesIO(data)) as im:
            icc = im.info.get("icc_profile")
        self.assertTrue(icc)
        year, month, day = struct.unpack(">3H", icc[24:30])
        self.assertGreaterEqual(year, 1900)
        self.assertTrue(1 <= month <= 12 and 1 <= day <= 31)  # ICC dateTimeNumber: month and day start at 1
        profile = ImageCms.ImageCmsProfile(io.BytesIO(icc)).profile
        if hasattr(profile, "creation_date"):
            self.assertEqual(profile.creation_date.year, year)  # Pillow can parse it (it raises on month 0)

    def test_transparency_is_flattened_onto_white(self):
        rgba = Image.new("RGBA", (40, 40), (255, 0, 0, 0))  # red hidden under full transparency
        rgb, _, notes = realism.load_srgb(rgba)
        self.assertEqual(rgb.getpixel((5, 5)), (255, 255, 255))
        self.assertIn("transparency flattened onto white", notes)

    def test_16_bit_gray_is_scaled_not_clipped(self):
        im = Image.fromarray(np.full((32, 32), 40000, np.uint16))
        rgb, mono, _ = realism.load_srgb(im)
        self.assertTrue(mono)
        self.assertAlmostEqual(rgb.getpixel((3, 3))[0], round(40000 / 257), delta=1)

    def test_grayscale_input_gets_no_color_noise(self):
        for mode in ("L", "RGB"):
            with self.subTest(mode):
                g = Image.fromarray(np.tile(np.linspace(10, 240, 200, dtype=np.uint8), (150, 1))).convert(mode)
                out, info = realism.process(g, "night", seed=3)
                self.assertTrue(info["mono"])
                self.assertEqual(out.mode, "L")

    def test_small_colored_area_keeps_the_image_in_color(self):
        a = np.tile(np.linspace(20, 230, 400).astype(np.uint8)[:, None], (1, 400))
        img = np.stack([a, a, a], axis=2)
        img[100:120, 100:120] = (220, 30, 30)  # 0.25% of the frame
        out, info = realism.process(Image.fromarray(img), "day", seed=0, ca=0)
        self.assertFalse(info["mono"])
        r, g, b = np.asarray(out, np.float32)[105:115, 105:115].reshape(-1, 3).mean(axis=0)
        self.assertGreater(r - g, 100)

    def test_fine_color_pattern_stays_in_color(self):
        a = np.zeros((256, 256, 3), np.uint8)
        a[:, 0::2] = (230, 40, 40)  # red and cyan columns: a 2x2 average is gray
        a[:, 1::2] = (40, 230, 230)
        rgb, mono, _ = realism.load_srgb(Image.fromarray(a))
        self.assertFalse(mono)
        out, _ = realism.process(Image.fromarray(a), "day", seed=0)
        self.assertEqual(out.mode, "RGB")

    def test_grainy_black_and_white_stays_monochrome_and_a_logo_stays_color(self):
        rng = np.random.default_rng(0)
        g = np.tile(np.linspace(20, 230, 800), (600, 1))
        grainy = np.clip(g[..., None] + rng.normal(0, 5, (600, 800, 3)), 0, 255).astype(np.uint8)
        self.assertTrue(realism.load_srgb(Image.fromarray(grainy))[1])
        logo = grainy.copy()
        logo[300:308, 400:408] = (220, 30, 30)
        self.assertFalse(realism.load_srgb(Image.fromarray(logo))[1])

    def test_mismatched_profile_is_named_not_called_unreadable(self):
        buf = io.BytesIO()
        Image.new("RGB", (32, 32), (90, 100, 110)).save(buf, "PNG", icc_profile=linear_gray_profile())
        with Image.open(buf) as im:
            rgb, _, notes = realism.load_srgb(im)
        self.assertEqual(rgb.getpixel((1, 1)), (90, 100, 110))
        self.assertIn("ignored a GRAY color profile on RGB data; treated as sRGB", notes)
        cmyk = io.BytesIO()
        Image.new("CMYK", (32, 32), (0, 40, 60, 10)).save(cmyk, "JPEG", icc_profile=p3_profile())
        with Image.open(cmyk) as im:
            rgb, _, notes = realism.load_srgb(im)  # no longer a hard failure
        self.assertIn("the embedded color profile is not a CMYK profile: colors are approximate", notes)

    def test_slightly_noisy_black_and_white_is_still_monochrome(self):
        rng = np.random.default_rng(0)
        g = np.tile(np.linspace(20, 230, 400)[:, None], (1, 400))
        a = np.clip(g[..., None] + rng.normal(0, 1.0, (400, 400, 3)), 0, 255).astype(np.uint8)
        self.assertTrue(realism.load_srgb(Image.fromarray(a))[1])

    def test_float_edge_cases(self):
        a = np.full((32, 32), 0.5, np.float32)
        a[0, 0] = np.nan
        self.assertAlmostEqual(realism.load_srgb(Image.fromarray(a))[0].getpixel((5, 5))[0], 128, delta=1)
        b = np.full((32, 32), 32768.0, np.float32)  # 0 to 65535 data
        self.assertAlmostEqual(realism.load_srgb(Image.fromarray(b))[0].getpixel((5, 5))[0], 128, delta=1)

    def test_float_and_dark_16_bit_inputs_keep_their_level(self):
        f = Image.fromarray(np.full((32, 32), 0.5, np.float32))  # mode F, 0 to 1
        self.assertAlmostEqual(realism.load_srgb(f)[0].getpixel((1, 1))[0], 128, delta=1)
        dark = io.BytesIO()
        Image.fromarray(np.full((32, 32), 200, np.uint16)).save(dark, "PNG")  # 16-bit, very dark
        with Image.open(dark) as im:
            self.assertLessEqual(realism.load_srgb(im)[0].getpixel((1, 1))[0], 1)

    def test_unreadable_profile_on_rgb_is_ignored_with_a_note(self):
        buf = io.BytesIO()
        Image.new("RGB", (32, 32), (90, 100, 110)).save(buf, "PNG", icc_profile=b"garbage" * 30)
        with Image.open(buf) as im:
            rgb, _, notes = realism.load_srgb(im)
        self.assertEqual(rgb.getpixel((1, 1)), (90, 100, 110))
        self.assertIn("ignored an unreadable color profile; treated as sRGB", notes)

    def test_cmyk_input_is_processed(self):
        out, info = realism.process(Image.new("CMYK", (64, 48), (0, 40, 60, 10)), "day", seed=0)
        self.assertEqual(out.size, (64, 48))
        self.assertIn("CMYK without a color profile: colors are approximate", info["notes"])


class Capture(unittest.TestCase):
    def test_noise_is_strongest_in_shadows(self):
        lin = np.zeros((128, 256, 3), np.float32)
        lin[:, :128] = 0.01  # shadow
        lin[:, 128:] = 0.4  # highlight
        p = realism.PRESETS["night"]
        noisy = realism.linear_to_srgb(
            realism.add_sensor_noise(lin, p["shot"], p["read"], p["chroma"], np.random.default_rng(0)))
        self.assertGreater(noisy[:, :128].std(), 1.5 * noisy[:, 128:].std())

    def test_chroma_noise_present(self):
        lin = np.full((128, 128, 3), 0.05, np.float32)
        p = realism.PRESETS["night"]
        noisy = realism.add_sensor_noise(lin, p["shot"], p["read"], p["chroma"], np.random.default_rng(1))
        self.assertGreater(float((noisy[..., 0] - noisy[..., 2]).std()), 0.002)

    def test_clipped_highlights_get_no_noise(self):
        lin = np.ones((64, 64, 3), np.float32)
        p = realism.PRESETS["night"]
        noisy = realism.add_sensor_noise(lin, p["shot"], p["read"], p["chroma"], np.random.default_rng(2))
        self.assertEqual(float(noisy.std()), 0.0)

    def test_noise_reduction_flattens_texture_but_keeps_edges(self):
        rng = np.random.default_rng(2)
        s = np.full((96, 96, 3), 0.5, np.float32) + rng.normal(0, 0.01, (96, 96, 1)).astype(np.float32)
        s[:, 48:] += 0.4  # hard edge
        out = realism.phone_nr(s, realism.flat_mask(np.full_like(s, 0.5)), 0.6, 2.0)
        self.assertLess(out[:, 5:40].std(), 0.8 * s[:, 5:40].std())
        self.assertGreater(out[:, 52:].mean() - out[:, :44].mean(), 0.35)

    def test_flat_areas_end_less_noisy_than_texture(self):
        rng = np.random.default_rng(4)
        a = np.full((200, 200, 3), 128, np.uint8)
        a[:, 100:] = np.clip(128 + rng.normal(0, 30, (200, 100, 1)), 0, 255).astype(np.uint8)
        img = Image.fromarray(a)
        x = np.asarray(realism.process(img, "night", 0.0, seed=1)[0], np.float32)
        y = np.asarray(realism.process(img, "night", 0.0, seed=2)[0], np.float32)
        noise = (x - y)[10:-10]  # two noise draws, same everything else
        self.assertLess(noise[:, 10:90].std(), noise[:, 110:190].std())

    def test_ca_is_zero_at_center_and_grows_outward(self):
        h, w = 101, 101
        lin = np.zeros((h, w, 3), np.float32)
        lin[:, ::10] = 1.0  # vertical lines every 10 px
        out = realism.lateral_ca(lin, 0.02)
        diff = np.abs(out[..., 0] - out[..., 2]).mean(axis=0)
        self.assertLess(diff[45:56].mean(), diff[:10].mean())


class Pipeline(unittest.TestCase):
    def test_downscale_only(self):
        small = Image.new("RGB", (400, 300), (120, 110, 100))
        big = Image.new("RGB", (3000, 2000), (120, 110, 100))
        self.assertEqual(realism.process(small, "day", seed=0)[0].size, (400, 300))
        self.assertEqual(realism.process(big, "day", seed=0)[0].size, (1600, 1067))

    def test_downscale_happens_in_linear_light(self):
        stripes = np.zeros((40, 400), np.uint8)
        stripes[:, ::2] = 255  # 1 px black and white lines
        lin = realism.to_linear(Image.fromarray(stripes).convert("RGB"), 200)
        level = float(realism.linear_to_srgb(lin[5:-5, 5:-5].mean(axis=(0, 1)))[0] * 255)
        self.assertAlmostEqual(level, 188, delta=4)  # half the light, not half the code value (128)

    def test_seed_is_repeatable(self):
        img = to_image(scene())
        a = np.asarray(realism.process(img, "night", seed=7)[0])
        b = np.asarray(realism.process(img, "night", seed=7)[0])
        self.assertTrue(np.array_equal(a, b))

    def test_presets_scale_the_noise(self):
        img = Image.new("RGB", (160, 160), (60, 60, 60))
        spread = {p: float(np.asarray(realism.process(img, p, seed=5)[0], np.float32).std())
                  for p in ("day", "indoor", "night")}
        self.assertLess(spread["day"], spread["indoor"])
        self.assertLess(spread["indoor"], spread["night"])


class Provenance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def path(self, name):
        return os.path.join(self.d, name)

    def save(self, name, img=None, data=None):
        p = self.path(name)
        if data is not None:
            with open(p, "wb") as f:
                f.write(data)
        else:
            (img or to_image(scene())).save(p)
        return p

    def test_generated_output_and_comparison_carry_exif_and_xmp(self):
        src = self.save("in.png", to_image(scene() * WARM))
        code, out, _ = run([src, "--origin", "generated", "--preset", "night", "--seed", "1", "--compare"])
        self.assertEqual(code, 0)
        for name in ("in_real.jpg", "in_real_compare.jpg"):
            with open(self.path(name), "rb") as f:
                data = f.read()
            with Image.open(io.BytesIO(data)) as im:
                self.assertEqual(im.format, "JPEG")
                self.assertIn("AI-generated image", im.getexif().get(0x010E, ""))
                self.assertTrue(im.info.get("icc_profile"))
            self.assertEqual(realism.disclosure_problems(data, "generated"), [])
            start = data.index(b"<x:xmpmeta")
            end = data.index(b"</x:xmpmeta>") + len(b"</x:xmpmeta>")
            root = ET.fromstring(data[start:end])
            desc = root.find(".//{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Description")
            self.assertEqual(desc.get("{http://iptc.org/std/Iptc4xmpExt/2008-02-29/}DigitalSourceType"),
                             "http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia")
            self.assertIn(b'begin="\xef\xbb\xbf"', data)  # the XMP packet's byte-order mark

    def test_edited_output_is_labelled_composite(self):
        src = self.save("edit.png")
        self.assertEqual(run([src, "--origin", "edited", "--preset", "day", "--seed", "1"])[0], 0)
        with open(self.path("edit_real.jpg"), "rb") as f:
            data = f.read()
        self.assertIn(b"digitalsourcetype/compositeWithTrainedAlgorithmicMedia", data)
        self.assertEqual(realism.disclosure_problems(data, "edited"), [])

    def test_camera_output_and_comparison_have_no_ai_marker(self):
        src = self.save("camera.png")
        self.assertEqual(run([src, "--origin", "camera", "--preset", "indoor", "--seed", "1", "--compare"])[0], 0)
        for name in ("camera_real.jpg", "camera_real_compare.jpg"):
            with open(self.path(name), "rb") as f:
                data = f.read()
            self.assertNotIn(b"digitalsourcetype", data.lower())
            with Image.open(io.BytesIO(data)) as im:
                text = str(im.getexif().get(0x010E, ""))
                self.assertIn("origin declared as camera, not verified", text)
                self.assertEqual(realism.ai_signals(data, im), [])

    def test_xmp_does_not_depend_on_pillow(self):
        plain = io.BytesIO()
        Image.new("RGB", (16, 16)).save(plain, "JPEG", exif=Image.Exif().tobytes())
        data = realism.insert_xmp(plain.getvalue(), realism.xmp_packet("trainedAlgorithmicMedia"))
        with Image.open(io.BytesIO(data)) as im:
            im.load()
        self.assertEqual(data[:2], b"\xff\xd8")
        self.assertIn(realism.XMP_SIG, data)

    def test_camera_mode_rejects_ai_marked_inputs(self):
        base = to_image(scene())
        xmp = realism.xmp_packet("compositeSynthetic")
        itxt = b"XML:com.adobe.xmp\x00\x00\x00\x00\x00" + xmp
        exif = Image.Exif()
        exif[0x010E] = "Made with AI"
        exif_jpeg = io.BytesIO()
        base.save(exif_jpeg, "JPEG", exif=exif.tobytes())
        tex = io.BytesIO()
        from PIL import PngImagePlugin
        info = PngImagePlugin.PngInfo()
        info.add_text("parameters", "a photo of a cat, Steps: 20, Sampler: Euler")
        base.save(tex, "PNG", pnginfo=info)
        cases = {
            "chatgpt.png": read(OPENAI_PNG),  # real OpenAI C2PA
            "c2pa-no-type.png": png_with_chunk(base, b"caBX", b"\x00\x00\x00\x20jumb\x00\x00\x00\x18jumdc2pa"),
            "xmp.png": png_with_chunk(base, b"iTXt", itxt),
            "a1111.png": tex.getvalue(),
            "exif.jpg": exif_jpeg.getvalue(),
        }
        for name, data in cases.items():
            with self.subTest(name):
                src = self.save(name, data=data)
                code, _, err = run([src, "--origin", "camera", "--preset", "day"])
                self.assertEqual(code, 2)
                self.assertIn("AI-generated or AI-edited", err)
                self.assertFalse(os.path.exists(self.path(os.path.splitext(name)[0] + "_real.jpg")))

    def test_camera_c2pa_edited_by_an_ai_tool_is_refused(self):
        # a camera ingredient declares digitalCapture; the AI edit names its tool but no source type
        capture = b"jumdc2pa" + cbor("http://cv.iptc.org/newscodes/digitalsourcetype/digitalCapture")
        agents = {"map": cbor("softwareAgent") + b"\xa1" + cbor("name") + cbor("Adobe Firefly"),
                  "string": cbor("softwareAgent") + cbor("Adobe Firefly")}
        for form, agent in agents.items():
            with self.subTest(form):
                payload = capture + cbor("action") + cbor("c2pa.edited") + agent
                src = self.save("fill-%s.png" % form, data=png_with_chunk(to_image(scene()), b"caBX", payload))
                code, _, err = run([src, "--origin", "camera", "--preset", "day"])
                self.assertEqual(code, 2)
                self.assertIn("naming an AI tool: firefly", err)

    def test_c2pa_tool_names_are_found_in_every_common_layout(self):
        icon = cbor("icon") + b"\xa2" + cbor("format") + cbor("image/svg+xml") + cbor("identifier") + cbor("x")
        firefly = cbor("name") + cbor("Adobe Firefly")
        layouts = {
            "array of two generators": cbor("claim_generator_info") + b"\x82\xa2" + cbor("name")
            + cbor("Adobe Photoshop") + cbor("version") + cbor("25") + b"\xa1" + firefly,
            "icon before name": cbor("claim_generator_info") + b"\xa2" + icon + firefly,
            "deterministic key order": cbor("claim_generator_info") + b"\xa3" + icon + cbor("name") + cbor("ChatGPT")
            + cbor("version") + cbor("1"),
            "five pairs before name": cbor("claim_generator_info") + b"\xa6"
            + b"".join(cbor("k%d" % i) + cbor("v") for i in range(5)) + firefly,
            "indefinite-length map": cbor("claim_generator_info") + b"\xbf" + firefly + b"\xff",
            "one-byte map length": cbor("claim_generator_info") + b"\xb8\x19"
            + b"".join(cbor("k%d" % i) + cbor("v") for i in range(24)) + firefly,
            "software agent with icon": cbor("softwareAgent") + b"\xa2" + icon + firefly,
        }
        for name, manifest in layouts.items():
            with self.subTest(name):
                self.assertTrue(realism.c2pa_ai_agents(b"jumdc2pa" + manifest), name)

    def test_c2pa_reader_never_raises(self):
        raw = read(OPENAI_PNG)
        start = raw.index(b"jumdc2pa")
        box = raw.index(b"caBX")
        manifest = raw[start:box + 4 + struct.unpack(">I", raw[box - 4:box])[0]]  # the whole caBX chunk, about 22 KB
        for cut in range(0, len(manifest), 7):  # every truncation point, a few bytes apart
            realism.c2pa_tool_names(manifest[:cut])
        rng = np.random.default_rng(3)
        for _ in range(2000):
            noise = rng.integers(0, 256, int(rng.integers(0, 40)), dtype=np.uint8).tobytes()
            realism.c2pa_tool_names(b"claim_generator_info" + noise)

    def test_real_openai_manifest_names_its_tools(self):
        agents = realism.c2pa_ai_agents(read(OPENAI_PNG))
        self.assertIn("chatgpt", agents)  # CBOR length bytes glue letters to the name
        self.assertIn("gpt-image", agents)

    def test_camera_c2pa_from_a_photo_editor_is_accepted(self):
        # the generator is Photoshop; an AI word in the nearby title must not be read as the tool
        payload = (b"jumdc2pa" + cbor("http://cv.iptc.org/newscodes/digitalsourcetype/digitalCapture")
                   + cbor("claim_generator_info") + b"\x81\xa2" + cbor("name") + cbor("Adobe Photoshop")
                   + cbor("version") + cbor("25.0") + cbor("dc:title") + cbor("Midjourney CEO on stage, imagen 1")
                   + cbor("c2pa.training-mining") + cbor("c2pa.ai_generative_training") + cbor("notAllowed"))
        self.assertEqual(realism.c2pa_ai_agents(payload), [])
        src = self.save("edit.png", data=png_with_chunk(to_image(scene()), b"caBX", payload))
        self.assertEqual(run([src, "--origin", "camera", "--preset", "day"])[0], 0)

    def test_camera_c2pa_with_a_capture_source_type_is_accepted_with_a_warning(self):
        # CBOR puts the next key's letters right after the code: "...digitalCapture" + "dwhen"
        payload = b"jumdc2pa" + b"http://cv.iptc.org/newscodes/digitalsourcetype/digitalCapturedwhent2026"
        src = self.save("leica.png", data=png_with_chunk(to_image(scene()), b"caBX", payload))
        code, out, _ = run([src, "--origin", "camera", "--preset", "day"])
        self.assertEqual(code, 0)
        self.assertIn("C2PA Content Credentials are not carried over", out)

    def test_camera_mode_rejects_less_common_ai_markers(self):
        from PIL import PngImagePlugin
        base = to_image(scene())
        xmp = realism.xmp_packet("trainedAlgorithmicMedia").decode("utf-8")
        z = PngImagePlugin.PngInfo()
        z.add_itxt("XML:com.adobe.xmp", xmp, zip=True)  # compressed, invisible to a byte scan
        compressed = io.BytesIO()
        base.save(compressed, "PNG", pnginfo=z)
        raw = PngImagePlugin.PngInfo()
        hexed = xmp.encode("utf-8").hex()
        raw.add_text("Raw profile type xmp", "\nxmp\n%8d\n%s\n" % (len(xmp), hexed), zip=True)  # ImageMagick
        magick = io.BytesIO()
        base.save(magick, "PNG", pnginfo=raw)
        unicode_uc = jpeg_with(base, exif_user_comment(
            b"UNICODE\x00" + "AI-generated with Stable Diffusion".encode("utf-16-le")))
        a1111_jpeg = jpeg_with(base, exif_user_comment(
            b"ASCII\x00\x00\x00" + b"cat, Steps: 20, Sampler: DPM++ 2M, CFG scale: 7"))
        com = jpeg_with(base, segment(0xFE, b"Generated by Midjourney v6"))
        cases = {"compressed-xmp.png": compressed.getvalue(), "magick.png": magick.getvalue(),
                 "unicode.jpg": unicode_uc, "a1111.jpg": a1111_jpeg, "com.jpg": com}
        for name, data in cases.items():
            with self.subTest(name):
                code, _, err = run([self.save(name, data=data), "--origin", "camera", "--preset", "day"])
                self.assertEqual(code, 2, err)

    def test_ai_claims_in_xmp_tiff_and_comfyui_webp_are_found(self):
        base = to_image(scene())
        xmp = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
               b'<rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:description>Made with AI'
               b'</dc:description></rdf:Description></rdf:RDF></x:xmpmeta>')
        cases = {"xmp.jpg": jpeg_with(base, segment(0xE1, realism.XMP_SIG + xmp))}
        tif = io.BytesIO()
        info = {270: "AI generated image"}
        base.save(tif, "TIFF", tiffinfo=info)
        cases["desc.tif"] = tif.getvalue()
        from PIL import features
        if features.check("webp"):
            exif = Image.Exif()
            exif[0x010F] = 'workflow:{"nodes": []}'
            webp = io.BytesIO()
            base.save(webp, "WEBP", exif=exif.tobytes())
            cases["comfy.webp"] = webp.getvalue()
        for name, data in cases.items():
            with self.subTest(name):
                code, _, err = run([self.save(name, data=data), "--origin", "camera", "--preset", "day"])
                self.assertEqual(code, 2, err)

    def test_c2pa_without_a_source_type_is_explained(self):
        src = self.save("ps.png", data=png_with_chunk(to_image(scene()), b"caBX", b"jumdc2pa c2pa.opened"))
        code, _, err = run([src, "--origin", "camera", "--preset", "day"])
        self.assertEqual(code, 2)
        self.assertIn("declares no source type, so camera origin cannot be confirmed", err)

    def test_explicit_claims_and_lookalikes(self):
        not_claims = ["made with AI-free film", "Made in Ai, Japan", "Created in Sora, Sardinia",
                      "created by Gemini Studios", "photo made with an AI Servo autofocus",
                      "Edited with AI Denoise in Lightroom", "Made by a human, not AI-generated", "Made in Italy",
                      "edited in Photoshop", "created with love", "created with GIMP"]
        claims = ["made with Google Gemini", "Generated with GPT-4o", "Created by Meta AI", "Created with Canva AI",
                  "generated via Midjourney", "Image created by OpenAI's DALL-E", "generated by Leonardo.ai",
                  "Made with Flux.1 dev", "Created by Bing Image Creator", "generated using Grok",
                  "Edited with Google Magic Editor", "AI-made image", "Image generated using DALL-E 3",
                  "Created with Adobe Firefly", "Generated by Midjourney v6", "AI-generated image"]
        self.assertEqual([t for t in not_claims if realism.AI_CLAIM.search(t)], [])
        self.assertEqual([t for t in claims if not realism.AI_CLAIM.search(t)], [])

    def test_ordinary_words_do_not_refuse_camera_photos(self):
        for text in ("Imagen de la playa al atardecer", "Firefly season in the backyard", "Sora at the park",
                     "Imagen 1", "Vista dalle colline", "Beatrice Dalle", "Speaker at the Generative AI summit",
                     "Midjourney CEO David Holz on stage", "stable diffusion of heat through copper"):
            # the same caption in three free-text fields must get the same answer
            from PIL import PngImagePlugin
            exif = Image.Exif()
            exif[0x010E] = text
            in_exif = io.BytesIO()
            to_image(scene()).save(in_exif, "JPEG", exif=exif.tobytes())
            info = PngImagePlugin.PngInfo()
            info.add_text("Description", text)
            in_png = io.BytesIO()
            to_image(scene()).save(in_png, "PNG", pnginfo=info)
            files = {"exif.jpg": in_exif.getvalue(), "com.jpg": jpeg_with(to_image(scene()), segment(0xFE, text.encode())),
                     "png.png": in_png.getvalue()}
            for name, data in files.items():
                with self.subTest(text=text, field=name):
                    src = self.save("p%d-%s" % (len(text), name), data=data)
                    self.assertEqual(run([src, "--origin", "camera", "--preset", "day"])[0], 0)

    def test_generated_mode_warns_that_c2pa_is_replaced(self):
        src = self.save("chatgpt.png", data=read(OPENAI_PNG))
        code, out, _ = run([src, "--origin", "generated", "--preset", "indoor", "--max-edge", "400"])
        self.assertEqual(code, 0)
        self.assertIn("C2PA Content Credentials are not carried over", out)

    def camera_jpeg(self, name, make="Apple", model="iPhone 13 Pro Max", iso=True, caption=None):
        exif = Image.Exif()
        exif[0x010F] = make
        exif[0x0110] = model
        if iso:  # ISO, one of the exposure settings a camera writes, in the Exif IFD where cameras put it
            exif[0x8769] = {0x8827: 400}
        if caption:
            exif[0x010E] = caption
        buf = io.BytesIO()
        to_image(scene() * WARM).save(buf, "JPEG", quality=95, exif=exif.tobytes())
        return self.save(name, data=buf.getvalue())

    def test_camera_metadata_keeps_default_effects_off_whatever_the_origin(self):
        src = self.camera_jpeg("phone.jpg")
        for origin in ("generated", "edited"):
            with self.subTest(origin=origin):
                code, out, _ = run([src, "--origin", origin, "--preset", "indoor", "--seed", "1",
                                    "-o", self.path("%s.jpg" % origin)])
                self.assertEqual(code, 0)
                self.assertIn("capture strength: 0\n", out)
                self.assertIn("warning: the input carries camera capture metadata (Apple iPhone 13 Pro Max): ", out)
                self.assertIn("Default capture effects and white balance left off", out)
                self.assertEqual("rerun with --origin camera" in out, origin == "generated")
                self.assertIn("white balance: off for a likely camera photo", out)

    def test_explicit_settings_still_apply_to_camera_metadata(self):
        src = self.camera_jpeg("phone.jpg")
        code, out, _ = run([src, "--origin", "generated", "--preset", "indoor", "--seed", "1",
                            "--capture-strength", "0.5", "--wb-strength", "0.8"])
        self.assertEqual(code, 0)
        self.assertIn("capture strength: 0.5\n", out)
        self.assertIn("camera capture metadata", out)
        self.assertNotIn("left off", out)
        self.assertNotIn("white balance: off", out)

    def test_camera_metadata_check_needs_exposure_settings(self):
        src = self.camera_jpeg("p-noexp.jpg", iso=False)
        code, out, _ = run([src, "--origin", "generated", "--preset", "indoor", "--seed", "1"])
        self.assertEqual(code, 0)
        self.assertIn("capture strength: 0.5\n", out)
        self.assertNotIn("camera capture metadata", out)

    def test_ai_edited_camera_photo_keeps_default_effects_off(self):
        # phone AI edits (Magic Editor, Galaxy AI, generative fill) keep the camera's EXIF and add a marker
        src = self.camera_jpeg("p-ai.jpg", caption="Made with AI")
        code, out, _ = run([src, "--origin", "generated", "--preset", "indoor", "--seed", "1"])
        self.assertEqual(code, 0)
        self.assertIn("capture strength: 0\n", out)
        self.assertIn("and an AI marker: it may be an AI edit of a camera photo", out)
        self.assertNotIn("--origin camera", out)

    def test_camera_capture_drops_control_characters(self):
        src = self.camera_jpeg("esc.jpg", make="\x1b[2JApple\x7f")
        with Image.open(src) as im:
            self.assertEqual(realism.camera_capture(im), "[2JApple iPhone 13 Pro Max")

    def test_camera_origin_gets_no_extra_camera_metadata_warning(self):
        src = self.camera_jpeg("phone.jpg")
        code, out, _ = run([src, "--origin", "camera", "--preset", "indoor", "--seed", "1"])
        self.assertEqual(code, 0)
        self.assertNotIn("camera capture metadata", out)
        self.assertIn("white balance: off for camera photos", out)

    def test_camera_capture_names_the_camera_once(self):
        src = self.camera_jpeg("canon.jpg", make="Canon", model="Canon EOS R5")
        with Image.open(src) as im:
            self.assertEqual(realism.camera_capture(im), "Canon EOS R5")
        with Image.open(self.save("plain.png")) as im:
            self.assertEqual(realism.camera_capture(im), "")

    def test_outputs_cannot_be_processed_again(self):
        src = self.save("once.png")
        self.assertEqual(run([src, "--origin", "generated", "--preset", "day"])[0], 0)
        for origin in ("camera", "generated"):
            code, _, err = run([self.path("once_real.jpg"), "--origin", origin, "--preset", "day"])
            self.assertEqual(code, 2)
            self.assertIn("already a snapshot-realism output", err)


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def path(self, name):
        return os.path.join(self.d, name)

    def save(self, name, img=None):
        p = self.path(name)
        (img or to_image(scene())).save(p)
        return p

    def test_output_never_replaces_the_input(self):
        src = self.save("photo.jpg")
        before = md5(src)
        for extra in (["-o", src], ["-o", src, "--overwrite"]):
            with self.subTest(extra):
                code, _, err = run([src, "--origin", "camera", "--preset", "day"] + extra)
                self.assertEqual(code, 2)
                self.assertEqual(md5(src), before)

    def test_comparison_never_replaces_the_input(self):
        src = self.save("shot_compare.jpg")
        before = md5(src)
        code, _, err = run([src, "--origin", "camera", "--preset", "day", "-o", self.path("shot.jpg"),
                            "--compare", "--overwrite"])
        self.assertEqual(code, 2)
        self.assertIn("refusing to overwrite the input file", err)
        self.assertEqual(md5(src), before)

    def test_case_variant_of_the_input_is_refused(self):
        src = self.save("Photo.JPG")
        if not os.path.exists(self.path("photo.jpg")):  # probe this folder: normcase folds case on Windows only
            self.skipTest("case-insensitive file system only")
        before = md5(src)
        code, _, _ = run([src, "--origin", "camera", "--preset", "day", "-o", self.path("photo.jpg"), "--overwrite"])
        self.assertEqual(code, 2)
        self.assertEqual(md5(src), before)

    def test_output_must_be_a_jpeg_in_an_existing_folder(self):
        src = self.save("in.png")
        for target in ("out.png", "out", os.path.join("missing", "out.jpg")):
            with self.subTest(target):
                code, _, _ = run([src, "--origin", "camera", "--preset", "day", "-o", self.path(target)])
                self.assertEqual(code, 2)
        self.assertEqual(os.listdir(self.d), ["in.png"])

    def test_same_basename_inputs_get_separate_outputs(self):
        png, jpg = self.save("photo.png"), self.save("photo.jpg")
        self.assertEqual(run([png, "--origin", "generated", "--preset", "day", "--compare"])[0], 0)
        first = md5(self.path("photo_real.jpg"))
        code, out, _ = run([jpg, "--origin", "camera", "--preset", "day", "--compare"])
        self.assertEqual(code, 0)
        self.assertIn("photo_real-2.jpg", out)
        self.assertEqual(md5(self.path("photo_real.jpg")), first)
        self.assertTrue(os.path.exists(self.path("photo_real-2_compare.jpg")))

    def test_existing_explicit_output_needs_overwrite(self):
        src = self.save("in.png")
        out = self.path("out.jpg")
        with open(out, "wb") as f:
            f.write(b"keep me")
        self.assertEqual(run([src, "--origin", "camera", "--preset", "day", "-o", out])[0], 2)
        with open(out, "rb") as f:
            self.assertEqual(f.read(), b"keep me")
        self.assertEqual(run([src, "--origin", "camera", "--preset", "day", "-o", out, "--overwrite"])[0], 0)
        with Image.open(out) as im:
            self.assertEqual(im.format, "JPEG")

    def test_invalid_numbers_are_rejected_before_any_file_is_written(self):
        src = self.save("in.png")
        bad = [("--wb-strength", "nan"), ("--wb-strength", "inf"), ("--wb-strength", "-1"), ("--wb-strength", "2"),
               ("--warmth", "nan"), ("--warmth", "inf"), ("--warmth", "50"), ("--ca", "nan"), ("--ca", "-0.1"),
               ("--ca", "1"), ("--ca", "0.5"), ("--max-edge", "0"), ("--max-edge", "-5"), ("--max-edge", "1"),
               ("--quality", "0"), ("--quality", "101"), ("--quality", "-1"), ("--seed", "-1"), ("--seed", "x")]
        for flag, value in bad:
            with self.subTest(flag=flag, value=value):
                code, _, err = run([src, "--origin", "generated", "--preset", "day", flag, value])
                self.assertEqual(code, 2)
                self.assertIn(flag, err)
        self.assertEqual(os.listdir(self.d), ["in.png"])

    def test_origin_and_preset_are_required(self):
        src = self.save("in.png")
        self.assertEqual(run([src, "--preset", "day"])[0], 2)
        self.assertEqual(run([src, "--origin", "camera"])[0], 2)

    def test_unreadable_inputs_fail_cleanly(self):
        empty = self.path("empty.jpg")
        open(empty, "wb").close()
        text = self.path("notes.jpg")
        with open(text, "w") as f:
            f.write("not an image")
        heic = self.path("phone.heic")
        with open(heic, "wb") as f:
            f.write(b"\x00\x00\x00\x18ftypheic" + b"\x00" * 32)
        for p in (empty, text, heic):
            with self.subTest(os.path.basename(p)):
                code, _, err = run([p, "--origin", "camera", "--preset", "day"])
                self.assertEqual(code, 1)
                self.assertIn("error: cannot read", err)
                self.assertNotIn("Traceback", err)
        self.assertIn("pillow-heif", run([heic, "--origin", "camera", "--preset", "day"])[2])
        self.assertEqual(run([self.path("missing.jpg"), "--origin", "camera", "--preset", "day"])[0], 2)
        self.assertEqual(run([self.d, "--origin", "camera", "--preset", "day"])[0], 2)

    def test_truncated_file_fails_cleanly(self):
        full = io.BytesIO()
        to_image(scene()).save(full, "PNG")
        data = full.getvalue()
        idat = data.index(b"IDAT") + 4
        for name, broken in (("half.png", data[: len(data) // 2]),
                             ("garbled.png", data[:idat + 2] + b"\xff" * 64 + data[idat + 66:])):
            with self.subTest(name):
                p = self.path(name)
                with open(p, "wb") as f:
                    f.write(broken)
                code, _, err = run([p, "--origin", "generated", "--preset", "day"])
                self.assertEqual(code, 1)
                self.assertIn("error: cannot read", err)
                self.assertNotIn("Traceback", err)

    def test_broken_second_page_fails_cleanly_or_uses_page_one(self):
        buf = io.BytesIO()
        Image.new("RGB", (8, 8), (90, 120, 150)).save(buf, "TIFF")
        d = bytearray(buf.getvalue())
        off = struct.unpack("<I", d[4:8])[0]
        nxt = off + 2 + 12 * struct.unpack("<H", d[off:off + 2])[0]
        d[nxt:nxt + 4] = struct.pack("<I", len(d))  # IFD0 points at a second page with no size
        d += struct.pack("<H", 1) + struct.pack("<HHII", 0x0103, 3, 1, 1) + b"\0\0\0\0"
        p = self.path("twopage.tif")
        with open(p, "wb") as f:
            f.write(bytes(d))
        code, _, err = run([p, "--origin", "generated", "--preset", "day"])
        self.assertIn(code, (0, 1))
        self.assertNotIn("Traceback", err)

    def test_same_input_gives_the_same_bytes(self):
        src = self.save("in.png")
        a, b = self.path("a.jpg"), self.path("b.jpg")
        for out in (a, b):
            self.assertEqual(run([src, "--origin", "generated", "--preset", "day", "--seed", "3", "-o", out])[0], 0)
        self.assertEqual(read(a), read(b))

    def test_too_small_after_downscaling_fails_cleanly(self):
        src = self.save("strip.png", Image.new("RGB", (20000, 2), (128, 128, 128)))
        code, _, err = run([src, "--origin", "camera", "--preset", "day"])
        self.assertEqual(code, 1)
        self.assertIn("image too small", err)
        self.assertEqual(os.listdir(self.d), ["strip.png"])

    def test_failed_comparison_writes_nothing(self):
        src = self.save("in.png")
        original = realism.side_by_side
        realism.side_by_side = lambda before, after: (_ for _ in ()).throw(ValueError("boom"))
        try:
            code, _, err = run([src, "--origin", "camera", "--preset", "day", "--compare"])
        finally:
            realism.side_by_side = original
        self.assertEqual(code, 1)
        self.assertEqual(os.listdir(self.d), ["in.png"])

    def test_writes_are_all_or_nothing(self):
        a, folder = self.path("a.jpg"), self.path("b.jpg")
        os.mkdir(folder)  # the second target cannot be replaced
        with open(a, "wb") as f:
            f.write(b"old")
        with self.assertRaises(OSError):
            realism.write_outputs([(a, b"new"), (folder, b"new")], overwrite=True)
        self.assertEqual(read(a), b"old")  # put back
        os.remove(a)
        with self.assertRaises(OSError):
            realism.write_outputs([(a, b"new"), (folder, b"new")], overwrite=False)
        self.assertFalse(os.path.exists(a))  # removed again
        self.assertEqual(sorted(os.listdir(self.d)), ["b.jpg"])  # no temp or backup files left

    def test_overwrite_replaces_a_read_only_output_without_leftovers(self):
        import stat
        src = self.save("in.png")
        out = self.path("out.jpg")
        self.assertEqual(run([src, "--origin", "camera", "--preset", "day", "-o", out])[0], 0)
        os.chmod(out, stat.S_IREAD)
        try:
            self.assertEqual(run([src, "--origin", "camera", "--preset", "night", "-o", out, "--overwrite"])[0], 0)
        finally:
            os.chmod(out, stat.S_IREAD | stat.S_IWRITE)
        self.assertEqual(sorted(os.listdir(self.d)), ["in.png", "out.jpg"])

    @unittest.skipUnless(os.name == "nt", "Windows sharing violations only")
    def test_a_brief_windows_lock_is_waited_out(self):
        from unittest import mock
        real, calls = os.replace, []

        def flaky(src, dst):
            calls.append(dst)
            if len(calls) == 1:
                raise PermissionError(13, "Access is denied", dst)  # a scanner opened the new file
            return real(src, dst)

        a = self.path("a.jpg")
        with mock.patch.object(os, "replace", flaky):
            realism.write_outputs([(a, b"new")])
        self.assertEqual(read(a), b"new")
        self.assertEqual(len(calls), 2)

    @unittest.skipUnless(os.name == "nt", "Windows sharing locks only")
    def test_a_cleanup_blocked_by_a_viewer_is_reported(self):
        # After the first file is placed, a viewer opens it and the second file's
        # claimed name (Python's open() does not share delete access), so the
        # second rename fails and so does removing the first: both are reported.
        from unittest import mock
        a, b = self.path("a.jpg"), self.path("a_compare.jpg")
        held, real = [], realism._replace

        def place(src, dst, tries=10):
            if dst == b and not held:
                held.extend([open(a, "rb"), open(b, "rb")])
            return real(src, dst, tries=2)

        try:
            with mock.patch.object(realism, "_replace", place):
                with self.assertRaises(PermissionError) as cm:
                    realism.write_outputs([(a, b"new"), (b, b"new")])
            left = getattr(cm.exception, "left_behind", [])
            self.assertEqual(len(left), 2, left)
            self.assertTrue(any(x.startswith(a) for x in left) and any(x.startswith(b) for x in left))
        finally:
            for h in held:
                h.close()

        src = self.save("in.png")
        err = io.StringIO()
        boom = OSError(13, "Access is denied")
        boom.left_behind = ["x.jpg (new or empty, could not be removed)"]
        with mock.patch.object(realism, "write_outputs", side_effect=boom), contextlib.redirect_stderr(err):
            self.assertEqual(realism.main([src, "--origin", "camera", "--preset", "day"]), 1)
        self.assertIn("could not clean up after it, so these remain: x.jpg", err.getvalue())
        self.assertNotIn("nothing written", err.getvalue())

    def test_an_interrupt_reports_what_it_left(self):
        from unittest import mock
        src = self.save("in.png")
        stop = KeyboardInterrupt()
        stop.left_behind = ["y.jpg (new or empty, could not be removed)"]
        err = io.StringIO()
        with mock.patch.object(realism, "write_outputs", side_effect=stop), contextlib.redirect_stderr(err):
            self.assertEqual(realism.main([src, "--origin", "camera", "--preset", "day"]), 130)
        self.assertIn("interrupted; could not clean up after it, so these remain: y.jpg", err.getvalue())

    def test_a_file_that_appears_meanwhile_is_not_replaced(self):
        a = self.path("a.jpg")
        with open(a, "wb") as f:
            f.write(b"theirs")
        with self.assertRaises(FileExistsError):
            realism.write_outputs([(a, b"mine")], overwrite=False)
        self.assertEqual(read(a), b"theirs")

    @unittest.skipIf(os.name == "nt", "POSIX permissions only")
    def test_outputs_get_normal_permissions(self):
        old = os.umask(0o022)
        try:
            realism.write_outputs([(self.path("a.jpg"), b"x")])
        finally:
            os.umask(old)
        self.assertEqual(os.stat(self.path("a.jpg")).st_mode & 0o777, 0o644)

    def test_warmth_is_reported_on_the_white_balance_line(self):
        src = self.save("in.png")
        code, out, _ = run([src, "--origin", "generated", "--preset", "day", "--warmth", "-0.1"])
        self.assertEqual(code, 0)
        self.assertIn("warmth -0.10 applied", out)
        self.assertIn("capture strength: 0.5", out)
        prefixes = ("note: ", "warning: ", "white balance: ", "capture strength: ", "wrote ")
        self.assertTrue(all(line.startswith(prefixes) for line in out.splitlines()))

    def test_default_capture_strength_depends_on_origin(self):
        # 0.5 for generated images; 0 for camera and edited photos, which already have real
        # capture texture, so the script adds none unless asked
        src = self.save("in.png")
        for origin, expected in (("generated", "0.5"), ("edited", "0"), ("camera", "0")):
            with self.subTest(origin=origin):
                code, out, _ = run([src, "--origin", origin, "--preset", "day", "--seed", "1",
                                    "-o", self.path("%s.jpg" % origin)])
                self.assertEqual(code, 0)
                self.assertIn("capture strength: %s\n" % expected, out)
                self.assertEqual("no capture effects by default" in out, origin != "generated")

    def test_camera_default_adds_no_capture_effects_but_explicit_strength_does(self):
        src = self.save("in.png")
        plain = self.path("plain.jpg")
        self.assertEqual(run([src, "--origin", "camera", "--preset", "night", "--seed", "1", "-o", plain])[0], 0)
        grain = self.path("grain.jpg")
        code, out, _ = run([src, "--origin", "camera", "--preset", "night", "--seed", "1",
                            "--capture-strength", "0.5", "-o", grain])
        self.assertEqual(code, 0)
        self.assertNotIn("no capture effects by default", out)
        with Image.open(src) as a, Image.open(plain) as b, Image.open(grain) as c:
            ref = np.asarray(a.convert("RGB"), np.float32)
            off = np.abs(np.asarray(b.convert("RGB"), np.float32) - ref).mean()
            on = np.abs(np.asarray(c.convert("RGB"), np.float32) - ref).mean()
        self.assertLess(off, 1.5)  # only the JPEG re-save
        self.assertGreater(on, off + 0.5)

    def test_non_ascii_name_with_a_cp1252_console(self):
        src = self.save("写真.png")
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="cp1252")
        with contextlib.redirect_stdout(stream):
            code = realism.main([src, "--origin", "camera", "--preset", "day", "--compare"])
        stream.flush()
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(self.path("写真_real_compare.jpg")))

    def test_quality_option_is_used(self):
        src = self.save("in.png")
        sizes = {}
        for q in ("10", "95"):
            out = self.path("q%s.jpg" % q)
            self.assertEqual(run([src, "--origin", "camera", "--preset", "day", "--seed", "1",
                                  "--quality", q, "-o", out])[0], 0)
            sizes[q] = os.path.getsize(out)
        self.assertLess(sizes["10"], sizes["95"])

    def test_readme_demo_command_reproduces_the_demo(self):
        # the command printed in the candid-male README (and the main README) rebuilds both shipped files
        prefix = "python skill/snapshot-realism/scripts/realism.py assets/demo/candid-male/"
        with open(os.path.join(DEMO, "candid-male", "README.md"), encoding="utf-8") as f:
            line = next(x.strip() for x in f if x.startswith(prefix))
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as f:
            self.assertIn(line, f.read())
        args = line.split()[2:]
        args[0] = os.path.join(ROOT, *args[0].split("/"))
        out = self.path("demo.jpg")
        args[args.index("-o") + 1] = out
        self.assertIn("--compare", args)
        self.assertEqual(run(args)[0], 0)
        for made, shipped in ((out, "skill-after-script.jpg"), (realism.compare_path(out), "script-comparison.jpg")):
            with self.subTest(shipped), Image.open(made) as a, Image.open(os.path.join(DEMO, "candid-male", shipped)) as b:
                self.assertEqual(a.size, b.size)
                diff = np.abs(np.asarray(a, np.float32) - np.asarray(b, np.float32))
                self.assertLess(float(diff.mean()), 1.0)  # tolerance: numpy and Pillow versions differ slightly


if __name__ == "__main__":
    unittest.main()
