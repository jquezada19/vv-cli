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

SINK = os.path.expanduser("~/.claude/metrics/vv-shadow.jsonl")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sweepguard as sg


def main():
    since = sys.argv[1] if len(sys.argv) > 1 else "0000"
    until = sys.argv[2] if len(sys.argv) > 2 else "9999"
    if not os.path.exists(SINK):
        sys.exit(f"shadow: {SINK} does not exist — no paired reads recorded. "
                 f"This is a missing measurement, not a clean result.")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from shadow import HARNESS_VERSION
    reads, adj, stale = [], collections.defaultdict(list), 0
    funnel = sg.Funnel("shadow", "lines", "parsed", "in_window", "reads")
    for line in open(SINK):
        if not line.strip():
            continue
        funnel.bump("lines")
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        funnel.bump("parsed")
        if not (since <= r.get("ts", "")[:len(since)] <= until):
            continue
        funnel.bump("in_window")
        if r.get("kind") == "adjudication":
            adj[r["op"]].append(r)
            continue
        if r.get("hv") != HARNESS_VERSION:
            # A record from an older harness measured a DIFFERENT instrument.
            # Pooling them would report normaliser bugs as tool disagreements.
            stale += 1
            continue
        funnel.bump("reads")
        reads.append(r)
    funnel.report()
    if stale:
        print(f"  set aside {stale} record(s) from an older harness version "
              f"(current: v{HARNESS_VERSION}) — they measured a different\n"
              f"  instrument and would report normaliser bugs as tool disagreements.")
    funnel.require("reads")

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
        v = collections.Counter(r["verdict"] for r in paired)
        q = f"{v['match']}/{len(paired)} agree"
        if v["match"] < len(paired):
            q += f" · {len(paired) - v['match']} differ"
        print(f"{op:<11}{len(rs):>4}{vm:>8.0f}{lm:>9.0f}{lm / max(vm, .01):>6.0f}x"
              f"{vb:>9.0f}{lb:>10.0f}{lb / max(vb, 1):>7.0f}x  {q}")

    if tot_lg_b:
        print(f"\ncontext bill over paired reads: vv {tot_vv_b:,} B vs old way "
              f"{tot_lg_b:,} B ({tot_lg_b / max(tot_vv_b, 1):.0f}x)")
        print("  (bytes an agent would have to CARRY — the token cost the pairing exists to price)")

    diffs = [r for r in reads if r.get("verdict") not in (None, "match", "vv-only")]
    print(f"\ndisagreements: {len(diffs)}")
    seen = set()
    for r in diffs:
        key = (r["op"], tuple(r.get("args", [])))
        if key in seen:
            continue
        seen.add(key)
        a = adj.get(r["op"], [])
        who = a[-1]["who"] if a else "UNADJUDICATED"
        print(f"  [{r['op']}] {' '.join(r.get('args', []))} → {r['verdict']}  ({who})")
        if r.get("vv_only"):
            print(f"      vv found, old way missed : {r['vv_only'][:4]}")
        if r.get("legacy_only"):
            print(f"      old way found, vv missed : {r['legacy_only'][:4]}")
        if a:
            print(f"      ruling: {a[-1]['reason']}")

    unadj = [r for r in diffs if not adj.get(r["op"])]
    if unadj:
        print(f"\n  !! {len(unadj)} disagreement(s) UNADJUDICATED — the checkpoint cannot "
              f"close on quality until each is ruled on:\n"
              f"     shadow.py --adjudicate <op> <vv-correct|legacy-correct|"
              f"both-defensible|unresolved> <reason>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
