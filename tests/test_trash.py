#!/usr/bin/env python3
"""P5b pins: vv trash — journaled removal that reports its blast radius.

  * dry-run by default: plan digest + the files whose links will BREAK (trash
    rewrites NOTHING — rewriting would repoint links into .trash/); nothing
    moves. --apply executes; --apply <digest> refuses a drifted plan (exit 3).
  * the note lands in .trash/ (dot-dir: invisible to every scan, so it leaves
    the graph); linking files' bytes are untouched; name collisions in .trash/
    auto-suffix rather than block.
  * duplicate-basename sources are refused (removing one silently repoints the
    survivors' bare links — same rule as move).
  * journaled via the same endpoints as rename/move: after --apply no pending
    journal remains; the native entry reaches trash via fallback.
"""
import json, os, subprocess, sys, tempfile, shutil

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VR = os.path.join(REPO, "vrust/target/release/vrust")
VV = os.path.join(REPO, "src/vv.py")

fails = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond: fails.append(name)

for label, entry in (("python", [sys.executable, VV]), ("native", [VR])):
    tv = tempfile.mkdtemp(prefix="vv-trash-")
    jr = tempfile.mkdtemp(prefix="vv-trash-j-")
    try:
        open(os.path.join(tv, "Gone.md"), "w").write("# Gone\nbye\n")
        open(os.path.join(tv, "Keeper.md"), "w").write("# K\nsee [[Gone]] twice [[Gone|alias]]\n")
        keeper_before = open(os.path.join(tv, "Keeper.md")).read()
        env = dict(os.environ, VV_NO_METRICS="1", VV_VAULT=tv, VV_JOURNAL_ROOT=jr)
        def run(*args, stdin=None):
            return subprocess.run(entry + list(args), capture_output=True, text=True, env=env, input=stdin)

        # dry-run: plan + blast radius, nothing moves
        r = run("trash", "Gone")
        check(f"T1/{label} dry-run plans, does not move",
              r.returncode == 0 and "plan " in r.stdout and "Keeper.md" in r.stdout
              and "dry-run" in r.stdout and os.path.exists(os.path.join(tv, "Gone.md")),
              r.stdout[:120] + r.stderr[:60])
        plan = r.stdout.split("plan ", 1)[1].split(":", 1)[0] if "plan " in r.stdout else "none"
        # stale plan refused after an edit
        open(os.path.join(tv, "Keeper.md"), "a").write("edited\n")
        r = run("trash", "Gone", "--apply", plan)
        check(f"T2/{label} drifted plan refused exit 3", r.returncode == 3 and "stale" in r.stderr, r.stderr[:80])
        keeper_before += "edited\n"
        # fresh plan applies: note in .trash/, keeper bytes untouched, no pending journal
        _o = run("trash", "Gone").stdout
        plan = _o.split("plan ", 1)[1].split(":", 1)[0] if "plan " in _o else "none"
        r = run("trash", "Gone", "--apply", plan)
        check(f"T3/{label} apply moves to .trash",
              r.returncode == 0 and not os.path.exists(os.path.join(tv, "Gone.md"))
              and os.path.exists(os.path.join(tv, ".trash", "Gone.md")), r.stdout[:80] + r.stderr[:60])
        check(f"T4/{label} linking file untouched",
              open(os.path.join(tv, "Keeper.md")).read() == keeper_before)
        r = run("set", "Keeper.md", "k", "v")
        check(f"T5/{label} no pending journal blocks writes", r.returncode == 0, r.stderr[:80])
        # trashed note left the graph
        r = run("unresolved")
        check(f"T6/{label} broken links now visible to unresolved", "Gone" in r.stdout, r.stdout[:80])
        # collision: same name trashed again auto-suffixes
        open(os.path.join(tv, "Gone.md"), "w").write("# Gone again\n")
        _o = run("trash", "Gone").stdout
        plan = _o.split("plan ", 1)[1].split(":", 1)[0] if "plan " in _o else "none"
        r = run("trash", "Gone", "--apply", plan)
        check(f"T7/{label} .trash collision auto-suffixes",
              r.returncode == 0 and os.path.exists(os.path.join(tv, ".trash", "Gone-2.md")),
              r.stdout[:80] + r.stderr[:60])
        # duplicate basename refused
        os.makedirs(os.path.join(tv, "Sub"))
        open(os.path.join(tv, "Dup.md"), "w").write("a\n")
        open(os.path.join(tv, "Sub/Dup.md"), "w").write("b\n")
        r = run("trash", "Dup.md")
        check(f"T8/{label} duplicate basename refused",
              r.returncode == 1 and "refused" in r.stderr, r.stderr[:80])
        # hard crash AFTER the move, BEFORE the commit: journal blocks writes,
        # doctor --rollback restores the note to its original path
        open(os.path.join(tv, "Crash.md"), "w").write("# C\nprecious\n")
        _o = run("trash", "Crash").stdout
        plan = _o.split("plan ", 1)[1].split(":", 1)[0] if "plan " in _o else "none"
        env["VV_FAULT_KILL_AFTER_RENAME"] = "1"
        r = run("trash", "Crash", "--apply", plan)
        del env["VV_FAULT_KILL_AFTER_RENAME"]
        check(f"T9/{label} hard kill mid-trash exits 137", r.returncode == 137, str(r.returncode))
        r = run("set", "Keeper.md", "k2", "v")
        check(f"T10/{label} pending journal blocks writes", r.returncode == 4, f"exit={r.returncode}")
        r = run("doctor", "--rollback")
        check(f"T11/{label} rollback restores the note",
              r.returncode == 0 and os.path.exists(os.path.join(tv, "Crash.md"))
              and open(os.path.join(tv, "Crash.md")).read() == "# C\nprecious\n",
              r.stdout[:100] + r.stderr[:60])
    finally:
        shutil.rmtree(tv, ignore_errors=True); shutil.rmtree(jr, ignore_errors=True)

print(f"\n{len(fails)} failures: {fails}" if fails else "\nALL PASS (trash: 22)")
sys.exit(1 if fails else 0)
