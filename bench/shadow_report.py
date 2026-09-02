#!/usr/bin/env python3
"""Read-out for the paired-read shadow protocol: quality, speed, and byte cost.

Answers the three questions the 2026-09-02 checkpoint has to close on:
  QUALITY  did the two ways give the same answer, and when they differed, who
           was right? (adjudicated per case -- agreement rate alone is not a
           quality measure when one instrument is known to be weaker)
  SPEED    vv vs the old way, per verb
  BYTES    what an agent would have had to carry as context each way -- the
           actual token bill, which is the reason the pairing costs anything
"""
import collections, json, os, statistics, sys

SINK = os.environ.get("VV_SHADOW_SINK") or os.path.expanduser("~/.claude/metrics/vv-shadow.jsonl")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sweepguard as sg
from shadow import RULINGS, LEGACY, HARNESS_VERSION, legacy_failed   # RULINGS: one source for the four ruling values
if os.environ.get("VV_SHADOW_SINK"):
    print(f"shadow: sink {SINK} (VV_SHADOW_SINK override)", file=sys.stderr)


def _harness_error(r):
    """A read whose legacy side FAILED (not merely answered 'nothing').

    Decides from the RECORDED EXIT CODE whenever one exists — a verdict written
    by an earlier producer could carry `legacy-error` for a grep exit 1 (a
    producer that predates the exit-code rule), and trusting it would
    re-import the misclassification the exit code refutes. The verdict is consulted only for a row with no exit
    code. grep's exit 1 is an answer, not a failure — see shadow.legacy_failed.
    # yagni: the exit-code branch reclassifies pre-verdict v4 rows (the
    # legacy-error verdict shipped without a HARNESS_VERSION bump on purpose);
    # it stays until no v4 row predating 2026-09-02 falls in a reported window.
    """
    rc = r.get("legacy_exit")
    if rc is None:
        return r.get("verdict") == "legacy-error"
    if rc == 0:
        return False
    build = LEGACY.get(r.get("op"), (None,))[0]
    if build is None:
        return True          # a vv-only op never ran a legacy command; a non-zero exit is a harness fault
    # Every analog's argv[0] is a constant independent of args, so a
    # placeholder recovers it whatever the recorded args were (or weren't).
    return legacy_failed(build(["_"]), rc)


def main():
    since = sys.argv[1] if len(sys.argv) > 1 else "0000"
    until = sys.argv[2] if len(sys.argv) > 2 else "9999"
    if not os.path.exists(SINK):
        sys.exit(f"shadow: {SINK} does not exist — no paired reads recorded. "
                 f"This is a missing measurement, not a clean result.")
    reads, adj, stale, herr, unscored_n = [], collections.defaultdict(list), 0, [], 0
    adj_case = {}   # (op, tuple(args)) -> ruling; exact wins over op-level
    # "scored" is the stage that survives the harness-error split: a sink of
    # nothing but failed legacy runs must abort loudly here, not print a
    # clean-looking "paired reads: 0".
    funnel = sg.Funnel("shadow", "lines", "parsed", "in_window", "reads", "scored")
    for line in open(SINK):
        if not line.strip():
            continue
        funnel.bump("lines")
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        funnel.bump("parsed")
        if r.get("kind") == "adjudication":
            # Rulings are never window-filtered: a disagreement inside the
            # window is usually ruled on AFTER it (the read-out day), and a
            # ruling dropped by the window resurfaced as UNADJUDICATED.
            # Malformed rows are skipped like a bad JSON line, never fatal.
            op, args = r.get("op"), r.get("args")
            if not isinstance(op, str) or r.get("who") not in RULINGS or not isinstance(r.get("reason"), str):
                continue   # an unknown `who` or a missing reason must not count as adjudicated
            if args is not None:
                if not (isinstance(args, list) and all(isinstance(x, str) for x in args)):
                    continue
                adj_case[(op, tuple(args))] = r
            else:
                adj[op].append(r)
            continue
        if not (since <= r.get("ts", "")[:len(since)] <= until):
            continue
        funnel.bump("in_window")
        if r.get("hv") != HARNESS_VERSION:
            # A record from an older harness measured a DIFFERENT instrument.
            # Pooling them would report normaliser bugs as tool disagreements.
            stale += 1
            continue
        funnel.bump("reads")
        if r.get("verdict") == "legacy-error" and not _harness_error(r):
            # A row from a producer that predates the exit-code rule: the
            # verdict says failure, the exit code says answer. The pair is real (bytes/latency count), but no answer-set
            # diff was retained, so it cannot be scored as a disagreement or a
            # match — mark it unscored rather than calling it a measured
            # difference.
            r = dict(r, verdict="unscored")
        if _harness_error(r):
            # The legacy command failed: a HARNESS error. Counted, shown, and
            # kept out of both the quality and the byte totals — scoring it
            # would bias the read-out against vv (3 such pairs in the pilot data).
            herr.append(r)
            continue
        if r.get("verdict") != "unscored":
            funnel.bump("scored")    # an unscored pair is paired (bytes count) but not scored
        else:
            unscored_n += 1
        reads.append(r)
    funnel.report()
    if herr or unscored_n:
        print(f"  reads − scored = {len(herr)} harness error(s) + {unscored_n} unscored")
    if stale:
        print(f"  set aside {stale} record(s) from an older harness version "
              f"(current: v{HARNESS_VERSION}) — they measured a different\n"
              f"  instrument and would report normaliser bugs as tool disagreements.")
    funnel.require("reads")
    funnel.require("scored")

    print(f"\npaired reads: {len(reads)}")
    by = collections.defaultdict(list)
    for r in reads:
        by[r["op"]].append(r)

    print(f"\n{'op':<11}{'n':>4}{'vv ms':>8}{'old ms':>9}{'x':>7}"
          f"{'vv B':>9}{'old B':>10}{'x':>8}  quality")
    print("-" * 82)
    tot_vv_b = tot_lg_b = 0
    for op in sorted(by):
        rs = by[op]
        paired = [r for r in rs if r.get("legacy_ms") is not None]
        vm = statistics.median([r["vv_ms"] for r in rs])
        vb = statistics.median([r["vv_bytes"] for r in rs])
        tot_vv_b += sum(r["vv_bytes"] for r in rs)
        if not paired:
            print(f"{op:<11}{len(rs):>4}{vm:>8.0f}{'—':>9}{'—':>7}{vb:>9.0f}{'—':>10}{'—':>8}"
                  f"  vv-only (no legacy equivalent)")
            continue
        lm = statistics.median([r["legacy_ms"] for r in paired])
        lb = statistics.median([r["legacy_bytes"] for r in paired])
        tot_lg_b += sum(r["legacy_bytes"] for r in paired)
        scored = [r for r in paired if r.get("verdict") != "unscored"]
        v = collections.Counter(r["verdict"] for r in scored)
        if not scored:
            q = f"nothing scored · {len(paired)} unscored"
        else:
            q = f"{v['match']}/{len(scored)} agree"
            if v["match"] < len(scored):
                q += f" · {len(scored) - v['match']} differ"
            if len(scored) < len(paired):
                q += f" · {len(paired) - len(scored)} unscored"
        print(f"{op:<11}{len(rs):>4}{vm:>8.0f}{lm:>9.0f}{lm / max(vm, .01):>6.0f}x"
              f"{vb:>9.0f}{lb:>10.0f}{lb / max(vb, 1):>7.0f}x  {q}")

    if tot_lg_b:
        print(f"\ncontext bill over paired reads: vv {tot_vv_b:,} B vs old way "
              f"{tot_lg_b:,} B ({tot_lg_b / max(tot_vv_b, 1):.0f}x)")
        print("  (bytes an agent would have to CARRY — the token cost the pairing exists to price)")

    unscored = [r for r in reads if r.get("verdict") == "unscored"]
    if unscored:
        print(f"\nunscored: {len(unscored)} (stale legacy-error verdict on an answering exit; no diff retained)")
        for r in unscored:
            print(f"  [{r['op']}] {' '.join(r.get('args', []))}")
    diffs = [r for r in reads if r.get("verdict") not in (None, "match", "vv-only", "unscored")]
    print(f"\nharness errors: {len(herr)} (legacy one-liner FAILED — grep exit 2+, "
          f"anything else non-zero — excluded from quality and byte totals)")
    for r in herr:
        print(f"  [{r['op']}] {' '.join(r.get('args', []))} legacy_exit={r.get('legacy_exit')}")

    distinct = len({(r["op"], tuple(r.get("args", []))) for r in diffs})
    print(f"\ndisagreements: {len(diffs)}" + (f" ({distinct} distinct cases)" if distinct != len(diffs) else ""))
    seen = set()
    for r in diffs:
        key = (r["op"], tuple(r.get("args", [])))
        if key in seen:
            continue
        seen.add(key)
        exact = adj_case.get(key)
        ruling = exact or (adj.get(r["op"]) or [None])[-1]
        who = (f"{ruling.get('who', '?')}, {'case ruling' if exact else 'op-level ruling reused'}"
               if ruling else "UNADJUDICATED")
        if ruling and ruling.get("hv") not in (None, HARNESS_VERSION):
            # A ruling made against a retired normaliser is evidence about
            # THAT instrument; say so instead of silently reusing it.
            who += f" (ruled under harness v{ruling['hv']})"
        print(f"  [{r['op']}] {' '.join(r.get('args', []))} → {r['verdict']}  ({who})")
        if r.get("vv_only"):
            print(f"      vv found, old way missed : {r['vv_only'][:4]}")
        if r.get("legacy_only"):
            print(f"      old way found, vv missed : {r['legacy_only'][:4]}")
        if ruling:
            print(f"      ruling: {ruling.get('reason', '?')}")

    unadj = [k for k in seen if not adj_case.get(k) and not adj.get(k[0])]
    if unadj:
        print(f"\n  !! {len(unadj)} disagreement(s) UNADJUDICATED — the checkpoint cannot "
              f"close on quality until each is ruled on:\n"
              f"     shadow.py --adjudicate <op> <vv-correct|legacy-correct|"
              f"both-defensible|unresolved> <reason> [-- <args...>]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
