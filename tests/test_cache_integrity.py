#!/usr/bin/env python3
"""Pin: the vvidx cache must DETECT a torn write, not serve one.

The cache is validated per invocation by (mtime_ns, size, ino) equality against
the source files. That proves each surviving F row still describes its file — it
does NOT prove the row's own L rows survived. So a crash that leaves a
RECORD-ALIGNED PREFIX (valid header, every surviving row well-formed, one F row
whose trailing L rows were lost) passes every structural check and silently
serves missing links.

Demonstrated on the real vault 2026-08-27: truncating one record's L rows made
`backlinks` drop a link that python still found. That is why the cache carries
an integrity footer (body length + checksum) and why fsync could only be dropped
once the footer existed. Remove the footer check and case 1 must fail.
"""
import hashlib, os, shutil, subprocess, sys, tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VR = os.path.join(REPO, "vrust/target/release/vrust")
VV = os.path.join(REPO, "src/vv.py")

def cache_of(vault):
    key = hashlib.sha256(os.path.realpath(vault).encode()).hexdigest()[:16]
    return os.path.expanduser(f"~/.cache/vv/index/{key}.vvidx")

def run(cmd, vault, py=False):
    env = dict(os.environ, VV_NO_METRICS="1", VV_VAULT=vault)
    if py:
        env["VV_NO_INDEX"] = "1"
        return subprocess.run([sys.executable, VV] + cmd, capture_output=True, env=env)
    return subprocess.run([VR] + cmd, capture_output=True, env=env)

def main():
    if not os.path.exists(VR):
        print("SKIP: binary not built"); return 0
    fails, ran = [], []
    def check(lbl, ok, info=""):
        print(("PASS " if ok else "FAIL ") + lbl + ("" if ok else f"  [{str(info)[:200]}]"))
        ran.append(lbl)
        if not ok: fails.append(lbl)

    tv = tempfile.mkdtemp(prefix="vv-integ-")
    # many files so the cache has several records; one late record owns the links
    for i in range(40):
        open(f"{tv}/n{i:02d}.md", "w").write(f"# n{i:02d}\nfiller\n")
    open(f"{tv}/target.md", "w").write("# target\n")
    open(f"{tv}/zz-source.md", "w").write("a [[target]] link\nand [[n01]] too\n")
    cp = cache_of(tv)
    if os.path.exists(cp): os.remove(cp)

    want = run(["backlinks", "target.md"], tv).stdout
    check("baseline finds the backlink", b"zz-source.md" in want, want)

    # --- case 1: record-aligned prefix truncation (Codex's hole) -------------
    raw = open(cp).read().splitlines(True)
    cut = None
    for i, l in enumerate(raw):
        if l.startswith("F\t") and "zz-source" in l:
            cut = i + 1; break          # keep zz-source's F row, drop its L rows
    check("fixture built a truncatable record", cut is not None, "no zz-source F row")
    if cut:
        open(cp, "w").write("".join(raw[:cut]))
        got = run(["backlinks", "target.md"], tv).stdout
        check("torn prefix is rejected, not served", got == want, f"got={got!r} want={want!r}")

    # --- case 2: corrupt the LOAD-BEARING link row, footer left intact ------
    # (a byte flipped in an unrelated record would not change any answer, so it
    # cannot tell a working checksum from an absent one — aim at the link.)
    run(["backlinks", "target.md"], tv)          # heal
    raw2 = open(cp).read().splitlines(True)
    hit = next((i for i, l in enumerate(raw2)
                if l.startswith("L\t") and "zz-source" in l and l.rstrip().endswith("target")), None)
    check("fixture found the load-bearing link row", hit is not None, "no zz-source->target L row")
    if hit is not None:
        raw2[hit] = raw2[hit].rstrip("\n")[:-1] + "Z\n"   # target -> targeZ, same length
        open(cp, "w").write("".join(raw2))
        got = run(["backlinks", "target.md"], tv).stdout
        check("corrupted link row is rejected", got == want, f"got={got!r} want={want!r}")

    # --- case 3: native still agrees with python ----------------------------
    a = run(["backlinks", "target.md"], tv)
    b = run(["backlinks", "target.md"], tv, py=True)
    check("native/python parity after healing",
          (a.returncode, a.stdout) == (b.returncode, b.stdout), f"{a.stdout!r} vs {b.stdout!r}")

    shutil.rmtree(tv, ignore_errors=True)
    if os.path.exists(cp): os.remove(cp)
    print(("ALL PASS (cache integrity: %d)" % len(ran)) if not fails
          else "FAILURES: " + ", ".join(fails))
    return 1 if fails else 0

if __name__ == "__main__":
    sys.exit(main())
