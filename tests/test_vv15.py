#!/usr/bin/env python3
"""v1.5 tests: rename/move corpus, impact, show continuation, deadends, lint --quick, doctor."""
import subprocess, sys, os, shutil, glob
_VAULT = os.environ.get("VV_VAULT") or os.path.expanduser("~/Documents/Obsidian Vault")

SB = os.path.join(_VAULT, "Sandbox/vv15test")
VV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "vv.py")

def run(*args, stdin=None):
    return subprocess.run([sys.executable, VV, *args], capture_output=True, text=True, input=stdin)


# --- suite safety (review 2026-08-26) ---------------------------------------
# 1. Never delete pre-existing Sandbox content: a non-empty fixture dir is MOVED
#    aside, not removed. 2. Journals go to a temp root so real pending recovery
#    journals can't be touched. 3. On failure the fixture dir is KEPT as evidence.
import tempfile, datetime as _dt
def fresh_fixture(path):
    # pre-existing content is preserved OUTSIDE the vault: an aside-dir inside
    # Sandbox would poison later duplicate-basename tests (found 2026-08-26)
    if os.path.isdir(path) and os.listdir(path):
        keep = tempfile.mkdtemp(prefix="vv-kept-" + os.path.basename(path) + "-")
        shutil.move(path, os.path.join(keep, os.path.basename(path)))
        print(f"note: pre-existing {path} moved to {keep}")
    shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path, exist_ok=True)
_JR = tempfile.mkdtemp(prefix="vv-test-journals-")
os.environ["VV_JOURNAL_ROOT"] = _JR
# -----------------------------------------------------------------------------

fails = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{str(detail)[:140]}]" if detail and not cond else ""))
    if not cond: fails.append(name)

fresh_fixture(SB)
os.makedirs(f"{SB}/sub", exist_ok=True)

# corpus: every typed link form pointing at "RN Old Note"
open(f"{SB}/RN Old Note.md", "w").write("---\ntype: test\n---\n# RN Old Note\ntarget content\n")
open(f"{SB}/Linker A.md", "w").write(
    "---\ntype: test\nrelated:\n  - \"[[RN Old Note]]\"\n---\n"
    "Bare [[RN Old Note]] and alias [[RN Old Note|the old one]] and heading [[RN Old Note#Section]].\n"
    "Block ref [[RN Old Note#^abc123]] and embed ![[RN Old Note]].\n"
    "Path form [[Sandbox/vv15test/RN Old Note]].\n"
    "Md link [text](Sandbox/vv15test/RN%20Old%20Note.md).\n"
    "```\nfenced [[RN Old Note]] must not change\n```\n"
    "inline `[[RN Old Note]]` must not change\n")
open(f"{SB}/sub/Linker B.md", "w").write("See [[RN Old Note]].\nUnrelated [[Some Other Note]].\n")

# R1: impact counts
r = run("impact", "Sandbox/vv15test/RN Old Note.md")
check("R1 impact counts files", "incoming-link files: 2" in r.stdout, r.stdout)

# R2: dry-run plan by default, no writes
before = open(f"{SB}/Linker A.md").read()
r = run("rename", "Sandbox/vv15test/RN Old Note.md", "RN New Note")
check("R2 dry-run default", "dry-run" in r.stdout and open(f"{SB}/Linker A.md").read() == before, r.stdout)

# R3: apply rename — all active forms rewritten, inert forms untouched
r = run("rename", "Sandbox/vv15test/RN Old Note.md", "RN New Note", "--apply")
a = open(f"{SB}/Linker A.md").read()
b = open(f"{SB}/sub/Linker B.md").read()
check("R3a apply succeeded + verified", "verification clean" in r.stdout, r.stdout + r.stderr)
check("R3b bare link", "[[RN New Note]] and alias" in a)
check("R3c alias preserved", "[[RN New Note|the old one]]" in a)
check("R3d heading fragment", "[[RN New Note#Section]]" in a)
check("R3e block fragment", "[[RN New Note#^abc123]]" in a)
check("R3f embed", "![[RN New Note]]" in a)
check("R3g path form", "[[Sandbox/vv15test/RN New Note]]" in a)
check("R3h md link re-encoded", "(Sandbox/vv15test/RN%20New%20Note.md)" in a)
check("R3i yaml related rewritten", '"[[RN New Note]]"' in a.split("---")[1])
check("R3j fenced untouched", "fenced [[RN Old Note]] must not change" in a)
check("R3k inline-code untouched", "inline `[[RN Old Note]]` must not change" in a)
check("R3l second file rewritten", "[[RN New Note]]" in b and "[[Some Other Note]]" in b)
check("R3m file actually renamed", os.path.exists(f"{SB}/RN New Note.md") and not os.path.exists(f"{SB}/RN Old Note.md"))
check("R3n no pending journal", not glob.glob(os.path.join(_JR, "*", "*")))

# R4: rename onto existing basename refused
open(f"{SB}/RN Taken.md", "w").write("x\n")
r = run("rename", "Sandbox/vv15test/RN New Note.md", "RN Taken")
check("R4 collision refused", r.returncode == 1 and "[[RN New Note]]" in open(f"{SB}/Linker A.md").read())

# R5: ambiguous source basename blocks rename
os.makedirs(f"{SB}/dup", exist_ok=True)
open(f"{SB}/dup/RN New Note.md", "w").write("impostor\n")
r = run("rename", "Sandbox/vv15test/RN New Note.md", "RN Whatever")
check("R5 ambiguous source blocked", r.returncode == 1, r.stdout + r.stderr)
os.remove(f"{SB}/dup/RN New Note.md")

# R6: move (folder change, same name) — bare links untouched, path forms rewritten
r = run("move", "Sandbox/vv15test/RN New Note.md", "Sandbox/vv15test/sub", "--apply")
a = open(f"{SB}/Linker A.md").read()
check("R6a move verified", "verification clean" in r.stdout, r.stdout + r.stderr)
check("R6b bare link stable across move", "[[RN New Note]] and alias" in a)
check("R6c path form updated", "[[Sandbox/vv15test/sub/RN New Note]]" in a, a)
check("R6d md link updated", "(Sandbox/vv15test/sub/RN%20New%20Note.md)" in a)
check("R6e file moved", os.path.exists(f"{SB}/sub/RN New Note.md"))

# R7: show with byte budget + continuation
open(f"{SB}/Long.md", "w").write("## A\n" + "a" * 3000 + "\n\n## B\n" + "b" * 3000 + "\n\n## C\nshort\n")
r = run("show", "Sandbox/vv15test/Long.md", "--max-bytes", "3500")
check("R7a budget stops with continuation", "[more:" in r.stdout and "bbbb" not in r.stdout, r.stdout[-200:])
tok = [w for w in r.stdout.split() if w.startswith("H")][0] if "[more:" in r.stdout else "H2"
r = run("show", "Sandbox/vv15test/Long.md", "--from", tok, "--max-bytes", "10000")
check("R7b continuation resumes", "bbb" in r.stdout and "aaa" not in r.stdout)

# R8: deadends includes note with no outgoing links
r = run("deadends")
check("R8 deadends finds linkless note", "vv15test/RN Taken" in r.stdout, r.stdout[:200])

# R9: lint --quick flags broken + memory-slug links, skips fenced
open(f"{SB}/Broken.md", "w").write("[[Definitely Missing Note zzq]] and [[reference-some-memory]]\n```\n[[Also Missing But Fenced]]\n```\n")
r = run("lint", "--quick", "--limit", "5000")
check("R9a broken flagged", "broken-link" in r.stdout and "Definitely Missing Note zzq" in r.stdout)
check("R9b memory-slug classed", "memory-slug" in r.stdout)
check("R9c fenced skipped", "Also Missing But Fenced" not in r.stdout)

# R10: doctor exits 0 with no journals
r = run("doctor")
check("R10 doctor clean", r.returncode == 0 and "journals: none pending" in r.stdout, r.stdout)

# --- R11-R14: HARD-CRASH recovery (2026-08-26) -----------------------------
# Every existing fault injector raises a catchable exception, so `except
# BaseException` always tidied up and the crash-recovery path was never
# exercised. A review seat traced the real hole: the journal persisted only
# source paths, backups and original hashes, so a kill between os.rename() and
# _journal_done left recovery blind. It restored the backup at the source, knew
# nothing of the destination, deleted the journal and exited 0 -- leaving BOTH
# notes and reporting success. VV_FAULT_KILL_AFTER_RENAME uses os._exit to
# bypass the handlers, which is the only way to test this.
import tempfile as _tf
_cv = _tf.mkdtemp(prefix="vv-crash-")
_cj = _tf.mkdtemp(prefix="vv-crashj-")
os.makedirs(os.path.join(_cv, "Notes"))
_T = os.path.join(_cv, "Notes", "Target.md")
_R = os.path.join(_cv, "Notes", "Renamed.md")
_L = os.path.join(_cv, "Notes", "L0.md")


def _cvv(*a, **kw):
    env = dict(os.environ, VV_NO_METRICS="1", VV_JOURNAL_ROOT=_cj, **kw.get("env", {}))
    return subprocess.run([sys.executable, VV, "--vault", _cv, *a],
                          capture_output=True, text=True, env=env)


def _reset_crash_vault():
    open(_T, "w").write("---\ntype: t\n---\nbody\n")
    open(_L, "w").write("---\ntype: t\n---\nsee [[Target]]\n")
    if os.path.exists(_R):
        os.remove(_R)


_reset_crash_vault()
_k = _cvv("rename", "Notes/Target.md", "Renamed", "--apply",
          env={"VV_FAULT_KILL_AFTER_RENAME": "1"})
check("R11 hard kill leaves no handler to run (rc 137)", _k.returncode == 137, _k.returncode)
check("R11b the rename did land before the kill",
      os.path.exists(_R) and not os.path.exists(_T))

_rb = _cvv("doctor", "--rollback")
check("R12 doctor --rollback recovers a crashed rename", _rb.returncode == 0,
      (_rb.stdout + _rb.stderr)[:120])
check("R12b NO duplicate left behind (the defect)",
      os.path.exists(_T) and not os.path.exists(_R),
      f"old={os.path.exists(_T)} new={os.path.exists(_R)}")
check("R12c links restored to the original target", "[[Target]]" in open(_L).read())

# A post-crash edit by another writer (Obsidian saving a buffer) must NOT be
# clobbered by recovery. Before the fix, recovery had no `written` hashes on
# disk and restored unconditionally.
_reset_crash_vault()
_cvv("rename", "Notes/Target.md", "Renamed", "--apply",
     env={"VV_FAULT_KILL_AFTER_RENAME": "1"})
open(_L, "w").write("---\ntype: t\n---\nsee [[Renamed]] plus a human edit\n")
_rb2 = _cvv("doctor", "--rollback")
check("R13 post-crash external edit is NOT clobbered",
      "human edit" in open(_L).read(), open(_L).read()[:80])
check("R13b and recovery says so loudly (conflict, exit 1)",
      _rb2.returncode == 1 and "conflict" in (_rb2.stdout + _rb2.stderr), _rb2.returncode)

# The journal must record what it needs BEFORE the step it describes.
import json as _json, glob as _glob
# R13 ends in a conflict, which deliberately KEEPS its journal — so the dirty
# gate would block this run before a new journal could exist.
_cvv("doctor", "--discard")
_reset_crash_vault()
_cvv("rename", "Notes/Target.md", "Renamed", "--apply",
     env={"VV_FAULT_KILL_AFTER_RENAME": "1"})
# journals nest under a per-vault key, so this is two levels deep
_mf = _glob.glob(os.path.join(_cj, "*", "*", "manifest.json"))
_man = _json.load(open(_mf[0])) if _mf else {}
check("R14 journal persists the rename endpoints",
      _man.get("src") == "Notes/Target.md" and _man.get("dest") == "Notes/Renamed.md", _man)
check("R14b journal persists the phase reached", _man.get("phase") == "renamed", _man.get("phase"))
check("R14c journal persists what THIS process wrote (for classification)",
      os.path.exists(os.path.join(os.path.dirname(_mf[0]), "written.jsonl")) if _mf else False)
_cvv("doctor", "--discard")
shutil.rmtree(_cv, ignore_errors=True)
shutil.rmtree(_cj, ignore_errors=True)

if not fails:
    shutil.rmtree(SB, ignore_errors=True)
shutil.rmtree(_JR, ignore_errors=True)
print(f"\n{len(fails)} failures: {fails}" if fails else "\nALL PASS (v1.5: 37)")
sys.exit(1 if fails else 0)
