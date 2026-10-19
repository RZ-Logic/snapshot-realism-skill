"""Build dist/snapshot-realism.zip for ChatGPT, claude.ai and API skill upload.

The zip holds the skill folder at its root (snapshot-realism/SKILL.md), which is
what skill uploaders expect, plus the MIT LICENSE as snapshot-realism/LICENSE.

The build is deterministic: entries are sorted, every entry gets the same date
(1980-01-01), permissions (0644) and compression, and text files are stored with
LF line endings whatever the checkout uses. Two builds of the same source give
the same zip.

Run:
  python tools/build_zip.py           write dist/snapshot-realism.zip
  python tools/build_zip.py --check   compare the committed zip with the source;
                                      exit 0 if they match, 1 if not
"""
import argparse
import io
import os
import re
import sys
import zipfile

NAME = "snapshot-realism"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "skill", NAME)
LICENSE = os.path.join(ROOT, "LICENSE")
DIST = os.path.join(ROOT, "dist", NAME + ".zip")
SKIP_DIRS = {"__pycache__", "node_modules", ".pytest_cache"}
SKIP_FILES = {".DS_Store", "Thumbs.db"}
# claude.ai rejects archive paths with other characters ("[wght]" in a font name was enough)
SAFE_PATH = re.compile(r"[A-Za-z0-9._/-]+")
TEXT_SUFFIXES = {".md", ".py", ".yaml", ".yml", ".txt", ".json", ".svg"}
DATE = (1980, 1, 1, 0, 0, 0)  # the earliest date a zip entry can hold
FILE_ATTR = (0o100644 & 0xFFFF) << 16  # regular file, rw-r--r--
LEVEL = 9


def skill_files():
    """(path relative to the skill folder, full path) for every file that ships, unsorted."""
    found = []
    for base, dirs, files in os.walk(SKILL):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f in SKIP_FILES or f.endswith((".pyc", ".pyo")):
                continue
            full = os.path.join(base, f)
            found.append((os.path.relpath(full, SKILL).replace(os.sep, "/"), full))
    return found


def unsafe_paths():
    """Paths inside the skill folder that skill uploaders would refuse."""
    return sorted(rel for rel, _ in skill_files() if not SAFE_PATH.fullmatch(rel))


def entries():
    """Sorted (archive name, bytes) pairs for the zip."""
    items = [(NAME + "/" + rel, full) for rel, full in skill_files()]
    items.append((NAME + "/LICENSE", LICENSE))
    out = []
    for arc, full in sorted(items):
        with open(full, "rb") as f:
            data = f.read()
        if arc.endswith("/LICENSE") or os.path.splitext(arc)[1].lower() in TEXT_SUFFIXES:
            data = data.replace(b"\r\n", b"\n")
        out.append((arc, data))
    return out


def build():
    """The zip as bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for arc, data in entries():
            info = zipfile.ZipInfo(arc, date_time=DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3  # Unix, so the permission bits mean the same on every OS
            info.external_attr = FILE_ATTR
            z.writestr(info, data, compresslevel=LEVEL)
    return buf.getvalue()


def differences(expected, actual):
    """What differs between two zips, entry by entry ([] = same names, order, data and metadata).

    Compressed bytes are not compared: zlib builds (zlib, zlib-ng) compress the
    same data differently, so CI on another OS would fail on identical content.
    """
    try:
        za = zipfile.ZipFile(io.BytesIO(expected))
        zb = zipfile.ZipFile(io.BytesIO(actual))
    except zipfile.BadZipFile as e:
        return ["not a readable zip: %s" % e]
    with za, zb:
        a, b = za.infolist(), zb.infolist()
        names_a, names_b = [i.filename for i in a], [i.filename for i in b]
        out = ["missing from the zip: " + n for n in names_a if n not in names_b]
        out += ["not in the source: " + n for n in names_b if n not in names_a]
        if not out and names_a != names_b:
            out.append("entries are in a different order")
        mine = {i.filename: i for i in b}
        for ia in a:
            ib = mine.get(ia.filename)
            if ib is None:
                continue
            if za.read(ia) != zb.read(ib):
                out.append("content differs: " + ia.filename)
            for field in ("date_time", "compress_type", "external_attr", "create_system"):
                if getattr(ia, field) != getattr(ib, field):
                    out.append("%s differs: %s (%r, zip has %r)"
                               % (field, ia.filename, getattr(ia, field), getattr(ib, field)))
    return out


def check(path=DIST):
    """[] if the zip at path matches a fresh build of the source, else what differs."""
    if not os.path.isfile(path):
        return ["%s not found; run python tools/build_zip.py" % path]
    with open(path, "rb") as f:
        actual = f.read()
    expected = build()
    if expected == actual:
        return []
    return differences(expected, actual)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build or check dist/%s.zip." % NAME)
    ap.add_argument("--check", action="store_true",
                    help="compare the existing zip with the source instead of writing it")
    args = ap.parse_args(argv)
    bad = unsafe_paths()
    if bad:
        print("refusing to build: these paths have characters skill uploaders reject (use A-Z a-z 0-9 . _ - /):")
        for b in bad:
            print("  " + b)
        return 1
    if args.check:
        diffs = check()
        if diffs:
            print("%s does not match the source; run python tools/build_zip.py" % DIST)
            for d in diffs:
                print("  " + d)
            return 1
        print("%s matches the source (%d files)" % (DIST, len(entries())))
        return 0
    data = build()
    os.makedirs(os.path.dirname(DIST), exist_ok=True)
    tmp = DIST + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, DIST)
    print("wrote %s (%d files)" % (DIST, len(entries())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
