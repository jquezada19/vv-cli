#!/usr/bin/env python3
"""Pin: pilot_report's corrected adoption, friction/protocol split, per-version
grouping, selector census, and sanitised op names.

Two defects motivated this file, both from real pilot review: the headline
adoption number counted every legacy-route row instead of only the ones that
were genuinely eligible vault work (the legacy hook was over-logging before it
started stamping `eligible`/`schema`), and a single flat error-rate number
hid the difference between "the tool got in the way" (friction: bad usage, a
ref that didn't resolve, an ambiguous match) and "the tool refused for a
reason the caller didn't cause" (protocol: a stale plan, a refused write, a
path escaping the vault, bad utf8 -- recognisable by exit code even when
`kind` is missing).

Standalone script, same shape as tests/test_*.py: its own check(), pass/FAIL
lines, exit 1 on any FAIL.
"""
import json, os, shlex, subprocess, sys, tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT = os.path.join(REPO, "bench/pilot_report.py")


def run_report(args, metrics_path, legacy_path):
    env = dict(os.environ, VV_METRICS_PATH=metrics_path, VV_LEGACY_PATH=legacy_path,
               PYTHONUTF8="1")
    return subprocess.run(
        [sys.executable, REPORT, *shlex.split(args)],
        env=env, capture_output=True, text=True).stdout


def main():
    fails = []
    checks_run = 0

    def check(lbl, ok, info=""):
        nonlocal checks_run
        checks_run += 1
        print(("PASS " if ok else "FAIL ") + lbl +
              ("" if ok else f"  [{str(info)[:400]!r}]"))
        if not ok:
            fails.append(lbl)

    vv_rows = [
        {"ts": "2026-09-20T10:00:00", "op": "read", "ms": 3, "out_bytes": 10,
         "exit": 0, "ver": "3.1.0", "engine": "native", "sel": "sha8"},
        {"ts": "2026-09-20T10:00:01", "op": "read", "ms": 3, "out_bytes": 10,
         "exit": 1, "kind": "usage", "ver": "3.0.0", "engine": "python"},
        {"ts": "2026-09-20T10:00:02", "op": "patch", "ms": 3, "out_bytes": 10,
         "exit": 3, "kind": "stale", "ver": "3.1.0", "engine": "python"},
        {"ts": "2026-09-20T10:00:03", "op": "board Link", "ms": 3, "out_bytes": 10,
         "exit": 1, "kind": "usage", "src": "bench"},
    ]
    legacy_rows = [
        {"ts": "2026-09-20T10:00:04", "route": "legacy", "op": "read", "tool": "Bash",
         "path_count": 0, "note_bytes": 0, "reason": "unknown", "argv0": "cat"},
        {"ts": "2026-09-20T10:00:05", "route": "legacy", "op": "read", "tool": "Read",
         "path_count": 1, "note_bytes": 500, "eligible": True, "reason": "unknown"},
    ]

    with tempfile.TemporaryDirectory(prefix="vv-pilot-report-") as tmp:
        metrics_path = os.path.join(tmp, "vv.jsonl")
        legacy_path = os.path.join(tmp, "vv-legacy.jsonl")
        with open(metrics_path, "w") as f:
            for r in vv_rows:
                f.write(json.dumps(r) + "\n")
        with open(legacy_path, "w") as f:
            for r in legacy_rows:
                f.write(json.dumps(r) + "\n")

        out = run_report("--since 2026-09-20 --until 2026-09-21", metrics_path, legacy_path)

        check("src rows excluded from headline", "3 ops" in out, out)
        check("adoption uses eligible legacy rows", "vv handled 3 of 4 eligible" in out, out)
        check("raw legacy shown secondary", "raw legacy rows: 2" in out, out)
        check("friction vs protocol split", "friction: 1" in out and "protocol: 1" in out, out)
        check("per-version block", "ver 3.1.0:" in out and "ver 3.0.0:" in out, out)
        check("op sanitised", "board Link" not in out and "board:" in out, out)
        check("sel census", "sel sha8: 1" in out, out)

        # --criteria must not crash and must print the pre-registered table.
        crit_out = run_report("--since 2026-09-20 --until 2026-09-21 --criteria",
                              metrics_path, legacy_path)
        check("criteria table prints", "| Metric | Baseline | Target | Measured |" in crit_out,
              crit_out)
        check("criteria table has read row", "read nonzero-exit rate" in crit_out, crit_out)

    # Regression pin: note-touching adoption must be computed over the SAME
    # ver>=3.1.0 cohort as the rest of the --criteria table -- a pre-3.1.0 vv
    # row must not pad a day's numerator, and a day with no ver>=3.1.0 vv
    # traffic at all must not be dragged in by legacy volume alone. Both bugs
    # would otherwise drop the printed value from the correct 50% to 29%.
    with tempfile.TemporaryDirectory(prefix="vv-pilot-report-ver-") as tmp:
        metrics_path = os.path.join(tmp, "vv.jsonl")
        legacy_path = os.path.join(tmp, "vv-legacy.jsonl")
        ver_rows = [
            # D1 2026-09-21: one ver>=3.1.0 row (the only one that should
            # count) plus three pre-3.1.0 rows that must NOT count.
            {"ts": "2026-09-21T09:00:00", "op": "read", "ms": 1, "out_bytes": 1,
             "exit": 0, "ver": "3.1.0"},
            {"ts": "2026-09-21T09:01:00", "op": "read", "ms": 1, "out_bytes": 1,
             "exit": 0, "ver": "3.0.0"},
            {"ts": "2026-09-21T09:02:00", "op": "read", "ms": 1, "out_bytes": 1,
             "exit": 0, "ver": "3.0.0"},
            {"ts": "2026-09-21T09:03:00", "op": "read", "ms": 1, "out_bytes": 1,
             "exit": 0, "ver": "3.0.0"},
            # D2 2026-09-22: pre-3.1.0 vv traffic only -- this day must be
            # excluded from the min entirely, not counted with 0 vv rows.
            {"ts": "2026-09-22T09:00:00", "op": "read", "ms": 1, "out_bytes": 1,
             "exit": 0, "ver": "3.0.0"},
            {"ts": "2026-09-22T09:01:00", "op": "read", "ms": 1, "out_bytes": 1,
             "exit": 0, "ver": "3.0.0"},
        ]
        ver_legacy = [
            {"ts": "2026-09-21T09:04:00", "op": "read", "tool": "Read",
             "note_bytes": 10, "eligible": True},
        ] + [
            {"ts": "2026-09-22T09:0%d:00" % (2 + i), "op": "read", "tool": "Read",
             "note_bytes": 10, "eligible": True}
            for i in range(5)
        ]
        with open(metrics_path, "w") as f:
            for r in ver_rows:
                f.write(json.dumps(r) + "\n")
        with open(legacy_path, "w") as f:
            for r in ver_legacy:
                f.write(json.dumps(r) + "\n")

        ver_crit_out = run_report("--since 2026-09-21 --until 2026-09-23 --criteria",
                                  metrics_path, legacy_path)
        check("note-touching adoption uses only the ver>=3.1.0 cohort",
              "note-touching adoption | baseline 80–99% | "
              "target ≥ 85% every active day | 50% |" in ver_crit_out
              and "29%" not in ver_crit_out,
              ver_crit_out)

    print(("ALL PASS (pilot report: %d)" % checks_run) if not fails
          else "FAILURES: " + ", ".join(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
