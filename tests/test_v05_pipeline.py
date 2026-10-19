"""v0.5.0 pixel pipeline properties (SPEC A1 to A9) plus the noise-variance guard.

Run: python -m unittest discover -s tests
"""
import inspect
import io
import os
import struct
import sys
import tracemalloc
import unittest
import warnings
import zlib
from unittest import mock

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_realism import OPENAI_PNG, WARM, gray_rb, realism, scene, textured, to_image  # noqa: E402

SEED = 5


def proc(img, preset, **kw):
    """process() with white balance and CA off unless asked; returns (float RGB array, info)."""
    kw.setdefault("wb_strength", 0.0)
    kw.setdefault("ca", 0.0)
    kw.setdefault("seed", SEED)
    out, info = realism.process(img, preset, **kw)
    return np.asarray(out.convert("RGB"), np.float64), info


def flat(level, size=(128, 128)):
    return Image.new("RGB", size, (level, level, level))


def identity(s, *args, **kwargs):
    return s


def step_image(w, h, x0, lo=50, hi=200):
    a = np.full((h, w), lo, np.uint8)
    a[:, x0:] = hi
    return Image.fromarray(a)


def overshoot(out, x0):
    """Bright-side overshoot of a vertical step at column x0: green channel averaged over rows."""
    prof = out[..., 1].mean(axis=0)
    plateau = float(np.median(prof[x0 + 15:x0 + 40]))
    return float(prof[x0:x0 + 8].max()) - plateau


def png_chunk_before_idat(data, ctype, payload):
    """PNG bytes with one chunk inserted right before the first IDAT (where tRNS belongs)."""
    i = data.index(b"IDAT") - 4
    chunk = struct.pack(">I", len(payload)) + ctype + payload + struct.pack(">I", zlib.crc32(ctype + payload))
    return data[:i] + chunk + data[i:]


class TonedImages(unittest.TestCase):
    """A1: a consistent signed tint keeps the image in color."""

    def test_sepia_stays_color_and_gets_no_white_balance(self):
        src = to_image(textured((120, 109, 99)))
        out, info = realism.process(src, "day", seed=SEED)  # default wb 0.8
        self.assertEqual(out.mode, "RGB")
        a = np.asarray(out, np.float64)
        self.assertGreaterEqual(float((a[..., 0] - a[..., 2]).mean()), 15)
        self.assertIn("toned monochrome", info["reason"])
        self.assertEqual(info["strength"], 0)

    def test_toned_image_keeps_its_tint_without_capture(self):
        src = to_image(textured((135, 124, 114)))
        out, _ = realism.process(src, "day", wb_strength=0, capture_strength=0, seed=SEED)
        self.assertEqual(out.mode, "RGB")
        a = np.asarray(out, np.float64)
        self.assertAlmostEqual(float((a[..., 0] - a[..., 2]).mean()), 21, delta=3)

    def test_split_tone_keeps_its_tone(self):
        # warm shadows, near-neutral highlights: the tint is not a constant chromaticity
        g = np.asarray(Image.open(OPENAI_PNG).convert("L").reduce(2), np.float64)[..., None] / 255.0
        dark, light = np.array([0.35, 0.18, -0.05]), np.array([1.0, 0.97, 0.90])
        src = Image.fromarray((np.clip(g * light + (1 - g) * g * dark, 0, 1) * 255 + 0.5).astype(np.uint8))
        self.check_tone_kept(src)

    def test_additive_sepia_keeps_its_tone(self):
        # the same texture added to all three sRGB channels of (120, 109, 99): chroma drifts with luminance
        rng = np.random.default_rng(1)
        tex = np.clip(rng.normal(0, 12, (300, 400, 1)), -60, 60)
        self.check_tone_kept(Image.fromarray(np.clip(np.array([120, 109, 99]) + tex, 0, 255).astype(np.uint8)))

    def check_tone_kept(self, src):
        a0 = np.asarray(src, np.float64)
        out, info = realism.process(src, "indoor", seed=SEED, capture_strength=1)  # default wb 0.8
        a = np.asarray(out.convert("RGB"), np.float64)
        self.assertEqual(info["reason"], "toned monochrome image")
        self.assertEqual(info["strength"], 0)
        rb0, rb = float((a0[..., 0] - a0[..., 2]).mean()), float((a[..., 0] - a[..., 2]).mean())
        self.assertGreater(rb0, 15)
        self.assertAlmostEqual(rb, rb0, delta=3)

    def test_warm_cast_scenes_are_still_corrected(self):
        # A cast is not a tone: the fixed scene has differently colored objects
        # at similar brightness. Demo photos can change lighting when replaced.
        for texture in (False, True):
            with self.subTest(texture=texture):
                lin = scene() * WARM
                if texture:
                    lin *= np.random.default_rng(1).uniform(0.95, 1.05, lin.shape[:2] + (1,)).astype(np.float32)
                lin = realism.SRGB_LUT[np.asarray(to_image(np.clip(lin, 0, 1)))]
                cast, conf, reason = realism.estimate_cast(lin)
                self.assertNotIn("toned", reason)
                self.assertGreater(conf, 0, reason)
                fixed = realism.white_balance(lin, cast, 0.8 * conf)
                self.assertLess(abs(np.log(gray_rb(fixed))), abs(np.log(gray_rb(lin))))

    def test_neutral_gray_texture_is_still_monochrome(self):
        out, info = realism.process(to_image(textured((110, 110, 110))), "day", seed=SEED)
        self.assertEqual(out.mode, "L")
        self.assertTrue(info["mono"])
        # R = G = B plus zero-mean channel noise: no consistent tint, still black and white
        rng = np.random.default_rng(1)
        a = np.asarray(to_image(textured((110, 110, 110))), np.float64)
        a = np.clip(a + rng.normal(0, 1.5, a.shape) + 0.5, 0, 255).astype(np.uint8)
        out, info = realism.process(Image.fromarray(a), "day", seed=SEED)
        self.assertEqual(out.mode, "L")


class MeanPreservingNoise(unittest.TestCase):
    """A2: noise neither lifts blacks nor shifts mid-tones."""

    def test_flat_patch_means_are_kept(self):
        bad = []
        for preset in realism.PRESETS:
            for cs in (1, 3):
                for level in (0, 4, 10, 20, 40, 80, 128, 200, 250):
                    out, _ = proc(flat(level), preset, capture_strength=cs)
                    err = abs(float(out.mean()) - level)
                    if err > (1.5 if level >= 8 else 2.5):
                        bad.append("%s cs=%d L=%d: %+.2f" % (preset, cs, level, float(out.mean()) - level))
        self.assertEqual(bad, [])

    def test_black_stays_black_at_any_strength(self):
        bad = []
        for preset in realism.PRESETS:
            for cs in (0.5, 1, 2, 3):
                out, _ = proc(flat(0), preset, capture_strength=cs)
                if out.mean() > 2.5:
                    bad.append("%s cs=%g: %.2f" % (preset, cs, float(out.mean())))
        self.assertEqual(bad, [])


class PhoneTexture(unittest.TestCase):
    """A3: edge-adaptive sharpening, mottled grain, a look that stays visible."""

    def test_sharpening_does_not_amplify_flat_noise(self):
        bad = []
        for preset in realism.PRESETS:
            for cs in (1, 3):
                for level in (30, 60, 120):
                    sharp, _ = proc(flat(level), preset, capture_strength=cs)
                    with mock.patch.object(realism, "phone_sharpen", identity):
                        soft, _ = proc(flat(level), preset, capture_strength=cs)
                    ratio = float(sharp.std() / max(soft.std(), 1e-9))
                    if ratio > 1.05:
                        bad.append("%s cs=%d L=%d: %.3f" % (preset, cs, level, ratio))
        self.assertEqual(bad, [])

    def test_edges_are_still_sharpened(self):
        w, h = 400, 300
        out, _ = proc(step_image(w, h, w // 2), "day", capture_strength=1)
        self.assertGreaterEqual(overshoot(out, w // 2), 5)

    def test_grain_is_mottled_not_per_pixel(self):
        for preset in ("indoor", "night"):
            with self.subTest(preset):
                out, _ = proc(flat(60, (256, 256)), preset, capture_strength=1)
                r = out[..., 1] - out[..., 1].mean()
                lag2 = float((r[:, :-2] * r[:, 2:]).mean() / (r * r).mean())
                self.assertGreaterEqual(lag2, 0.15)

    def test_grain_rolls_off_toward_the_highlights(self):
        # phones denoise highlights hardest (v0.5.0 before the retune: 0.59 to 0.63)
        for preset in realism.PRESETS:
            with self.subTest(preset):
                std = {}
                for level in (20, 200):
                    out, _ = proc(flat(level, (256, 256)), preset, capture_strength=1)
                    std[level] = float((out @ np.array([0.299, 0.587, 0.114])).std())
                self.assertTrue(0.3 <= std[200] / std[20] <= 0.5, "%s L200/L20 %.2f" % (preset, std[200] / std[20]))

    def test_bright_surfaces_keep_little_chroma_noise(self):
        # gray 60, gray 200 and a red strip (so the image stays in color); before the retune night
        # strength 3 left 4.15 DN of chroma noise at 200, 0.82 of the level at 60
        a = np.zeros((256, 520, 3), np.uint8)
        a[:, :256], a[:, 256:512], a[:, 512:] = 60, 200, (200, 40, 40)
        for preset in realism.PRESETS:
            for cs in (1, 3):
                with self.subTest(preset=preset, cs=cs):
                    out, _ = proc(Image.fromarray(a), preset, capture_strength=cs)
                    y = out @ np.array([0.299, 0.587, 0.114])

                    def chroma(x0):
                        sl = (slice(20, 236), slice(x0 + 20, x0 + 236))
                        return float(np.hypot((out[..., 0][sl] - y[sl]).std(), (out[..., 2][sl] - y[sl]).std()))
                    c60, c200 = chroma(0), chroma(256)
                    self.assertLessEqual(c200, 2.0)
                    self.assertLessEqual(c200, 0.7 * c60, "c200 %.2f c60 %.2f" % (c200, c60))

    def test_strength_makes_grain_rougher_not_blurrier(self):
        # lag-4 autocorrelation of the grain at strength 3 (before the retune: indoor 0.22, night 0.33)
        for preset in ("indoor", "night"):
            with self.subTest(preset):
                out, _ = proc(flat(60, (256, 256)), preset, capture_strength=3)
                r = out[..., 1] - out[..., 1].mean()
                lag4 = float((r[:, :-4] * r[:, 4:]).mean() / (r * r).mean())
                self.assertLessEqual(lag4, 0.2)

    def test_the_look_does_not_vanish(self):
        ranges = {"day": (0.7, 1.6), "indoor": (1.6, 3.2), "night": (2.2, 4.2)}
        for preset, (lo, hi) in ranges.items():
            with self.subTest(preset):
                out, _ = proc(flat(118, (256, 256)), preset, capture_strength=1)
                luma = out @ np.array([0.299, 0.587, 0.114])
                self.assertTrue(lo <= luma.std() <= hi, "%s luma std %.2f" % (preset, luma.std()))


class Spy(object):
    """Wraps a module function, records its bound arguments, calls the original."""

    def __init__(self, name):
        self.name = name
        self.orig = getattr(realism, name)
        self.calls = []

    def __call__(self, *args, **kwargs):
        bound = inspect.signature(self.orig).bind(*args, **kwargs)
        self.calls.append(bound.arguments)
        return self.orig(*args, **kwargs)

    def last(self, param):
        if not self.calls:
            raise AssertionError("%s was not called" % self.name)
        if param not in self.calls[-1]:
            raise AssertionError("%s was called without a %r argument: %s" % (
                self.name, param, sorted(self.calls[-1])))
        return self.calls[-1][param]


def colored(w=96, h=72, seed=7):
    rng = np.random.default_rng(seed)
    return Image.fromarray(rng.integers(30, 225, size=(h, w, 3), dtype=np.uint8))


class CaptureStrengthMapping(unittest.TestCase):
    """A4: capture strength is how rough the capture was; sharpening never grows past the preset."""

    def spied(self, preset, cs, ca=0.002):
        spies = {n: Spy(n) for n in ("lateral_ca", "phone_nr", "phone_sharpen")}
        with mock.patch.multiple(realism, **spies):
            realism.process(colored(), preset, wb_strength=0, ca=ca, seed=SEED, capture_strength=cs)
        return spies

    def test_sharpening_amount_is_capped_at_the_preset(self):
        for preset, p in realism.PRESETS.items():
            for cs in (0.5, 1, 2, 3):
                with self.subTest(preset=preset, cs=cs):
                    amount = self.spied(preset, cs)["phone_sharpen"].last("amount")
                    self.assertAlmostEqual(float(amount), p["sharpen"] * min(cs, 1.0), places=6)

    def test_ca_grows_to_one_and_a_half_times(self):
        for cs in (0.5, 1, 1.5, 2, 3):
            with self.subTest(cs=cs):
                k = self.spied("day", cs, ca=0.002)["lateral_ca"].last("k")
                self.assertAlmostEqual(float(k), 0.002 * min(cs, 1.5), places=9)

    def test_noise_reduction_grows_with_capped_strength(self):
        # phone_nr(s, flat, smear, chroma_sigma): the parameter names are part of this test
        for preset, p in realism.PRESETS.items():
            for cs in (0.5, 1, 2, 3):
                with self.subTest(preset=preset, cs=cs):
                    nr = self.spied(preset, cs)["phone_nr"]
                    # caps 0.5 and 6.0 (were 0.8 and 4.0): strength drives grain amplitude, not blur,
                    # and leftover chroma noise is soft rather than 4 px blotches
                    self.assertAlmostEqual(float(nr.last("smear")), min(p["smear"] * cs, 0.5), places=6)
                    self.assertAlmostEqual(float(nr.last("chroma_sigma")), min(p["chroma_nr"] * cs, 6.0), places=6)

    def test_zero_strength_is_pixel_identical_to_no_capture(self):
        src = colored(140, 90)
        expected = realism.linear_to_srgb(realism.to_linear(src, 96))
        expected = np.clip(expected * 255 + 0.5, 0, 255).astype(np.uint8)
        for preset in realism.PRESETS:
            with self.subTest(preset):
                out, _ = realism.process(src, preset, wb_strength=0, ca=0.01, max_edge=96, seed=1,
                                         capture_strength=0)
                np.testing.assert_array_equal(np.asarray(out), expected)

    def test_noise_variance_scales_with_strength_squared(self):
        # guard for v0.4.0 behaviour: NR and sharpening off, std ratio between strength 2 and 1 near 2
        with mock.patch.object(realism, "phone_nr", identity), mock.patch.object(realism, "phone_sharpen", identity):
            for preset in realism.PRESETS:
                with self.subTest(preset):
                    one, _ = proc(flat(118, (256, 256)), preset, capture_strength=1)
                    two, _ = proc(flat(118, (256, 256)), preset, capture_strength=2)
                    ratio = float(two[..., 1].std() / one[..., 1].std())
                    self.assertTrue(1.9 <= ratio <= 2.1, "%s std ratio %.3f" % (preset, ratio))


class CornerSoftness(unittest.TestCase):
    """A5: sharpening tapers toward the corners; no vignetting is added."""

    def test_edge_near_the_frame_side_is_sharpened_less(self):
        w, h = 400, 300
        center, _ = proc(step_image(w, h, w // 2), "day", capture_strength=1)
        side, _ = proc(step_image(w, h, w // 20), "day", capture_strength=1)
        c, s = overshoot(center, w // 2), overshoot(side, w // 20)
        self.assertGreater(c, 0)
        self.assertLessEqual(s, 0.8 * c, "side %.2f center %.2f" % (s, c))

    def test_no_vignetting(self):
        for preset in realism.PRESETS:
            with self.subTest(preset):
                out, _ = proc(flat(118, (320, 240)), preset, capture_strength=1)
                g = out[..., 1]
                corners = np.concatenate([g[:30, :30].ravel(), g[:30, -30:].ravel(),
                                          g[-30:, :30].ravel(), g[-30:, -30:].ravel()])
                self.assertLess(abs(float(corners.mean()) - float(g[105:135, 145:175].mean())), 1.0)


class Memory(unittest.TestCase):
    """A6: float32 noise, a subsampled cast estimate and no kept 'before' image."""

    def image(self):
        rng = np.random.default_rng(0)
        yy, xx = np.mgrid[0:1536, 0:2048]
        a = np.stack([xx / 2048 * 200 + 30, yy / 1536 * 180 + 40, (xx + yy) / 3584 * 150 + 50], 2)
        return Image.fromarray(np.clip(a + rng.normal(0, 6, a.shape), 0, 255).astype(np.uint8))

    def test_keep_before_false_drops_the_before_image(self):
        src = colored()
        _, info = realism.process(src, "day", seed=SEED, keep_before=False)
        self.assertIsNone(info["before"])
        _, info = realism.process(src, "day", seed=SEED)
        self.assertIsNotNone(info["before"])

    def test_peak_memory_per_output_pixel(self):
        src = self.image()
        tracemalloc.start()
        try:
            out, _ = realism.process(src, "indoor", max_edge=2048, seed=SEED, keep_before=False)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        per_px = peak / float(out.width * out.height)
        self.assertLessEqual(per_px, 80, "%.1f bytes per output pixel" % per_px)


class CompareSheet(unittest.TestCase):
    """A7: the before half is downscaled in linear light, like the after half."""

    def test_fine_stripes_keep_their_brightness_in_both_halves(self):
        a = np.zeros((300, 400), np.uint8)
        a[:, ::2] = 255
        out, info = realism.process(Image.fromarray(a), "day", wb_strength=0, ca=0, capture_strength=0,
                                    max_edge=200, seed=SEED)
        sheet = np.asarray(realism.side_by_side(info["before"], out), np.float64)
        w = out.width
        before, after = sheet[:, :w].mean(), sheet[:, w + 8:].mean()
        self.assertLessEqual(abs(before - after), 3, "before %.1f after %.1f" % (before, after))


class ColorKeyTransparency(unittest.TestCase):
    """A8: PNG tRNS color keys on RGB, L and 16-bit gray are flattened onto white."""

    def keyed(self, arr, trns):
        buf = io.BytesIO()
        Image.fromarray(arr).save(buf, "PNG")
        return Image.open(io.BytesIO(png_chunk_before_idat(buf.getvalue(), b"tRNS", trns)))

    def check(self, img, key_value):
        rgb, _, notes = realism.load_srgb(img)
        a = np.asarray(rgb)
        self.assertIn("transparency flattened onto white", notes)
        self.assertTrue((a[:16, :16] == 255).all(), a[:16, :16].reshape(-1, 3)[0])  # the keyed block
        self.assertTrue((a[20:, 20:] == key_value).all(), a[20:, 20:].reshape(-1, 3)[0])  # everything else untouched

    def test_rgb_color_key(self):
        a = np.zeros((32, 32, 3), np.uint8)
        a[:] = (100, 150, 200)
        a[:16, :16] = (10, 20, 30)
        self.check(self.keyed(a, struct.pack(">3H", 10, 20, 30)), np.array([100, 150, 200]))

    def test_gray_color_key(self):
        a = np.full((32, 32), 100, np.uint8)
        a[:16, :16] = 7
        self.check(self.keyed(a, struct.pack(">H", 7)), 100)

    def test_16_bit_gray_color_key(self):
        a = np.full((32, 32), 30000, np.uint16)
        a[:16, :16] = 1234
        self.check(self.keyed(a, struct.pack(">H", 1234)), round(30000 / 257.0))


class TinyStrengths(unittest.TestCase):
    """A9: a tiny capture strength gives no RuntimeWarning (blur sigmas below about 0.05 are no blur)."""

    def test_no_runtime_warning(self):
        src = colored()
        for preset in realism.PRESETS:
            for cs in (1e-300, 1e-6):
                with self.subTest(preset=preset, cs=cs), warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    out, _ = realism.process(src, preset, seed=SEED, capture_strength=cs)  # numpy's default error state
                    runtime = [str(w.message) for w in caught if issubclass(w.category, RuntimeWarning)]
                    self.assertEqual(runtime, [])
                    self.assertEqual(out.size, src.size)


if __name__ == "__main__":
    unittest.main()
