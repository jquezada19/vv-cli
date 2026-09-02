#!/usr/bin/env python3
"""Regressions for the 2026-09-02 shadow-pilot read-out follow-ups.

The week's friction was the AFFORDANCE class — vv was right and unhelpful at
the same time. Each pin below names what motivated it.

Window: 2026-08-26T21:06 → 2026-09-02 (the pilot register's window).
Checks suffixed "(control)" pass on pre-fix code by design; every other check
was watched to fail with its fix reverted (mutation pass, 2026-09-02).

R1  `board FOLDER status open` (space, not `=`) died as a bare Python
    traceback: exit 1, no usage line, no `next:`, no metrics row. Found by
    probing, not by telemetry (the traceback bypasses the logger, so the
    pilot sink holds zero occurrences). Now a usage error with a runnable
    `next:`. R1x: `board ../x` is refused by containment, both engines.
R2  `board FOLDER status=open` still works (control for R1).
R3  `journal` is not a command; one (double-logged) attempt in the week. The typo hint is
    edit-distance only, so `doctor` was never suggested. Alias table.
R4  `read NOTE` with no section pointed at the generic no-args usage line;
    the honest next step is `vv outline NOTE` — a RUNNABLE command, per the
    `next:` contract. 9 of 228 read calls at the read-out moment (8 of 226
    before that day's probing), counted over the register's interactive rows.
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

def vv(*args, env=None, stdin=None):
    return run(VV, *args, env=env, stdin=stdin)

# ---------- fixture (pre-existing content is set aside and RESTORED at exit) ----------
_kept = None
if os.path.lexists(SB):                     # even an EMPTY pre-existing dir is the user's
    # NOT registered in _TMP: the holding dir must outlive the temp sweep so a
    # failing run can never delete the user's data (security seat, round 2).
    _kept = os.path.join(tempfile.mkdtemp(prefix="vv-kept-vvreadout-"), "vvreadout")
    shutil.move(SB, _kept)
    print(f"note: pre-existing {SB} set aside at {_kept}; restored at exit")
    import atexit, signal
    def _restore_on_exit():
        # belt-and-braces for an exit that skips the finally (SystemExit from a
        # signal handler, an exception before the try): if the original is
        # still set aside and SB is free, put it back.
        if os.path.lexists(_kept) and not os.path.lexists(SB):
            shutil.move(_kept, SB)
            print(f"note: restored pre-existing {SB} at exit")
    atexit.register(_restore_on_exit)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))   # let atexit run on SIGTERM
NOTE = "Sandbox/vvreadout/Readout Note.md"

def affordance_checks(tag, runner):
    """The three CLI affordances, through whichever entry `runner` is."""
    # Sandbox notes are excluded from the index by design (~vv_impl.py:844),
    # so the root-folder pins below use a fixture OUTSIDE Sandbox on the
    # indexed arm; see root_checks().
    r = runner("board", "Sandbox/vvreadout", "status", "open")
    check(f"{tag}1a bad board filter exits 1 (control: pre-fix also 1)", r.returncode == 1, f"rc={r.returncode}")
    check(f"{tag}1b bad board filter is a usage error", r.stderr.startswith("usage: board filters are KEY=VALUE"), r.stderr)
    check(f"{tag}1c no traceback", "Traceback" not in r.stderr, r.stderr)
    check(f"{tag}1d names the token and a runnable next step",
          "got status " in r.stderr and r.stderr.rstrip().endswith("— next: vv board Sandbox/vvreadout status=VALUE"), r.stderr)
    r = runner("board", "Sandbox/vvreadout", "sta tus")
    check(f"{tag}1e a token with a space is quoted in the next step", "'sta tus=VALUE'" in r.stderr, r.stderr)
    r = runner("board", "../", "status=open")
    check(f"{tag}1x board is vault-contained", r.returncode == 1 and r.stderr.startswith("escape:"), r.stderr)
    r = runner("board", "Sandbox/vvreadout", "status=open")
    check(f"{tag}2 board KEY=VALUE filter works (control)", r.returncode == 0 and "Readout Note" in r.stdout
          and "Closed Note" not in r.stdout, r.stdout + r.stderr)
    root_checks(tag, runner)

def root_checks(tag, runner):
    """`board .`/`board ""`/`props KEY .` must cover the vault root."""
    for folder in (".", ""):
        r = runner("board", folder, "type=test")
        check(f"{tag}2r board {folder!r} covers the vault root (control: the walk always did)", r.returncode == 0 and "Readout Note" in r.stdout
              and "Closed Note" in r.stdout, (r.stdout + r.stderr)[:300])
    r = runner("props", "type", ".")
    check(f"{tag}2p props KEY . covers the vault root" + (" (control: native never had it)" if tag == "RN" else ""),
          r.returncode == 0 and "\ttest" in r.stdout and "(0 notes" not in r.stdout, (r.stdout + r.stderr)[:300])
    r = runner("orphans", ".")
    check(f"{tag}2o orphans . covers the vault root (was 0)", r.returncode == 0 and "(0 orphans" not in r.stdout
          and "Closed Note" in r.stdout, (r.stdout + r.stderr)[:300])
    r = runner("journal")
    check(f"{tag}3a journal is still not a command (control)", r.returncode == 1 and r.stderr.startswith("usage: unknown command journal"), r.stderr)
    check(f"{tag}3b journal suggests doctor", "(did you mean: doctor)" in r.stderr, r.stderr)
    r = runner("outlien", "x")
    check(f"{tag}3c edit-distance hint unchanged (control)", "(did you mean: outline)" in r.stderr, r.stderr)
    r = runner("read", NOTE)
    check(f"{tag}4a read NOTE alone is a usage error (control)", r.returncode == 1 and r.stderr.startswith("usage: read takes 2 positional args, got 1"), r.stderr)
    check(f"{tag}4b next step is the runnable outline command for THIS note",
          r.stderr.rstrip().endswith("— next: vv outline 'Sandbox/vvreadout/Readout Note.md'"), r.stderr)
    r = runner("read")
    check(f"{tag}4d with no note the next step keeps the placeholder (control: placeholder pre-existed)",
          r.stderr.rstrip().endswith("— next: vv outline NOTE"), r.stderr)
    r = runner("read", NOTE, "First")
    check(f"{tag}4c read NOTE SEC unchanged (control)", r.returncode == 0 and "alpha" in r.stdout, r.stdout + r.stderr)

try:
    # fixture creation is INSIDE the try so a failure here still restores
    shutil.rmtree(SB, ignore_errors=True)
    os.makedirs(SB, exist_ok=True)
    with open(os.path.join(_VAULT, NOTE), "w") as f:
        f.write("---\ntype: test\nstatus: open\n---\n# Readout Note\n\n## First\n\nalpha\n\n## Second\n\nbeta\n")
    with open(os.path.join(SB, "Closed Note.md"), "w") as f:
        f.write("---\ntype: test\nstatus: done\n---\n# Closed Note\n\nbody\n")
    affordance_checks("R", lambda *a: vv(*a, env={"VV_ENGINE": "python"}))
    r = vv("batch", env={"VV_ENGINE": "python"}, stdin=json.dumps({"cmd": "read", "args": [NOTE]}) + "\n")
    check("R4e batch read arity miss carries the same interpolated next-step",
          f"vv outline '{NOTE}'" in r.stdout + r.stderr, (r.stdout + r.stderr)[:300])
    # The INDEXED python arm — the one that returned zero rows for "." (buddy
    # seat, round 3: with VV_JOURNAL_ROOT set and no VV_INDEX_ROOT the index is
    # off, so the plain R2r/R2p above exercise only the walk arm). Sandbox is
    # not indexed, so the fixture lives at the vault root for this block and
    # is removed right after.
    TV = mkdtemp("vv-readout-tv-")            # a throwaway vault: never the real one
    os.makedirs(os.path.join(TV, "Sub"))
    os.makedirs(os.path.join(TV, "graphify-out"))
    with open(os.path.join(TV, "vvreadout-root-fixture.md"), "w") as f:
        f.write("---\ntype: vvreadout-fixture\n---\n# root fixture\n")
    with open(os.path.join(TV, "Sub", "sub-fixture.md"), "w") as f:
        f.write("---\ntype: vvreadout-fixture\n---\n# sub\n")
    with open(os.path.join(TV, "graphify-out", "vvreadout-gen-fixture.md"), "w") as f:
        f.write("---\ntype: vvreadout-fixture\n---\n# gen\n")
    try:
        ienv = {"VV_ENGINE": "python", "VV_INDEX_ROOT": mkdtemp("vv-readout-index-"), "VV_VAULT": TV}
        r = vv("index", "--rebuild", env=ienv)
        check("RI index built for the indexed-arm pins (setup)", r.returncode == 0, r.stderr[-200:])
        for folder in (".", "", "./"):
            r = vv("board", folder, "type=vvreadout-fixture", env=ienv)
            check(f"RI2r indexed board {folder!r} covers the vault root", r.returncode == 0
                  and "vvreadout-root-fixture" in r.stdout, (r.stdout + r.stderr)[:300])
        r = vv("props", "type", ".", env=ienv)
        check("RI2p indexed props KEY . covers the vault root", r.returncode == 0 and "\tvvreadout-fixture" in r.stdout,
              (r.stdout + r.stderr)[:300])
        # retirement: a "." SCOPE saw no DB rows, so a deleted note's row was
        # never retired by a root query (secondary review, round 5). Index a
        # note, delete it, query the root: the stale row must be gone.
        GONE = os.path.join(TV, "Sub", "gone-fixture.md")
        with open(GONE, "w") as f:
            f.write("---\ntype: vvreadout-fixture\n---\n# gone\n")
        vv("board", ".", "type=vvreadout-fixture", env=ienv)      # index it via the root query
        os.remove(GONE)
        r = vv("board", ".", "type=vvreadout-fixture", env=ienv)
        check("RI2x root query retires a deleted note's index row", r.returncode == 0 and "gone-fixture" not in r.stdout
              and "vvreadout-root-fixture" in r.stdout, (r.stdout + r.stderr)[:300])
        r = vv("board", "Sub", "type=vvreadout-fixture", env=ienv)
        check("RI2c control: a real subfolder still filters", r.returncode == 0 and "sub-fixture" in r.stdout
              and "vvreadout-root-fixture" not in r.stdout, (r.stdout + r.stderr)[:300])
        r = vv("props", "type", "Sub/sub-fixture.md", env=ienv)
        check("RI2f props with a FILE scope is refused, not a silent zero", r.returncode == 1
              and r.stderr.startswith("not-found: no such folder"), r.stdout + r.stderr)
        # generated dir parity: graphify-out/ is excluded by the index; the
        # walk and the native engine must exclude it too (three seats, round 3)
        for label, runner in (("indexed", lambda *a: vv(*a, env=ienv)),
                              ("walk", lambda *a: vv(*a, env={"VV_ENGINE": "python", "VV_NO_INDEX": "1", "VV_VAULT": TV})),
                              ("native", lambda *a: subprocess.run([VRUST, *a], capture_output=True, text=True,
                                                                   env=dict(os.environ, VV_VAULT=TV)))):
            if label == "native" and not os.path.exists(VRUST):
                continue
            r = runner("board", ".", "type=vvreadout-fixture")
            check(f"RI2g {label} board . excludes graphify-out/" + (" (control: the index always did)" if label == "indexed" else ""),
                  r.returncode == 0
                  and "vvreadout-root-fixture" in r.stdout and "vvreadout-gen-fixture" not in r.stdout, (r.stdout + r.stderr)[:300])
        # a `..` component: python resolves it; native must fall back, never answer 0
        for label, runner in (("python", lambda *a: vv(*a, env={"VV_ENGINE": "python", "VV_NO_INDEX": "1", "VV_VAULT": TV})),
                              ("native", lambda *a: subprocess.run([VRUST, *a], capture_output=True, text=True,
                                                                   env=dict(os.environ, VV_VAULT=TV)))):
            if label == "native" and not os.path.exists(VRUST):
                continue
            r = runner("orphans", "Sub/..")
            check(f"RI2d {label} orphans Sub/.. resolves to the root", r.returncode == 0
                  and "vvreadout-root-fixture" in r.stdout and "(0 orphans" not in r.stdout, (r.stdout + r.stderr)[:300])
    finally:
        pass   # TV is in _TMP; removed at exit
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
        # unbuildable args on a grep analog: argv[0] recovered → exit 1 is scored
        dict(base, op="props", args=[], legacy_ms=5.0, legacy_bytes=0, legacy_exit=1,
             verdict="differ", vv_only=["p"], legacy_only=[]),
        # unbuildable args on a strict analog (awk): exit 1 is a harness error
        dict(base, op="head", args=[], legacy_ms=5.0, legacy_bytes=0, legacy_exit=1,
             verdict="differ", vv_only=["h"], legacy_only=[]),
        # a builder-less (vv-only) op carrying a stray non-zero exit: harness error
        dict(base, op="deadends", args=[], legacy_ms=5.0, legacy_bytes=0, legacy_exit=2,
             verdict="differ", vv_only=["d"], legacy_only=[]),
        # the SAME disagreement recorded twice (a re-run): one distinct case
        dict(base, op="backlinks", args=["A.md"], legacy_ms=40.0, legacy_bytes=0, legacy_exit=0,
             verdict="differ", vv_only=["C.md"], legacy_only=["D.md"]),
        # a disagreement whose op has NO op-level ruling — only a case ruling
        # (pins the dedupe/unadj expression, code-review seat round 2)
        dict(base, op="props", args=["status"], legacy_ms=40.0, legacy_bytes=0, legacy_exit=0,
             verdict="differ", vv_only=["x"], legacy_only=[]),
        # a record from an OLDER harness: must be set aside, never pooled
        dict(base, hv=HARNESS_VERSION - 1, op="outline", args=["Old.md"], legacy_ms=30.0,
             legacy_bytes=9999, legacy_exit=0, verdict="differ", vv_only=["z"], legacy_only=[]),
        # a row the ROUND-1 producer would have written: verdict legacy-error
        # on a grep exit 1. The exit code wins — it is an answer, scored.
        dict(base, op="backlinks", args=["R1.md"], legacy_ms=5.0, legacy_bytes=0, legacy_exit=1,
             verdict="legacy-error", vv_only=None, legacy_only=None),
        # a malformed adjudication row: skipped, never fatal
        {"kind": "adjudication", "who": "vv-correct", "reason": "no op field"},
        # a disagreement whose ONLY ruling carries an unknown `who`
        dict(base, op="head", args=["U.md"], legacy_ms=5.0, legacy_bytes=0, legacy_exit=0,
             verdict="differ", vv_only=["u"], legacy_only=[]),
        {"kind": "adjudication", "op": "head", "args": ["U.md"], "who": "sure", "reason": "?"},
        # a ruling made under an older harness: honoured but labelled
        {"kind": "adjudication", "hv": HARNESS_VERSION - 1, "op": "tags", "who": "vv-correct", "reason": "old instrument"},
    ]
    def write_sink(rs):
        with open(sink, "w") as f:
            for r_ in rs:
                f.write(json.dumps(r_) + "\n")
    write_sink(rows)
    env = {"VV_SHADOW_SINK": sink}
    r = run(SHADOW, "--adjudicate", "backlinks", "vv-correct", "grep misses alias links", env=env)
    check("R6a op-level adjudication still accepted (control)", r.returncode == 0, r.stdout + r.stderr)
    r = run(SHADOW, "--adjudicate", "backlinks", "both-defensible", "E has a duplicate basename", "--", "E.md", env=env)
    check("R6b case adjudication accepted", r.returncode == 0, r.stdout + r.stderr)
    last = json.loads(open(sink).read().strip().splitlines()[-1])
    check("R6c case adjudication records its args and harness version",
          last.get("kind") == "adjudication" and last.get("args") == ["E.md"] and last.get("hv") == HARNESS_VERSION, last)
    r = run(SHADOW, "--adjudicate", "backlinks", "vv-correct", "trailing separator", "--", env=env)
    check("R6g `--` with no case args is refused", r.returncode != 0 and "no case args" in (r.stdout + r.stderr), r.stdout + r.stderr)
    r = run(SHADOW, "--adjudicate", "backlinks", "vv-correct", "a", "--", "b", "--", "X.md", env=env)
    check("R6h more than one `--` is refused as ambiguous", r.returncode != 0 and "ambiguous" in (r.stdout + r.stderr), r.stdout + r.stderr)
    r = run(SHADOW, "--adjudicate", "props", "vv-correct", "grep sees quoted values", "--", "status", env=env)
    check("R6j case-only ruling accepted", r.returncode == 0, r.stdout + r.stderr)
    r = run(SHADOW, "--adjudicate", "x", "vv-correct", "y", env={"VV_SHADOW_SINK": sink + ".txt"})
    check("R6k VV_SHADOW_SINK must be .jsonl", r.returncode != 0 and "must name a .jsonl" in (r.stdout + r.stderr), r.stdout + r.stderr)
    check("R6l shadow prints the override banner", "VV_SHADOW_SINK override" in run(SHADOW, "--adjudicate", "x", "vv-correct", "y", env=env).stderr)

    r = run(SHADOW_REPORT, "2026-09-01", "2026-09-01", env=env)
    out = r.stdout + r.stderr
    check("R5a report runs (control)", r.returncode == 0, out)
    check("R5b grep exit 2 counted as a harness error", "harness errors: 3" in out and "[links] A.md legacy_exit=2" in out, out)
    check("R5c harness error not listed as a disagreement", "[links]" not in out.split("disagreements:")[-1], out)
    check("R5d grep exit 1 is an answer: scored, not a harness error",
          "paired reads: 9" in out and "[backlinks] Z.md → vv-superset" in out, out)
    check("R5e byte totals exclude the failed pair on BOTH sides",
          "vv 900 B vs old way 1,900 B" in out, out)
    check("R5g funnel shows the split", "reads=12 -> scored=9" in out, out)
    check("R5k stale legacy-error verdict on a grep exit 1 is not a harness error and not a measured difference",
          "unscored: 1" in out and "[backlinks] R1.md" in out.split("unscored:")[1].split("\n\n")[0]
          and "R1.md → legacy-error" not in out and "harness errors: 3" in out, out)
    check("R6o unknown `who` does not adjudicate", "[head] U.md → differ  (UNADJUDICATED)" in out, out)
    check("R5i older-harness record set aside, not pooled (control: pre-existing)",
          "set aside 1 record(s)" in out and "Old.md" not in out and "9,999" not in out, out)
    check("R5j report prints the override banner", "VV_SHADOW_SINK override" in out, out)
    check("R6d exact ruling labelled as a case ruling", "E.md → differ  (both-defensible, case ruling)" in out, out)
    check("R6e op-level ruling labelled as reused", "A.md → differ  (vv-correct, op-level ruling reused)" in out, out)
    check("R6f only the unknown-`who` case is left unadjudicated",
          out.count("UNADJUDICATED") == 3 and "2 disagreement(s) UNADJUDICATED" in out, out)
    check("R6i malformed adjudication row skipped, not fatal (control)", "Traceback" not in out, out)
    check("R6m case-only ruling closes its case", "[props] status → differ  (vv-correct, case ruling)" in out, out)
    check("R6p repeated disagreement counted once as a case", "disagreements: 7 (6 distinct cases)" in out, out)
    check("R5l unbuildable args on a grep analog: exit 1 still scored", "[props]  → differ" in out, out)
    check("R5m unbuildable args on a strict analog: exit 1 is a harness error", "[head]  legacy_exit=1" in out, out)
    check("R5n builder-less op with a stray exit: harness error", "[deadends]  legacy_exit=2" in out, out)
    check("R5o harness error count includes both", "harness errors: 3" in out, out)

    # R5 positive control: with the failed pair's exit code cleared the same
    # record must come back as a disagreement — the exclusion keys on the
    # exit code, not on something incidental to the fixture.
    rows[0]["legacy_exit"] = 0
    write_sink(rows)
    r = run(SHADOW_REPORT, "2026-09-01", "2026-09-01", env=env)
    out = r.stdout + r.stderr
    check("R5f control: same record with exit 0 IS a disagreement",
          "harness errors: 2" in out and "[links] A.md → vv-superset" in out and "paired reads: 10" in out
          and "vv 1,000 B vs old way 2,400 B" in out, out)
    # a ruling made under an older harness is honoured but labelled
    rows.append(dict(base, op="tags", args=[], legacy_ms=10.0, legacy_bytes=10, legacy_exit=0,
                     verdict="differ", vv_only=["t"], legacy_only=[]))
    write_sink(rows)
    r = run(SHADOW_REPORT, "2026-09-01", "2026-09-01", env=env)
    out = r.stdout + r.stderr
    check("R6n cross-version ruling is labelled",
          f"[tags]  → differ  (vv-correct, op-level ruling reused (ruled under harness v{HARNESS_VERSION - 1}))" in out, out)
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
    check("R7d matching answer → match (control)", rec.get("verdict") == "match", rec)
    # R7e — a legacy BUILDER that raises (missing positional) is recorded as a
    # harness error, never a traceback: round 2 lost this record (3 seats).
    r = run(SHADOW, "read", env=env)
    rec = json.loads(open(sink).read().strip().splitlines()[-1])
    check("R7e builder exception is recorded, not a traceback",
          "Traceback" not in r.stderr and rec.get("op") == "read" and rec.get("legacy_exit") == -1
          and rec.get("verdict") == "legacy-error" and "index out of range" in rec.get("legacy_error", ""),
          r.stderr[-200:] + str(rec))
finally:
    def set_aside_fixture():
        """Move the test fixture out of SB; never into or through a symlink.
        SB-failed-fixture is a name this suite owns: it holds only the previous
        failing run's fixture and is replaced, never set aside."""
        aside = SB + "-failed-fixture"
        if os.path.islink(aside):
            os.unlink(aside)
        shutil.rmtree(aside, ignore_errors=True)
        if os.path.exists(aside):        # rmtree failed silently: refuse to move onto it
            raise RuntimeError(f"cannot clear {aside}")
        shutil.move(SB, aside)
    try:
        if not fails:
            shutil.rmtree(SB, ignore_errors=True)
        else:
            print(f"note: fixture kept at {SB}-failed-fixture for inspection")
        if os.path.lexists(SB):
            # Never move the original INTO a leftover dir (POSIX move nests it
            # as SB/vvreadout — buddy seat, round 3). Set the leftover aside.
            set_aside_fixture()
    except Exception as e:                                             # noqa: BLE001
        print(f"note: fixture teardown failed ({e}); restoring the original regardless")
    if _kept:
        if os.path.lexists(SB):
            print(f"note: {SB} still present; original left at {_kept}")
        else:
            shutil.move(_kept, SB)      # unconditional: pass or fail
            try:
                os.rmdir(os.path.dirname(_kept))   # the holding dir's now-empty parent
            except OSError:
                pass
            print(f"note: restored pre-existing {SB}")
    for d in _TMP:
        shutil.rmtree(d, ignore_errors=True)

print(f"\n{len(fails)} failure(s)" if fails else "\nALL PASS")
sys.exit(1 if fails else 0)
