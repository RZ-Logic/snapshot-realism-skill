"""Print the CHANGELOG.md notes for the version in realism.py, for a GitHub release.

The release workflow (.github/workflows/release.yml) takes the tag name and the
release notes from here, so a release always carries the version the script
writes into its output metadata and the changelog section written for it.

Run:
  python tools/release_notes.py             print the text under the '## <version>' heading;
                                            exit 1 if there is no such heading or it is empty
  python tools/release_notes.py --version   print the version only, e.g. 0.6.1
"""
import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "skill", "snapshot-realism", "scripts", "realism.py")
CHANGELOG = os.path.join(ROOT, "CHANGELOG.md")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def version():
    """__version__ from realism.py."""
    return re.search(r'^__version__ = "([^"]+)"', read(SCRIPT), re.M).group(1)


def notes(text, ver):
    """The text under the '## <ver>' heading of a changelog, up to the next '## ' heading; None if missing or empty."""
    heading = re.search(r"^##\s+\[?v?%s\]?(?=\s|$).*$" % re.escape(ver), text, re.M)
    if heading is None:
        return None
    rest = text[heading.end():]
    following = re.search(r"^## ", rest, re.M)
    body = rest[:following.start()] if following else rest
    return body.strip() or None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Print the CHANGELOG.md notes for the version in realism.py.")
    ap.add_argument("--version", action="store_true", help="print the version only")
    args = ap.parse_args(argv)
    ver = version()
    if args.version:
        print(ver)
        return 0
    body = notes(read(CHANGELOG), ver)
    if body is None:
        print("CHANGELOG.md has no notes under a '## %s' heading" % ver, file=sys.stderr)
        return 1
    print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
