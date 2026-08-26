#!/usr/bin/env python3
"""Regressions for the 2026-08-26 round-2 review (multi-model panel + Codex).

V1  rename/move destination is containment-checked: a relative escape or an
    absolute path must never write outside the vault.
V2  code_spans: a longer backtick run is skipped whole — its tail is never a
    closer (CommonMark; probed: Obsidian masks the link in `a``` [[X]] `).
V3  move with a duplicated basename is refused: relocating one duplicate changes
    which note bare links resolve to (same-folder/shortest-path tiers).
V4  lint table-pipe catches the fragment form [[Note#Sec|alias]].
V5  empty wikilink targets are not links; alias may contain a single ]; ]] never
    opens a [text]( markdown link (all probed against Obsidian).
V6  moving a ROOT-level note leaves bare links alone (basename unchanged) instead
    of rewriting them to path form.
V7  orphans uses the same winner rules as backlinks: a bare [[Dup]] rescues only
    the duplicate it resolves to.
V8  an interrupted apply (SystemExit — e.g. a non-UTF-8 file read mid-apply)
    still rolls back.
"""
import subprocess, sys, os, shutil

SB = os.path.expanduser("~/Documents/Obsidian Vault/Sandbox/vvround2")
OUTSIDE = os.path.expanduser("~/Documents/Obsidian Vault/Sandbox")  # parent of SB
VV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "vv.py")

def run(*args, env=None):
    e = dict(os.environ, **(env or {}))
    return subprocess.run([sys.executable, VV, *args], capture_output=True, text=True, env=e)


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

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import vv  # noqa: E402

fresh_fixture(SB)
os.makedirs(f"{SB}/A", exist_ok=True); os.makedirs(f"{SB}/B"); os.makedirs(f"{SB}/C")

# V1: destination containment (isolated throwaway vault so nothing real is at risk)
import tempfile
tv = tempfile.mkdtemp(prefix="vv-r2-")
os.makedirs(f"{tv}/vault"); open(f"{tv}/vault/Note.md", "w").write("x\n")
r = run("rename", "Note.md", "../evil", "--apply", env={"VV_VAULT": f"{tv}/vault"})
check("V1a relative escape refused", r.returncode != 0 and not os.path.exists(f"{tv}/evil.md"), r.stdout + r.stderr)
r = run("move", "Note.md", "/tmp", "--apply", env={"VV_VAULT": f"{tv}/vault"})
check("V1b absolute destination refused", r.returncode != 0 and os.path.exists(f"{tv}/vault/Note.md"), r.stdout + r.stderr)
shutil.rmtree(tv, ignore_errors=True)

# V2: run-length closer
spans = vv.code_spans("`a``` [[X]] ` [[Y]]")
check("V2a maximal run skipped", spans == [(0, 13)], spans)
t = [t for _, k, t in vv.link_targets_in("`a``` [[X]] ` [[Y]]")]
check("V2b only Y active", t == ["Y"], t)

# V3: move with duplicate basename refused
open(f"{SB}/A/RtDup QQ.md", "w").write("a\n")
open(f"{SB}/B/RtDup QQ.md", "w").write("b\n")
open(f"{SB}/A/L.md", "w").write("[[RtDup QQ]]\n")
r = run("move", "Sandbox/vvround2/A/RtDup QQ.md", "Sandbox/vvround2/C", "--apply")
check("V3 ambiguous move refused", r.returncode != 0 and os.path.exists(f"{SB}/A/RtDup QQ.md"), r.stdout + r.stderr)
os.remove(f"{SB}/B/RtDup QQ.md")

# V4: fragment-form table pipe
open(f"{SB}/TP.md", "w").write("| a |\n|---|\n| [[RtDup QQ#Sec|alias]] |\n")
r = run("lint", "--quick", "--limit", "5000")
check("V4 fragment table-pipe flagged", "table-pipe" in r.stdout and "TP.md" in r.stdout, r.stdout[-300:])
os.remove(f"{SB}/TP.md")

# V5: lexer semantics (Python side; parity test holds Rust to the same)
t = [t for _, k, t in vv.link_targets_in("[[ ]] and [[\\]] and [[Real]]")]
check("V5a empty targets skipped", t == ["Real"], t)
t = [(k, t) for _, k, t in vv.link_targets_in("[[RSQ|a]b]] x")]
check("V5b alias may contain ]", t == [("wiki", "RSQ")], t)
t = [(k, t) for _, k, t in vv.link_targets_in("[[G]](x.md) and a]](y.md) and [t](z.md)")]
check("V5c ]] never opens mdlink", t == [("wiki", "G"), ("md", "z.md")], t)
t = [t for _, k, t in vv.link_targets_in("[[Dbl\\\\|alias]]")]
check("V5d second backslash stays in target", t == ["Dbl\\"], t)

# V6: root-note move keeps bare links
open(f"{SB}/RtRoot QQ.md", "w").write("x\n")
open(f"{SB}/RtRootLinker.md", "w").write("see [[RtRoot QQ]]\n")
r = run("move", "Sandbox/vvround2/RtRoot QQ.md", "Sandbox/vvround2/C", "--apply")
a = open(f"{SB}/RtRootLinker.md").read()
check("V6 bare link untouched on move", "verification clean" in r.stdout and "[[RtRoot QQ]]" in a, r.stdout + a)

# V7: orphans winner attribution — loser dup with no other links IS an orphan
open(f"{SB}/A/OrDup QQ.md", "w").write("winner\n")   # shorter path than C/deep
os.makedirs(f"{SB}/C/deep", exist_ok=True)
open(f"{SB}/C/deep/OrDup QQ.md", "w").write("loser\n")
open(f"{SB}/A/OrL.md", "w").write("[[OrDup QQ]]\n")
r = run("orphans", "Sandbox/vvround2")
check("V7a loser is orphan", "C/deep/OrDup QQ.md" in r.stdout, r.stdout)
check("V7b winner is not", "A/OrDup QQ.md" not in r.stdout, r.stdout)

# V8: SystemExit mid-apply still rolls back (VV_FAULT_KIND=exit injects SystemExit
# at the write loop — the same path a read_raw exit-5 mid-apply takes)
tv = tempfile.mkdtemp(prefix="vv-r2b-")
os.makedirs(f"{tv}/vault")
open(f"{tv}/vault/Tgt.md", "w").write("x\n")
open(f"{tv}/vault/Linker.md", "w").write("[[Tgt]]\n")
r = run("rename", "Tgt.md", "Tgt2", "--apply",
        env={"VV_VAULT": f"{tv}/vault", "VV_FAULT_AFTER": "0", "VV_FAULT_KIND": "exit"})
rolled = "rolled-back" in (r.stdout + r.stderr)
intact = os.path.exists(f"{tv}/vault/Tgt.md") and not os.path.exists(f"{tv}/vault/Tgt2.md")
check("V8 SystemExit mid-apply rolls back", r.returncode != 0 and rolled and intact,
      r.stdout + r.stderr)
shutil.rmtree(tv, ignore_errors=True)

# V9: ROOT-level duplicate — basename == rel path must still take the bare branch,
# so the losing root copy gets no backlink/impact through the path-form fallback
tv = tempfile.mkdtemp(prefix="vv-r2c-")
os.makedirs(f"{tv}/vault/deep")
open(f"{tv}/vault/Dup.md", "w").write("root\n")
open(f"{tv}/vault/deep/Dup.md", "w").write("deep\n")
open(f"{tv}/vault/deep/Link.md", "w").write("[[Dup]]\n")
env = {"VV_VAULT": f"{tv}/vault"}
r = run("backlinks", "deep/Dup.md", env=env)
check("V9a same-folder deep copy gets the link", "deep/Link.md" in r.stdout, r.stdout)
r = run("backlinks", "Dup.md", env=env)
check("V9b root copy does NOT (no path-form leak)", "Link.md" not in r.stdout, r.stdout)
r = run("impact", "Dup.md", env=env)
check("V9c impact agrees", "incoming-link files: 0" in r.stdout, r.stdout)
shutil.rmtree(tv, ignore_errors=True)

# V10: mixed active/inert on ONE line — rename must rewrite the active link,
# leave the comment/code content byte-identical, and SUCCEED (stale offsets in
# sequential substitutions made these valid renames abort — review 2026-08-26)
tv = tempfile.mkdtemp(prefix="vv-r2d-")
os.makedirs(f"{tv}/vault")
open(f"{tv}/vault/Old.md", "w").write("x\n")
open(f"{tv}/vault/Mix.md", "w").write(
    "[[Old]] <!-- [hidden](Old.md) -->\n[[Old]] `[hidden2](Old.md)`\n")
r = run("rename", "Old.md", "Much Longer New Name", "--apply", env={"VV_VAULT": f"{tv}/vault"})
a = open(f"{tv}/vault/Mix.md").read()
check("V10a mixed-line rename succeeds", "verification clean" in r.stdout, r.stdout + r.stderr)
check("V10b active links rewritten", a.count("[[Much Longer New Name]]") == 2, a)
check("V10c comment md-link untouched", "<!-- [hidden](Old.md) -->" in a, a)
check("V10d code md-link untouched", "`[hidden2](Old.md)`" in a, a)
shutil.rmtree(tv, ignore_errors=True)

# V11: table structure — no-leading-pipe tables flagged; a lone |-prefixed
# paragraph (no delimiter row) is not a table and not flagged
open(f"{SB}/T2.md", "w").write(
    "Name | Link\n--- | ---\nx | [[NoLead QQ|alias]]\n\n| [[Para QQ|alias]] not a table\n")
open(f"{SB}/NoLead QQ.md", "w").write("x\n"); open(f"{SB}/Para QQ.md", "w").write("x\n")
r = run("lint", "--quick", "--limit", "5000")
check("V11a headerless-pipe table flagged", "table-pipe" in r.stdout and "NoLead QQ" in r.stdout, r.stdout[-400:])
check("V11b lone pipe paragraph not flagged", "Para QQ" not in r.stdout, r.stdout[-400:])

# V12: rollback classification (unit) — a journaled file we never wrote, edited by
# a third party mid-operation, keeps the third party's bytes; our own write is
# restored; an overwritten write is left alone and reported
tv = tempfile.mkdtemp(prefix="vv-r2e-")
os.makedirs(f"{tv}/vault")
a_p, b_p = f"{tv}/vault/A.md", f"{tv}/vault/B.md"
open(a_p, "w").write("a orig\n"); open(b_p, "w").write("b orig\n")
old_vault, old_real = vv.VAULT, vv._VAULT_REAL
vv.VAULT = f"{tv}/vault"; vv._VAULT_REAL = os.path.realpath(vv.VAULT)
try:
    jdir = vv._journal_start("test", [a_p, b_p])
    open(a_p, "w").write("a OURS\n")          # our write
    open(b_p, "w").write("b THIRD PARTY\n")   # someone else's edit, we never wrote B
    import hashlib as _hl
    written = {"A.md": _hl.sha256(b"a OURS\n").hexdigest()}
    left = vv._journal_rollback(jdir, written)
    check("V12a our write restored", open(a_p).read() == "a orig\n")
    check("V12b third-party bytes survive", open(b_p).read() == "b THIRD PARTY\n", open(b_p).read())
    check("V12c nothing reported left", left == [], left)
    open(a_p, "w").write("a OVERWRITTEN AFTER US\n")
    left = vv._journal_rollback(jdir, written)
    check("V12d overwritten write left + reported", left == ["A.md"]
          and open(a_p).read() == "a OVERWRITTEN AFTER US\n", left)
finally:
    vv.VAULT, vv._VAULT_REAL = old_vault, old_real
shutil.rmtree(tv, ignore_errors=True)

# V13: a bare --- (frontmatter fence / hr) is NOT a table delimiter row — a real
# delimiter always contains a pipe (even single-column: |---|). Without this, the
# line above a closing --- was classified as a table header, flagging aliased
# wikilinks inside quoted YAML frontmatter (4 live false positives, 2026-08-26).
open(f"{SB}/FM QQ.md", "w").write(
    '---\ntitle: x\nrelated: ["[[FmTgt QQ|alias]]"]\n---\nbody\n\ntext | [[HrTgt QQ|alias]]\n---\n\nafter\n')
open(f"{SB}/T3.md", "w").write("| h |\n|---|\n| [[OneCol QQ|alias]] |\n")
for n in ("FmTgt QQ", "HrTgt QQ", "OneCol QQ"):
    open(f"{SB}/{n}.md", "w").write("x\n")
r = run("lint", "--quick", "--limit", "5000")
check("V13a frontmatter alias pipe not flagged", "FmTgt QQ" not in r.stdout, r.stdout[-400:])
check("V13b hr below pipe-line not a delimiter", "HrTgt QQ" not in r.stdout, r.stdout[-400:])
check("V13c single-column |---| still flagged", "table-pipe" in r.stdout and "OneCol QQ" in r.stdout, r.stdout[-400:])

# V16: plan digest — --apply <sha8> binds to the previewed plan; a concurrent
# edit changes the digest and the bound apply exits stale (3)
import re as _re
tv = tempfile.mkdtemp(prefix="vv-r2f-")
os.makedirs(f"{tv}/vault")
open(f"{tv}/vault/T.md", "w").write("x\n"); open(f"{tv}/vault/L.md", "w").write("[[T]]\n")
env = {"VV_VAULT": f"{tv}/vault"}
r = run("rename", "T.md", "T2", env=env)
m = _re.search(r"^plan ([0-9a-f]{8}):", r.stdout, _re.M)
check("V16a dry-run prints plan digest", bool(m), r.stdout)
open(f"{tv}/vault/L.md", "a").write("changed\n")
r = run("rename", "T.md", "T2", "--apply", m.group(1) if m else "00000000", env=env)
check("V16b bound apply exits stale on drift", r.returncode == 3 and "stale:" in r.stderr, r.stdout + r.stderr)
r = run("rename", "T.md", "T2", env=env)
m2 = _re.search(r"^plan ([0-9a-f]{8}):", r.stdout, _re.M)
r = run("rename", "T.md", "T2", "--apply", m2.group(1), env=env)
check("V16c bound apply executes on match", "verification clean" in r.stdout, r.stdout + r.stderr)
shutil.rmtree(tv, ignore_errors=True)

# V17: --vault flag targets another vault without env
tv = tempfile.mkdtemp(prefix="vv-r2g-")
os.makedirs(f"{tv}/vault"); open(f"{tv}/vault/Solo.md", "w").write("## A\nbody\n")
r = run("--vault", f"{tv}/vault", "outline", "Solo.md")
check("V17 --vault targets the given vault", r.returncode == 0 and "A" in r.stdout, r.stdout + r.stderr)
shutil.rmtree(tv, ignore_errors=True)

# V18: error grammar — usage/engine kinds; arity checked at the boundary
r = run("outline")
check("V18a missing arg is usage:", r.returncode == 1 and r.stderr.startswith("usage:"), r.stderr)
r = run("outline", "a", "b", "c")
check("V18b extra args are usage:", r.returncode == 1 and r.stderr.startswith("usage:"), r.stderr)
r = run("search", "x", env={"VV_ENGINE": "turbo"})
check("V18c unknown engine is engine:", r.returncode == 1 and r.stderr.startswith("engine:"), r.stderr)

if not fails:
    shutil.rmtree(SB, ignore_errors=True)
shutil.rmtree(_JR, ignore_errors=True)
print(f"\n{len(fails)} failures: {fails}" if fails else "\nALL PASS (round-2: 37)")
sys.exit(1 if fails else 0)
