"""Guards for v0.4.0 behaviour the review found unguarded (SPEC C). All of these pass on v0.4.0.

Run: python -m unittest discover -s tests
"""
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_realism import (DEMO, WARM, cbor, jpeg_with, png_with_chunk, read, realism, run, scene,  # noqa: E402
                          segment, textured, to_image)
from test_v05_io import iptc_segment, software_jpeg  # noqa: E402

OPENAI_PNG = os.path.join(DEMO, "candid-male", "skill-before-script.png")  # real OpenAI C2PA manifest
COOL = np.array([0.82, 1.0, 1.22], dtype=np.float32)
GREEN = np.array([0.92, 1.15, 0.86], dtype=np.float32)  # green tint, a touch warm


class TempDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def path(self, name):
        return os.path.join(self.d, name)

    def save(self, name, data=None, img=None):
        p = self.path(name)
        if data is not None:
            with open(p, "wb") as f:
                f.write(data)
        else:
            (img or to_image(scene(96, 128))).save(p)
        return p

    def files(self):
        return sorted(os.listdir(self.d))


class CameraRefusalPerDetector(TempDir):
    """One input per detector; each must be refused in camera mode with nothing written."""

    def refused(self, name, data, word):
        src = self.save(name, data)
        code, _, err = run([src, "--origin", "camera", "--preset", "day"])
        self.assertEqual(code, 2, err)
        self.assertIn(word.lower(), err.lower())
        self.assertEqual(self.files(), [name])

    def test_jpeg_xmp_digital_source_type_only(self):
        xmp = segment(0xE1, realism.XMP_SIG + realism.xmp_packet("trainedAlgorithmicMedia"))
        self.refused("dst.jpg", jpeg_with(to_image(scene(96, 128)), xmp), "trainedalgorithmicmedia")

    def test_comfyui_png_prompt_and_workflow(self):
        from PIL import PngImagePlugin
        info = PngImagePlugin.PngInfo()
        info.add_text("prompt", '{"3": {"class_type": "KSampler", "inputs": {"seed": 1, "steps": 20}}}')
        info.add_text("workflow", '{"last_node_id": 9, "nodes": []}')
        buf = io.BytesIO()
        to_image(scene(96, 128)).save(buf, "PNG", pnginfo=info)
        self.refused("comfy.png", buf.getvalue(), "generator")

    def test_exif_software_midjourney(self):
        self.refused("mj.jpg", software_jpeg("Midjourney", to_image(scene(96, 128))), "Software")

    def test_iptc_caption_ai_generated(self):
        data = jpeg_with(to_image(scene(96, 128)), iptc_segment({(2, 120): b"AI-generated image"}))
        self.refused("iim.jpg", data, "IPTC")

    def test_c2pa_capture_edited_by_gemini(self):
        payload = (b"jumdc2pa" + cbor("http://cv.iptc.org/newscodes/digitalsourcetype/digitalCapture")
                   + cbor("action") + cbor("c2pa.edited") + cbor("softwareAgent") + cbor("Google Gemini"))
        self.refused("gemini.png", png_with_chunk(to_image(scene(96, 128)), b"caBX", payload), "gemini")

    def test_real_openai_manifest(self):
        self.refused("chatgpt.png", read(OPENAI_PNG), "C2PA")


class RealOpenAiFixture(TempDir):
    def test_manifest_names_its_tools(self):
        agents = realism.c2pa_ai_agents(read(OPENAI_PNG))
        self.assertIn("chatgpt", agents)
        self.assertIn("gpt-image", agents)

    def test_reader_never_raises_on_a_truncated_manifest(self):
        raw = read(OPENAI_PNG)
        start = raw.index(b"jumdc2pa")
        manifest = raw[start:start + 12000]
        for cut in range(0, len(manifest), 11):
            realism.c2pa_tool_names(manifest[:cut])

    def test_generated_mode_warns_that_credentials_are_not_carried_over(self):
        src = self.save("chatgpt.png", read(OPENAI_PNG))
        code, out, err = run([src, "--origin", "generated", "--preset", "indoor", "--max-edge", "300"])
        self.assertEqual(code, 0, err)
        self.assertIn("C2PA Content Credentials are not carried over", out)


def wrong_when_encoding(real):
    """description() that is wrong only when encode_jpeg asks, so the check compares against the truth."""
    def fake(origin):
        if sys._getframe(1).f_code.co_name == "encode_jpeg":
            return "Edited with a different tool"
        return real(origin)
    return fake


class DisclosureNegatives(TempDir):
    """A broken disclosure must stop encode_jpeg and leave no file behind."""

    def broken(self, origin, *patches):
        img = to_image(scene(48, 64))
        src = self.save("in.png")
        for p in patches:
            p.start()
        try:
            with self.assertRaises(ValueError):
                realism.encode_jpeg(img, 90, origin)
            code, _, err = run([src, "--origin", origin, "--preset", "day", "--compare"])
        finally:
            for p in reversed(patches):
                p.stop()
        self.assertEqual(code, 1, err)
        self.assertEqual(self.files(), ["in.png"])

    def test_missing_xmp(self):
        for origin in ("generated", "edited"):
            with self.subTest(origin):
                self.broken(origin, mock.patch.object(realism, "insert_xmp", lambda jpeg, xmp: jpeg))

    def test_wrong_description(self):
        for origin in realism.ORIGINS:
            with self.subTest(origin):
                self.broken(origin, mock.patch.object(realism, "description", wrong_when_encoding(realism.description)))

    def test_wrong_source_type_code(self):
        real = realism.xmp_packet
        for origin in ("generated", "edited"):
            with self.subTest(origin):
                self.broken(origin, mock.patch.object(realism, "xmp_packet", lambda code: real("compositeSynthetic")))

    def test_camera_output_with_a_source_type(self):
        self.broken("camera", mock.patch.dict(realism.ORIGINS, {"camera": ("digitalCapture", "Camera photo")}))

    def test_disclosure_check_reads_every_field(self):
        good = realism.encode_jpeg(to_image(scene(48, 64)), 90, "camera")
        self.assertEqual(realism.disclosure_problems(good, "camera"), [])
        typed = realism.insert_xmp(good, realism.xmp_packet("digitalCapture"))
        self.assertTrue(realism.disclosure_problems(typed, "camera"))
        for software in (None, "Adobe Photoshop 25.0"):
            with self.subTest(software=software):
                exif = Image.Exif()
                exif[0x010E] = realism.description("camera")
                if software:
                    exif[0x0131] = software
                buf = io.BytesIO()
                to_image(scene(48, 64)).save(buf, "JPEG", exif=exif.tobytes(), icc_profile=realism.SRGB_ICC)
                self.assertTrue(realism.disclosure_problems(buf.getvalue(), "camera"))
        generated = realism.encode_jpeg(to_image(scene(48, 64)), 90, "generated")
        self.assertEqual(realism.disclosure_problems(generated, "generated"), [])
        self.assertTrue(realism.disclosure_problems(generated, "edited"))  # wrong code for the origin


class CameraWhiteBalance(TempDir):
    def test_camera_default_is_off_through_the_cli(self):
        src = self.save("cast.png", img=to_image(scene() * WARM))
        code, out, err = run([src, "--origin", "camera", "--preset", "day", "--seed", "1", "-o", self.path("c.jpg")])
        self.assertEqual(code, 0, err)
        self.assertIn("white balance: off for camera photos", out)
        code, out, _ = run([src, "--origin", "generated", "--preset", "day", "--seed", "1", "-o", self.path("g.jpg")])
        self.assertIn("white balance: removed", out)
        a = np.asarray(Image.open(self.path("c.jpg")), np.float64)
        b = np.asarray(Image.open(self.path("g.jpg")), np.float64)
        self.assertGreater(np.abs(a - b).mean(), 2)


def gray_wall(tint):
    """A textured gray wall (64% of the frame) beside jeans and a red top, under a tinted light."""
    lin = textured((150, 150, 150), 200, 200)
    lin[:, 140:] = textured((60, 80, 120), 200, 60, seed=1)
    lin[:60, :140] = textured((180, 30, 40), 60, 140, seed=2)
    return lin * tint


class CastGates(unittest.TestCase):
    """Each estimate_cast gate on its own."""

    def test_cool_light_is_left_alone(self):
        _, conf, _ = realism.estimate_cast(gray_wall(WARM))
        self.assertEqual(conf, 1.0)  # the same wall under a warm light of similar size is corrected
        cast, conf, reason = realism.estimate_cast(gray_wall(COOL))
        self.assertEqual(conf, 0.0)
        self.assertEqual(reason, "cool light left alone")

    def test_green_or_magenta_surface_is_left_alone(self):
        _, conf, reason = realism.estimate_cast(gray_wall(GREEN))
        self.assertEqual(conf, 0.0)
        self.assertIn("green or magenta", reason)

    def test_min_frac_gate(self):
        lin = scene() * WARM
        _, conf, _ = realism.estimate_cast(lin)
        self.assertGreater(conf, 0)  # the default lets this scene's gray patches through
        _, conf, reason = realism.estimate_cast(lin, min_frac=0.9)
        self.assertEqual(conf, 0.0)
        self.assertTrue(reason.startswith("no dominant near-neutral surface"), reason)

    def test_clipped_highlights_are_not_a_neutral_surface(self):
        # a blown-out window reads exactly neutral because it clipped, not because the light is neutral
        plain, _, _ = realism.estimate_cast(gray_wall(WARM))
        lin = gray_wall(WARM)
        lin[:, 180:] = 1.0
        cast, conf, _ = realism.estimate_cast(lin)
        self.assertEqual(conf, 1.0)
        np.testing.assert_allclose(cast, plain, atol=0.02)
        self.assertGreater(float(cast[0] - cast[2]), 0.3)  # still the warm cast, not zero


class CaptureGuards(unittest.TestCase):
    def test_zero_strength_calls_no_capture_function(self):
        names = ("lateral_ca", "add_sensor_noise", "phone_nr", "phone_sharpen")
        spies = {n: mock.Mock(side_effect=getattr(realism, n)) for n in names}
        img = Image.fromarray(np.random.default_rng(7).integers(30, 225, size=(72, 96, 3), dtype=np.uint8))
        with mock.patch.multiple(realism, **spies):
            realism.process(img, "night", wb_strength=0.0, ca=0.01, seed=1, capture_strength=0)
            self.assertEqual([n for n in names if spies[n].called], [])
            realism.process(img, "night", wb_strength=0.0, ca=0.01, seed=1, capture_strength=1)
            self.assertEqual([n for n in names if not spies[n].called], [])

    def test_sharpening_cores_out_the_faintest_detail(self):
        s = np.full((32, 32, 3), 0.5, np.float32)
        s[::2, ::2] += 0.0015  # a checker well below the 0.004 threshold
        s[1::2, 1::2] += 0.0015
        np.testing.assert_array_equal(realism.phone_sharpen(s, 0.45), s)

    def test_chroma_is_blurred_less_on_edges_than_in_flat_areas(self):
        s = np.zeros((32, 32, 3), np.float32)
        s[:, :16] = (0.8, 0.2, 0.2)
        s[:, 16:] = (0.2, 0.2, 0.8)
        edge = realism.phone_nr(s, np.zeros((32, 32), np.float32), 0.0, 3.0)
        flat = realism.phone_nr(s, np.ones((32, 32), np.float32), 0.0, 3.0)
        self.assertGreater(float(edge[16, 14, 0] - flat[16, 14, 0]), 0.03)  # red two px from the edge


class WhiteBalanceGuards(unittest.TestCase):
    def test_a_pixel_pushed_past_white_keeps_its_corrected_hue(self):
        # scaled as a whole rather than clipped per channel: G/B is the corrected ratio, not a clipped one
        lin = np.full((64, 64, 3), 0.2, np.float32)
        lin[:8, :8] = (0.80, 0.86, 0.6)
        out = realism.white_balance(lin, np.array([0.3, 0.0, -0.3], np.float32), strength=1.0, warmth=-0.5)
        px = out[:8, :8].reshape(-1, 3).mean(axis=0)
        self.assertAlmostEqual(float(px.max()), 1.0, places=5)
        shift = np.array([-0.3, 0.0, 0.3]) + np.array([0.5, 0.0, -0.5]) * -0.5  # -strength * cast + warmth
        self.assertAlmostEqual(float(px[1] / px[2]), 0.86 / 0.6 * np.exp(shift[1] - shift[2]), delta=0.005)


class AutoNaming(TempDir):
    def test_existing_comparison_moves_both_names_on(self):
        src = self.save("photo.png")
        taken = self.save("photo_real_compare.jpg", b"not mine")
        code, _, err = run([src, "--origin", "generated", "--preset", "day", "--compare"])
        self.assertEqual(code, 0, err)
        self.assertEqual(self.files(), ["photo.png", "photo_real-2.jpg", "photo_real-2_compare.jpg",
                                        "photo_real_compare.jpg"])
        self.assertEqual(read(taken), b"not mine")


if __name__ == "__main__":
    unittest.main()
