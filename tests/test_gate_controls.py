#!/usr/bin/env python3
"""Negative controls for the measurement gates that had none (census 2026-09-02).

House rule: watch every gate fail before trusting it. A gate that has never
gone red is indistinguishable from a print statement. Each pin here feeds a
gate a deliberately broken input and requires the red it promises — and where
the gate has a green path, the same test drives that too, so the pin cannot
pass by the gate being unconditionally red.

G1  pilot_report: both metrics logs missing/empty → ABORT (rc 2), never a
    report. The script that decides keep/kill must not read "nobody used it"
    off a dead logger.
G2  pilot_report: every op machine-paced (≥120/min, unmarked) → ABORT (rc 2);
    control: the same rows spread across minutes → a report.
G3  shadow.py refuses to pair a WRITE verb, and refuses a verb with no legacy
    analog, before running anything (both refusals happen before any write).
G4  run_tests.sh's own aggregator: a suite that prints a FAIL line but exits 0
    must fail the gate (the ok branch shows only tail -1, so the FAIL line
    was invisible). Extracted `run()` is exercised both ways.
G5  bench.py corpus floor: a one-note vault must refuse to print timings.
G6  index_bench: a case that exits non-zero VOIDs the run (rc 1) and writes
    no timing rows.
G7  verify_real_vault floors: an empty vault fails both floors (rc 1).
"""
import json, os, re, shutil, subprocess, sys, tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
os.environ.setdefault("VV_NO_METRICS", "1")

fails = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{str(detail)[:160]}]" if detail and not cond else ""))
    if not cond: fails.append(name)

_TMP = []
def tmpdir(prefix):
    d = tempfile.mkdtemp(prefix=prefix); _TMP.append(d); return d

def run(argv, env=None, cwd=None):
    e = dict(os.environ, **(env or {}))
    return subprocess.run(argv, capture_output=True, text=True, env=e, cwd=cwd or REPO)

def mkvault(n_notes):
    v = tmpdir("vv-gc-vault-")
    for i in range(n_notes):
        with open(os.path.join(v, f"Note {i}.md"), "w") as f:
            f.write(f"---\ntype: test\n---\n# Note {i}\n\n## Body\n\nlinks [[Note {(i + 1) % max(n_notes, 1)}]]\n")
    return v

try:
    # ---------- G1 / G2: pilot_report aborts ----------
    home = tmpdir("vv-gc-home-")
    r = run([PY, "bench/pilot_report.py", "--since", "2026-09-01"], env={"HOME": home})
    check("G1 both logs missing → ABORT rc 2", r.returncode == 2 and "ABORT: both metrics logs" in r.stdout + r.stderr, r.stdout[-300:] + r.stderr[-300:])
    os.makedirs(os.path.join(home, ".claude", "metrics"))
    sink = os.path.join(home, ".claude", "metrics", "vv.jsonl")
    open(sink, "w").close()
    r = run([PY, "bench/pilot_report.py", "--since", "2026-09-01"], env={"HOME": home})
    check("G1b both logs empty → ABORT rc 2", r.returncode == 2 and "ABORT: both metrics logs" in r.stdout + r.stderr, r.stdout[-300:])
    # 130 unmarked ops in one minute, after provenance stamping landed: machine-paced
    with open(sink, "w") as f:
        for i in range(130):
            f.write(json.dumps({"ts": "2026-09-01T10:00:%02d" % (i % 60), "op": "read", "ms": 1, "out_bytes": 10, "exit": 0}) + "\n")
    r = run([PY, "bench/pilot_report.py", "--since", "2026-09-01"], env={"HOME": home})
    check("G2 all traffic machine-paced → ABORT rc 2", r.returncode == 2 and "ABORT: every logged op was synthetic or machine-paced" in r.stdout + r.stderr, r.stdout[-400:])
    check("G2a the unmarked burst is called out", "still\n        carry no `src`" in r.stdout or "carry no `src`" in r.stdout, r.stdout[-400:])
    # control: the same 130 ops spread over 130 minutes are interactive
    with open(sink, "w") as f:
        for i in range(130):
            f.write(json.dumps({"ts": "2026-09-01T%02d:%02d:00" % (10 + i // 60, i % 60), "op": "read", "ms": 1, "out_bytes": 10, "exit": 0}) + "\n")
    r = run([PY, "bench/pilot_report.py", "--since", "2026-09-01"], env={"HOME": home})
    check("G2b control: spread ops → a report, rc 0", r.returncode == 0 and "window 2026-09-01" in r.stdout and "130 ops" in r.stdout, r.stdout[-400:] + r.stderr[-200:])

    # ---------- G3: shadow.py refusals ----------
    ssink = os.path.join(tmpdir("vv-gc-shadow-"), "s.jsonl")   # honoured once the sink override lands
    env = {"VV_SHADOW_SINK": ssink, "VV_VAULT": mkvault(3)}
    r = run([PY, "bench/shadow.py", "set", "Note 0.md", "k", "v"], env=env)
    check("G3a write verb refused before running", r.returncode != 0 and "MUTATES" in r.stderr and not os.path.exists(ssink), r.stderr[-200:])
    r = run([PY, "bench/shadow.py", "frobnicate", "x"], env=env)
    check("G3b unknown verb refused", r.returncode != 0 and "no legacy equivalent" in r.stderr and not os.path.exists(ssink), r.stderr[-200:])
    # The green path (a read verb runs and records) is pinned by R7 in
    # tests/test_readout_followups.py against a temp sink; on this base the
    # harness has no sink override, so it is deliberately NOT driven here.

    # ---------- G4: the aggregator's own hole ----------
    src = open(os.path.join(REPO, "run_tests.sh")).read()
    m = re.search(r"^run\(\) \{.*?^\}", src, re.S | re.M)
    check("G4 run() extracted from run_tests.sh", bool(m))
    harness = m.group(0) + '''
fail=0
run "prints FAIL exits 0" %(py)s -c 'print("FAIL vacuous"); import sys; sys.exit(0)'
echo "fail_after_vacuous=$fail"
fail=0
run "prints PASS exits 0" %(py)s -c 'print("PASS fine"); import sys; sys.exit(0)'
echo "fail_after_green=$fail"
fail=0
run "exits 1" %(py)s -c 'print("FAIL real"); import sys; sys.exit(1)'
echo "fail_after_red=$fail"
''' % {"py": PY}
    hp = os.path.join(tmpdir("vv-gc-agg-"), "h.sh")
    open(hp, "w").write(harness)
    r = run(["bash", hp])
    check("G4a FAIL-line-but-exit-0 suite fails the gate", "fail_after_vacuous=1" in r.stdout, r.stdout + r.stderr)
    check("G4b control: green suite stays green", "fail_after_green=0" in r.stdout, r.stdout)
    check("G4c control: non-zero exit fails the gate", "fail_after_red=1" in r.stdout, r.stdout)

    # ---------- G5: bench.py corpus floor ----------
    r = run([PY, "bench/bench.py", "--runs", "1", "--note", "Note 0.md", "--term", "links"], env={"VV_VAULT": mkvault(1)})
    check("G5 one-note vault refuses timings", r.returncode != 0 and "only 1 notes" in r.stderr and "meaningless" in r.stderr, r.stderr[-300:])

    # ---------- G6: index_bench VOID ----------
    metrics_home = tmpdir("vv-gc-ib-home-")
    r = run([PY, "bench/index_bench.py", "before"],
            env={"VV_VAULT": mkvault(12), "VV_BENCH_HUB": "NoSuchNote.md", "VV_BENCH_TERM": "links", "HOME": metrics_home})
    check("G6 a failing case VOIDs the run (rc 1)", r.returncode == 1 and "VOID: backlinks exited" in r.stdout, r.stdout[-300:] + r.stderr[-200:])
    check("G6a VOID writes no timing sink", not os.path.exists(os.path.join(metrics_home, ".claude", "metrics", "vv-index-bench.jsonl")))
    r = run([PY, "bench/index_bench.py", "before"], env={"VV_VAULT": mkvault(12), "HOME": metrics_home})
    check("G6b missing HUB/TERM refused up front", r.returncode != 0 and "VV_BENCH_HUB" in r.stderr, r.stderr[-200:])

    # ---------- G7: verify_real_vault floors ----------
    r = run([PY, "tests/verify_real_vault.py", "--sample", "3"], env={"VV_VAULT": tmpdir("vv-gc-empty-")})
    check("G7 empty vault fails the corpus floor", r.returncode == 1 and "FAIL corpus floor" in r.stdout, r.stdout[-400:])
    check("G7a … and the section floor", "FAIL section floor" in r.stdout, r.stdout[-400:])
finally:
    for d in _TMP:
        shutil.rmtree(d, ignore_errors=True)

print(f"\n{len(fails)} failure(s)" if fails else "\nALL PASS")
sys.exit(1 if fails else 0)
