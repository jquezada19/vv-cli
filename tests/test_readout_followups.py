#!/usr/bin/env python3
"""Regressions for the 2026-09-02 shadow-pilot read-out follow-ups.

The week's friction was the AFFORDANCE class — vv was right and unhelpful at
the same time. Each pin below is the one line of telemetry that motivated it.

R1  `board FOLDER status open` (space, not `=`) crashed with a Python traceback
    and exit 0 — a usage error reported as success. 7 of 19 board calls.
R2  `board FOLDER status=open` still works (control for R1).
R3  `journal` is not a command; 3 attempts in the week. The typo hint is
    edit-distance only, so `doctor` was never suggested. Alias table.
R4  `read NOTE` with no section pointed at the generic no-args usage line; the
    honest next step is `vv outline NOTE`. 11 of 230 read calls.
R5  shadow harness: a legacy one-liner that exits non-zero is a HARNESS error,
    never a tool disagreement (3 pairs scored as vv-superset with
    legacy_exit=2). Excluded from quality and byte totals, counted separately.
R6  shadow rulings are keyed by (op, args) first; an op-level ruling is still
    honoured but labelled as reused, so the read-out can see it.
"""
import json, os, shutil, subprocess, sys, tempfile

_VAULT = os.environ.get("VV_VAULT") or os.path.expanduser("~/Documents/Obsidian Vault")
SB = os.path.join(_VAULT, "Sandbox/vvreadout")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VV = os.path.join(REPO, "src", "vv.py")
SHADOW = os.path.join(REPO, "bench", "shadow.py")
SHADOW_REPORT = os.path.join(REPO, "bench", "shadow_report.py")

_JR = tempfile.mkdtemp(prefix="vv-test-journals-")
os.environ["VV_JOURNAL_ROOT"] = _JR
os.environ.setdefault("VV_NO_METRICS", "1")

fails = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{str(detail)[:160]}]" if detail and not cond else ""))
    if not cond: fails.append(name)

def run(*args, env=None, stdin=None):
    e = dict(os.environ, **(env or {}))
    return subprocess.run([sys.executable, *args], capture_output=True, text=True, env=e, input=stdin)

def vv(*args):
    return run(VV, *args)

# ---------- fixture ----------
if os.path.isdir(SB) and os.listdir(SB):
    keep = tempfile.mkdtemp(prefix="vv-kept-vvreadout-")
    shutil.move(SB, os.path.join(keep, "vvreadout"))
    print(f"note: pre-existing {SB} moved to {keep}")
shutil.rmtree(SB, ignore_errors=True)
os.makedirs(SB, exist_ok=True)
NOTE = "Sandbox/vvreadout/Readout Note.md"
with open(os.path.join(_VAULT, NOTE), "w") as f:
    f.write("---\ntype: test\nstatus: open\n---\n# Readout Note\n\n## First\n\nalpha\n\n## Second\n\nbeta\n")
with open(os.path.join(SB, "Closed Note.md"), "w") as f:
    f.write("---\ntype: test\nstatus: done\n---\n# Closed Note\n\nbody\n")

try:
    # R1 — a malformed board filter is a usage error, not a traceback
    r = vv("board", "Sandbox/vvreadout", "status", "open")
    check("R1a bad board filter exits 1", r.returncode == 1, f"rc={r.returncode}")
    check("R1b bad board filter is a usage error", r.stderr.startswith("usage: board filters are KEY=VALUE"), r.stderr)
    check("R1c no traceback", "Traceback" not in r.stderr, r.stderr)
    check("R1d names the offending token and the next step",
          "'status'" in r.stderr and "— next: vv board" in r.stderr, r.stderr)

    # R2 — control: the documented shape still works
    r = vv("board", "Sandbox/vvreadout", "status=open")
    check("R2 board KEY=VALUE filter works", r.returncode == 0 and "Readout Note" in r.stdout
          and "Closed Note" not in r.stdout, r.stdout + r.stderr)

    # R3 — journal → doctor
    r = vv("journal")
    check("R3a journal is still not a command", r.returncode == 1 and r.stderr.startswith("usage: unknown command journal"), r.stderr)
    check("R3b journal suggests doctor", "(did you mean: doctor)" in r.stderr, r.stderr)
    r = vv("outlien", "x")
    check("R3c edit-distance hint unchanged", "(did you mean: outline)" in r.stderr, r.stderr)

    # R4 — read with no section points at outline
    r = vv("read", NOTE)
    check("R4a read NOTE alone is a usage error", r.returncode == 1 and r.stderr.startswith("usage: read takes 2 positional args, got 1"), r.stderr)
    check("R4b next step is vv outline", "next: vv outline" in r.stderr, r.stderr)
    r = vv("read", NOTE, "First")
    check("R4c read NOTE SEC unchanged", r.returncode == 0 and "alpha" in r.stdout, r.stdout + r.stderr)

    # R5/R6 — shadow harness report over a synthetic sink
    sys.path.insert(0, os.path.join(REPO, "bench"))
    from shadow import HARNESS_VERSION
    sink = os.path.join(tempfile.mkdtemp(prefix="vv-shadow-sink-"), "vv-shadow.jsonl")
    base = {"ts": "2026-09-01T10:00:00", "hv": HARNESS_VERSION, "vv_ms": 5.0, "vv_bytes": 100, "vv_exit": 0}
    rows = [
        # legacy one-liner failed: must be a harness error, not a disagreement
        dict(base, op="links", args=["A.md"], legacy_ms=50.0, legacy_bytes=0, legacy_exit=2,
             verdict="vv-superset", vv_only=["B.md"], legacy_only=[]),
        # a real disagreement with an op-level ruling only
        dict(base, op="backlinks", args=["A.md"], legacy_ms=40.0, legacy_bytes=900, legacy_exit=0,
             verdict="differ", vv_only=["C.md"], legacy_only=["D.md"]),
        # a real disagreement with an exact (op, args) ruling
        dict(base, op="backlinks", args=["E.md"], legacy_ms=40.0, legacy_bytes=700, legacy_exit=0,
             verdict="differ", vv_only=["F.md"], legacy_only=[]),
        # a clean match
        dict(base, op="outline", args=["A.md"], legacy_ms=30.0, legacy_bytes=300, legacy_exit=0, verdict="match"),
    ]
    with open(sink, "w") as f:
        for r_ in rows:
            f.write(json.dumps(r_) + "\n")
    env = {"VV_SHADOW_SINK": sink}
    r = run(SHADOW, "--adjudicate", "backlinks", "vv-correct", "grep misses alias links", env=env)
    check("R6a op-level adjudication still accepted", r.returncode == 0, r.stdout + r.stderr)
    r = run(SHADOW, "--adjudicate", "backlinks", "both-defensible", "E has a duplicate basename", "--", "E.md", env=env)
    check("R6b case adjudication accepted", r.returncode == 0, r.stdout + r.stderr)
    last = json.loads(open(sink).read().strip().splitlines()[-1])
    check("R6c case adjudication records its args", last.get("kind") == "adjudication" and last.get("args") == ["E.md"], last)

    r = run(SHADOW_REPORT, "2026-09-01", "2026-09-01", env=env)
    out = r.stdout + r.stderr
    check("R5a report runs", r.returncode == 0, out)
    check("R5b legacy failure counted as harness error", "harness errors: 1" in out, out)
    check("R5c harness error not listed as a disagreement", "[links]" not in out.split("disagreements:")[-1], out)
    check("R5d harness error excluded from paired reads", "paired reads: 3" in out, out)
    check("R5e byte total excludes the failed pair", "old way 1,900 B" in out, out)
    check("R6d exact ruling labelled as a case ruling", "E.md → differ  (both-defensible, case ruling)" in out, out)
    check("R6e op-level ruling labelled as reused", "A.md → differ  (vv-correct, op-level ruling reused)" in out, out)
    check("R6f nothing left unadjudicated", "UNADJUDICATED" not in out, out)

    # R5 positive control: with the failed pair's exit code cleared the same
    # record must come back as a disagreement — proves the exclusion keys on
    # legacy_exit and not on something incidental to the fixture.
    rows[0]["legacy_exit"] = 0
    with open(sink, "w") as f:
        for r_ in rows:
            f.write(json.dumps(r_) + "\n")
    r = run(SHADOW_REPORT, "2026-09-01", "2026-09-01", env=env)
    out = r.stdout + r.stderr
    check("R5f control: same record with exit 0 IS a disagreement",
          "harness errors: 0" in out and "[links] A.md → vv-superset" in out and "paired reads: 4" in out, out)
finally:
    if not fails:
        shutil.rmtree(SB, ignore_errors=True)
    else:
        print(f"note: fixture kept at {SB} for inspection")

print(f"\n{len(fails)} failure(s)" if fails else "\nALL PASS")
sys.exit(1 if fails else 0)
