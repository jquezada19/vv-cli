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

VV = os.path.expanduser("~/Desktop/Git/vv-cli/src/vv.py")
SINK = os.path.expanduser("~/.claude/metrics/vv-index-bench.jsonl")
REPS = 7
CASES = {
    "backlinks":  ["backlinks", "Avigilon MOC"],
    "orphans":    ["orphans", "Knowledge"],
    "tags":       ["tags", "--counts"],
    "props":      ["props", "status", "Work Items"],
    "board":      ["board", "Work Items/In Flight", "status=in-progress"],
    "resolve":    ["resolve", "Avigilon MOC"],
    "impact":     ["impact", "Avigilon MOC"],
    # controls the index must NOT change:
    "search":     ["search", "avigilon", "--k", "5"],
    "outline":    ["outline", "Avigilon MOC"],
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
