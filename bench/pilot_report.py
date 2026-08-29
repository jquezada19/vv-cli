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
import argparse, collections, json, os, statistics, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sweepguard as sg

METRICS = os.path.expanduser("~/.claude/metrics/vv.jsonl")
LEGACY = os.path.expanduser("~/.claude/metrics/vv-legacy.jsonl")


def load(path, since, until, funnel=None, label=""):
    """Rows in [since, until].

    Returns (rows, diag) where diag distinguishes the THREE ways this can be
    empty, because they mean opposite things for a keep/kill decision:
      missing   -- the log is not there. Says nothing about usage.
      empty     -- the log exists but has no rows at all. Logger likely dead.
      no-window -- rows exist, none in this window. THIS is real non-use.
    The old code swallowed OSError and returned [], so a deleted or renamed
    metrics file was indistinguishable from a tool nobody used -- on the very
    script that decides whether the tool survives its pilot.
    """
    rows = []
    diag = {"exists": os.path.exists(path), "lines": 0, "parsed": 0,
            "no_ts": 0, "in_window": 0}
    if not diag["exists"]:
        diag["state"] = "missing"
        return rows, diag
    try:
        with open(path) as f:
            for line in f:
                if not line.strip():
                    continue
                diag["lines"] += 1
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                diag["parsed"] += 1
                if not r.get("ts"):
                    diag["no_ts"] += 1
                    continue
                # Compare at the precision the caller asked for: a bare date
                # bounds by day, a full ISO stamp bounds by second. The pilot
                # needs the latter -- the pre-suppression test fixtures share a
                # calendar day with the start of real usage.
                ts = r.get("ts", "")
                if since <= ts[:len(since)] and ts[:len(until)] <= until:
                    rows.append(r)
                    diag["in_window"] += 1
    except OSError as e:
        diag["state"] = "unreadable"
        diag["error"] = str(e)
        return rows, diag
    diag["state"] = ("empty" if diag["parsed"] == 0
                     else "no-window" if not rows else "ok")
    if funnel is not None:
        funnel.bump(f"{label}lines", diag["lines"])
        funnel.bump(f"{label}parsed", diag["parsed"])
        funnel.bump(f"{label}in_window", diag["in_window"])
    return rows, diag


MACHINE_OPS_PER_MIN = 120       # sustained; interactive use does not reach this

# When vv started stamping VV_METRICS_SRC into each row. Rows written before
# this COULD NOT have been marked, so calling them "unmarked" is not a reproach
# and must not read as an outstanding action -- an alarm that fires forever on
# something nobody can fix is an alarm people learn to scroll past.
PROVENANCE_SINCE = "2026-08-27T11:49"


def classify_traffic(rows):
    """Split rows into marked-synthetic / unmarked-but-machine-paced / usage.

    Two mechanisms, in order of trustworthiness:

    1. PROVENANCE (`src`): the invocation said what it was. vv writes this from
       VV_METRICS_SRC, which bench/sweepguard.mark_bench() sets. Exact, no
       heuristic, no false positives.
    2. RATE: a backstop for traffic that arrived unmarked. No interactive
       session sustains >=120 ops/minute for minutes on end.

    The gap between them is the interesting number and is reported, not hidden:
    unmarked machine-paced rows mean a benchmark ran WITHOUT calling
    mark_bench() -- most likely an ad-hoc loop, which is exactly what produced
    the 117,312 contaminating rows on 2026-08-27. Seeing that count is how a
    forgotten mark becomes visible instead of silently skewing adoption.
    """
    marked = [r for r in rows if r.get("src")]
    rest = [r for r in rows if not r.get("src")]
    by_min = collections.Counter(r.get("ts", "")[:16] for r in rest)
    hot = {m for m, n in by_min.items() if n >= MACHINE_OPS_PER_MIN}
    unmarked_machine = [r for r in rest if r.get("ts", "")[:16] in hot]
    human = [r for r in rest if r.get("ts", "")[:16] not in hot]
    labels = collections.Counter(r.get("src") for r in marked)
    return human, marked, unmarked_machine, sorted(hot), labels


def _explain(name, path, diag):
    st = diag.get("state")
    if st == "missing":
        return (f"  !! {name}: {path} DOES NOT EXIST. This is a broken measurement, "
                f"NOT evidence of non-use — do not read a keep/kill signal from it.")
    if st == "unreadable":
        return (f"  !! {name}: {path} unreadable ({diag.get('error')}). Broken "
                f"measurement, not non-use.")
    if st == "empty":
        return (f"  !! {name}: {path} exists but holds 0 parseable rows — the "
                f"logger is probably dead. Not evidence of non-use.")
    if diag.get("no_ts"):
        return (f"  note: {name}: {diag['no_ts']} row(s) had no timestamp and were "
                f"excluded from the window silently by the old code.")
    return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True)
    ap.add_argument("--until", default="9999")
    a = ap.parse_args()
    funnel = sg.Funnel("pilot", "vv_lines", "vv_parsed", "vv_in_window",
                       "legacy_lines", "legacy_parsed", "legacy_in_window")
    rows, d_vv = load(METRICS, a.since, a.until, funnel, "vv_")
    legacy, d_lg = load(LEGACY, a.since, a.until, funnel, "legacy_")
    funnel.report()
    for name, path, d in (("vv.jsonl", METRICS, d_vv), ("vv-legacy.jsonl", LEGACY, d_lg)):
        msg = _explain(name, path, d)
        if msg:
            print(msg)
    # A broken measurement must never be read as a result on the script that
    # decides the pilot's fate.
    if d_vv["state"] in ("missing", "unreadable", "empty") and \
       d_lg["state"] in ("missing", "unreadable", "empty"):
        print("\nABORT: both metrics logs are missing/empty — there is nothing to "
              "report. Fix the logger; do not treat this as a pilot signal.")
        return 2
    human, marked, unmarked_machine, hot_min, labels = classify_traffic(rows)
    if marked:
        print(f"\n  provenance: {len(marked):,} op(s) are self-declared synthetic "
              f"({', '.join(f'{k}={v:,}' for k, v in labels.most_common())}) — excluded.")
    if unmarked_machine:
        pre_provenance = [r for r in unmarked_machine
                          if r.get("ts", "") < PROVENANCE_SINCE]
        recent = [r for r in unmarked_machine if r.get("ts", "") >= PROVENANCE_SINCE]
        print(f"\n  machine-paced, excluded by RATE: {len(unmarked_machine):,} op(s) "
              f"across {len(hot_min)} minute(s) at >={MACHINE_OPS_PER_MIN}/min.")
        if pre_provenance:
            print(f"     {len(pre_provenance):,} predate provenance stamping "
                  f"({PROVENANCE_SINCE}) — they could not have been marked. No action.")
        if recent:
            print(f"     !! {len(recent):,} were written AFTER stamping landed and still "
                  f"carry no `src`.\n"
                  f"        A benchmark ran without bench/sweepguard.mark_bench(), or vv\n"
                  f"        was driven by an ad-hoc loop. FIX THE MARKING — a heuristic\n"
                  f"        that has to guess will eventually guess wrong.")
    excluded = len(marked) + len(unmarked_machine)
    if excluded:
        print(f"     reporting on the {len(human):,} remaining plausibly-interactive op(s).")
        rows = human
        if not rows:
            print("\nABORT: every logged op was synthetic or machine-paced. There is no "
                  "usage signal in this window — do not read a keep/kill decision from it.")
            return 2
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
    # sys.exit(main()), not main(): every ABORT above returns a non-zero code,
    # and without this the process still exited 0 -- a guard that printed loudly
    # and could not actually fail anything.
    sys.exit(main())
