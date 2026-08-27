#!/usr/bin/env python3
"""Shadow-pilot report over ~/.claude/metrics/vv.jsonl.

Answers the pilot questions from real day-to-day usage (not bench fixtures):
  1. Is it used?         ops/day, distinct commands -- AND share of eligible
                         vault work that went to vv vs the legacy route
  2. Is it cheaper?      out_bytes vs cf_bytes (MODELLED, see caveat below)
  3. Does it get in the way?  error rate by kind (stale/dirty/not-found/ambiguous...)

Two sinks:
  ~/.claude/metrics/vv.jsonl         -- vv's own ops (self-logged)
  ~/.claude/metrics/vv-legacy.jsonl  -- vault ops that took the OLD route,
                                        captured by the vv-pilot-legacy-logger
                                        hook. Without it the adoption number has
                                        a numerator and no denominator.

CAVEAT on cf_bytes (Codex review 2026-08-26): it is the size of the notes an op
resolved, i.e. a MODEL of what a naive whole-file read would have cost -- not an
observed measurement of the old way. It is deduped per invocation, but a chain
of separate invocations over one note still bills it once each, and for `search`
it is meaningless (grep is a different retrieval strategy, not a whole-file
read). Report it as a workload figure; the honest savings number comes from
paired read-only tasks recorded by hand in the pilot todo.

Run:  python3 bench/pilot_report.py --since 2026-08-27 [--until YYYY-MM-DD]
"""
import argparse, collections, json, os, statistics

METRICS = os.path.expanduser("~/.claude/metrics/vv.jsonl")
LEGACY = os.path.expanduser("~/.claude/metrics/vv-legacy.jsonl")


def load(path, since, until):
    rows = []
    try:
        with open(path) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if since <= r.get("ts", "")[:10] <= until:
                    rows.append(r)
    except OSError:
        pass
    return rows

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True)
    ap.add_argument("--until", default="9999")
    a = ap.parse_args()
    rows = load(METRICS, a.since, a.until)
    legacy = load(LEGACY, a.since, a.until)
    if not rows and not legacy:
        print(f"no vault ops logged in [{a.since}, {a.until}] -- "
              f"check the logger is alive before reading this as non-use"); return
    if not rows:
        print(f"no vv ops logged in [{a.since}, {a.until}] "
              f"({len(legacy)} legacy-route ops were)"); return

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
              f"{cf_full:,} B resolved-note size → {pct:.0f}% MODELLED "
              f"(not an observed baseline — see the caveat in this file's docstring)")

    # Adoption: the denominator. Legacy ops are the same vault work done the old
    # way; a low share is a finding about the pilot, not a failure of the week.
    if legacy:
        lk = collections.Counter(r.get("op", "?") for r in legacy)
        tot = len(rows) + len(legacy)
        print(f"adoption: vv handled {len(rows)} of {tot} logged vault ops "
              f"({100 * len(rows) / tot:.0f}%) · legacy {len(legacy)} — "
              + ", ".join(f"{k}:{n}" for k, n in lk.most_common()))
        lb = sum(r.get("note_bytes", 0) for r in legacy)
        if lb:
            print(f"legacy note bytes touched: {lb:,} B "
                  f"(whole-file for reads; touched-note size otherwise)")
    else:
        print("adoption: no legacy-route ops logged — either vv took everything, "
              "or the legacy logger is not running (verify before concluding)")
    er = 100 * len(errs) / len(rows)
    print(f"errors: {len(errs)}/{len(rows)} ({er:.1f}%)"
          + (" — " + ", ".join(f"{k}:{n}" for k, n in kinds.most_common()) if errs else ""))
    print("by command: " + ", ".join(f"{o}:{n}" for o, n in ops.most_common()))

if __name__ == "__main__":
    main()
