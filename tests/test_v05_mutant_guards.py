"""Guards for v0.5.0 mutants that survived the full suite (one class per mutant group).

Each test fails on its named mutant and passes on the real code. Pixel tests use
ceilings with margin, not exact values, so retuned noise still passes as long as
it does not get worse.

Run: python -m unittest discover -s tests
"""
import errno
import os
import struct
import sys
import tempfile
import time
import unittest
from unittest import mock

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_realism import jpeg_with, realism, segment  # noqa: E402
from test_v05_io import exif_segment, exif_tables, jpeg_bytes, run_safe, small  # noqa: E402

REC601 = np.array([0.299, 0.587, 0.114])


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
            (img or small()).save(p)
        return p

    def files(self):
        return sorted(os.listdir(self.d))

    def read(self, name):
        with open(self.path(name), "rb") as f:
            return f.read()


class KeepDateFormat(TempDir):
    """kd_format_check_off: --keep-date copies a date tag only when it is a well-formed date."""

    def test_free_text_in_date_original_is_dropped(self):
        exif = {0x9003: "43.6532N 79.3832W home", 0x9011: "+02:00"}  # coordinates where a date belongs
        src = self.save("in.jpg", jpeg_with(small(), exif_segment({0x0131: "Adobe Lightroom 9.0"}, exif)))
        out = self.path("out.jpg")
        code, _, err = run_safe([src, "--origin", "camera", "--preset", "day", "--keep-date", "-o", out])
        self.assertEqual(code, 0, err)
        data = self.read("out.jpg")
        self.assertNotIn(b"43.6532N", data)
        self.assertNotIn(b"home", data)
        self.assertEqual(exif_tables(data)[1], {0x9011: "+02:00"})
        self.assertEqual(realism.disclosure_problems(data, "camera"), [])


class DisclosureIfd0(unittest.TestCase):
    """kd_check_ifd0_extra_off: camera, lens and owner tags in IFD0 fail the output check."""

    def handmade(self, extra, gps=None):
        ifd0 = {0x010E: realism.description("camera"), 0x0131: "snapshot-realism " + realism.__version__}
        ifd0.update(extra)
        data = jpeg_bytes(small(), icc_profile=realism.SRGB_ICC)
        return data[:2] + exif_segment(ifd0, None, gps) + data[2:]

    def test_baseline_is_clean(self):
        self.assertEqual(realism.disclosure_problems(self.handmade({}), "camera"), [])

    def test_each_ifd0_tag_is_reported(self):
        for tag, value in ((0x010F, "Canon"), (0x0110, "Canon EOS RP"), (0x013B, "Jane Doe"),
                           (0x0132, "2023:05:15 09:00:00")):  # Make, Model, Artist, DateTime
            with self.subTest(hex(tag)):
                problems = realism.disclosure_problems(self.handmade({tag: value}), "camera")
                self.assertTrue(problems)
                self.assertIn("0x%04X" % tag, " ".join(problems))

    def test_gps_ifd_is_reported(self):
        problems = realism.disclosure_problems(self.handmade({}, gps={0x0001: "N", 0x0003: "W"}), "camera")
        self.assertTrue(problems)
        self.assertIn("EXIF carries other tags", " ".join(problems))


class RollbackOnInterrupt(TempDir):
    """int_rollback_on_ki_off: Ctrl+C during the final rename still puts everything back."""

    def interrupt_on(self, target):
        real = realism._replace

        def fake(src, dst, tries=10):
            if src.endswith(".tmp") and dst == target:  # the rename into the target, not the rollback
                raise KeyboardInterrupt
            return real(src, dst, tries)
        return mock.patch.object(realism, "_replace", fake)

    def test_overwrite_restores_the_originals(self):
        a, b = self.save("a.jpg", b"old a"), self.save("b.jpg", b"old b")
        with self.interrupt_on(b), self.assertRaises(KeyboardInterrupt):
            realism.write_outputs([(a, b"new a"), (b, b"new b")], overwrite=True)
        self.assertEqual(self.read("a.jpg"), b"old a")
        self.assertEqual(self.read("b.jpg"), b"old b")
        self.assertEqual(self.files(), ["a.jpg", "b.jpg"])  # no .tmp or .bak left

    def test_new_files_are_removed(self):
        keep = self.save("keep.txt", b"unrelated")
        a, b = self.path("a.jpg"), self.path("b.jpg")
        with self.interrupt_on(b), self.assertRaises(KeyboardInterrupt):
            realism.write_outputs([(a, b"new a"), (b, b"new b")], overwrite=False)
        self.assertEqual(self.files(), ["keep.txt"])
        with open(keep, "rb") as f:
            self.assertEqual(f.read(), b"unrelated")


class CborLongStrings(unittest.TestCase):
    """cbor_max_string_off: strings longer than CBOR_MAX_STRING are skipped, not copied."""

    def test_megabyte_string_headers_decode_quickly(self):
        body = b"\xff" * (4 << 20)  # invalid UTF-8: slow to decode if it were copied
        length = len(body) - 8
        key = b"claim_generator" + b"\x7a" + struct.pack(">I", length)  # text string of ~4 MB
        raw = b"jumdc2pa" + key * 200 + body
        t = time.perf_counter()
        names = realism.c2pa_tool_names(raw)
        self.assertLess(time.perf_counter() - t, 0.5)
        self.assertTrue(all(len(n) <= realism.CBOR_MAX_STRING for n in names))

    def test_long_name_is_not_returned(self):
        raw = b"jumdc2pa" + b"softwareAgent" + b"\x79" + struct.pack(">H", 5000) + b"Midjourney " * 454 + b"xxxxxx"
        names = realism.c2pa_tool_names(raw)
        self.assertEqual([n for n in names if len(n) > realism.CBOR_MAX_STRING], [])
        self.assertEqual(realism.c2pa_ai_agents(raw), [])


class CompareWriteError(TempDir):
    """write_err_target_off, write_err_names_main_output: a failed comparison write names that file."""

    def test_error_names_the_compare_path(self):
        src = self.save("in.png")
        out = self.path("out.jpg")
        cpath = realism.compare_path(out)
        real = realism._replace

        def fake(src_, dst, tries=10):
            if dst.endswith("_compare.jpg"):
                raise PermissionError(errno.EACCES, "Permission denied")  # no file name on the exception
            return real(src_, dst, tries)
        with mock.patch.object(realism, "_replace", fake):
            code, _, err = run_safe([src, "--origin", "generated", "--preset", "day", "--compare", "-o", out])
        self.assertEqual(code, 1, err)
        self.assertIn("error: cannot write %s: " % cpath, err)
        self.assertNotIn("cannot write %s:" % out, err)
        self.assertFalse(os.path.exists(out))
        self.assertFalse(os.path.exists(cpath))
        self.assertEqual(self.files(), ["in.png"])


def xmp(tool):
    return ('<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            '<rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmp:CreatorTool="%s"/>'
            '</rdf:RDF></x:xmpmeta>' % tool).encode("utf-8")


class XmpPacketBounds(TempDir):
    """xmp_close_window_off and the packet limit: a close tag only counts within the window."""

    def test_close_tag_beyond_the_window_is_not_a_packet(self):
        self.assertEqual(realism.xmp_packets(b"<x:xmpmeta " + b"A" * 5000 + b"</x:xmpmeta>", window=1024), [])

    def test_close_tag_inside_the_window_is_a_packet(self):
        packet = b"<x:xmpmeta " + b"A" * 500 + b"</x:xmpmeta>"
        self.assertEqual(realism.xmp_packets(b"junk" + packet + b"junk", window=1024), [packet])

    def test_decoy_packets_cannot_hide_a_generator(self):
        decoys = [segment(0xE1, realism.XMP_SIG + xmp("Adobe Lightroom 9.0")) for _ in range(20)]
        tell = segment(0xE1, realism.XMP_SIG + xmp("Midjourney v6"))
        src = self.save("decoys.jpg", jpeg_with(small(), *(decoys + [tell])))
        code, _, err = run_safe([src, "--origin", "camera", "--preset", "day"])
        self.assertEqual(code, 2, err)
        self.assertIn("XMP names an image generator", err)
        self.assertEqual(self.files(), ["decoys.jpg"])

    def test_decoys_alone_are_accepted(self):
        decoys = [segment(0xE1, realism.XMP_SIG + xmp("Adobe Lightroom 9.0")) for _ in range(21)]
        src = self.save("decoys.jpg", jpeg_with(small(), *decoys))
        code, _, err = run_safe([src, "--origin", "camera", "--preset", "day"])
        self.assertEqual(code, 0, err)

    def test_packet_limit(self):
        packet = b"<x:xmpmeta></x:xmpmeta>"
        self.assertEqual(len(realism.xmp_packets(packet * 5000)), 4096)


class NoiseLevels(unittest.TestCase):
    """noise_highlight_fade_partial, noise_luma_mean_off, noise_chroma_field_mean_off."""

    def test_highlight_noise_fades_near_white(self):
        # 0.32 DN measured at v0.5.0; noise without the fade from 0.92 up gives about 1.3 DN
        img = Image.new("RGB", (64, 64), (253, 253, 253))
        out, _ = realism.process(img, "night", wb_strength=0.0, ca=0.0, seed=7)
        a = np.asarray(out.convert("RGB"), np.float64)
        self.assertLessEqual(float((a @ REC601).std()), 0.6)

    def test_small_tinted_patch_keeps_its_mean(self):
        # worst channel 0.13 DN measured at v0.5.0; a field that is not zero-mean shifts it 1.3 to 2 DN
        level = np.array([138, 128, 118], np.float64)
        img = Image.new("RGB", (16, 16), tuple(int(v) for v in level))
        worst = np.zeros(3)
        for seed in range(20):
            out, info = realism.process(img, "night", wb_strength=0.0, ca=0.0, seed=seed)
            self.assertFalse(info["mono"])
            shift = np.asarray(out, np.float64).mean(axis=(0, 1)) - level
            worst = np.maximum(worst, np.abs(shift))
        self.assertTrue((worst <= 0.5).all(), worst.round(3))


class ChromaticAberrationSign(unittest.TestCase):
    """ca_sign: red is magnified and blue shrunk, so red reaches further toward the edge."""

    def test_red_edge_sits_outside_blue(self):
        lin = np.zeros((201, 401, 3), np.float32)
        lin[50:151, 100:301] = 1.0  # centered bright square, its right edge 100 px from the center
        out = realism.lateral_ca(lin, 0.01)
        row = out[100, 200:]  # center row, from the center to the right edge

        def edge(ch):
            return float(row[:, ch].sum())  # the channel's extent to the right, with subpixel weight
        self.assertGreater(edge(0), edge(1) + 0.5)
        self.assertLess(edge(2), edge(1) - 0.5)


if __name__ == "__main__":
    unittest.main()
