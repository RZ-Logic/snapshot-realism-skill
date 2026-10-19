"""v0.5.0 provenance and I/O properties (SPEC B1 to B8).

Run: python -m unittest discover -s tests
"""
import contextlib
import errno
import io
import os
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import zlib
from unittest import mock

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_realism import ROOT, jpeg_with, png_with_chunk, realism, scene, segment, to_image  # noqa: E402

SCRIPTS = os.path.join(ROOT, "skill", "snapshot-realism", "scripts")
DATES = {0x9003: "2023:05:14 10:20:30", 0x9011: "+02:00", 0x9291: "123"}  # DateTimeOriginal, offset, subseconds


def run_safe(argv):
    """main() with output captured; a KeyboardInterrupt that escapes main is reported, not raised."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = realism.main(argv)
        except SystemExit as e:
            code = e.code
        except KeyboardInterrupt:
            code = "KeyboardInterrupt escaped main()"
    return code, out.getvalue(), err.getvalue()


def small(w=64, h=48):
    return to_image(scene(h, w))


def jpeg_bytes(img, **kw):
    buf = io.BytesIO()
    img.save(buf, "JPEG", **kw)
    return buf.getvalue()


def software_jpeg(name, img=None):
    exif = Image.Exif()
    exif[0x0131] = name
    return jpeg_bytes(img or small(), exif=exif.tobytes())


def exif_segment(ifd0, exif=None, gps=None):
    """APP1 Exif segment, little-endian, built by hand so it works on any Pillow.

    Each table maps tag -> str (ASCII) or int (LONG); the Exif and GPS IFD
    pointers are added to IFD0 when those tables are given.
    """
    ifd0 = dict(ifd0)
    subs = [(tag, table) for tag, table in ((0x8769, exif), (0x8825, gps)) if table]
    for tag, _ in subs:
        ifd0[tag] = 0
    tables = [ifd0] + [table for _, table in subs]
    sizes = [2 + 12 * len(t) + 4 for t in tables]
    offsets = [8]
    for s in sizes[:-1]:
        offsets.append(offsets[-1] + s)
    for k, (tag, _) in enumerate(subs):
        ifd0[tag] = offsets[k + 1]
    data_at, body, data = offsets[-1] + sizes[-1], b"", b""
    for table in tables:
        body += struct.pack("<H", len(table))
        for tag in sorted(table):
            v = table[tag]
            if isinstance(v, int):
                body += struct.pack("<HHII", tag, 4, 1, v)
                continue
            b = v.encode("ascii") + b"\0"
            if len(b) <= 4:
                body += struct.pack("<HHI", tag, 2, len(b)) + b.ljust(4, b"\0")
            else:
                body += struct.pack("<HHII", tag, 2, len(b), data_at + len(data))
                data += b + (b"\0" if len(b) % 2 else b"")
        body += struct.pack("<I", 0)
    return segment(0xE1, b"Exif\x00\x00" + b"II*\x00" + struct.pack("<I", 8) + body + data)


def exif_tables(jpeg):
    """(IFD0, Exif IFD, GPS IFD) of a JPEG's APP1 Exif as {tag: value}; read by hand, any Pillow."""
    i = 2
    while i + 4 <= len(jpeg) and jpeg[i] == 0xFF:
        marker, n = jpeg[i + 1], struct.unpack(">H", jpeg[i + 2:i + 4])[0]
        if marker == 0xDA:
            break
        body = jpeg[i + 4:i + 2 + n]
        if marker == 0xE1 and body.startswith(b"Exif\x00\x00"):
            return _tiff_tables(body[6:])
        i += 2 + n
    return {}, {}, {}


def _tiff_tables(t):
    e = "<" if t[:2] == b"II" else ">"
    sizes = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}

    def ifd(off):
        out = {}
        for k in range(struct.unpack(e + "H", t[off:off + 2])[0]):
            tag, typ, cnt = struct.unpack(e + "HHI", t[off + 2 + 12 * k:off + 10 + 12 * k])
            raw = t[off + 10 + 12 * k:off + 14 + 12 * k]
            size = sizes.get(typ, 1) * cnt
            val = raw[:size] if size <= 4 else t[struct.unpack(e + "I", raw)[0]:][:size]
            if typ == 2:
                val = val.split(b"\0")[0].decode("latin-1")
            elif typ == 4 and cnt == 1:
                val = struct.unpack(e + "I", val)[0]
            elif typ == 3 and cnt == 1:
                val = struct.unpack(e + "H", val)[0]
            out[tag] = val
        return out

    ifd0 = ifd(struct.unpack(e + "I", t[4:8])[0])
    return (ifd0, ifd(ifd0[0x8769]) if 0x8769 in ifd0 else {}, ifd(ifd0[0x8825]) if 0x8825 in ifd0 else {})


def png_header_only(w, h):
    """A PNG that declares w x h 8-bit RGB with one tiny IDAT: enough for Image.open, never decoded."""
    def chunk(ctype, payload):
        return struct.pack(">I", len(payload)) + ctype + payload + struct.pack(">I", zlib.crc32(ctype + payload))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\0" * 16)) + chunk(b"IEND", b""))


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


class BoundedXmpScan(TempDir):
    """B1: a crafted file cannot stall the XMP scan."""

    def test_many_unterminated_packets_finish_quickly(self):
        raw = jpeg_bytes(Image.new("RGB", (64, 64), (90, 90, 90))) + b"<x:xmpmeta " * 16000 + b"A" * (2 << 20)
        src = self.save("crafted.jpg", raw)
        child = ("import io, sys, time; sys.path.insert(0, sys.argv[1]); import realism; from PIL import Image\n"
                 "raw = open(sys.argv[2], 'rb').read(); im = Image.open(io.BytesIO(raw)); im.load()\n"
                 "t = time.perf_counter(); realism.ai_signals(raw, im); print('%.3f' % (time.perf_counter() - t))\n")
        try:
            p = subprocess.run([sys.executable, "-c", child, SCRIPTS, src], capture_output=True, text=True,
                               timeout=10)
        except subprocess.TimeoutExpired:
            self.fail("ai_signals did not finish within 10 s")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertLess(float(p.stdout.strip().splitlines()[-1]), 3.0)


class Interrupts(TempDir):
    """B2: Ctrl+C at any stage exits 130 with one line and writes nothing."""

    def check(self, target, attr):
        src = self.save("in.png")
        with mock.patch.object(target, attr, side_effect=KeyboardInterrupt):
            code, _, err = run_safe([src, "--origin", "generated", "--preset", "day", "--compare"])
        self.assertEqual(code, 130, err)
        self.assertIn("interrupted", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(len(err.strip().splitlines()), 1, err)
        self.assertEqual(self.files(), ["in.png"])

    def test_interrupt_while_reading(self):
        self.check(realism.Image, "open")

    def test_interrupt_while_scanning_metadata(self):
        self.check(realism, "ai_signals")

    def test_interrupt_while_processing(self):
        self.check(realism, "process")

    def test_interrupt_while_encoding(self):
        self.check(realism, "encode_jpeg")

    def test_interrupt_while_writing(self):
        self.check(realism, "write_outputs")


class LargePhotos(TempDir):
    """B3: 300 MP limit, a clean refusal above twice that, and DCT draft decoding for huge JPEGs."""

    def test_pixel_limit_is_raised_at_import(self):
        self.assertEqual(realism.MAX_PIXELS, 300000000)
        self.assertEqual(Image.MAX_IMAGE_PIXELS, realism.MAX_PIXELS)

    def test_200_megapixel_photo_is_not_a_bomb(self):
        import warnings
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            Image.open(io.BytesIO(png_header_only(20000, 10000)))
        self.assertEqual([str(w.message) for w in caught if issubclass(w.category, Image.DecompressionBombWarning)], [])

    def test_too_large_input_is_refused_in_plain_words(self):
        src = self.save("huge.png", png_header_only(30000, 25000))  # 750 MP, over twice the limit
        code, _, err = run_safe([src, "--origin", "generated", "--preset", "day"])
        self.assertEqual(code, 1, err)
        self.assertRegex(err, r"error: .*huge\.png is too large \(750(\.0+)? megapixels\); downscale it first")
        self.assertNotIn("DOS attack", err)
        self.assertEqual(self.files(), ["huge.png"])

    def test_bomb_warning_and_error_in_a_real_process(self):
        # the warnings filter of a test runner differs from a plain run, so this runs a child process
        # with both limits lowered: 40x40 (1600 px) is over the limit, 50x50 (2500 px) over twice it
        warn_src = self.save("warn.png", img=small(40, 40))
        err_src = self.save("err.png", img=small(50, 50))
        child = ("import sys; sys.path.insert(0, sys.argv[1]); import realism; from PIL import Image\n"
                 "realism.MAX_PIXELS = Image.MAX_IMAGE_PIXELS = 1000\n"
                 "try:\n    code = realism.main([sys.argv[2], '--origin', 'generated', '--preset', 'day'])\n"
                 "except SystemExit as e:\n    code = e.code\n"
                 "sys.exit(code)\n")
        p = subprocess.run([sys.executable, "-c", child, SCRIPTS, warn_src], capture_output=True, text=True,
                           timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn("DecompressionBomb", p.stderr)
        self.assertNotIn("decompression bomb", p.stderr.lower())
        p = subprocess.run([sys.executable, "-c", child, SCRIPTS, err_src], capture_output=True, text=True,
                           timeout=60)
        self.assertEqual(p.returncode, 1, p.stderr)
        self.assertRegex(p.stderr, r"error: .*err\.png is too large \([0-9.,]+ megapixels\); downscale it first")
        self.assertNotIn("DOS attack", p.stderr)
        self.assertNotIn("Traceback", p.stderr)

    def big_jpeg(self, orientation=None, software=None):
        yy, xx = np.mgrid[0:2000, 0:3000]
        a = np.stack([xx % 256, yy % 256, (xx + yy) % 256], 2).astype(np.uint8)
        exif = Image.Exif()
        if orientation:
            exif[0x0112] = orientation
        if software:
            exif[0x0131] = software
        return jpeg_bytes(Image.fromarray(a), quality=80, exif=exif.tobytes(), icc_profile=realism.SRGB_ICC)

    def test_open_image_uses_draft_above_the_threshold(self):
        raw = self.big_jpeg(orientation=6, software="Pixel Camera")
        with mock.patch.object(realism, "DRAFT_PIXELS", 1000000):
            img = realism.open_image(raw, 500)
        self.assertTrue(1000 <= max(img.size) < 3000, img.size)
        np.asarray(img)  # loaded
        self.assertEqual(str(img.getexif().get(0x0131)), "Pixel Camera")
        self.assertEqual(img.info.get("icc_profile"), realism.SRGB_ICC)
        rgb, _, _ = realism.load_srgb(img)
        self.assertGreater(rgb.size[1], rgb.size[0])  # orientation 6 still applied: portrait

    def test_open_image_below_the_threshold_is_unchanged(self):
        raw = self.big_jpeg()
        img = realism.open_image(raw, 500)  # 6 MP is below the default DRAFT_PIXELS
        self.assertEqual(img.size, (3000, 2000))

    def test_drafted_input_is_still_checked_and_processed(self):
        src = self.save("big.jpg", self.big_jpeg(software="Midjourney 7"))
        with mock.patch.object(realism, "DRAFT_PIXELS", 1000000):
            code, _, err = run_safe([src, "--origin", "camera", "--preset", "day", "--max-edge", "500"])
            self.assertEqual(code, 2, err)
            code, _, err = run_safe([src, "--origin", "generated", "--preset", "day", "--max-edge", "500"])
        self.assertEqual(code, 0, err)
        with Image.open(self.path("big_real.jpg")) as out:
            self.assertEqual(out.size, (500, 333))


# names a tool gives itself in a software field (SPEC B4)
GENERATORS = ["ChatGPT", "OpenAI", "gpt-image", "GPT-4o", "DALL-E", "Sora", "Gemini", "Imagen", "Nano Banana",
              "Google AI", "Midjourney", "Stable Diffusion", "SDXL", "FLUX.1", "Black Forest Labs", "Ideogram",
              "Leonardo.Ai", "Runway", "Krea", "Recraft", "Meta AI", "Grok", "Kling", "Seedream", "Qwen-Image",
              "Hunyuan", "Firefly"]
REFUSED_SOFTWARE = ["ChatGPT", "Google Gemini", "Imagen 4", "FLUX.1 [dev]", "Ideogram 3.0"]
ACCEPTED_SOFTWARE = ["Adobe Lightroom 9.0", "Snapseed 2.0", "Luminar Neo", "Aurora HDR 2019", "GIMP 2.10.36",
                     "HDR+ 1.0.641377693", "S928BXXU4AXK1", "18.1", "Pixel Camera", "Google Photos",
                     "Editor de imagen 2.1"]  # Spanish "image editor", not Google Imagen


def iptc_segment(records):
    """APP13 Photoshop IRB 0x0404 holding IPTC IIM datasets {(record, dataset): bytes}."""
    iim = b"".join(b"\x1c" + bytes(k) + struct.pack(">H", len(v)) + v for k, v in records.items())
    irb = b"8BIM" + struct.pack(">H", 0x0404) + b"\0\0" + struct.pack(">I", len(iim)) + iim
    return segment(0xED, b"Photoshop 3.0\x00" + irb + (b"\0" if len(iim) % 2 else b""))


def xmp_creator_tool(name):
    return (('<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
             '<rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmp:CreatorTool="%s"/>'
             '</rdf:RDF></x:xmpmeta>') % name).encode("utf-8")


def signals(data):
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        return realism.ai_signals(data, im)


class GeneratorNames(TempDir):
    """B4: current generators are named in software fields; captions that only mention them do not count."""

    def test_every_listed_generator_is_found_in_exif_software(self):
        missed = [name for name in GENERATORS if not signals(software_jpeg(name))]
        self.assertEqual(missed, [])

    def test_generators_are_found_in_other_software_fields(self):
        from PIL import PngImagePlugin
        base = small()
        info = PngImagePlugin.PngInfo()
        info.add_text("Software", "Kling AI")
        png = io.BytesIO()
        base.save(png, "PNG", pnginfo=info)
        cases = {
            "XMP CreatorTool": jpeg_with(base, segment(0xE1, realism.XMP_SIG + xmp_creator_tool("Nano Banana"))),
            "IPTC originating program": jpeg_with(base, iptc_segment({(2, 65): b"Seedream 4.0"})),
            "PNG Software text": png.getvalue(),
        }
        for field, data in cases.items():
            with self.subTest(field):
                self.assertTrue(signals(data), field)

    def test_camera_mode_refuses_generator_software(self):
        for name in REFUSED_SOFTWARE:
            with self.subTest(name):
                src = self.save("r%d.jpg" % len(self.files()), software_jpeg(name))
                code, _, err = run_safe([src, "--origin", "camera", "--preset", "day"])
                self.assertEqual(code, 2, err)
                self.assertFalse(os.path.exists(os.path.splitext(src)[0] + "_real.jpg"))

    def test_camera_mode_accepts_editors_and_phone_firmware(self):
        for name in ACCEPTED_SOFTWARE:
            with self.subTest(name):
                src = self.save("a%d.jpg" % len(self.files()), software_jpeg(name))
                code, _, err = run_safe([src, "--origin", "camera", "--preset", "day"])
                self.assertEqual(code, 0, err)

    def test_captions_that_only_mention_a_generator_do_not_count(self):
        for text in ("Runway show in Milan", "Gemini Studios", "Krea and Recraft booth at the expo",
                     "Grok sticker on a laptop", "Nano Banana smoothie", "Kling family reunion", "Sora at the park"):
            with self.subTest(text):
                exif = Image.Exif()
                exif[0x010E] = text
                self.assertEqual(signals(jpeg_bytes(small(), exif=exif.tobytes())), [])


class CborBudget(unittest.TestCase):
    """B5: the CBOR decode budget is per file, not per key."""

    def test_many_generator_keys_decode_quickly(self):
        raw = b"jumdc2pa" + (b"claim_generator\x9f" + b"\x00" * 48) * 2000  # 128 KB
        t = time.perf_counter()
        realism.c2pa_tool_names(raw)
        self.assertLess(time.perf_counter() - t, 0.5)


class C2paWithoutSourceType(TempDir):
    """B6: a manifest with no source type still cannot confirm camera origin."""

    def test_refusal_message(self):
        src = self.save("ps.png", png_with_chunk(small(), b"caBX", b"jumdc2pa c2pa.opened"))
        code, _, err = run_safe([src, "--origin", "camera", "--preset", "day"])
        self.assertEqual(code, 2)
        self.assertIn("camera origin cannot be confirmed", err)


class WriteErrors(TempDir):
    """B7: write errors name the output path; processing errors name the input."""

    def test_write_error_names_the_output(self):
        src = self.save("in.png")
        out = self.path("out.jpg")
        failures = {"with a file name": PermissionError(errno.EACCES, "Permission denied", out),
                    "without a file name": OSError(errno.ENOSPC, "No space left on device")}
        for name, exc in failures.items():
            with self.subTest(name), mock.patch.object(realism, "write_outputs", side_effect=exc):
                code, _, err = run_safe([src, "--origin", "generated", "--preset", "day", "-o", out])
                self.assertEqual(code, 1, err)
                self.assertIn("error: cannot write %s: " % out, err)
                self.assertFalse(os.path.exists(out))

    def test_processing_error_still_names_the_input(self):
        src = self.save("in.png")
        with mock.patch.object(realism, "process", side_effect=ValueError("boom")):
            code, _, err = run_safe([src, "--origin", "generated", "--preset", "day"])
        self.assertEqual(code, 1)
        self.assertIn("error: %s: boom" % src, err)


class KeepDate(TempDir):
    """B8: --keep-date copies only the capture date, its offset and subseconds."""

    def camera_jpeg(self):
        ifd0 = {0x010F: "Canon", 0x0110: "Canon EOS RP", 0x0132: "2023:05:15 09:00:00", 0x0131: "Adobe Lightroom 9.0"}
        exif = dict(DATES)
        exif.update({0x9004: "2023:05:14 10:20:30", 0xA431: "123456789", 0xA434: "RF85mm F2 MACRO IS STM"})
        gps = {0x0001: "N", 0x0003: "W"}
        return jpeg_with(small(), exif_segment(ifd0, exif, gps))

    def test_generated_with_keep_date_is_an_argument_error(self):
        src = self.save("in.jpg", self.camera_jpeg())
        code, _, err = run_safe([src, "--origin", "generated", "--preset", "day", "--keep-date"])
        self.assertEqual(code, 2)
        self.assertIn("--keep-date", err)
        self.assertNotIn("unrecognized arguments", err)
        self.assertEqual(self.files(), ["in.jpg"])

    def test_camera_and_edited_keep_only_the_three_date_tags(self):
        src = self.save("in.jpg", self.camera_jpeg())
        for origin in ("camera", "edited"):
            with self.subTest(origin):
                out = self.path("%s.jpg" % origin)
                code, _, err = run_safe([src, "--origin", origin, "--preset", "day", "--keep-date", "-o", out])
                self.assertEqual(code, 0, err)
                with open(out, "rb") as f:
                    data = f.read()
                ifd0, exif, gps = exif_tables(data)
                self.assertEqual(exif, DATES)
                self.assertEqual(gps, {})
                self.assertLessEqual(set(ifd0), {0x010E, 0x0131, 0x8769})  # no Make, Model, DateTime, GPS
                self.assertEqual(realism.disclosure_problems(data, origin), [])
                self.assertNotIn(b"Canon", data)
                self.assertNotIn(b"123456789", data)

    def test_without_the_flag_no_date_is_copied(self):
        src = self.save("in.jpg", self.camera_jpeg())
        code, _, err = run_safe([src, "--origin", "camera", "--preset", "day"])
        self.assertEqual(code, 0, err)
        with open(self.path("in_real.jpg"), "rb") as f:
            data = f.read()
        self.assertNotIn(0x9003, exif_tables(data)[1])
        self.assertNotIn(b"2023:05:14", data)

    def test_keep_date_on_an_input_without_dates(self):
        src = self.save("plain.png")
        code, _, err = run_safe([src, "--origin", "camera", "--preset", "day", "--keep-date"])
        self.assertEqual(code, 0, err)
        with open(self.path("plain_real.jpg"), "rb") as f:
            self.assertEqual(exif_tables(f.read())[1], {})

    def test_encode_jpeg_dates(self):
        img = small()
        for origin in ("camera", "edited"):
            with self.subTest(origin):
                data = realism.encode_jpeg(img, 90, origin, dates=dict(DATES))
                self.assertEqual(exif_tables(data)[1], DATES)
                self.assertEqual(realism.disclosure_problems(data, origin), [])
        extra = dict(DATES)
        extra[0xA431] = "123456789"  # a body serial number is never allowed
        with self.assertRaises(ValueError):
            realism.encode_jpeg(img, 90, "camera", dates=extra)

    def handmade(self, exif):
        ifd0 = {0x010E: realism.description("camera"), 0x0131: "snapshot-realism " + realism.__version__}
        data = jpeg_bytes(small(), icc_profile=realism.SRGB_ICC)
        return data[:2] + exif_segment(ifd0, exif) + data[2:]

    def test_disclosure_allows_exactly_the_three_date_tags(self):
        self.assertEqual(realism.disclosure_problems(self.handmade(dict(DATES)), "camera"), [])
        for tag, value in ((0xA434, "RF85mm F2 MACRO IS STM"), (0x9004, "2023:05:14 10:20:30"),
                           (0xA431, "123456789")):
            with self.subTest(hex(tag)):
                exif = dict(DATES)
                exif[tag] = value
                self.assertTrue(realism.disclosure_problems(self.handmade(exif), "camera"))


if __name__ == "__main__":
    unittest.main()
