#!/usr/bin/env python3
"""Shadow-pilot report over ~/.claude/metrics/vv.jsonl.

Answers the three pilot questions from real day-to-day usage (not bench fixtures):
  1. Is it used?         ops/day, distinct commands
  2. Is it cheaper?      out_bytes vs cf_bytes (what whole-file reads would cost)
  3. Does it get in the way?  error rate by kind (stale/dirty/not-found/ambiguous...)

Run:  python3 bench/pilot_report.py --since 2026-08-27 [--until YYYY-MM-DD]
"""
import argparse, collections, json, os, statistics

METRICS = os.path.expanduser("~/.claude/metrics/vv.jsonl")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True)
    ap.add_argument("--until", default="9999")
    a = ap.parse_args()
    rows = []
    with open(METRICS) as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if a.since <= r.get("ts", "")[:10] <= a.until:
                rows.append(r)
    if not rows:
        print(f"no vv ops logged in [{a.since}, {a.until}]"); return

    days = collections.Counter(r["ts"][:10] for r in rows)
    ops = collections.Counter(r["op"] for r in rows)
    errs = [r for r in rows if r.get("exit", 0) != 0]
    kinds = collections.Counter(r.get("kind", f"exit-{r['exit']}") for r in errs)
    out_b = sum(r.get("out_bytes", 0) for r in rows)
    cf_rows = [r for r in rows if r.get("cf_bytes")]
    cf_out = sum(r["out_bytes"] for r in cf_rows)
    cf_full = sum(r["cf_bytes"] for r in cf_rows)
    ms = [r["ms"] for r in rows if "ms" in r]

    print(f"window {a.since}..{min(a.until, max(days))} · {len(rows)} ops "
          f"over {len(days)} active days ({', '.join(f'{d}:{n}' for d, n in sorted(days.items()))})")
    print(f"latency: median {statistics.median(ms):.0f} ms · p90 "
          f"{sorted(ms)[int(len(ms) * 0.9)]:.0f} ms" if ms else "latency: n/a")
    print(f"context bill: {out_b:,} B emitted total")
    if cf_rows:
        pct = 100 * (1 - cf_out / cf_full) if cf_full else 0
        print(f"note-addressed ops ({len(cf_rows)}): {cf_out:,} B emitted vs "
              f"{cf_full:,} B whole-file counterfactual → {pct:.0f}% saved")
    er = 100 * len(errs) / len(rows)
    print(f"errors: {len(errs)}/{len(rows)} ({er:.1f}%)"
          + (" — " + ", ".join(f"{k}:{n}" for k, n in kinds.most_common()) if errs else ""))
    print("by command: " + ", ".join(f"{o}:{n}" for o, n in ops.most_common()))

if __name__ == "__main__":
    main()
