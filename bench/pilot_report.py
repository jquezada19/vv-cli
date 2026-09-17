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

METRICS = os.environ.get("VV_METRICS_PATH") or os.path.expanduser("~/.claude/metrics/vv.jsonl")
LEGACY = os.environ.get("VV_LEGACY_PATH") or os.path.expanduser("~/.claude/metrics/vv-legacy.jsonl")


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
                # Some rows carry operands in `op` (e.g. "board Link" from a
                # sweep invocation) -- keep only the command word everywhere
                # downstream groups/counts by op.
                if r.get("op"):
                    r["op"] = (str(r["op"]).split() or ["?"])[0]
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

# --- corrected adoption / friction-protocol split / per-version / criteria ---
#
# friction = the tool got in the way of something the caller was trying to do
# (bad usage, a ref that didn't resolve, an ambiguous match). protocol = the
# tool refused for a reason the caller didn't cause directly (a stale plan, a
# journal refusing a write, a path escaping the vault, bad utf8) -- these are
# also recognisable by exit code alone (3/4/5) even when `kind` is absent,
# because the native engine doesn't always stamp `kind`.
FRICTION_KINDS = {"usage", "not-found", "ambiguous"}
PROTOCOL_KINDS = {"stale", "refused", "escape", "utf8"}
PROTOCOL_EXITS = {3, 4, 5}


def classify_error(r):
    """None for a clean row, else 'friction' / 'protocol' / 'other'."""
    if r.get("exit", 0) == 0:
        return None
    kind = r.get("kind")
    if kind in FRICTION_KINDS:
        return "friction"
    if kind in PROTOCOL_KINDS or r.get("exit") in PROTOCOL_EXITS:
        return "protocol"
    return "other"


def is_eligible_legacy(r):
    """A legacy row counts toward the adoption denominator only if it was
    genuinely vv-shaped vault work, not any Bash invocation the hook happened
    to see. New rows declare it (`eligible: true`, `schema: 2`); old
    schema-1 rows are inferred the same way the hook used to gate on."""
    if r.get("eligible") is True:
        return True
    return r.get("note_bytes", 0) > 0 and r.get("tool") in (
        "Read", "Edit", "MultiEdit", "Write")


def strip_src(rows):
    """Drop rows carrying a `src` label (bench/sweep traffic) from every
    headline number. Returns (kept, dropped)."""
    kept = [r for r in rows if "src" not in r]
    dropped = [r for r in rows if "src" in r]
    return kept, dropped


def _ver_key(v):
    if v == "pre-3.0.0":
        return (-1,)
    try:
        return tuple(int(p) for p in v.split("."))
    except (ValueError, AttributeError):
        return (-1,)


def _ver_ge(v, floor):
    if not v:
        return False
    key = _ver_key(v)
    return key != (-1,) and key >= floor


VER_OPS = ("read", "append", "appendsec", "set", "daily-append")
SEL_OPS = ("read", "appendsec", "patch", "daily-append")


def print_version_blocks(rows):
    """Group vv rows by `ver` (missing -> pre-3.0.0) and show per-op friction
    rate for the ops the pilot cares about most."""
    groups = collections.defaultdict(list)
    for r in rows:
        groups[r.get("ver") or "pre-3.0.0"].append(r)
    if not groups:
        return
    print("\nby version:")
    for v in sorted(groups, key=_ver_key, reverse=True):
        vrows = groups[v]
        print(f"  ver {v}:")
        vops = collections.Counter(r["op"] for r in vrows)
        print("    ops: " + ", ".join(f"{o}:{n}" for o, n in vops.most_common()))
        for op in VER_OPS:
            op_rows = [r for r in vrows if r["op"] == op]
            if not op_rows:
                continue
            frict = [r for r in op_rows if classify_error(r) == "friction"]
            rate = 100 * len(frict) / len(op_rows)
            print(f"      {op} friction rate: {len(frict)}/{len(op_rows)} ({rate:.0f}%)")


def print_sel_census(rows):
    """Selector-kind census for the ops that take a selector argument."""
    sel_rows = [r for r in rows if r.get("op") in SEL_OPS and r.get("sel")]
    if not sel_rows:
        return
    counts = collections.Counter(r["sel"] for r in sel_rows)
    print("\nselector census: " + ", ".join(f"sel {k}: {n}" for k, n in counts.most_common()))


def per_day_eligible_adoption_min(rows, legacy, restrict_days=None):
    """min over active days of (vv rows / (vv rows + eligible legacy rows)),
    as a percentage -- None if there is no day with any eligible activity.

    `restrict_days`, when given, limits the days considered to that set --
    the caller uses this to keep the legacy side scoped to the same date
    window as a `rows` cohort that has already been filtered (e.g. by
    `ver`), so a legacy-only day with no matching vv activity can't drag the
    min down (or a vv-only day inflate it) outside that window."""
    vv_by_day = collections.Counter(r["ts"][:10] for r in rows)
    legacy_by_day = collections.Counter(
        r["ts"][:10] for r in legacy if is_eligible_legacy(r))
    days = set(vv_by_day) | set(legacy_by_day)
    if restrict_days is not None:
        days &= restrict_days
    pcts = []
    for d in days:
        tot = vv_by_day.get(d, 0) + legacy_by_day.get(d, 0)
        if tot:
            pcts.append(100 * vv_by_day.get(d, 0) / tot)
    return min(pcts) if pcts else None


def print_criteria(rows, legacy):
    """The plan's pre-registered criteria table, measured value beside each
    target. `ver` >= 3.1.0 and src-labelled rows already excluded by the
    caller's filtering of `rows`."""
    measured = [r for r in rows if _ver_ge(r.get("ver"), (3, 1, 0))]

    read_rows = [r for r in measured if r["op"] == "read"]
    read_err = [r for r in read_rows if r.get("exit", 0) != 0]
    read_rate = (f"{100 * len(read_err) / len(read_rows):.0f}% "
                 f"({len(read_err)}/{len(read_rows)})") if read_rows else "n/a"

    append_usage = len([r for r in measured
                        if r["op"] == "append" and r.get("kind") == "usage"])
    set_nf = len([r for r in measured
                  if r["op"] == "set" and r.get("kind") == "not-found"])

    sel_counts = collections.Counter(
        r.get("sel") for r in measured if r["op"] == "read" and r.get("sel"))
    sel_summary = ", ".join(f"{k}:{sel_counts.get(k, 0)}"
                            for k in ("flag", "sha8", "prefix"))

    # Same ver>=3.1.0 cohort as every other row in this table: a pre-3.1.0 vv
    # row must not pad the numerator, and a day with no ver>=3.1.0 vv traffic
    # must not be pulled in by legacy volume alone.
    measured_days = {r["ts"][:10] for r in measured}
    note_touch = per_day_eligible_adoption_min(measured, legacy, restrict_days=measured_days)
    note_touch_s = f"{note_touch:.0f}%" if note_touch is not None else "n/a"

    da_rows = [r for r in measured
               if r["op"] == "daily-append" and r.get("exit", 0) == 0 and r.get("sel")]
    da_today = len([r for r in da_rows if r["sel"] == "today"])
    da_landing = f"{100 * da_today / len(da_rows):.0f}%" if da_rows else "n/a"

    print("\n| Metric | Baseline | Target | Measured |")
    print("|---|---|---|---|")
    print(f"| read nonzero-exit rate | baseline 36% (77/216) | target < 10% | {read_rate} |")
    print(f"| append usage rows | baseline 8 | target 0 | {append_usage} |")
    print(f"| set not-found rows | baseline 15 | target 0 | {set_nf} |")
    print(f"| daily-append Today landings | baseline n/a | target 100% | {da_landing} |")
    print(f"| note-touching adoption | baseline 80–99% | target ≥ 85% every active day | {note_touch_s} |")
    print(f"| read selector kinds (sel) | baseline unmeasured | target flag, sha8, prefix each > 0 | {sel_summary} |")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True)
    ap.add_argument("--until", default="9999")
    ap.add_argument("--criteria", action="store_true",
                    help="print the pre-registered pilot criteria table")
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
    rows, dropped_vv = strip_src(rows)
    legacy, dropped_legacy = strip_src(legacy)
    dropped_all = dropped_vv + dropped_legacy
    if dropped_all:
        dops = collections.Counter(r.get("op", "?") for r in dropped_all)
        print(f"excluded {len(dropped_all)} labelled rows (src): "
              + ", ".join(f"{o}:{n}" for o, n in dops.most_common()))
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
    # dropped_vv rows never reach classify_traffic (strip_src ran above), so
    # `marked` alone under-counts synthetic traffic — a window whose vv rows
    # are ALL src-labelled leaves `marked` empty and `excluded` falsely zero,
    # which skipped the synthetic-only abort below and fell through to the
    # silent "no vault ops logged" exit-0 message instead.
    excluded = len(marked) + len(unmarked_machine) + len(dropped_vv)
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
    # The headline is the ELIGIBLE figure -- counting every legacy row here
    # was what over-logging inflated, before the hook started stamping
    # `eligible: true`. The pre-eligibility figure (every row the legacy hook
    # ever logged, regardless of whether it was genuinely vv-shaped work) is
    # kept as a secondary line for continuity with history/dashboards that
    # read the raw denominator.
    if legacy:
        lk = collections.Counter(r.get("op", "?") for r in legacy)
        eligible_legacy = [r for r in legacy if is_eligible_legacy(r)]
        elig_tot = len(rows) + len(eligible_legacy)
        elig_pct = f"{100 * len(rows) / elig_tot:.0f}%" if elig_tot else "n/a"
        print(f"adoption: vv handled {len(rows)} of {elig_tot} eligible vault ops "
              f"({elig_pct}) · raw legacy rows: {len(legacy)} — "
              + ", ".join(f"{k}:{n}" for k, n in lk.most_common()))
        lb = sum(r.get("note_bytes", 0) for r in legacy)
        if lb:
            print(f"legacy note bytes touched: {lb:,} B "
                  f"(whole-file for reads; touched-note size otherwise)")
        tot = len(rows) + len(legacy)
        print(f"raw adoption (all logged legacy rows): {len(rows)} of {tot} "
              f"({100 * len(rows) / tot:.0f}%)")
    else:
        print("adoption: no legacy-route ops logged — either vv took everything, "
              "or the legacy logger is not running (verify before concluding)")
    er = 100 * len(errs) / len(rows)
    print(f"errors: {len(errs)}/{len(rows)} ({er:.1f}%)"
          + (" — " + ", ".join(f"{k}:{n}" for k, n in kinds.most_common()) if errs else ""))
    if errs:
        friction_n = len([r for r in errs if classify_error(r) == "friction"])
        protocol_n = len([r for r in errs if classify_error(r) == "protocol"])
        other_n = len([r for r in errs if classify_error(r) == "other"])
        line = f"  friction: {friction_n} · protocol: {protocol_n}"
        if other_n:
            line += f" · other: {other_n}"
        print(line)
    print("by command: " + ", ".join(f"{o}:{n}" for o, n in ops.most_common()))

    print_version_blocks(rows)
    print_sel_census(rows)
    if a.criteria:
        print_criteria(rows, legacy)

if __name__ == "__main__":
    # sys.exit(main()), not main(): every ABORT above returns a non-zero code,
    # and without this the process still exited 0 -- a guard that printed loudly
    # and could not actually fail anything.
    sys.exit(main())
