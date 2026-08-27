#!/usr/bin/env python3
"""Test-the-test: prove every sweep guard CAN fail, and does, on its own bug.

A guard that cannot fail is decoration, and this repo produced two of those in
one afternoon: a recall floor set at 50% (the real miss recovered 53% and sailed
through) and a "high-recall" baseline that under-counted the extractor it was
supposed to bound (making recall >100%, permanently green). Both were caught by
running them against the ACTUAL historical failure, not by review.

So each case below feeds a guard the real defect it exists to catch and asserts
it raises. If a guard is ever weakened, one of these goes red.
"""
import os, subprocess, sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "bench"))
import sweepguard as sg           # noqa: E402
import vvops                      # noqa: E402


def main():
    fails = []
    def check(lbl, ok, info=""):
        print(("PASS " if ok else "FAIL ") + lbl + ("" if ok else f"  [{str(info)[:180]}]"))
        if not ok: fails.append(lbl)

    def raises(fn):
        try:
            fn(); return False
        except sg.SweepError:
            return True

    # --- the real 2026-08-27 defects, replayed at the guard ------------------
    # class 1: extractor lost the native entry form -> 156 of 294 (53%)
    check("recall floor catches the REAL 53% partial miss",
          raises(lambda: sg.require_recall("t", 156, 294)))
    check("recall floor is not satisfied by a bare non-zero count",
          raises(lambda: sg.require_recall("t", 1, 294)))
    check("recall floor passes a healthy extractor", not raises(
          lambda: sg.require_recall("t", 290, 294)))

    # class 2: argv[0] != verb rejected 100% of candidates -> "0 operations"
    r = sg.RejectLog("t")
    for _ in range(50): r.reject("argv[0] != verb", "vv outline A.md")
    check("unanimous reject reason is an error, not a zero result",
          raises(r.require_not_unanimous))
    r2 = sg.RejectLog("t")
    for _ in range(50): r2.keep()
    for _ in range(3): r2.reject("genuinely unparseable", "x")
    check("a normal reject minority does NOT trip it", not raises(r2.require_not_unanimous))

    # class 3: relative paths + a cwd reset -> 0 files, 3 greps all "0 matches"
    check("relative corpus paths abort before the sweep",
          raises(lambda: sg.preflight_corpus("t", ["rel/x.jsonl"])))
    check("empty corpus aborts", raises(lambda: sg.preflight_corpus("t", [])))
    check("absolute readable corpus passes", not raises(
          lambda: sg.preflight_corpus("t", [os.path.join(REPO, "README.md")])))

    # funnel attribution
    f = sg.Funnel("t", "a", "b")
    f.bump("a", 10)
    check("funnel floor names the dead stage", raises(lambda: f.require("b")))
    check("funnel rejects an unknown stage", raises(lambda: f.bump("nope")))

    # --- the extractor canary itself ----------------------------------------
    check("vvops canary passes as shipped", vvops.self_test() > 0)

    # mutation: reintroduce the historical bug in a SUBPROCESS (module-level
    # regex, so it must not be mutated in-process for other tests)
    mutant = os.path.join(REPO, "bench", "_mutant_vvops.py")
    src = open(os.path.join(REPO, "bench", "vvops.py")).read()
    broken = src.replace('r"""(?:(?P<py>vv\\.py)["\']?|(?<![\\w./=-])(?P<nat>vv))"""',
                         'r"""(?:(?P<py>vv\\.py)["\']?)"""')
    check("mutation actually changed the source", broken != src)
    open(mutant, "w").write(broken)
    try:
        r = subprocess.run([sys.executable, "-c",
                            "import sys; sys.path.insert(0,'bench'); "
                            "import _mutant_vvops as m; m.self_test()"],
                           capture_output=True, text=True, cwd=REPO)
        check("canary FAILS when the native entry form is dropped (the real bug)",
              r.returncode != 0 and "canary" in (r.stderr + r.stdout).lower(),
              r.stderr[-200:])
    finally:
        if os.path.exists(mutant): os.remove(mutant)

    # --- defects found by an adversarial review OF THESE GUARDS -------------
    # 0/0 is not agreement, it is two broken things agreeing
    check("recall guard rejects 0/0 instead of trusting it",
          raises(lambda: sg.require_recall("t", 0, 0)))
    check("recall guard still tolerates an under-counting baseline when work was done",
          not raises(lambda: sg.require_recall("t", 12, 0)))
    # kept==0 slipped through when rejects were few, or split across reasons
    r3 = sg.RejectLog("t")
    for _ in range(10): r3.reject("A", "x")
    for _ in range(10): r3.reject("B", "y")
    check("kept==0 aborts even when reject reasons are split",
          raises(r3.require_not_unanimous))
    r4 = sg.RejectLog("t")
    for _ in range(19): r4.reject("A", "x")
    check("kept==0 aborts below the min_candidates threshold too",
          raises(r4.require_not_unanimous))

    # the baseline must dominate the strict extractor or the differential is fake
    bad = [c for c, _ in vvops.CANARY_CASES
           if len(vvops.LOOSE_RE.findall(c)) < len(vvops.parse_invocations(c))]
    check("loose baseline over-approximates on every canary case", not bad, bad[:3])

    # the verb list is DERIVED, not hand-copied (it silently drifted 3 verbs)
    check("verb list derived from src/vv_impl.py", len(vvops.SOURCE_VERBS) >= 20,
          sorted(vvops.SOURCE_VERBS))
    check("lint/index/doctor are recognised (they were silently dropped)",
          {"lint", "index", "doctor"} <= vvops.SOURCE_VERBS)

    print(("ALL PASS (sweepguard: %d)" % (21 - len(fails))) if not fails
          else "FAILURES: " + ", ".join(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
