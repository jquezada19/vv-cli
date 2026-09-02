#!/usr/bin/env python3
"""Write-safety pins from the 2026-08-30 hardening sweep (vault Todos, vv-*).

W1  `patch` is compare-and-swapped on the SECTION hash but, until 2026-09-02,
    not on the FILE signature: the hash and the splice came from one read
    (single-read discipline held), yet a second writer landing between that
    read and os.replace() — Obsidian saving a different section — was
    silently overwritten. Every other writer already passed `expect_sig`;
    patch did not. Pinned in-process: a wrapped read_raw plays the second
    writer, so the race is deterministic rather than timing-dependent.
W2  control for W1: with no concurrent writer the same patch succeeds.
W3  `rename --apply <sha8>` refuses when a NEW backlink appeared after the
    dry-run: the plan digest is recomputed from a fresh link scan at apply
    time, so the "24th site" is inside the digest, not outside it. This is
    the backlink-set certificate by construction — the pin exists so a future
    cache shortcut cannot quietly remove it.
W4  plain `rename --apply` (no token) rewrites the late backlink too: the
    scan at apply time is fresh, not the dry-run's snapshot.
"""
import io, os, shutil, subprocess, sys, tempfile

_VAULT = os.environ.get("VV_VAULT") or os.path.expanduser("~/Documents/Obsidian Vault")
SB = os.path.join(_VAULT, "Sandbox/vvwsafe")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VV = os.path.join(REPO, "src", "vv.py")

_JR = tempfile.mkdtemp(prefix="vv-test-journals-")
os.environ["VV_JOURNAL_ROOT"] = _JR
os.environ.setdefault("VV_NO_METRICS", "1")
os.environ["VV_VAULT"] = _VAULT   # vv_impl reads VAULT at import

fails = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{str(detail)[:160]}]" if detail and not cond else ""))
    if not cond: fails.append(name)

def run(*args, stdin=None, env=None):
    e = dict(os.environ, **(env or {}))
    return subprocess.run([sys.executable, VV, *args], capture_output=True, text=True, input=stdin, env=e)

if os.path.isdir(SB) and os.listdir(SB):
    keep = tempfile.mkdtemp(prefix="vv-kept-vvwsafe-")
    shutil.move(SB, os.path.join(keep, "vvwsafe"))
    print(f"note: pre-existing {SB} moved to {keep}")
shutil.rmtree(SB, ignore_errors=True)
os.makedirs(SB, exist_ok=True)

NOTE_REL = "Sandbox/vvwsafe/Race Note.md"
NOTE = os.path.join(_VAULT, NOTE_REL)
ORIG = "# Race Note\n\n## Target\n\nold body\n\n## Other\n\nuntouched\n"

def write(p, t):
    with open(p, "w", newline="") as f:
        f.write(t)

try:
    sys.path.insert(0, os.path.join(REPO, "src"))
    import vv_impl

    # --- W1: a second writer between patch's read and its write --------------
    write(NOTE, ORIG)
    r = run("outline", NOTE_REL)
    sha = [l.split("\t") for l in r.stdout.splitlines() if l.split("\t")[2] == "Target"][0][4]
    real_read_raw = vv_impl.read_raw
    fired = []
    def racing_read_raw(fp):
        text = real_read_raw(fp)
        if os.path.realpath(fp) == os.path.realpath(NOTE) and not fired:
            fired.append(1)
            # Obsidian saves a DIFFERENT section after vv has read the file.
            write(NOTE, text.replace("untouched", "obsidian wrote this"))
        return text
    vv_impl.read_raw = racing_read_raw
    sys.stdin = io.StringIO("new body\n")
    code = None
    try:
        vv_impl.cmd_patch(NOTE_REL, "Target", sha)
    except SystemExit as e:
        code = e.code
    finally:
        vv_impl.read_raw = real_read_raw
        sys.stdin = sys.__stdin__
    after = open(NOTE, newline="").read()
    check("W1a concurrent write makes patch refuse with exit 3", code == 3, f"exit={code}")
    check("W1b the second writer's bytes survive", "obsidian wrote this" in after, after)
    check("W1c our patch was NOT applied over them", "new body" not in after, after)
    check("W1d the second writer actually fired (test is not vacuous)", fired == [1])

    # --- W2: control — no second writer, same patch succeeds -----------------
    write(NOTE, ORIG)
    r = run("patch", NOTE_REL, "Target", sha, stdin="new body\n")
    after = open(NOTE, newline="").read()
    check("W2 unraced patch succeeds", r.returncode == 0 and "new body" in after and "untouched" in after,
          r.stderr + after)

    # --- W3/W4: a backlink written between dry-run and apply -----------------
    A = os.path.join(SB, "Alpha Note.md"); B = os.path.join(SB, "Beta Note.md"); C = os.path.join(SB, "Late Note.md")
    write(A, "# Alpha Note\n\nbody\n")
    write(B, "# Beta Note\n\nsee [[Alpha Note]]\n")
    r = run("rename", "Sandbox/vvwsafe/Alpha Note.md", "Sandbox/vvwsafe/Alpha Renamed")
    plan = [l for l in r.stdout.splitlines() if l.startswith("plan ")]
    check("W3a dry-run prints a plan token", r.returncode == 0 and plan, r.stdout + r.stderr)
    token = plan[0].split()[1].rstrip(":") if plan else "00000000"
    check("W3b dry-run saw exactly one backlink file", "files to rewrite: 1 " in r.stdout, r.stdout)
    write(C, "# Late Note\n\nalso [[Alpha Note]]\n")          # the 24th site
    r = run("rename", "Sandbox/vvwsafe/Alpha Note.md", "Sandbox/vvwsafe/Alpha Renamed", "--apply", token)
    check("W3c apply with the reviewed token refuses (exit 3)", r.returncode == 3, f"exit={r.returncode} {r.stderr}")
    check("W3d refusal names the drift and the next step", r.stderr.startswith("stale: plan is now") and "re-run the dry-run" in r.stderr, r.stderr)
    check("W3e nothing was renamed", os.path.exists(A) and not os.path.exists(os.path.join(SB, "Alpha Renamed.md")))
    check("W3f the late link is untouched", "[[Alpha Note]]" in open(C).read())
    r = run("rename", "Sandbox/vvwsafe/Alpha Note.md", "Sandbox/vvwsafe/Alpha Renamed", "--apply")
    check("W4a plain apply succeeds after a fresh scan", r.returncode == 0, r.stderr)
    check("W4b the late backlink was rewritten too", "[[Alpha Renamed]]" in open(C).read(), open(C).read())
    check("W4c the original backlink was rewritten", "[[Alpha Renamed]]" in open(B).read(), open(B).read())
finally:
    if not fails:
        shutil.rmtree(SB, ignore_errors=True)
    else:
        print(f"note: fixture kept at {SB} for inspection")

print(f"\n{len(fails)} failure(s)" if fails else "\nALL PASS")
sys.exit(1 if fails else 0)
