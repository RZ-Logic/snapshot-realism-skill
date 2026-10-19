"""Behavioral checks for capture strength independent of light and provenance."""
import contextlib
import io
import os
import sys
import unittest

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "skill", "snapshot-realism", "scripts"))
import realism  # noqa: E402


class CaptureStrengthTests(unittest.TestCase):
    def test_zero_preserves_resized_pixels_and_ignores_seed_and_ca(self):
        rng = np.random.default_rng(7)
        source = Image.fromarray(rng.integers(30, 225, size=(90, 140, 3), dtype=np.uint8))
        expected = realism.linear_to_srgb(realism.to_linear(source, 96))
        expected = np.clip(expected * 255 + 0.5, 0, 255).astype(np.uint8)
        for preset in realism.PRESETS:
            with self.subTest(preset=preset):
                a, info = realism.process(source, preset, wb_strength=0, max_edge=96,
                                          ca=0.01, seed=1, capture_strength=0)
                b, _ = realism.process(source, preset, wb_strength=0, max_edge=96,
                                       ca=0, seed=993, capture_strength=0)
                np.testing.assert_array_equal(a, expected)
                np.testing.assert_array_equal(a, b)
                self.assertEqual(info["capture_strength"], 0)
                self.assertEqual(info["quality"], realism.PRESETS[preset]["quality"])

    def test_zero_keeps_white_balance_and_manual_warmth_active(self):
        # A warm gray surface supplies the estimator with a usable neutral patch.
        lin = np.full((100, 100, 3), 0.35, np.float32)
        lin[50:] = (0.4, 0.12, 0.1)  # a colored object keeps this from being a toned monochrome frame
        lin *= np.array([1.18, 1, 0.78], np.float32)
        source = Image.fromarray((realism.linear_to_srgb(lin) * 255 + 0.5).astype(np.uint8))
        off, _ = realism.process(source, "day", capture_strength=0, wb_strength=0)
        balanced, info = realism.process(source, "day", capture_strength=0, wb_strength=0.8)
        self.assertGreater(info["strength"], 0)
        a, b = np.asarray(off, float), np.asarray(balanced, float)
        self.assertGreater(np.abs(a - b).mean(), 1)
        self.assertLess(abs((b[:50, :, 0] - b[:50, :, 2]).mean()),
                        abs((a[:50, :, 0] - a[:50, :, 2]).mean()))
        warmer, _ = realism.process(source, "day", capture_strength=0, wb_strength=0, warmth=0.2)
        self.assertGreater((np.asarray(warmer, float)[..., 0] - a[..., 0]).mean(), 0)

    def test_invalid_api_strength_rejected_before_image_read(self):
        class Unreadable:
            def __getattribute__(self, name):
                raise AssertionError("image was accessed before strength validation")
        for value in (-0.01, 3.01, float("nan"), float("inf"), -float("inf"), None, "bad"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "capture_strength"):
                realism.process(Unreadable(), "day", capture_strength=value)

    def test_invalid_cli_strength_rejected_by_argument_parser(self):
        for value in ("-0.01", "3.01", "nan", "inf", "-inf", "bad"):
            with self.subTest(value=value), contextlib.redirect_stderr(io.StringIO()) as err:
                with self.assertRaises(SystemExit) as caught:
                    realism.main(["missing.png", "--origin", "generated", "--preset", "day",
                                  "--capture-strength=" + value])
                self.assertEqual(caught.exception.code, 2)
                self.assertIn("--capture-strength", err.getvalue())

    def test_seed_repeatability_and_stronger_texture(self):
        source = Image.new("RGB", (256, 192), (90, 90, 90))
        images = {}
        for strength in (0, 1, 2):
            a, _ = realism.process(source, "day", wb_strength=0, ca=0, seed=42,
                                    capture_strength=strength)
            b, _ = realism.process(source, "day", wb_strength=0, ca=0, seed=42,
                                    capture_strength=strength)
            np.testing.assert_array_equal(a, b)
            images[strength] = np.asarray(a, np.float32)
        base = images[0]
        one = float(np.std(images[1] - base))
        two = float(np.std(images[2] - base))
        self.assertGreater(one, 0.5)
        self.assertGreater(two, one * 1.4)
        different_seed, _ = realism.process(source, "day", wb_strength=0, ca=0, seed=43,
                                             capture_strength=2)
        self.assertFalse(np.array_equal(images[2], different_seed))

    def test_strength_leaves_quality_and_declared_origin_independent(self):
        source = Image.new("RGB", (80, 80), (130, 120, 100))
        metadata = {}
        quantization = {}
        for strength in (0, 1, 3):
            image, info = realism.process(source, "indoor", seed=42, capture_strength=strength)
            self.assertEqual(info["quality"], realism.PRESETS["indoor"]["quality"])
            for origin in realism.ORIGINS:
                data = realism.encode_jpeg(image, info["quality"], origin)
                self.assertEqual(realism.disclosure_problems(data, origin), [])
                with Image.open(io.BytesIO(data)) as jpeg:
                    self.assertEqual(jpeg.size, image.size)
                    self.assertEqual(jpeg.layer, [(1, 2, 2, 0), (2, 1, 1, 1), (3, 1, 1, 1)])
                    current = (str(jpeg.getexif().get(0x010E)),
                               str(jpeg.getexif().get(0x0131)), realism.source_types(data))
                    if origin in metadata:
                        self.assertEqual(current, metadata[origin])
                        self.assertEqual(jpeg.quantization, quantization[origin])
                    metadata[origin] = current
                    quantization[origin] = jpeg.quantization

    def test_higher_strength_increases_texture_in_dark_and_mid_gray(self):
        # Excessive luma NR can otherwise reverse the strength control at night.
        for level in (16, 90):
            source = Image.new("RGB", (384, 256), (level, level, level))
            for preset in realism.PRESETS:
                with self.subTest(level=level, preset=preset):
                    deviations = []
                    for strength in (1, 2, 3):
                        image, _ = realism.process(source, preset, wb_strength=0, ca=0,
                                                   seed=42, capture_strength=strength)
                        deviations.append(float(np.std(np.asarray(image, dtype=np.float32))))
                    self.assertGreater(deviations[1], deviations[0], deviations)
                    self.assertGreater(deviations[2], deviations[1], deviations)


if __name__ == "__main__":
    unittest.main()
