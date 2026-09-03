#!/usr/bin/env python3
"""graph.rs (agent A, 2026-08-27): backlinks/links/orphans/deadends MUST be
byte-identical (stdout+stderr+exit) to the Python implementation; `impact` is
left as a deliberate Fallback (see docs/rust-rewrite-plan.md agent-A notes)
and is exercised here only to confirm it still falls through cleanly.

Fixtures cover: duplicate basenames (same-folder winner vs shortest-path
winner vs lexicographic tie), a fenced/table-embedded link that must NOT
count, a percent-encoded markdown link, and a plain orphan/deadend pair.
"""
import os, subprocess, sys, tempfile, shutil

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VR = os.path.join(REPO, "vrust/target/release/vrust")
VV = os.path.join(REPO, "src/vv.py")

FIXTURES = {
    # duplicate basenames: root "Dup.md" vs "FolderA/Dup.md" vs "FolderB/Dup.md"
    "Dup.md": "# Root Dup\nshortest path candidate\n",
    "FolderA/Dup.md": "# A Dup\nsame-folder candidate for FolderA/Linker\n",
    "FolderB/Dup.md": "# B Dup\nanother dup\n",
    "FolderA/Linker.md": (
        "links to [[Dup]] bare (same-folder tier should win FolderA/Dup)\n"
        "and a fenced one that must not count:\n```\n[[Dup]]\n```\n"
        "and a table row | [[Dup]] | cell |\n"
    ),
    "RootLinker.md": "root-level bare link [[Dup]] — shortest-path tier (root Dup.md)\n",
    # percent-encoded markdown link + relative path resolution
    "Notes/Target Note.md": "# Target\nbody\n",
    "Notes/MdLinker.md": "see [Target](Target%20Note.md) and [[Target Note]]\n",
    # deadend: no outgoing links at all
    "NoLinks.md": "# Alone\njust text, no links here.\n",
    # orphan: nothing points at this one
    "Orphaned.md": "# Nobody links here\ntext\n",
    # unique basename bare link (trivial winner path)
    "Unique.md": "# Unique\nbody\n",
    "PointsAtUnique.md": "see [[Unique]]\n",
}

CASES = [
    ["backlinks", "Dup"],
    ["backlinks", "FolderA/Dup"],
    ["backlinks", "Unique"],
    ["backlinks", "Target Note"],
    ["links", "FolderA/Linker"],
    ["links", "Notes/MdLinker"],
    ["links", "NoLinks"],
    ["orphans"],
    ["orphans", "FolderA"],
    ["orphans", "Notes"],
    ["deadends"],
    ["impact", "Unique"],  # deliberate Fallback — must still match python exactly
]


def main():
    if not os.path.exists(VR):
        print("SKIP: vrust binary not built")
        return 0
    tv = tempfile.mkdtemp(prefix="vv-graph-")
    fails = []
    try:
        for name, content in FIXTURES.items():
            p = os.path.join(tv, name)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "w", newline="").write(content)
        env = dict(os.environ, VV_NO_METRICS="1", VV_VAULT=tv,
                   VV_INDEX_ROOT=tempfile.mkdtemp(prefix="vv-idx-"))   # both engines' caches stay out of ~/.cache
        n = 0
        for args in CASES:
            a = subprocess.run([VR] + args, capture_output=True, env=env)
            b = subprocess.run([sys.executable, VV] + args, capture_output=True, env=env)
            n += 1
            if (a.stdout, a.stderr, a.returncode) != (b.stdout, b.stderr, b.returncode):
                fails.append((args, a.stdout, b.stdout, a.returncode, b.returncode))
        # error paths: must fall through to python-canonical text
        for args in (["backlinks", "No Such Note Qq"], ["links", "definitely missing"]):
            a = subprocess.run([VR] + args, capture_output=True, env=env)
            b = subprocess.run([sys.executable, VV] + args, capture_output=True, env=env)
            n += 1
            if (a.stdout, a.stderr, a.returncode) != (b.stdout, b.stderr, b.returncode):
                fails.append(("errpath", args, a.stdout, b.stdout, a.returncode, b.returncode))
        if fails:
            for f in fails[:8]:
                print("FAIL", f)
            print(f"\n{len(fails)} of {n} DIVERGED")
            return 1
        print(f"ALL PASS (graph-parity: {n})")
        return 0
    finally:
        shutil.rmtree(tv, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
