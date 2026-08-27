#!/usr/bin/env python3
"""Before/after harness for the vv index (un-parked 2026-08-27).

Measures END-TO-END wall time (subprocess, warm cache) of the commands the
index targets, plus untouched controls. Emits one JSONL row per run to
~/.claude/metrics/vv-index-bench.jsonl with a phase label so before/after are
comparable rows of the same instrument. Fail-closed: any non-zero exit voids
the run (exit 1), no partial row is written.
"""
import json, os, statistics, subprocess, sys, time

import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import sweepguard as _sg
_sg.mark_bench("index-bench")   # tag this run's vv rows as benchmark traffic

VV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "vv.py")
SINK = os.path.join(os.path.expanduser(os.environ.get("VV_METRICS_DIR", "~/.claude/metrics")),
                    "vv-index-bench.jsonl")
REPS = 7
# The graph cases need a well-linked hub note and a term that actually occurs in
# the vault under test, so they are supplied by the environment rather than
# baked in. Fail-closed, like the rest of this harness: a case pointed at a note
# that does not exist would VOID the run anyway, so refuse up front instead.
HUB    = os.environ.get("VV_BENCH_HUB")     # a hub note with many backlinks
TERM   = os.environ.get("VV_BENCH_TERM")    # a term that occurs in several notes
FOLDER = os.environ.get("VV_BENCH_FOLDER", "Knowledge")
BOARD  = os.environ.get("VV_BENCH_BOARD",  "Work Items")
if not HUB or not TERM:
    sys.exit("set VV_BENCH_HUB (a well-linked note) and VV_BENCH_TERM "
             "(a term present in your vault) before running the index bench")

CASES = {
    "backlinks":  ["backlinks", HUB],
    "orphans":    ["orphans", FOLDER],
    "tags":       ["tags", "--counts"],
    "props":      ["props", "status", "Work Items"],
    "board":      ["board", BOARD, "status=in-progress"],
    "resolve":    ["resolve", HUB],
    "impact":     ["impact", HUB],
    # controls the index must NOT change:
    "search":     ["search", TERM, "--k", "5"],
    "outline":    ["outline", HUB],
}

def main():
    phase = sys.argv[1] if len(sys.argv) > 1 else "before"
    env = dict(os.environ, VV_NO_METRICS="1")
    out = {}
    for name, args in CASES.items():
        times = []
        for _ in range(REPS):
            t0 = time.perf_counter()
            r = subprocess.run([sys.executable, VV] + args,
                               capture_output=True, text=True, env=env)
            dt = (time.perf_counter() - t0) * 1000
            if r.returncode != 0:
                print(f"VOID: {name} exited {r.returncode}: {r.stderr[:120]}")
                return 1
            times.append(dt)
        times.sort()
        out[name] = {"median_ms": round(statistics.median(times), 1),
                     "min_ms": round(times[0], 1), "max_ms": round(times[-1], 1)}
        print(f"  {name:10} median {out[name]['median_ms']:7.1f} ms  "
              f"(min {out[name]['min_ms']}, max {out[name]['max_ms']})")
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "phase": phase,
           "reps": REPS, "cases": out}
    os.makedirs(os.path.dirname(SINK), exist_ok=True)
    with open(SINK, "a") as f:
        f.write(json.dumps(row) + "\n")
    print(f"\nphase={phase} written to {SINK}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
