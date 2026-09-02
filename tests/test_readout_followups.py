#!/usr/bin/env python3
"""Regressions for the 2026-09-02 shadow-pilot read-out follow-ups.

The week's friction was the AFFORDANCE class — vv was right and unhelpful at
the same time. Each pin below names what motivated it.

R1  `board FOLDER status open` (space, not `=`) died as a bare Python
    traceback: exit 1, no usage line, no `next:`, no metrics row. Found by
    probing, not by telemetry (the traceback bypasses the logger, so the
    pilot sink holds zero occurrences). Now a usage error with a runnable
    `next:`. R1x: `board ../x` is refused by containment, both engines.
R2  `board FOLDER status=open` still works (control for R1).
R3  `journal` is not a command; 3 attempts in the week. The typo hint is
    edit-distance only, so `doctor` was never suggested. Alias table.
R4  `read NOTE` with no section pointed at the generic no-args usage line;
    the honest next step is `vv outline NOTE` — a RUNNABLE command, per the
    `next:` contract. 11 of 230 read calls.
R5  shadow harness: a legacy one-liner that FAILS is a harness error, never a
    tool disagreement (3 pairs scored vv-superset with legacy_exit=2) — but
    grep's exit 1 is an answer ("no selected lines"), not a failure. Excluded
    from quality and byte totals, counted separately; a sink of nothing but
    failures aborts loudly.
R6  shadow rulings are keyed by (op, args) first; an op-level ruling is still
    honoured but labelled as reused; rulings are never window-filtered.
R7  the shadow PRODUCER writes `legacy-error` (not a normal verdict) when the
    legacy side fails, keeps no answer-set diff for it, and compares grep's
    exit 1 normally — exercised in-process with a stubbed runner.
RN  the three affordance errors are identical through the native entry.
"""
import io, json, os, shutil, subprocess, sys, tempfile

_VAULT = os.environ.get("VV_VAULT") or os.path.expanduser("~/Documents/Obsidian Vault")
SB = os.path.join(_VAULT, "Sandbox/vvreadout")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VV = os.path.join(REPO, "src", "vv.py")
VRUST = os.path.join(REPO, "vrust", "target", "release", "vrust")
SHADOW = os.path.join(REPO, "bench", "shadow.py")
SHADOW_REPORT = os.path.join(REPO, "bench", "shadow_report.py")

_TMP = []   # every temp dir this suite makes; removed at exit
def mkdtemp(prefix):
    d = tempfile.mkdtemp(prefix=prefix); _TMP.append(d); return d
_JR = mkdtemp("vv-test-journals-")
os.environ["VV_JOURNAL_ROOT"] = _JR
os.environ.setdefault("VV_NO_METRICS", "1")

fails = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{str(detail)[:160]}]" if detail and not cond else ""))
    if not cond: fails.append(name)

def run(*args, env=None, stdin=None):
    e = dict(os.environ, **(env or {}))
    return subprocess.run([sys.executable, *args], capture_output=True, text=True, env=e, input=stdin)

def vv(*args, env=None):
    return run(VV, *args, env=env)

# ---------- fixture (pre-existing content is set aside and RESTORED at exit) ----------
_kept = None
if os.path.isdir(SB) and os.listdir(SB):
    _kept = os.path.join(mkdtemp("vv-kept-vvreadout-"), "vvreadout")
    shutil.move(SB, _kept)
    print(f"note: pre-existing {SB} set aside; restored at exit")
shutil.rmtree(SB, ignore_errors=True)
os.makedirs(SB, exist_ok=True)
NOTE = "Sandbox/vvreadout/Readout Note.md"
with open(os.path.join(_VAULT, NOTE), "w") as f:
    f.write("---\ntype: test\nstatus: open\n---\n# Readout Note\n\n## First\n\nalpha\n\n## Second\n\nbeta\n")
with open(os.path.join(SB, "Closed Note.md"), "w") as f:
    f.write("---\ntype: test\nstatus: done\n---\n# Closed Note\n\nbody\n")

def affordance_checks(tag, runner):
    """The three CLI affordances, through whichever entry `runner` is."""
    r = runner("board", "Sandbox/vvreadout", "status", "open")
    check(f"{tag}1a bad board filter exits 1", r.returncode == 1, f"rc={r.returncode}")
    check(f"{tag}1b bad board filter is a usage error", r.stderr.startswith("usage: board filters are KEY=VALUE"), r.stderr)
    check(f"{tag}1c no traceback", "Traceback" not in r.stderr, r.stderr)
    check(f"{tag}1d names the token and a runnable next step",
          "got status " in r.stderr and r.stderr.rstrip().endswith("— next: vv board Sandbox/vvreadout status=VALUE"), r.stderr)
    r = runner("board", "Sandbox/vvreadout", "sta tus")
    check(f"{tag}1e a token with a space is quoted in the next step", "'sta tus=VALUE'" in r.stderr, r.stderr)
    r = runner("board", "../", "status=open")
    check(f"{tag}1x board is vault-contained", r.returncode == 1 and r.stderr.startswith("escape:"), r.stderr)
    r = runner("board", "Sandbox/vvreadout", "status=open")
    check(f"{tag}2 board KEY=VALUE filter works", r.returncode == 0 and "Readout Note" in r.stdout
          and "Closed Note" not in r.stdout, r.stdout + r.stderr)
    r = runner("journal")
    check(f"{tag}3a journal is still not a command", r.returncode == 1 and r.stderr.startswith("usage: unknown command journal"), r.stderr)
    check(f"{tag}3b journal suggests doctor", "(did you mean: doctor)" in r.stderr, r.stderr)
    r = runner("outlien", "x")
    check(f"{tag}3c edit-distance hint unchanged", "(did you mean: outline)" in r.stderr, r.stderr)
    r = runner("read", NOTE)
    check(f"{tag}4a read NOTE alone is a usage error", r.returncode == 1 and r.stderr.startswith("usage: read takes 2 positional args, got 1"), r.stderr)
    check(f"{tag}4b next step is the runnable outline command", r.stderr.rstrip().endswith("— next: vv outline NOTE"), r.stderr)
    r = runner("read", NOTE, "First")
    check(f"{tag}4c read NOTE SEC unchanged", r.returncode == 0 and "alpha" in r.stdout, r.stdout + r.stderr)

try:
    affordance_checks("R", lambda *a: vv(*a, env={"VV_ENGINE": "python"}))
    if os.path.exists(VRUST):
        # The native entry itself: every one of these must Fallback/exec to
        # python and print the identical text (Codex + buddy seats asked for
        # this pin — the python launcher alone never exercises the binary).
        def native(*a):
            return subprocess.run([VRUST, *a], capture_output=True, text=True,
                                  env=dict(os.environ, VV_VAULT=_VAULT))
        affordance_checks("RN", native)
    else:
        print("SKIP RN native entry not built")

    # ---------- R5/R6 — shadow report over a synthetic sink ----------
    sys.path.insert(0, os.path.join(REPO, "bench"))
    sink = os.path.join(mkdtemp("vv-shadow-sink-"), "vv-shadow.jsonl")
    os.environ["VV_SHADOW_SINK"] = sink          # set BEFORE importing shadow
    import shadow
    from shadow import HARNESS_VERSION
    base = {"ts": "2026-09-01T10:00:00", "hv": HARNESS_VERSION, "vv_ms": 5.0, "vv_bytes": 100, "vv_exit": 0}
    rows = [
        # legacy one-liner failed (grep exit 2) — a harness error. Non-zero
        # bytes on BOTH sides so the exclusion is visible in both totals.
        dict(base, op="links", args=["A.md"], legacy_ms=50.0, legacy_bytes=500, legacy_exit=2,
             verdict="vv-superset", vv_only=["B.md"], legacy_only=[]),
        # grep exit 1 = "no matches": an ANSWER, scored normally (vv-superset)
        dict(base, op="backlinks", args=["Z.md"], legacy_ms=45.0, legacy_bytes=0, legacy_exit=1,
             verdict="vv-superset", vv_only=["Y.md"], legacy_only=[]),
        # a real disagreement with an op-level ruling only
        dict(base, op="backlinks", args=["A.md"], legacy_ms=40.0, legacy_bytes=900, legacy_exit=0,
             verdict="differ", vv_only=["C.md"], legacy_only=["D.md"]),
        # a real disagreement with an exact (op, args) ruling
        dict(base, op="backlinks", args=["E.md"], legacy_ms=40.0, legacy_bytes=700, legacy_exit=0,
             verdict="differ", vv_only=["F.md"], legacy_only=[]),
        # a clean match
        dict(base, op="outline", args=["A.md"], legacy_ms=30.0, legacy_bytes=300, legacy_exit=0, verdict="match"),
        # a malformed adjudication row: skipped, never fatal
        {"kind": "adjudication", "who": "vv-correct", "reason": "no op field"},
    ]
    def write_sink(rs):
        with open(sink, "w") as f:
            for r_ in rs:
                f.write(json.dumps(r_) + "\n")
    write_sink(rows)
    env = {"VV_SHADOW_SINK": sink}
    r = run(SHADOW, "--adjudicate", "backlinks", "vv-correct", "grep misses alias links", env=env)
    check("R6a op-level adjudication still accepted", r.returncode == 0, r.stdout + r.stderr)
    r = run(SHADOW, "--adjudicate", "backlinks", "both-defensible", "E has a duplicate basename", "--", "E.md", env=env)
    check("R6b case adjudication accepted", r.returncode == 0, r.stdout + r.stderr)
    last = json.loads(open(sink).read().strip().splitlines()[-1])
    check("R6c case adjudication records its args and harness version",
          last.get("kind") == "adjudication" and last.get("args") == ["E.md"] and last.get("hv") == HARNESS_VERSION, last)
    r = run(SHADOW, "--adjudicate", "backlinks", "vv-correct", "trailing separator", "--", env=env)
    check("R6g `--` with no case args is refused", r.returncode != 0 and "no case args" in (r.stdout + r.stderr), r.stdout + r.stderr)
    r = run(SHADOW, "--adjudicate", "backlinks", "vv-correct", "a", "--", "b", "--", "X.md", env=env)
    check("R6h more than one `--` is refused as ambiguous", r.returncode != 0 and "ambiguous" in (r.stdout + r.stderr), r.stdout + r.stderr)

    r = run(SHADOW_REPORT, "2026-09-01", "2026-09-01", env=env)
    out = r.stdout + r.stderr
    check("R5a report runs", r.returncode == 0, out)
    check("R5b grep exit 2 counted as a harness error", "harness errors: 1" in out and "[links] A.md legacy_exit=2" in out, out)
    check("R5c harness error not listed as a disagreement", "[links]" not in out.split("disagreements:")[-1], out)
    check("R5d grep exit 1 is an answer: scored, not a harness error",
          "paired reads: 4" in out and "[backlinks] Z.md → vv-superset" in out, out)
    check("R5e byte totals exclude the failed pair on BOTH sides",
          "vv 400 B vs old way 1,900 B" in out, out)
    check("R5g funnel shows the split", "reads=5 -> scored=4" in out, out)
    check("R6d exact ruling labelled as a case ruling", "E.md → differ  (both-defensible, case ruling)" in out, out)
    check("R6e op-level ruling labelled as reused", "A.md → differ  (vv-correct, op-level ruling reused)" in out, out)
    check("R6f nothing left unadjudicated", "UNADJUDICATED" not in out, out)
    check("R6i malformed adjudication row skipped, not fatal", "Traceback" not in out, out)

    # R5 positive control: with the failed pair's exit code cleared the same
    # record must come back as a disagreement — the exclusion keys on the
    # exit code, not on something incidental to the fixture.
    rows[0]["legacy_exit"] = 0
    write_sink(rows)
    r = run(SHADOW_REPORT, "2026-09-01", "2026-09-01", env=env)
    out = r.stdout + r.stderr
    check("R5f control: same record with exit 0 IS a disagreement",
          "harness errors: 0" in out and "[links] A.md → vv-superset" in out and "paired reads: 5" in out
          and "vv 500 B vs old way 2,400 B" in out, out)
    # a sink of nothing but failed pairs must abort loudly, not print a clean zero
    write_sink([dict(rows[0], legacy_exit=2)])
    r = run(SHADOW_REPORT, "2026-09-01", "2026-09-01", env=env)
    out = r.stdout + r.stderr
    check("R5h only-harness-errors sink aborts", r.returncode != 0 and "scored" in out and "SWEEP is broken" in out, out)

    # ---------- R7 — the producer path, in-process with a stubbed runner ----------
    write_sink([])
    real_sh = shadow.sh
    def fake_sh_factory(legacy_rc, legacy_out):
        def fake_sh(argv, shell=False):
            if os.path.basename(argv[0]) in ("vrust", "vv") or argv[0] == shadow.VV:
                return 1.0, "Sandbox/vvreadout/Readout Note.md\n(1 backlinks)\n", 0
            return 2.0, legacy_out, legacy_rc
        return fake_sh
    def produce(rc, out_text):
        shadow.sh = fake_sh_factory(rc, out_text)
        sys.argv = ["shadow.py", "backlinks", "Readout Note"]
        saved = sys.stdout; sys.stdout = io.StringIO()
        try:
            shadow.main()
        finally:
            sys.stdout = saved; shadow.sh = real_sh
        return json.loads(open(sink).read().strip().splitlines()[-1])
    rec = produce(2, "")
    check("R7a grep exit 2 → legacy-error", rec.get("verdict") == "legacy-error" and rec.get("legacy_exit") == 2, rec)
    check("R7b legacy-error keeps no answer-set diff",
          rec.get("vv_only") is None and rec.get("legacy_only") is None and rec.get("n_legacy") is None, rec)
    rec = produce(1, "")
    check("R7c grep exit 1 (no matches) is compared normally",
          rec.get("verdict") == "vv-superset" and rec.get("vv_only") == ["Sandbox/vvreadout/Readout Note.md"], rec)
    rec = produce(0, "Sandbox/vvreadout/Readout Note.md\n")
    check("R7d matching answer → match", rec.get("verdict") == "match", rec)
finally:
    if not fails:
        shutil.rmtree(SB, ignore_errors=True)
    else:
        print(f"note: fixture kept at {SB} for inspection")
    if _kept and not os.path.exists(SB):
        shutil.move(_kept, SB)
        print(f"note: restored pre-existing {SB}")
    for d in _TMP:
        shutil.rmtree(d, ignore_errors=True)

print(f"\n{len(fails)} failure(s)" if fails else "\nALL PASS")
sys.exit(1 if fails else 0)
