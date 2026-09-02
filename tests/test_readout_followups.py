#!/usr/bin/env python3
"""Regressions for the 2026-09-02 shadow-pilot read-out follow-ups.

The week's friction was the AFFORDANCE class — vv was right and unhelpful at
the same time. Each pin below names what motivated it.

Window: 2026-08-26T21:06 → 2026-09-02 (the pilot register's window).
Checks suffixed "(control…)" pass on pre-fix code by design and "(setup)"
checks only gate what follows; every other check was watched to fail with its
fix reverted (a mutation pass). The suite runs in a throwaway vault.

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

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VV = os.path.join(REPO, "src", "vv.py")
VRUST = os.path.join(REPO, "vrust", "target", "release", "vrust")
SHADOW = os.path.join(REPO, "bench", "shadow.py")
SHADOW_REPORT = os.path.join(REPO, "bench", "shadow_report.py")

_TMP = []   # every temp dir this suite makes; removed at exit
def mkdtemp(prefix):
    d = tempfile.mkdtemp(prefix=prefix); _TMP.append(d); return d

# A THROWAWAY vault. Nothing here asserts anything about real notes — only
# error grammar and folder scoping — so the suite never touches the user's
# vault. (An earlier version grew a lock, a sentinel, a holding dir, an
# exit hook and signal handlers to protect a real-vault fixture; the hazard
# was the fixture's location, not its teardown. Deleting the hazard beats
# guarding it.)
_VAULT = mkdtemp("vv-readout-vault-")
os.environ["VV_VAULT"] = _VAULT          # every vv child, and bench/shadow.py at import
os.environ["VV_INDEX_ROOT"] = mkdtemp("vv-readout-index-")   # both engines' caches, for EVERY child
_REAL_CACHE = os.path.expanduser("~/.cache/vv/index")
import hashlib
def _cache_key(vault):
    """The native cache file name for a vault: sha256(canonical path)[:16]."""
    return hashlib.sha256(os.path.realpath(vault).encode()).hexdigest()[:16] + ".vvidx"
_OUR_VAULTS = [_VAULT]                      # every vault this suite drives natively; TV is appended later
SB = os.path.join(_VAULT, "Sandbox/vvreadout")
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

_native_warned = []
def native_available():
    """The native binary, or a LOUD skip: a suite that silently skips its native
    arms passes while proving nothing about the engine on PATH.
    run_tests.sh builds the binary first, so the gate never skips."""
    if os.path.exists(VRUST):
        return True
    if not _native_warned:
        print(f"SKIP native pins: {VRUST} not built (run `cargo build --release` in vrust/)")
        _native_warned.append(1)
    return False

def vv(*args, env=None, stdin=None):
    return run(VV, *args, env=env, stdin=stdin)

NOTE = "Sandbox/vvreadout/Readout Note.md"

def affordance_checks(tag, runner):
    """The three CLI affordances, through whichever entry `runner` is."""
    # The indexed-arm pins run in their own throwaway vault (the RI block);
    # these run through whichever entry `runner` is, with the index off.
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
    os.makedirs(SB, exist_ok=True)
    with open(os.path.join(_VAULT, NOTE), "w") as f:
        f.write("---\ntype: test\nstatus: open\n---\n# Readout Note\n\n## First\n\nalpha\n\n## Second\n\nbeta\n")
    with open(os.path.join(SB, "Closed Note.md"), "w") as f:
        f.write("---\ntype: test\nstatus: done\n---\n# Closed Note\n\nbody\n")
    affordance_checks("R", lambda *a: vv(*a, env={"VV_ENGINE": "python"}))
    r = vv("batch", env={"VV_ENGINE": "python"}, stdin=json.dumps({"cmd": "read", "args": [NOTE]}) + "\n")
    check("R4e batch read arity miss carries the same interpolated next-step",
          f"vv outline '{NOTE}'" in r.stdout + r.stderr, (r.stdout + r.stderr)[:300])
    # The INDEXED python arm — the one that returned zero rows for "." (with
    # VV_JOURNAL_ROOT set and no VV_INDEX_ROOT the index is off, so the plain
    # R2r/R2p above exercise only the walk arm). Sandbox is
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
        native_env = dict(os.environ, VV_VAULT=TV, VV_INDEX_ROOT=ienv["VV_INDEX_ROOT"])  # native cache in the temp dir too
        _OUR_VAULTS.append(TV)
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
        # never retired by a root query. Index a
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
        check("RI2c a real subfolder still filters (control)", r.returncode == 0 and "sub-fixture" in r.stdout
              and "vvreadout-root-fixture" not in r.stdout, (r.stdout + r.stderr)[:300])
        for label, runner in (("python", lambda *a: vv(*a, env=ienv)),
                              ("native", lambda *a: subprocess.run([VRUST, *a], capture_output=True, text=True, env=native_env))):
            if label == "native" and not native_available():
                continue
            r = runner("props", "type", "Sub/sub-fixture.md")
            check(f"RI2f {label} props with a FILE scope is refused, not a silent zero" + (" (control: python pre-existed)" if label == "python" else ""), r.returncode == 1
                  and r.stderr.startswith("not-found: no such folder"), r.stdout + r.stderr)
            r = runner("orphans", "NoSuchFolder")
            check(f"RI2n {label} orphans on a missing folder is refused, not a clean zero", r.returncode == 1
                  and r.stderr.startswith("not-found: no such folder"), r.stdout + r.stderr)
        r = vv("orphans", "Sub", env={"VV_ENGINE": "python", "VV_NO_INDEX": "1", "VV_VAULT": TV + "//"})
        check("RI2v a non-normalised VV_VAULT (trailing //) still finds orphans in a subfolder on the walk arm (was 0; control: the shared VAULT form fixed it, the source normpath is defence in depth)",
              r.returncode == 0 and "sub-fixture" in r.stdout, (r.stdout + r.stderr)[:300])
        if native_available():
            subprocess.run([VRUST, "backlinks", "vvreadout-root-fixture"], capture_output=True, text=True, env=native_env)
            idx_files = [f for f in os.listdir(ienv["VV_INDEX_ROOT"]) if f.endswith(".vvidx")]
            check("RI2i native cache lands in VV_INDEX_ROOT", bool(idx_files), os.listdir(ienv["VV_INDEX_ROOT"])[:5])
            noidx = mkdtemp("vv-readout-noidx-")
            r = subprocess.run([VRUST, "backlinks", "vvreadout-root-fixture"], capture_output=True, text=True,
                               env=dict(native_env, VV_INDEX_ROOT=noidx, VV_NO_INDEX="1"))
            check("RI2j VV_NO_INDEX writes no native cache (and the command still answers)",
                  r.returncode == 0 and not os.listdir(noidx), os.listdir(noidx) or r.stderr[-120:])
            # empty knobs mean UNSET (python's `or`): an empty index root must not
            # drop the cache into the CWD; an empty VV_NO_INDEX keeps the cache on
            emptycwd = mkdtemp("vv-readout-emptycwd-")
            emptyroot = mkdtemp("vv-readout-emptyroot-")
            fakehome = mkdtemp("vv-readout-home-")    # "unset" means the HOME cache: point HOME at a temp dir
            r = subprocess.run([VRUST, "backlinks", "vvreadout-root-fixture"], capture_output=True, text=True,
                               env=dict(native_env, VV_INDEX_ROOT="", HOME=fakehome), cwd=emptycwd)
            check("RI2m an empty VV_INDEX_ROOT means unset: nothing in the CWD, the cache under HOME",
                  r.returncode == 0 and not os.listdir(emptycwd) and os.path.isdir(os.path.join(fakehome, ".cache/vv/index")),
                  (os.listdir(emptycwd), os.path.exists(os.path.join(fakehome, ".cache/vv/index")), r.stderr[-120:]))
            r = subprocess.run([VRUST, "backlinks", "vvreadout-root-fixture"], capture_output=True, text=True,
                               env=dict(native_env, VV_INDEX_ROOT=emptyroot, VV_NO_INDEX=""))
            check("RI2m2 an empty VV_NO_INDEX keeps the native cache on", r.returncode == 0 and bool(os.listdir(emptyroot)),
                  os.listdir(emptyroot) or r.stderr[-120:])
            r = subprocess.run([VRUST, "orphans", "."], capture_output=True, text=True,
                               env=dict(native_env, VV_VAULT=TV + "/"))
            check("RI2t native orphans . under a trailing-slash VV_VAULT (was a silent 0)",
                  r.returncode == 0 and "vvreadout-root-fixture" in r.stdout, (r.stdout + r.stderr)[:300])
            # VV_VAULT="<TV>/Elsewhere/.." — lexically TV; resolved through the
            # symlink it is the sibling temp dir's PARENT (the system temp dir),
            # which holds no fixture. Both engines must answer for TV.
            other = mkdtemp("vv-readout-other-")
            os.symlink(other, os.path.join(TV, "Elsewhere"))
            dotdot = os.path.join(TV, "Elsewhere", "..")
            rp = vv("orphans", ".", env={"VV_ENGINE": "python", "VV_NO_INDEX": "1", "VV_VAULT": dotdot})
            rn = subprocess.run([VRUST, "orphans", "."], capture_output=True, text=True, env=dict(native_env, VV_VAULT=dotdot))
            check("RI2u a `..` through a symlink in VV_VAULT resolves lexically on both engines",
                  "vvreadout-root-fixture" in rp.stdout and rp.stdout == rn.stdout, (rp.stdout + "|" + rn.stdout + rn.stderr)[:300])
        # an explicitly named SKIP_DIRS member as the scope: every arm answers it
        for label, runner in (("indexed", lambda *a: vv(*a, env=ienv)),
                              ("walk", lambda *a: vv(*a, env={"VV_ENGINE": "python", "VV_NO_INDEX": "1", "VV_VAULT": TV})),
                              ("native", lambda *a: subprocess.run([VRUST, *a], capture_output=True, text=True, env=native_env))):
            if label == "native" and not native_available():
                continue
            r = runner("props", "type", "graphify-out")
            check(f"RI2h {label} props on an explicit graphify-out scope answers it (walk arm was 0)" + (" (control: this arm always did)" if label != "walk" else ""),
                  r.returncode == 0 and "\tvvreadout-fixture" in r.stdout, (r.stdout + r.stderr)[:300])
        os.symlink(os.path.join(TV, "Sub"), os.path.join(TV, "Link"))
        for label, runner in (("python", lambda *a: vv(*a, env=ienv)),
                              ("native", lambda *a: subprocess.run([VRUST, *a], capture_output=True, text=True, env=native_env))):
            if label == "native" and not native_available():
                continue
            r = runner("orphans", "Link")
            check(f"RI2s {label} orphans through an in-vault symlink resolves the folder (was 0)" + (" (control: python pre-existed)" if label == "python" else ""),
                  r.returncode == 0 and "sub-fixture" in r.stdout, (r.stdout + r.stderr)[:300])
        # generated dir parity: graphify-out/ is excluded by the index; the
        # walk and the native engine must exclude it too
        for label, runner in (("indexed", lambda *a: vv(*a, env=ienv)),
                              ("walk", lambda *a: vv(*a, env={"VV_ENGINE": "python", "VV_NO_INDEX": "1", "VV_VAULT": TV})),
                              ("native", lambda *a: subprocess.run([VRUST, *a], capture_output=True, text=True, env=native_env))):
            if label == "native" and not native_available():
                continue
            r = runner("board", ".", "type=vvreadout-fixture")
            check(f"RI2g {label} board . excludes graphify-out/" + (" (control: the index always did)" if label == "indexed" else ""),
                  r.returncode == 0
                  and "vvreadout-root-fixture" in r.stdout and "vvreadout-gen-fixture" not in r.stdout, (r.stdout + r.stderr)[:300])
        # a `..` component: python resolves it; native must fall back, never answer 0
        for label, runner in (("python", lambda *a: vv(*a, env={"VV_ENGINE": "python", "VV_NO_INDEX": "1", "VV_VAULT": TV})),
                              ("native", lambda *a: subprocess.run([VRUST, *a], capture_output=True, text=True, env=native_env))):
            if label == "native" and not native_available():
                continue
            r = runner("orphans", "Sub/..")
            check(f"RI2d {label} orphans Sub/.. resolves to the root" + (" (control)" if label == "python" else ""), r.returncode == 0
                  and "vvreadout-root-fixture" in r.stdout and "(0 orphans" not in r.stdout, (r.stdout + r.stderr)[:300])
        # graph commands never enter SKIP_DIRS: those notes are outside the
        # link graph, so "orphans of graphify-out" has no answer. It used to
        # print a silent 0 — the affordance class this branch closes; now it
        # refuses with a next-step, both engines.
        # board/props DO answer for an explicitly named skip dir.
        for label, runner in (("python", lambda *a: vv(*a, env={"VV_ENGINE": "python", "VV_NO_INDEX": "1", "VV_VAULT": TV})),
                              ("native", lambda *a: subprocess.run([VRUST, *a], capture_output=True, text=True, env=native_env))):
            if label == "native" and not native_available():
                continue
            r = runner("orphans", "graphify-out")
            check(f"RI2k {label} orphans on a named skip dir refuses with a next-step (was a silent 0)",
                  r.returncode == 1 and r.stderr.startswith("refused: graphify-out is outside the link graph")
                  and "next: vv board graphify-out" in r.stderr, (r.stdout + r.stderr)[:200])
        # a RELATIVE VV_VAULT of "." (cd into the vault): python's normpath keeps
        # ".", the native lexical normalise once yielded "" and every raw walk
        # read_dir("") answered a silent zero
        for label, runner in (("python", lambda *a: subprocess.run([sys.executable, VV, *a], capture_output=True, text=True,
                                                                   env=dict(os.environ, VV_ENGINE="python", VV_VAULT=".", VV_NO_INDEX="1"), cwd=TV)),
                              ("native", lambda *a: subprocess.run([VRUST, *a], capture_output=True, text=True,
                                                                   env=dict(os.environ, VV_VAULT="."), cwd=TV))):
            if label == "native" and not native_available():
                continue
            r = runner("props", "type")
            check(f"RI2w {label} VV_VAULT=. (relative) still walks the vault (native was a silent 0)",
                  r.returncode == 0 and "\tvvreadout-fixture" in r.stdout, (r.stdout + r.stderr)[:200])
    finally:
        pass   # TV is in _TMP; removed at exit
    if native_available():
        # The native entry itself: every one of these must Fallback/exec to
        # python and print the identical text (the python launcher alone
        # never exercises the binary).
        def native(*a):
            return subprocess.run([VRUST, *a], capture_output=True, text=True,
                                  env=dict(os.environ, VV_VAULT=_VAULT))
        affordance_checks("RN", native)
    else:
        pass   # native_available() already printed the SKIP line

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
        # (pins the dedupe/unadj expression)
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
        # an op whose ONLY pair is unscored (stale legacy-error verdict, grep exit 1)
        dict(base, op="search", args=["zzz"], legacy_ms=5.0, legacy_bytes=0, legacy_exit=1,
             verdict="legacy-error", vv_only=None, legacy_only=None),   # grep analog: exit 1 is an answer
        # a disagreement whose only ruling has a valid `who` but NO reason
        dict(base, op="show", args=["N.md"], legacy_ms=5.0, legacy_bytes=0, legacy_exit=0,
             verdict="differ", vv_only=["n"], legacy_only=[]),
        {"kind": "adjudication", "op": "show", "args": ["N.md"], "who": "vv-correct"},
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
    check("R6b case adjudication accepted (setup)", r.returncode == 0, r.stdout + r.stderr)
    last = json.loads(open(sink).read().strip().splitlines()[-1])
    check("R6c case adjudication records its args and harness version",
          last.get("kind") == "adjudication" and last.get("args") == ["E.md"] and last.get("hv") == HARNESS_VERSION, last)
    r = run(SHADOW, "--adjudicate", "backlinks", "vv-correct", "trailing separator", "--", env=env)
    check("R6g `--` with no case args is refused", r.returncode != 0 and "no case args" in (r.stdout + r.stderr), r.stdout + r.stderr)
    r = run(SHADOW, "--adjudicate", "backlinks", "vv-correct", "a", "--", "b", "--", "X.md", env=env)
    check("R6h more than one `--` is refused as ambiguous", r.returncode != 0 and "ambiguous" in (r.stdout + r.stderr), r.stdout + r.stderr)
    r = run(SHADOW, "--adjudicate", "props", "vv-correct", "grep sees quoted values", "--", "status", env=env)
    check("R6j case-only ruling accepted (setup)", r.returncode == 0, r.stdout + r.stderr)
    r = run(SHADOW, "--adjudicate", "x", "vv-correct", "y", env={"VV_SHADOW_SINK": sink + ".txt"})
    check("R6k VV_SHADOW_SINK must be .jsonl", r.returncode != 0 and "must name a .jsonl" in (r.stdout + r.stderr), r.stdout + r.stderr)
    check("R6l shadow prints the override banner", "VV_SHADOW_SINK override" in run(SHADOW, "--adjudicate", "x", "vv-correct", "y", env=env).stderr)

    r = run(SHADOW_REPORT, "2026-09-01", "2026-09-01", env=env)
    out = r.stdout + r.stderr
    check("R5a report runs (control)", r.returncode == 0, out)
    check("R5b grep exit 2 counted as a harness error", "harness errors: 3" in out and "[links] A.md legacy_exit=2" in out, out)
    check("R5c harness error not listed as a disagreement", "[links]" not in out.split("disagreements:")[-1], out)
    check("R5d grep exit 1 is an answer: scored, not a harness error",
          "paired reads: 11" in out and "[backlinks] Z.md → vv-superset" in out, out)
    check("R5e byte totals exclude the failed pair on BOTH sides",
          "vv 1,100 B vs old way 1,900 B" in out, out)
    check("R5g funnel shows the split (unscored is paired, not scored)", "reads=14 -> scored=9" in out, out)
    _bl = [l for l in out.splitlines() if l.startswith("backlinks")]
    check("R5p unscored rows leave the agree/differ denominator (the backlinks row itself says so)",
          bool(_bl) and "1 unscored" in _bl[0] and "0/0 agree" not in out, _bl[:1] or out)
    check("R5q an op with nothing scored says so", "nothing scored · 1 unscored" in out, out)
    check("R5r the funnel line reconciles reads and scored", "reads − scored = 3 harness error(s) + 2 unscored" in out, out)
    check("R5k stale legacy-error verdict on a grep exit 1 is not a harness error and not a measured difference",
          "unscored: 2" in out and "[backlinks] R1.md" in out.split("unscored:")[1].split("\n\n")[0]
          and "R1.md → legacy-error" not in out and "harness errors: 3" in out, out)
    check("R6o unknown `who` does not adjudicate", "[head] U.md → differ  (UNADJUDICATED)" in out, out)
    check("R6q a ruling without a reason does not adjudicate", "[show] N.md → differ  (UNADJUDICATED)" in out, out)
    check("R5i older-harness record set aside, not pooled (control: pre-existing)",
          "set aside 1 record(s)" in out and "Old.md" not in out and "9,999" not in out, out)
    check("R5j report prints the override banner", "VV_SHADOW_SINK override" in out, out)
    check("R6d exact ruling labelled as a case ruling", "E.md → differ  (both-defensible, case ruling)" in out, out)
    check("R6e op-level ruling labelled as reused", "A.md → differ  (vv-correct, op-level ruling reused)" in out, out)
    check("R6f only the unknown-`who` case is left unadjudicated",
          out.count("UNADJUDICATED") == 4 and "3 disagreement(s) UNADJUDICATED" in out, out)
    check("R6i malformed adjudication row skipped, not fatal (control)", "Traceback" not in out, out)
    check("R6m case-only ruling closes its case", "[props] status → differ  (vv-correct, case ruling)" in out, out)
    check("R6p repeated disagreement counted once as a case", "disagreements: 8 (7 distinct cases)" in out, out)
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
    check("R5f same record with exit 0 IS a disagreement (positive control: a real pin)",
          "harness errors: 2" in out and "[links] A.md → vv-superset" in out and "paired reads: 12" in out
          and "vv 1,200 B vs old way 2,400 B" in out, out)
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
    # harness error, never a traceback: an earlier version lost this record.
    r = run(SHADOW, "read", env=env)
    rec = json.loads(open(sink).read().strip().splitlines()[-1])
    check("R7e builder exception is recorded, not a traceback",
          "Traceback" not in r.stderr and rec.get("op") == "read" and rec.get("legacy_exit") == -1
          and rec.get("verdict") == "legacy-error" and "index out of range" in rec.get("legacy_error", ""),
          r.stderr[-200:] + str(rec))
finally:
    for d in _TMP:
        shutil.rmtree(d, ignore_errors=True)

# hermetic: only THIS suite's vault keys are asserted absent (another process
# may legitimately write its own vault's cache while we run)
_leaked = [k for k in map(_cache_key, _OUR_VAULTS) if os.path.exists(os.path.join(_REAL_CACHE, k))]
check("RI2l the WHOLE suite wrote none of its own vaults' caches to ~/.cache/vv/index", not _leaked, _leaked)
print(f"\n{len(fails)} failure(s)" if fails else "\nALL PASS")
sys.exit(1 if fails else 0)
