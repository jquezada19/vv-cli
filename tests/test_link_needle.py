#!/usr/bin/env python3
"""Pin: the wikilink needle filter must NEVER be applied to markdown links.

backlinks() prefilters wikilinks by `target.lower().contains(basename)` — a
sound optimization for [[...]], because a wikilink names its target. A markdown
link does not: [text](My%20Spaced%20Note.md) resolves by PATH, and its raw
target contains no substring "my spaced note". Applying the needle to markdown
links silently drops those backlinks.

Positive control history (2026-08-27): a 120-note sweep of the real vault and a
same-basename fixture BOTH passed against a deliberately broken build that
dropped markdown links from the candidate set — neither exercised a target that
resolves without containing the basename. Only URL-encoding separates them.
Every case below must FAIL on a build that needles markdown links.
"""
import os, subprocess, sys, tempfile, atexit, shutil
def _idx_root():   # a throwaway index root for both engines, removed at exit
    d = tempfile.mkdtemp(prefix="vv-idx-"); atexit.register(shutil.rmtree, d, True); return d

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VR = os.path.join(REPO, "vrust/target/release/vrust")
VV = os.path.join(REPO, "src/vv.py")

def run(cmd, vault, py=False):
    env = dict(os.environ, VV_NO_METRICS="1", VV_VAULT=vault,
               VV_INDEX_ROOT=_idx_root())   # both engines' caches stay out of ~/.cache
    argv = [sys.executable, VV] + cmd if py else [VR] + cmd
    if py:
        env["VV_NO_INDEX"] = "1"
    return subprocess.run(argv, capture_output=True, env=env)

def main():
    if not os.path.exists(VR):
        print("SKIP: binary not built"); return 0
    fails = []
    def check(lbl, ok, info=""):
        print(("PASS " if ok else "FAIL ") + lbl + ("" if ok else f"  [{str(info)[:200]}]"))
        if not ok: fails.append(lbl)

    tv = tempfile.mkdtemp(prefix="vv-needle-")
    os.makedirs(f"{tv}/sub", exist_ok=True)
    # target whose basename never appears literally in an encoded link target
    open(f"{tv}/My Spaced Note.md", "w").write("# My Spaced Note\nbody\n")
    open(f"{tv}/enc.md", "w").write("see [spaced](My%20Spaced%20Note.md)\n")
    # relative markdown link out of a subfolder, encoded
    open(f"{tv}/sub/rel.md", "w").write("see [up](../My%20Spaced%20Note.md)\n")
    # a wikilink to the same note, to prove the needle path still works
    open(f"{tv}/wiki.md", "w").write("see [[My Spaced Note]]\n")
    # a decoy markdown link that must NOT match
    open(f"{tv}/decoy.md", "w").write("see [other](Some%20Other%20Note.md)\n")

    expect = {"enc.md", "sub/rel.md", "wiki.md"}
    for engine, py in (("native", False), ("python", True)):
        r = run(["backlinks", "My Spaced Note.md"], tv, py=py)
        got = {l for l in r.stdout.decode().splitlines() if l and not l.startswith("(")}
        check(f"{engine}: encoded markdown links are not needle-filtered",
              got == expect, f"got={sorted(got)} want={sorted(expect)}")

    # native and python must agree exactly (bytes)
    a = run(["backlinks", "My Spaced Note.md"], tv)
    b = run(["backlinks", "My Spaced Note.md"], tv, py=True)
    check("native/python byte parity on encoded md links",
          (a.returncode, a.stdout) == (b.returncode, b.stdout),
          f"{a.stdout[:120]!r} vs {b.stdout[:120]!r}")

    print(("ALL PASS (link needle: %d)" % (3 - len(fails))) if not fails
          else "FAILURES: " + ", ".join(fails))
    return 1 if fails else 0

if __name__ == "__main__":
    sys.exit(main())
