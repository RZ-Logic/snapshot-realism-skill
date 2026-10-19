"""Tests for tools/blind_test.py, the blind A/B test kit. Run: python -m unittest discover -s tests"""
import contextlib
import csv
import importlib.util
import io
import math
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "tools", "blind_test.py")
spec = importlib.util.spec_from_file_location("blind_test", TOOL)
blind_test = importlib.util.module_from_spec(spec)
spec.loader.exec_module(blind_test)

ORIGINAL = (200, 30, 30)  # red: the original of every pair
PROCESSED = (30, 30, 200)  # blue: the processed image of every pair


def run_main(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = blind_test.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def read_key(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


class BinomialTest(unittest.TestCase):
    def test_tail_matches_exact_sums(self):
        self.assertEqual(blind_test.binom_sf(0, 10), 1.0)
        self.assertEqual(blind_test.binom_sf(11, 10), 0.0)
        self.assertEqual(blind_test.binom_sf(10, 10), 1 / 1024)
        self.assertAlmostEqual(blind_test.binom_sf(9, 10), 11 / 1024, places=15)
        self.assertAlmostEqual(blind_test.binom_sf(5, 10), 638 / 1024, places=15)
        # large n stays exact enough and in range
        p = blind_test.binom_sf(600, 1000)
        self.assertTrue(0 < p < 1e-9, p)
        self.assertAlmostEqual(blind_test.binom_sf(500, 1000), 0.5 + math.comb(1000, 500) / 2 ** 1001, places=12)


class MakeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sr-blind-")
        self.pairs = []
        for i in range(8):
            a = os.path.join(self.tmp, "p%d.png" % i)
            b = os.path.join(self.tmp, "p%d_real.png" % i)
            # the processed copy is smaller, like realism.py's 1600 px output of a bigger original
            Image.new("RGB", (120 + 8 * i, 90), ORIGINAL).save(a)
            Image.new("RGB", (80 + 4 * i, 60), PROCESSED).save(b)
            self.pairs += [a, b]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make(self, name, seed, *extra):
        out = os.path.join(self.tmp, name)
        code, stdout, err = run_main("make", out, *(self.pairs + ["--seed", str(seed)] + list(extra)))
        self.assertEqual(code, 0, err)
        return out

    def test_writes_numbered_sheets_key_and_answer_form(self):
        out = self.make("run", 3)
        files = sorted(os.listdir(out))
        self.assertEqual(files, ["answers.csv", "key.csv"] + ["sheet-%02d.png" % n for n in range(1, 9)])
        rows = read_key(os.path.join(out, "key.csv"))
        self.assertEqual([r["sheet"] for r in rows], ["%02d" % n for n in range(1, 9)])
        self.assertEqual(sorted(r["processed"] for r in rows), sorted(os.path.abspath(p) for p in self.pairs[1::2]))
        with open(os.path.join(out, "answers.csv"), encoding="utf-8") as f:
            self.assertEqual(f.read().splitlines(), ["sheet,pick"] + ["%02d," % n for n in range(1, 9)])

    def test_processed_image_is_on_the_side_the_key_names(self):
        out = self.make("run", 3)
        sides = set()
        for row in read_key(os.path.join(out, "key.csv")):
            with Image.open(os.path.join(out, "sheet-%s.png" % row["sheet"])) as im:
                im = im.convert("RGB")
                y = blind_test.HEADER + 30
                left = im.getpixel((blind_test.GUTTER + 5, y))
                right = im.getpixel((im.width - blind_test.GUTTER - 5, y))
            want = (PROCESSED, ORIGINAL) if row["processed_side"] == "L" else (ORIGINAL, PROCESSED)
            self.assertEqual((left, right), want, row)
            sides.add(row["processed_side"])
        self.assertEqual(sides, {"L", "R"})  # 8 pairs with seed 3 use both sides

    def test_both_images_share_one_height(self):
        out = self.make("run", 3)
        for row in read_key(os.path.join(out, "key.csv")):
            with Image.open(os.path.join(out, "sheet-%s.png" % row["sheet"])) as im:
                # the shorter (processed) image sets the height: 60 px
                self.assertEqual(im.height, blind_test.HEADER + 60 + blind_test.GUTTER)
                rgb = im.convert("RGB")
                self.assertNotEqual(rgb.getpixel((blind_test.GUTTER + 2, blind_test.HEADER + 59)), (255, 255, 255))
                self.assertEqual(rgb.getpixel((blind_test.GUTTER + 2, blind_test.HEADER + 60)), (255, 255, 255))

    def test_max_height_caps_the_sheet(self):
        out = self.make("small", 3, "--max-height", "40")
        with Image.open(os.path.join(out, "sheet-01.png")) as im:
            self.assertEqual(im.height, blind_test.HEADER + 40 + blind_test.GUTTER)

    def test_same_seed_gives_identical_files_and_other_seeds_differ(self):
        a, b, c = self.make("a", 11), self.make("b", 11), self.make("c", 12)
        for name in os.listdir(a):
            with open(os.path.join(a, name), "rb") as fa, open(os.path.join(b, name), "rb") as fb:
                self.assertEqual(fa.read(), fb.read(), name)
        key_a = [(r["processed_side"], r["original"]) for r in read_key(os.path.join(a, "key.csv"))]
        key_c = [(r["processed_side"], r["original"]) for r in read_key(os.path.join(c, "key.csv"))]
        self.assertNotEqual(key_a, key_c)

    def test_labels_without_a_truetype_font(self):
        # Linux CI may have no Arial or DejaVu: the built-in bitmap font is scaled up instead
        original = blind_test.label_font
        blind_test.label_font = lambda size: None
        try:
            out = self.make("bitmap", 3)
        finally:
            blind_test.label_font = original
        with Image.open(os.path.join(out, "sheet-01.png")) as im:
            strip = im.convert("L").crop((0, 0, im.width, blind_test.HEADER))
            self.assertLess(strip.getextrema()[0], 128)  # some label ink in the header

    def test_jpg_format(self):
        out = self.make("jpg", 3, "--format", "jpg")
        self.assertIn("sheet-01.jpg", os.listdir(out))

    def test_odd_count_and_missing_file_are_errors(self):
        code, _, err = run_main("make", os.path.join(self.tmp, "odd"), *self.pairs[:3])
        self.assertEqual(code, 2)
        self.assertIn("pairs", err)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "odd")))
        code, _, err = run_main("make", os.path.join(self.tmp, "gone"), self.pairs[0], os.path.join(self.tmp, "nope.jpg"))
        self.assertEqual(code, 2)
        self.assertIn("not found", err)

    def test_exif_orientation_is_applied(self):
        a = os.path.join(self.tmp, "rot.jpg")
        exif = Image.Exif()
        exif[0x0112] = 6  # rotate 90 degrees clockwise to display
        Image.new("RGB", (100, 60), ORIGINAL).save(a, exif=exif.tobytes())
        b = os.path.join(self.tmp, "rot_real.jpg")
        Image.new("RGB", (60, 100), PROCESSED).save(b)
        out = os.path.join(self.tmp, "rot")
        code, _, err = run_main("make", out, a, b, "--seed", "1")
        self.assertEqual(code, 0, err)
        with Image.open(os.path.join(out, "sheet-01.png")) as im:
            # both upright 60x100, so the sheet is two 60 px wide images plus three gutters
            self.assertEqual(im.size, (2 * 60 + 3 * blind_test.GUTTER, blind_test.HEADER + 100 + blind_test.GUTTER))

    def test_command_line_runs(self):
        out = os.path.join(self.tmp, "cli")
        p = subprocess.run([sys.executable, TOOL, "make", out] + self.pairs[:2] + ["--seed", "5"],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("seed 5", p.stdout)
        self.assertTrue(os.path.isfile(os.path.join(out, "sheet-01.png")))


class ScoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sr-score-")
        self.key = os.path.join(self.tmp, "key.csv")
        sides = "LRRLLRLRRL"
        with open(self.key, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["sheet", "processed_side", "original", "processed"])
            for n, side in enumerate(sides, 1):
                w.writerow(["%02d" % n, side, "a%d.png" % n, "a%d_real.jpg" % n])
        self.sides = sides

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def answers(self, name, text):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def flip(self, side):
        return "R" if side == "L" else "L"

    def test_counts_processed_picks_with_p_value(self):
        # processed picked on 9 of 10 sheets, in mixed formats
        lines = ["sheet,pick"]
        for n, side in enumerate(self.sides, 1):
            pick = side if n != 4 else self.flip(side)
            fmt = ["%02d,%s", "%d %s", "sheet-%02d.png\t%s"][n % 3]
            lines.append(fmt % (n, pick if n % 2 else {"L": "left", "R": "Right"}[pick]))
        path = self.answers("rater1.csv", "\n".join(lines) + "\n")
        code, out, err = run_main("score", self.key, path)
        self.assertEqual(code, 0, err)
        self.assertIn("picked as the phone photo 9 of 10 times (90%)", out)
        self.assertIn("p = %.4g" % (11 / 1024), out)

    def test_chance_level_and_blank_picks(self):
        lines = ["sheet,pick"]
        for n, side in enumerate(self.sides, 1):
            if n > 8:
                lines.append("%02d," % n)  # left blank
            else:
                lines.append("%02d,%s" % (n, side if n % 2 else self.flip(side)))
        code, out, err = run_main("score", self.key, self.answers("r.csv", "\n".join(lines)))
        self.assertEqual(code, 0, err)
        self.assertIn("4 of 8 times (50%), 8 of 10 sheets answered", out)
        self.assertIn("p = %.4g" % blind_test.binom_sf(4, 8), out)

    def test_several_raters_are_pooled(self):
        right = self.answers("a.csv", "\n".join("%d,%s" % (n, s) for n, s in enumerate(self.sides, 1)))
        wrong = self.answers("b.csv", "\n".join("%d,%s" % (n, self.flip(s)) for n, s in enumerate(self.sides, 1)))
        code, out, err = run_main("score", self.key, right, wrong)
        self.assertEqual(code, 0, err)
        self.assertIn("a.csv: processed image picked as the phone photo 10 of 10", out)
        self.assertIn("b.csv: processed image picked as the phone photo 0 of 10", out)
        self.assertIn("all raters: processed image picked as the phone photo 10 of 20 times (50%)", out)
        self.assertIn("0 of 10 times (0%), 10 of 10 sheets answered; one-sided binomial p = 1\n", out)

    def test_bad_answers_are_errors(self):
        for text, msg in (("01,maybe\n", "not L or R"),
                          ("11,L\n", "not in the key"),
                          ("01,L\n1,R\n", "answered twice"),
                          ("sheet,pick\nfoo,L\n", "no sheet number")):
            with self.subTest(text=text):
                code, _, err = run_main("score", self.key, self.answers("bad.csv", text))
                self.assertEqual(code, 2)
                self.assertIn(msg, err)

    def test_round_trip_with_make(self):
        src = os.path.join(self.tmp, "src")
        os.makedirs(src)
        pairs = []
        for i in range(4):
            a, b = os.path.join(src, "o%d.png" % i), os.path.join(src, "o%d_real.png" % i)
            Image.new("RGB", (40, 30), ORIGINAL).save(a)
            Image.new("RGB", (40, 30), PROCESSED).save(b)
            pairs += [a, b]
        out = os.path.join(self.tmp, "kit")
        self.assertEqual(run_main("make", out, *(pairs + ["--seed", "9"]))[0], 0)
        key = read_key(os.path.join(out, "key.csv"))
        # a rater who always picks the processed image fills in the blank form
        form = os.path.join(out, "answers.csv")
        with open(form, "w", encoding="utf-8") as f:
            f.write("sheet,pick\n" + "".join("%s,%s\n" % (r["sheet"], r["processed_side"]) for r in key))
        code, out_text, err = run_main("score", os.path.join(out, "key.csv"), form)
        self.assertEqual(code, 0, err)
        self.assertIn("4 of 4 times (100%)", out_text)
        self.assertIn("p = 0.0625", out_text)


if __name__ == "__main__":
    unittest.main()
