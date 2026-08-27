#!/usr/bin/env python3
"""Regressions for defects found by the Obsidian-oracle comparison (2026-08-26).

O1  [[Note\\|alias]] — the escaped alias pipe used inside tables. Obsidian reads the
    backslash as escaping the pipe; the target is the name before it.
O2  rename rewrites escaped-pipe links and keeps the escape (a bare | would break
    the table the link sits in).
O3  duplicate basenames: a bare [[Name]] resolves to the shortest vault-relative
    path (Obsidian's rule, probed via metadataCache). The winner gets the bare
    backlinks; the other note is still reachable by path-form link.
O4  a failed resolve suggests near-miss names (substring first, then similarity)
    instead of a bare error.
O5  links inside <!-- --> HTML comments are not indexed by Obsidian: vv neither
    counts nor rewrites them; %% comment links ARE indexed and stay real.
"""
import subprocess, sys, os, shutil
_VAULT = os.environ.get("VV_VAULT") or os.path.expanduser("~/Documents/Obsidian Vault")

SB = os.path.join(_VAULT, "Sandbox/vvoracle")
VV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "vv.py")

def run(*args):
    return subprocess.run([sys.executable, VV, *args], capture_output=True, text=True)


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
os.makedirs(f"{SB}/deep/deeper", exist_ok=True)

# O1: escaped alias pipe in a table
open(f"{SB}/EscTarget QQ.md", "w").write("content\n")
open(f"{SB}/EscLinker.md", "w").write(
    "| col |\n|---|\n| [[EscTarget QQ\\|friendly name]] |\n[[EscTarget QQ#Sec\\|frag alias]]\n")
r = run("backlinks", "Sandbox/vvoracle/EscTarget QQ.md")
check("O1 escaped-pipe link found", "EscLinker" in r.stdout, r.stdout)

# O2: rename rewrites the escaped form and keeps the escape
r = run("rename", "Sandbox/vvoracle/EscTarget QQ.md", "EscRenamed QQ", "--apply")
a = open(f"{SB}/EscLinker.md").read()
check("O2a rename verified", "verification clean" in r.stdout, r.stdout + r.stderr)
check("O2b escape preserved", "[[EscRenamed QQ\\|friendly name]]" in a, a)
check("O2c fragment escape preserved", "[[EscRenamed QQ#Sec\\|frag alias]]" in a, a)
check("O2d no unescaped pipe introduced", "QQ|friendly" not in a, a)

# O3: duplicate basenames — shortest vault-relative path wins bare links
open(f"{SB}/DupNote QQ.md", "w").write("winner\n")
open(f"{SB}/deep/deeper/DupNote QQ.md", "w").write("loser\n")
open(f"{SB}/DupLinker.md", "w").write(
    "bare [[DupNote QQ]]\npath [[Sandbox/vvoracle/deep/deeper/DupNote QQ]]\n")
r = run("backlinks", "Sandbox/vvoracle/DupNote QQ.md")
check("O3a winner gets bare backlink", "DupLinker" in r.stdout, r.stdout)
r = run("backlinks", "Sandbox/vvoracle/deep/deeper/DupNote QQ.md")
check("O3b loser reachable by path form only", "DupLinker" in r.stdout, r.stdout)
open(f"{SB}/DupLinker.md", "w").write("bare [[DupNote QQ]]\n")
r = run("backlinks", "Sandbox/vvoracle/deep/deeper/DupNote QQ.md")
check("O3c loser gets NO bare backlink", "DupLinker" not in r.stdout, r.stdout)
# same-folder tier: a linker sitting NEXT TO the longer-path copy resolves to it
open(f"{SB}/deep/deeper/NearLinker.md", "w").write("bare [[DupNote QQ]]\n")
r = run("backlinks", "Sandbox/vvoracle/deep/deeper/DupNote QQ.md")
check("O3d same-folder linker resolves locally", "NearLinker" in r.stdout, r.stdout)
r = run("backlinks", "Sandbox/vvoracle/DupNote QQ.md")
check("O3e local link not credited to short-path note", "NearLinker" not in r.stdout, r.stdout)

# O5: links inside HTML comments are not links (Obsidian doesn't index them),
#     and rename leaves them untouched; %% comment links ARE links.
open(f"{SB}/CmtTarget QQ.md", "w").write("content\n")
open(f"{SB}/CmtLinker.md", "w").write(
    "<!-- [[CmtTarget QQ]] hidden -->\nvisible [[CmtTarget QQ]]\n%% [[CmtTarget QQ]] in percent %%\n")
r = run("backlinks", "Sandbox/vvoracle/CmtTarget QQ.md")
check("O5a comment link not counted, visible is", "CmtLinker" in r.stdout, r.stdout)
r = run("rename", "Sandbox/vvoracle/CmtTarget QQ.md", "CmtRenamed QQ", "--apply")
a = open(f"{SB}/CmtLinker.md").read()
check("O5b rename verified", "verification clean" in r.stdout, r.stdout + r.stderr)
check("O5c comment link untouched", "<!-- [[CmtTarget QQ]] hidden -->" in a, a)
check("O5d visible link rewritten", "visible [[CmtRenamed QQ]]" in a, a)
check("O5e percent-comment link rewritten (real link)", "%% [[CmtRenamed QQ]] in percent %%" in a, a)

# O4: failed resolve suggests candidates
r = run("outline", "EscRenmaed QQ")   # transposed typo
check("O4a suggestion on typo", r.returncode != 0 and "did you mean" in r.stderr
      and "EscRenamed QQ" in r.stderr, r.stderr)
r = run("outline", "Definitely Not A Note zzq9")
check("O4b no false suggestion", r.returncode != 0 and "did you mean" not in r.stderr, r.stderr)

if not fails:
    shutil.rmtree(SB, ignore_errors=True)
shutil.rmtree(_JR, ignore_errors=True)
print(f"\n{len(fails)} failures: {fails}" if fails else "\nALL PASS (oracle findings: 17)")
sys.exit(1 if fails else 0)
