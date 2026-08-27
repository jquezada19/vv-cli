#!/usr/bin/env python3
"""Phase 2 pins: the vvidx cache's freshness properties and native patch.

Freshness: an edit made AFTER the cache was built must be reflected on the very
next command (positive control for this lives in the session record: freezing
the stat-diff serves stale). Corruption: a garbage cache must not change any
answer and must self-heal. Patch: stdin ordering — a stale sha falls back
BEFORE stdin is consumed, so python's canonical exit-3 flow still works; the
happy path must produce byte-identical files.
"""
import os, subprocess, sys, tempfile, shutil, glob

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VR = os.path.join(REPO, "vrust/target/release/vrust")
VV = os.path.join(REPO, "src/vv.py")

def run(cmd, vault, stdin=None, py=False):
    env = dict(os.environ, VV_NO_METRICS="1", VV_VAULT=vault)
    if py:
        env["VV_NO_INDEX"] = "1"
        argv = [sys.executable, VV] + cmd
    else:
        argv = [VR] + cmd
    return subprocess.run(argv, capture_output=True, input=stdin, env=env)

def main():
    if not os.path.exists(VR):
        print("SKIP: binary not built"); return 0
    fails = []
    def check(lbl, ok, info=""):
        print(("PASS " if ok else "FAIL ") + lbl + ("" if ok else f"  [{str(info)[:140]}]"))
        if not ok: fails.append(lbl)

    tv = tempfile.mkdtemp(prefix="vv-p2-")
    open(f"{tv}/A.md", "w").write("---\ntype: note\n---\nlink to [[B]]\n")
    open(f"{tv}/B.md", "w").write("---\ntype: note\n---\nno links\n")
    # build cache, then edit immediately
    run(["backlinks", "B.md"], tv)
    open(f"{tv}/A.md", "w").write("---\ntype: note\n---\nlink to [[C]]\n")
    r = run(["backlinks", "B.md"], tv)
    check("P2a fresh edit reflected next command",
          b"(0 backlinks)" in r.stdout and b"A.md" not in r.stdout, r.stdout)
    # parity after deletion
    open(f"{tv}/C.md", "w").write("---\ntype: note\n---\nx\n")
    os.remove(f"{tv}/B.md")
    a = run(["deadends"], tv); b = run(["deadends"], tv, py=True)
    check("P2b deletion parity", a.stdout == b.stdout and a.returncode == b.returncode,
          a.stdout + b.stdout)
    # corruption self-heal
    caches = sorted(glob.glob(os.path.expanduser("~/.cache/vv/index/*.vvidx")),
                    key=os.path.getmtime)
    if caches:
        open(caches[-1], "w").write("garbage")
        a = run(["backlinks", "C.md"], tv); b = run(["backlinks", "C.md"], tv, py=True)
        check("P2c corrupt cache still answers correctly",
              a.stdout == b.stdout, a.stdout + b.stdout)
        check("P2d cache self-healed", open(caches[-1]).readline().startswith("vvidx"),
              open(caches[-1]).readline())
    # patch: happy + stale + CRLF, byte-compared against python on twin vaults
    tb = tempfile.mkdtemp(prefix="vv-p2b-")
    for name, content in (("N.md", "---\ntype: note\n---\n# H\nold body\n## Two\nx\n"),
                          ("C.md", "---\r\ntype: note\r\n---\r\n# H\r\nold\r\n")):
        open(f"{tv}/{name}", "w", newline="").write(content)
        open(f"{tb}/{name}", "w", newline="").write(content)
    for name in ("N.md", "C.md"):
        o = run(["outline", name], tv).stdout.decode()
        sha = [l.split("\t")[4] for l in o.splitlines() if l.startswith("H1\t")][0]
        a = run(["patch", name, "H1", sha], tv, stdin=b"fresh line\n")
        b = run(["patch", name, "H1", sha], tb, stdin=b"fresh line\n", py=True)
        check(f"P2e patch stdout parity {name}",
              a.stdout == b.stdout and a.returncode == b.returncode, a.stdout + b.stdout)
        check(f"P2f patch file bytes {name}",
              open(f"{tv}/{name}", "rb").read() == open(f"{tb}/{name}", "rb").read())
    a = run(["patch", "N.md", "H1", "deadbeef"], tv, stdin=b"x\n")
    b = run(["patch", "N.md", "H1", "deadbeef"], tb, stdin=b"x\n", py=True)
    check("P2g stale patch: exit 3 + stderr parity (stdin ordering)",
          a.returncode == 3 == b.returncode and a.stderr == b.stderr, a.stderr + b.stderr)
    check("P2h stale patch touched nothing",
          open(f"{tv}/N.md", "rb").read() == open(f"{tb}/N.md", "rb").read())
    shutil.rmtree(tv, ignore_errors=True); shutil.rmtree(tb, ignore_errors=True)
    if fails:
        print(f"\n{len(fails)} failures: {fails}"); return 1
    print("\nALL PASS (phase2: 10)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
