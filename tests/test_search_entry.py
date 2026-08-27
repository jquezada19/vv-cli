#!/usr/bin/env python3
"""Pin: the zero-hit phrase hint survives on BOTH entry points, exactly once.

`vv search "a b"` passes ONE quoted phrase; `vv search a b` ANDs two terms. Both
print "(0 of 0 matches)" on failure, so a shell-quoting slip is indistinguishable
from a real true negative. The phrase hint exists to break that tie.

Two regressions this pins, both found 2026-08-27 by replaying real sessions:

1. The hint lived only in python. When the native binary became the DEFAULT
   entry it answered `search` itself and printed a bare "(0 of 0 matches)" —
   silently un-shipping the fix on the path everyone actually uses.
2. The naive repair (native hands off to python on zero hits) recursed forever,
   because python's own `search` shells straight back to the native engine.
   A 2-minute hang. The handoff therefore forces python's in-process scanner,
   and python tells the engine (VV_FROM_PY) not to hand off at all — without
   that flag the hint prints TWICE.

So the invariant is: exactly one hint, from either entry, and it must terminate.
"""
import os, subprocess, sys, tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VR = os.path.join(REPO, "vrust/target/release/vrust")
VV = os.path.join(REPO, "src/vv.py")

def main():
    if not os.path.exists(VR):
        print("SKIP: binary not built"); return 0
    fails = []
    def check(lbl, ok, info=""):
        print(("PASS " if ok else "FAIL ") + lbl + ("" if ok else f"  [{str(info)[:200]}]"))
        if not ok: fails.append(lbl)

    tv = tempfile.mkdtemp(prefix="vv-searchentry-")
    # both words present, never adjacent -> the PHRASE misses, the split terms hit
    open(f"{tv}/one.md", "w").write("alpha appears here\nand beta appears later\n")
    open(f"{tv}/two.md", "w").write("beta first\nthen alpha\n")

    def run(argv, engine=None, timeout=60):
        env = dict(os.environ, VV_NO_METRICS="1", VV_VAULT=tv)
        if engine: env["VV_ENGINE"] = engine
        try:
            r = subprocess.run(argv, capture_output=True, text=True, env=env,
                               cwd=tv, timeout=timeout)
            return r.returncode, r.stdout
        except subprocess.TimeoutExpired:
            return "TIMEOUT", ""

    PHRASE = ["search", "alpha beta"]
    nat = run([VR] + PHRASE)
    pye = run([sys.executable, VV] + PHRASE)
    pure = run([sys.executable, VV] + PHRASE, engine="python")

    check("native entry terminates (no handoff recursion)", nat[0] != "TIMEOUT", nat)
    check("python entry terminates", pye[0] != "TIMEOUT", pye)
    for lbl, res in (("native", nat), ("python", pye), ("python-pure", pure)):
        if res[0] == "TIMEOUT": continue
        check(f"{lbl}: zero-hit phrase emits the hint",
              "matched as ONE phrase" in res[1], res[1])
        check(f"{lbl}: hint appears exactly once",
              res[1].count("matched as ONE phrase") == 1, res[1])
    check("native == python entry, byte-for-byte", nat == pye, f"{nat} vs {pye}")

    # a genuine true negative must NOT get a hint (nothing to suggest)
    tn = run([VR, "search", "zzz nothingmatcheszzz"])
    check("true negative gets no hint",
          tn[0] != "TIMEOUT" and "matched as ONE phrase" not in tn[1], tn)
    # a single-word query cannot have the phrase/AND ambiguity at all
    sw = run([VR, "search", "nothingmatcheszzz"])
    check("single-word miss gets no hint",
          sw[0] != "TIMEOUT" and "matched as ONE phrase" not in sw[1], sw)

    print(("ALL PASS (search entry: %d)" % (12 - len(fails))) if not fails
          else "FAILURES: " + ", ".join(fails))
    return 1 if fails else 0

if __name__ == "__main__":
    sys.exit(main())
