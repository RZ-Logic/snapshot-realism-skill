"""Packaging checks: the committed zip, SKILL.md frontmatter, versions, links, line endings.

Run: python -m unittest discover -s tests
"""
import importlib.util
import io
import os
import re
import unittest
import zipfile
from urllib.parse import unquote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_DIR = os.path.join(ROOT, "skill", "snapshot-realism")
SKIP_DIRS = {".git", "dist", "__pycache__", "node_modules", ".pytest_cache", ".venv", "venv"}
EM_DASH = chr(0x2014)


def load_build_zip():
    spec = importlib.util.spec_from_file_location("build_zip", os.path.join(ROOT, "tools", "build_zip.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_zip = load_build_zip()


def read_text(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def unquote_yaml(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def frontmatter(text):
    """Top-level keys of a YAML frontmatter block, one level of nesting, plain and block scalars."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("no frontmatter: the file must start with ---")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise ValueError("frontmatter is not closed with ---")
    data, parent, i = {}, None, 1
    while i < end:
        line = lines[i]
        i += 1
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        key, _, value = line.strip().partition(":")
        value = value.strip()
        if indent == 0:
            parent, target = key, data
        else:
            target = data.setdefault(parent, {})
            if not isinstance(target, dict):
                raise ValueError("unexpected indented line under %r: %r" % (parent, line))
        if value[:1] in (">", "|"):
            block = []
            while i < end and (not lines[i].strip() or len(lines[i]) - len(lines[i].lstrip(" ")) > indent):
                block.append(lines[i].strip())
                i += 1
            target[key] = (" " if value[0] == ">" else "\n").join(block).strip()
        elif value == "" and indent == 0:
            data[key] = {}
        else:
            target[key] = unquote_yaml(value)
    return data


def repo_files(suffixes):
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if os.path.splitext(f)[1].lower() in suffixes:
                yield os.path.join(base, f)


class CommittedZip(unittest.TestCase):
    def test_committed_zip_matches_the_source(self):
        diffs = build_zip.check()
        self.assertEqual(diffs, [], "dist/snapshot-realism.zip is stale; run python tools/build_zip.py\n"
                         + "\n".join(diffs))

    def test_build_is_deterministic(self):
        self.assertEqual(build_zip.build(), build_zip.build())

    def test_zip_members(self):
        with zipfile.ZipFile(io.BytesIO(build_zip.build())) as z:
            names = z.namelist()
            self.assertEqual(sorted(names), names)
            self.assertEqual(set(names), {
                "snapshot-realism/SKILL.md",
                "snapshot-realism/agents/openai.yaml",
                "snapshot-realism/references/prompt-recipe.md",
                "snapshot-realism/scripts/realism.py",
                "snapshot-realism/scripts/make_cert.py",
                "snapshot-realism/LICENSE",
            })
            self.assertFalse([n for n in names if n.endswith((".pyc", ".pyo")) or "__pycache__" in n])
            with open(os.path.join(ROOT, "LICENSE"), "rb") as f:
                self.assertEqual(z.read("snapshot-realism/LICENSE"), f.read().replace(b"\r\n", b"\n"))

    def test_skill_paths_are_safe_for_uploaders(self):
        self.assertEqual(build_zip.unsafe_paths(), [])


class SkillMetadata(unittest.TestCase):
    def setUp(self):
        self.meta = frontmatter(read_text("skill", "snapshot-realism", "SKILL.md"))

    def test_name(self):
        name = self.meta.get("name")
        self.assertEqual(name, "snapshot-realism")
        self.assertRegex(name, r"^[a-z0-9]+(-[a-z0-9]+)*$")
        self.assertLessEqual(len(name), 64)
        self.assertEqual(name, os.path.basename(SKILL_DIR))

    def test_description(self):
        description = self.meta.get("description", "")
        self.assertTrue(description, "description is empty")
        self.assertLessEqual(len(description), 1024, "description is %d chars" % len(description))

    def test_license_and_version(self):
        self.assertEqual(self.meta.get("license"), "MIT")
        self.assertIsInstance(self.meta.get("metadata"), dict)
        self.assertRegex(self.meta["metadata"].get("version", ""), r"^\d+\.\d+\.\d+$")


class Consistency(unittest.TestCase):
    def test_version_agrees_everywhere(self):
        script = re.search(r'^__version__ = "([^"]+)"', read_text("skill", "snapshot-realism", "scripts",
                                                                   "realism.py"), re.M)
        badge = re.search(r"badge/Version-([0-9][0-9A-Za-z.]*)-", read_text("README.md"))
        changelog = re.search(r"^##\s+\[?v?(\d+\.\d+\.\d+)", read_text("CHANGELOG.md"), re.M)
        self.assertIsNotNone(script, "no __version__ in realism.py")
        self.assertIsNotNone(badge, "no Version badge in README.md")
        self.assertIsNotNone(changelog, "no '## x.y.z' heading in CHANGELOG.md")
        found = {
            "realism.py __version__": script.group(1),
            "SKILL.md metadata.version": frontmatter(read_text("skill", "snapshot-realism", "SKILL.md"))
            .get("metadata", {}).get("version"),
            "README.md Version badge": badge.group(1),
            "CHANGELOG.md first heading": changelog.group(1),
        }
        self.assertEqual(len(set(found.values())), 1, "versions disagree: %r" % found)

    def test_ci_floor_job_matches_requirements(self):
        req = read_text("requirements.txt")
        ci = read_text(".github", "workflows", "tests.yml")
        for package in ("numpy", "pillow"):
            floor = re.search(r"^%s>=([\d.]+)" % package, req, re.M | re.I)
            self.assertIsNotNone(floor, "no %s>= line in requirements.txt" % package)
            self.assertRegex(ci, r"(?i)\b%s==%s\b" % (package, re.escape(floor.group(1))),
                             "tests.yml floor job does not pin %s==%s" % (package, floor.group(1)))

    def test_word_lists_match_between_recipe_and_chatgpt_text(self):
        """The two copies of the word lists stay identical.

        Maintainer note: skill/snapshot-realism/references/prompt-recipe.md is the canonical copy of the
        word lists and the quality gates. chatgpt/project-instructions.md repeats the lists (the README
        describes them), and this test keeps both tiers identical: the "drop" list of pure polish
        words and the "translate by intent" list of words that can carry a real requirement.
        """
        recipe = " ".join(read_text("skill", "snapshot-realism", "references", "prompt-recipe.md").split())
        chatgpt = " ".join(read_text("chatgpt", "project-instructions.md").split())
        for label, pattern in (("drop", r"say so: (.+?)\."),
                               ("translate", r"Translate these by intent \(do not just delete\): (.+?)\.")):
            in_recipe = re.search(pattern, recipe)
            in_chatgpt = re.search(pattern, chatgpt)
            self.assertIsNotNone(in_recipe, "no %s list in prompt-recipe.md" % label)
            self.assertIsNotNone(in_chatgpt, "no %s list in project-instructions.md" % label)
            self.assertEqual(in_recipe.group(1), in_chatgpt.group(1),
                             "the %s list differs between the recipe and the ChatGPT text" % label)
        drop = set(re.search(r"say so: (.+?)\.", recipe).group(1).split(", "))
        translate = set(re.search(r"Translate these by intent \(do not just delete\): (.+?)\.", recipe)
                        .group(1).split(", "))
        self.assertEqual(drop & translate, set(), "a word is in both tiers")
        self.assertGreaterEqual(len(drop), 10)
        self.assertGreaterEqual(len(translate), 10)
        for word in translate:  # every translated word has guidance in the recipe's table
            self.assertRegex(recipe, r"\| [^|]*\b%s\b[^|]* \|" % re.escape(word), "no guidance for %r" % word)

    def test_chatgpt_instructions_fit_the_project_limit(self):
        self.assertLess(len(read_text("chatgpt", "project-instructions.md")), 7900)

    def test_skill_md_stays_short_and_declares_compatibility(self):
        text = read_text("skill", "snapshot-realism", "SKILL.md")
        self.assertLess(len(text.splitlines()), 260)
        self.assertIn("compatibility", frontmatter(text))

    def test_readme_carries_the_measure_tables_for_this_version(self):
        version = re.search(r'^__version__ = "([^"]+)"', read_text("skill", "snapshot-realism", "scripts",
                                                                    "realism.py"), re.M).group(1)
        text = read_text("README.md")
        for marker in ("<!-- measure:start -->", "<!-- measure:end -->"):
            self.assertEqual(text.count(marker), 1, marker)
        block = text.split("<!-- measure:start -->", 1)[1].split("<!-- measure:end -->", 1)[0]
        self.assertIn("`realism.py %s`" % version, block, "rerun python tools/measure.py --readme")
        rows = [line for line in block.splitlines() if line.startswith("| ") and not line.startswith("| Preset")
                and not line.startswith("| Input")]
        self.assertEqual(len(rows), 12 + 3, "3 presets x 4 strengths, then the 3 demo photos")
        # the numbers are current: a fresh run matches them (same text, numbers within rounding)
        spec = importlib.util.spec_from_file_location("measure", os.path.join(ROOT, "tools", "measure.py"))
        measure = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(measure)
        fresh = measure.report(measure.load(measure.SCRIPT)).splitlines()[1:]  # line 1 names the numpy version
        shown = block.strip().splitlines()[1:]
        self.assertEqual(len(shown), len(fresh), "rerun python tools/measure.py --readme")
        number = re.compile(r"[-+]?\d+(?:\.\d+)?")
        for old, new in zip(shown, fresh):
            self.assertEqual(number.sub("#", old), number.sub("#", new), "rerun python tools/measure.py --readme")
            for a, b in zip(number.findall(old), number.findall(new)):
                self.assertLessEqual(abs(float(a) - float(b)), 0.02 + 0.01 * abs(float(b)),
                                     "%s in README, %s now: rerun python tools/measure.py --readme\n%s" % (a, b, old))

    def test_script_version_is_the_one_documented(self):
        version = re.search(r'^__version__ = "([^"]+)"', read_text("skill", "snapshot-realism", "scripts",
                                                                    "realism.py"), re.M).group(1)
        for parts in (("skill", "snapshot-realism", "SKILL.md"), ("chatgpt", "project-instructions.md"),
                      ("README.md",)):
            for found in re.findall(r"snapshot-realism (\d+\.\d+\.\d+)", read_text(*parts)):
                self.assertEqual(found, version, "/".join(parts))


class TextFiles(unittest.TestCase):
    def test_no_em_dashes(self):
        hits = []
        for path in repo_files({".md", ".py", ".bat", ".yaml", ".yml", ".svg"}):
            with open(path, encoding="utf-8", errors="replace") as f:
                for n, line in enumerate(f, 1):
                    if EM_DASH in line:
                        hits.append("%s:%d" % (os.path.relpath(path, ROOT), n))
        self.assertEqual(hits, [], "em dashes found (use a colon, comma, period or semicolon)")

    def test_bat_has_crlf_line_endings(self):
        with open(os.path.join(ROOT, "realism.bat"), "rb") as f:
            data = f.read()
        self.assertIn(b"\n", data)
        self.assertEqual(data.count(b"\n"), data.count(b"\r\n"),
                         "realism.bat needs CRLF line endings; cmd.exe misreads LF-only batch files")

    def test_readme_relative_links_exist(self):
        text = read_text("README.md")
        text = re.sub(r"(?ms)^\s*(```|~~~).*?^\s*\1", "", text)  # fenced code
        text = re.sub(r"`[^`\n]*`", "", text)  # inline code
        targets = re.findall(r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+[\"'][^)]*[\"'])?\s*\)", text)
        targets += re.findall(r"\b(?:href|src)\s*=\s*[\"']([^\"']+)[\"']", text, re.I)
        targets += re.findall(r"(?m)^\s*\[[^\]]+\]:\s*<?(\S+?)>?(?:\s|$)", text)  # reference definitions
        missing = []
        for target in targets:
            if re.match(r"(?i)^([a-z][a-z0-9+.-]*:|//|#)", target):
                continue  # absolute URL, mailto: or in-page anchor
            rel = unquote(target.split("#", 1)[0].split("?", 1)[0])
            if rel and not os.path.exists(os.path.join(ROOT, *rel.split("/"))):
                missing.append(target)
        self.assertTrue(targets, "no links found in README.md")
        self.assertEqual(missing, [], "README.md links to files that do not exist")


if __name__ == "__main__":
    unittest.main()
