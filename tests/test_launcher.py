"""Tests for realism.bat, the Windows drag-and-drop launcher. Run: python -m unittest discover -s tests

Each run starts the real launcher through cmd.exe in a temporary copy of the
repo layout. The answers to its choice prompts come from a file redirected to
stdin: choice reads one character per prompt and returns 255 at the end of the
file, which the launcher treats as no answer. (A pipe also feeds choice, but a
prompt after the pipe has run dry hangs, so the tests use a file.)
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BAT = os.path.join(ROOT, "realism.bat")
SKILL = os.path.join(ROOT, "skill", "snapshot-realism")
# carries a real ChatGPT C2PA manifest
AI_PNG = os.path.join(ROOT, "assets", "demo", "candid-male", "skill-before-script.png")
SYSTEM32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")

# Names a quoted command-line argument must carry through unchanged, with the
# answers given for each: origin (C, E, G), light (D, I, N),
# then capture strength (0 = off, L = light, N = normal, S = strong).
TRICKY = [
    ("bang!name.png", "CD0"),
    ("with space.png", "GIL"),
    ("paren (1).png", "ENN"),
    ("amp & space.png", "GDS"),
    ("pct100%.png", "GDN"),
    ("caret^name.png", "GDN"),
    ("comma,name.png", "GDN"),
    ("semi;name.png", "GDN"),
    ("eq=name.png", "GDN"),
    ("caf\u00e9_\u65e5\u672c.png", "GDN"),
]
INJECT = "x&echo.INJECTED&.png"


def small_png(path):
    Image.new("RGB", (64, 48), (120, 110, 100)).save(path)


def write_bat(path, *lines):
    with open(path, "w", encoding="ascii", newline="\r\n") as f:
        f.write("\n".join(("@echo off",) + lines) + "\n")


@unittest.skipUnless(os.name == "nt", "realism.bat runs on Windows only")
class LauncherTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="sr-launcher-")
        kit = os.path.join(cls.tmp, "kit!a & b")  # the launcher's own folder has "!" and "&"
        os.makedirs(kit)
        shutil.copy(BAT, kit)
        shutil.copytree(SKILL, os.path.join(kit, "skill", "snapshot-realism"),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        cls.bat = os.path.join(kit, "realism.bat")
        # the same interpreter (with numpy and Pillow) must be the "python" the launcher finds
        cls.env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                       PATH=os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", ""))

        # Command-line use, every name quoted. /d skips any AutoRun command,
        # which could read the answers.
        cls.inp = os.path.join(cls.tmp, "in!x & y")
        os.makedirs(os.path.join(cls.inp, "sub dir"))
        for name, _ in TRICKY:
            small_png(os.path.join(cls.inp, name))
        shutil.copy(AI_PNG, os.path.join(cls.inp, "ai-marked.png"))
        small_png(os.path.join(cls.inp, "nolight.png"))
        args = [os.path.join(cls.inp, n) for n, _ in TRICKY] + [
            os.path.join(cls.inp, "ai-marked.png"),  # answered C, D, N: refused
            os.path.join(cls.inp, "missing.png"),  # skipped, no prompt
            os.path.join(cls.inp, "sub dir"),  # skipped, no prompt
            os.path.join(cls.inp, "nolight.png"),  # answered G, then the answers end: skipped
        ]
        answers = "".join(a for _, a in TRICKY) + "CDN" + "G"
        cmdline = 'cmd.exe /d /c ""%s" %s"' % (cls.bat, " ".join('"%s"' % a for a in args))
        cls.cli = cls.run_bat(cmdline, answers)

        # A drop, started the way Explorer starts it: no /d, and a name without
        # a space is not quoted. cmd may run an AutoRun command first, which can
        # read stdin, so this run needs no answers.
        cls.drop = os.path.join(cls.tmp, "drop!dir")
        os.makedirs(cls.drop)
        small_png(os.path.join(cls.drop, INJECT))
        comspec = os.environ.get("ComSpec", r"C:\WINDOWS\system32\cmd.exe")
        cls.dropped = cls.run_bat('%s /c ""%s" %s"' % (comspec, cls.bat, os.path.join(cls.drop, INJECT)), "")
        # A dropped name split at a comma: the piece "i.png" must not match the
        # unrelated i.png in the current folder (cwd is cls.tmp).
        small_png(os.path.join(cls.drop, "h,i.png"))
        small_png(os.path.join(cls.tmp, "i.png"))
        cls.split = cls.run_bat('%s /c ""%s" %s"' % (comspec, cls.bat, os.path.join(cls.drop, "h,i.png")), "")
        # The same, but the first piece "Trip" is an existing folder: the piece
        # "day1.png" after it must not match day1.png in the current folder.
        os.makedirs(os.path.join(cls.drop, "Trip"))
        small_png(os.path.join(cls.drop, "Trip,day1.png"))
        small_png(os.path.join(cls.tmp, "day1.png"))
        cls.split_folder = cls.run_bat('%s /c ""%s" %s"' % (comspec, cls.bat, os.path.join(cls.drop, "Trip,day1.png")), "")
        # Two whole dropped names are both full paths, so neither is a piece.
        # The drop gives no answers, so each ends at the first prompt.
        small_png(os.path.join(cls.drop, "one.png"))
        small_png(os.path.join(cls.drop, "two.png"))
        cls.two_drops = cls.run_bat('%s /c ""%s" %s %s"' % (comspec, cls.bat, os.path.join(cls.drop, "one.png"),
                                                            os.path.join(cls.drop, "two.png")), "")

        # Interpreter lookup, with PATH cut down to one folder plus System32.
        exe = sys.executable
        # pyenv-win style shim: running python.bat directly from a batch file
        # never returns, which ended the old launcher silently.
        shim = os.path.join(cls.tmp, "shim dir")
        os.makedirs(shim)
        write_bat(os.path.join(shim, "python.bat"), '"%s" %%*' % exe)
        small_png(os.path.join(cls.tmp, "shim.png"))
        cls.shim = cls.run_bat('cmd.exe /d /c ""%s" "shim.png""' % cls.bat, "GDN",
                               env=dict(cls.env, PATH=shim + os.pathsep + SYSTEM32))
        # No python, only the py launcher (a py.bat stand-in that insists on -3).
        pyonly = os.path.join(cls.tmp, "py only")
        os.makedirs(pyonly)
        write_bat(os.path.join(pyonly, "py.bat"),
                  'if not "%~1"=="-3" (echo py.bat: expected -3 1>&2) & exit /b 9',
                  '"%s" %%2 %%3 %%4 %%5 %%6 %%7 %%8 %%9' % exe)
        small_png(os.path.join(cls.tmp, "pyonly.png"))
        cls.py_only = cls.run_bat('cmd.exe /d /c ""%s" "pyonly.png""' % cls.bat, "GDN",
                                  env=dict(cls.env, PATH=pyonly + os.pathsep + SYSTEM32))
        # Only the Microsoft Store stub, which prints a message and exits 9009.
        stub = os.path.join(cls.tmp, "store stub")
        os.makedirs(stub)
        write_bat(os.path.join(stub, "python.cmd"),
                  "echo Python was not found; run without arguments to install from the Microsoft Store.",
                  "exit /b 9009")
        small_png(os.path.join(cls.tmp, "stub.png"))
        cls.no_python = cls.run_bat('cmd.exe /d /c ""%s" "stub.png""' % cls.bat, "GDN",
                                    env=dict(cls.env, PATH=stub + os.pathsep + SYSTEM32))

        cls.empty = cls.run_bat('cmd.exe /d /c ""%s""' % cls.bat, "")

        # A plain relative name (as typed in PowerShell or Git Bash) is processed;
        # only pieces right after a missing name count as a split.
        # A missing name before them must not make later quoted names look like split pieces.
        small_png(os.path.join(cls.tmp, "rel.png"))
        small_png(os.path.join(cls.tmp, "rel2.png"))
        cls.relative = cls.run_bat('cmd.exe /d /c ""%s" "missing.png" "rel.png" "rel2.png""' % cls.bat, "CDNCDN")

        # End-of-file at the third prompt must skip the image, not reuse the
        # previous image's capture strength or start Python with an empty value.
        small_png(os.path.join(cls.tmp, "prior-strength.png"))
        small_png(os.path.join(cls.tmp, "nostrength.png"))
        cls.no_strength = cls.run_bat('cmd.exe /d /c ""%s" "prior-strength.png" "nostrength.png""' % cls.bat,
                                     "GDSGD")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def run_bat(cls, cmdline, answers, env=None):
        ans = os.path.join(cls.tmp, "answers.txt")
        with open(ans, "wb") as f:
            f.write(answers.encode("ascii"))
        with open(ans, "rb") as stdin:
            p = subprocess.Popen(cmdline, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 cwd=cls.tmp, env=env or cls.env)
            try:
                out, _ = p.communicate(timeout=120)
            except subprocess.TimeoutExpired:
                subprocess.run("taskkill /T /F /PID %d" % p.pid, capture_output=True)
                p.communicate()
                raise AssertionError("realism.bat hung: %s" % cmdline)
        # cmd writes the OEM code page and Python the ANSI one; the checks only need ASCII
        return p.returncode, out.decode("latin-1")

    def wrote(self, out, name):
        """(preset, origin) from realism.py's 'wrote' line for this output name, or None."""
        m = re.search(re.escape(name) + r" \(\d+x\d+, preset (\w+), origin (\w+)\)", out)
        return m.groups() if m else None

    def test_cli_writes_every_tricky_name(self):
        for name, _ in TRICKY:
            stem = os.path.splitext(name)[0]
            for suffix in ("_real.jpg", "_real_compare.jpg"):
                self.assertTrue(os.path.isfile(os.path.join(self.inp, stem + suffix)), stem + suffix)

    def test_cli_answers_reach_the_script(self):
        code, out = self.cli
        self.assertEqual(self.wrote(out, "bang!name_real.jpg"), ("day", "camera"), out)
        self.assertEqual(self.wrote(out, "with space_real.jpg"), ("indoor", "generated"), out)
        self.assertEqual(self.wrote(out, "paren (1)_real.jpg"), ("night", "edited"), out)
        with Image.open(os.path.join(self.inp, "paren (1)_real.jpg")) as im:
            self.assertTrue(str(im.getexif().get(0x010E, "")).startswith("AI-edited image"))

    def test_cli_refuses_ai_marked_image_as_camera(self):
        code, out = self.cli
        self.assertIn("[refused]", out)
        self.assertIn("C2PA", out)
        self.assertFalse(os.path.exists(os.path.join(self.inp, "ai-marked_real.jpg")))

    def test_capture_strength_answers_reach_the_script(self):
        _, out = self.cli
        for name, expected in (("bang!name.png", 0), ("with space.png", 0.5),
                               ("paren (1).png", 1), ("amp & space.png", 2)):
            with self.subTest(name=name):
                start = out.index('File: "%s"' % os.path.join(self.inp, name))
                next_file = out.find('File: "', start + 1)
                section = out[start:next_file] if next_file != -1 else out[start:]
                match = re.search(r"capture strength:\s*([0-9.]+)", section)
                self.assertIsNotNone(match, section)
                self.assertEqual(float(match.group(1)), expected, section)

    def test_missing_capture_strength_answer_is_skipped(self):
        code, out = self.no_strength
        self.assertIn("[skip] No answer", out)
        self.assertIn("Done: 1 written, 0 refused, 0 failed, 1 skipped.", out)
        self.assertEqual(self.wrote(out, "prior-strength_real.jpg"), ("day", "generated"), out)
        self.assertIsNone(self.wrote(out, "nostrength_real.jpg"), out)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "nostrength_real.jpg")))
        self.assertEqual(code, 0, out)

    def test_cli_skips_folder_missing_file_and_unanswered_prompt(self):
        code, out = self.cli
        self.assertIn("[skip] Not found", out)
        self.assertIn("[skip] Folder, not an image", out)
        self.assertIn("[skip] No answer", out)  # end of answers: choice returns 255
        self.assertFalse(os.path.exists(os.path.join(self.inp, "nolight_real.jpg")))

    def test_cli_summary_exit_code_and_no_pause(self):
        code, out = self.cli
        self.assertIn("Done: 10 written, 1 refused, 0 failed, 3 skipped.", out)
        self.assertEqual(code, 1, out)  # a refusal makes the run fail
        self.assertNotIn("Press any key", out)

    def test_drop_stops_text_after_ampersand_and_pauses(self):
        code, out = self.dropped
        self.assertIn("[skip] Not found", out)  # x&echo.INJECTED&.png arrives as ...\x
        self.assertIn("Rename the file", out)
        self.assertNotIn("INJECTED", out)  # cmd did not run "echo.INJECTED" after the launcher
        self.assertIn("Done: 0 written, 0 refused, 0 failed, 1 skipped.", out)
        self.assertIn("Press any key", out)
        self.assertEqual(code, 0, out)

    def test_drop_skips_pieces_of_a_split_name(self):
        code, out = self.split
        self.assertIn('[skip] Part of a split name: "i.png"', out)
        self.assertIn("Done: 0 written, 0 refused, 0 failed, 2 skipped.", out)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "i_real.jpg")))

    def test_drop_skips_piece_after_a_folder_that_matches_the_first_piece(self):
        code, out = self.split_folder
        self.assertIn("[skip] Folder, not an image", out)
        self.assertIn('[skip] Part of a split name: "day1.png"', out)
        self.assertNotIn('File: "%s"' % os.path.join(self.tmp, "day1.png"), out)
        self.assertIn("Done: 0 written, 0 refused, 0 failed, 2 skipped.", out)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "day1_real.jpg")))

    def test_drop_of_two_whole_names_skips_no_piece(self):
        code, out = self.two_drops
        self.assertNotIn("Part of a split name", out)
        for name in ("one.png", "two.png"):
            self.assertIn('File: "%s"' % os.path.join(self.drop, name), out)
        self.assertEqual(out.count("[skip] No answer"), 2, out)
        self.assertIn("Done: 0 written, 0 refused, 0 failed, 2 skipped.", out)

    def test_python_bat_shim_does_not_end_the_launcher(self):
        code, out = self.shim
        self.assertEqual(self.wrote(out, "shim_real.jpg"), ("day", "generated"), out)
        self.assertIn("Done: 1 written, 0 refused, 0 failed, 0 skipped.", out)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "shim_real.jpg")))
        self.assertEqual(code, 0, out)

    def test_falls_back_to_py_launcher(self):
        code, out = self.py_only
        self.assertNotIn("expected -3", out)
        self.assertEqual(self.wrote(out, "pyonly_real.jpg"), ("day", "generated"), out)
        self.assertIn("Done: 1 written, 0 refused, 0 failed, 0 skipped.", out)
        self.assertEqual(code, 0, out)

    def test_store_stub_alone_reports_no_python(self):
        code, out = self.no_python
        self.assertIn("No working Python found", out)
        self.assertNotIn("Done:", out)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "stub_real.jpg")))
        self.assertEqual(code, 1, out)

    def test_relative_name_is_processed(self):
        code, out = self.relative
        self.assertIn("Done: 2 written, 0 refused, 0 failed, 1 skipped.", out)
        self.assertNotIn("Part of a split name", out)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "rel_real.jpg")))
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "rel2_real.jpg")))
        self.assertEqual(code, 0, out)

    def test_no_files_is_a_usage_error(self):
        code, out = self.empty
        self.assertIn("Drag one or more images", out)
        self.assertEqual(code, 2, out)


if __name__ == "__main__":
    unittest.main()
