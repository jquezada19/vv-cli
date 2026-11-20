#!/usr/bin/env python3
"""Replay the golden vectors against the native binary with the python entry
replaced by a sentinel.

Every record in tests/golden/<command>.jsonl says how its case is served:

  native     the binary must give exactly the recorded stdout, stderr, exit
             code and resulting files, without handing off.
  handoff    the binary must hand off cleanly: the sentinel's marker file
             exists, argv and stdin arrived as given, the binary printed
             nothing of its own and did not touch the vault first. Answering a case this
             suite expects to be handed off is a failure too: the port moves a
             case to `native` deliberately, in the record.
  divergent  the binary answers, but not with the python engine's bytes. Its
             answer must still be exactly the one recorded in
             known-divergences.jsonl, so a repair is a visible edit there.

The vectors are regenerated and compared by `tests/golden/generate.py --check`
(the gate's `golden fresh` line); this suite is the replay.
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
GOLDEN = os.path.join(HERE, "golden")
sys.path.insert(0, GOLDEN)
import generate as gen             # noqa: E402

fails = []
def check(name, cond, detail=""):
    if not cond:
        print("FAIL " + name + (f"  [{str(detail)[:400]}]" if detail else ""))
        fails.append(name)

if not os.path.exists(gen.VRUST):
    print("FAIL vrust/target/release/vrust is not built — the replay has nothing to run")
    sys.exit(1)

divergent = {}
with open(os.path.join(GOLDEN, gen.DIVERGENCES), encoding="utf-8") as f:
    for line in f:
        if line.strip():
            r = json.loads(line)
            divergent[r["id"]] = r
            check(f"{r['id']}: a divergence carries a note saying what differs", bool(r.get("note", "").strip()))

counts = {"native": 0, "handoff": 0, "divergent": 0}
names = sorted(n for n in os.listdir(GOLDEN) if n.endswith(".jsonl") and n != gen.DIVERGENCES)
for name in names:
    with open(os.path.join(GOLDEN, name), encoding="utf-8") as f:
        records = [json.loads(l) for l in f if l.strip()]
    for rec in records:
        served, cid = rec["served"], rec["id"]
        sb = gen.Sandbox()
        try:
            got, handed_off = sb.run_native(rec["argv"], rec["stdin"])
        except SystemExit as e:
            check(f"{cid}: a hand-off is clean", False, e)
            continue
        finally:
            sb.close()
        counts[served] = counts.get(served, 0) + 1
        want = {k: rec[k] for k in ("exit", "stdout", "stderr", "files")}
        if served == "native":
            check(f"{cid}: answered natively, not handed off", not handed_off, got)
            if not handed_off:
                for k in want:
                    check(f"{cid}: {k} equals the golden", got[k] == want[k], (got[k], want[k]))
        elif served == "handoff":
            check(f"{cid}: handed off", handed_off, got)
        elif served == "divergent":
            d = divergent.get(cid)
            check(f"{cid}: listed in {gen.DIVERGENCES}", d is not None)
            check(f"{cid}: a divergence is the binary's own answer, not a hand-off", not handed_off, got)
            if d:
                check(f"{cid}: the native answer is still the recorded one", got == d["native"], (got, d["native"]))
                check(f"{cid}: the recorded oracle answer matches the golden", d["oracle"] == want)
                check(f"{cid}: it does differ from the oracle", d["native"] != d["oracle"])
        else:
            check(f"{cid}: served is one of native/handoff/divergent", False, served)

all_recs = [json.loads(l) for n in names for l in open(os.path.join(GOLDEN, n), encoding="utf-8") if l.strip()]
ids = [r["id"] for r in all_recs]
check("case ids are unique", len(ids) == len(set(ids)))
check("the divergence file lists exactly the cases marked divergent",
      set(divergent) == {r["id"] for r in all_recs if r["served"] == "divergent"},
      (sorted(divergent), sorted(r["id"] for r in all_recs if r["served"] == "divergent")))
from cases import CASES            # noqa: E402
check("every case in cases.py has a record, and no record lacks a case",
      sorted(ids) == sorted(f"{c}/{i}" for c, cs in CASES.items() for i, _a, _s in cs))

total = sum(counts.values())
print(f"\n{len(fails)} failures" if fails else
      f"ALL PASS (golden replay: {total} cases — native {counts['native']}, handoff {counts['handoff']}, divergent {counts['divergent']})")
sys.exit(1 if fails else 0)
