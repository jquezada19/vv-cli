#!/usr/bin/env python3
"""P4 pins: vv changed --since, vv batch (invocation amortization).

  * changed --since TS (epoch seconds or ISO date/datetime): vault-relative
    paths whose mtime is strictly newer, mtime-desc then path, honoring
    --limit and --jsonl ({"path","mtime"} records). --since is required.
  * batch: JSONL ops on stdin {"cmd":..,"args":[..]}, READ commands only,
    executed in ONE process; one result record per op {"i","exit","out"},
    errors carried per-op ({"i","exit","error"}), a bad op never kills the
    batch. Writers are refused per-op (batch is a read surface).
  * both are python-owned; the native entry execs python for unknown verbs,
    which the native-entry cases here prove end to end.
"""
import json, os, subprocess, sys, tempfile, shutil, time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VR = os.path.join(REPO, "vrust/target/release/vrust")
VV = os.path.join(REPO, "src/vv.py")

fails = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond: fails.append(name)

tv = tempfile.mkdtemp(prefix="vv-batch-")
try:
    open(os.path.join(tv, "Old.md"), "w").write("# Old\nstale\n")
    open(os.path.join(tv, "New.md"), "w").write("# New\nfresh [[Old]]\n")
    t_old, t_new = 1700000000, 1700000100   # fixed epochs: deterministic
    os.utime(os.path.join(tv, "Old.md"), (t_old, t_old))
    os.utime(os.path.join(tv, "New.md"), (t_new, t_new))
    env = dict(os.environ, VV_NO_METRICS="1", VV_VAULT=tv,
               VV_INDEX_ROOT=tempfile.mkdtemp(prefix="vv-idx-"))   # both engines' caches stay out of ~/.cache

    def run(cmd, *args, stdin=None):
        return subprocess.run(cmd + list(args), capture_output=True, text=True,
                              env=env, input=stdin)

    for label, entry in (("python", [sys.executable, VV]), ("native", [VR])):
        # changed: strictly-newer filter, epoch form
        r = run(entry, "changed", "--since", str(t_old))
        check(f"C1/{label} changed epoch", r.returncode == 0
              and r.stdout.splitlines()[0] == "New.md" and "(1 changed)" in r.stdout,
              r.stdout[:80] + r.stderr[:60])
        # ISO date form: everything is newer than the epoch date given
        r = run(entry, "changed", "--since", "2023-11-14")
        check(f"C2/{label} changed ISO", r.returncode == 0 and "(2 changed)" in r.stdout,
              r.stdout[:80] + r.stderr[:60])
        # --since required
        r = run(entry, "changed")
        check(f"C3/{label} changed requires --since", r.returncode == 1 and "next:" in r.stderr,
              r.stderr[:80])
        # --jsonl record shape
        r = run(entry, "changed", "--since", str(t_old), "--jsonl")
        try:
            recs = [json.loads(l) for l in r.stdout.strip().split("\n")]
            ok = recs[1]["path"] == "New.md" and "mtime" in recs[1] and recs[-1]["total"] == 1
        except Exception:
            ok = False
        check(f"C4/{label} changed --jsonl", ok, r.stdout[:100])

        # batch: three ops in one process; middle op fails, batch continues
        ops = "\n".join([
            json.dumps({"cmd": "outline", "args": ["New.md"]}),
            json.dumps({"cmd": "outline", "args": ["No Such Note Qq"]}),
            json.dumps({"cmd": "backlinks", "args": ["Old"]}),
        ]) + "\n"
        r = run(entry, "batch", stdin=ops)
        try:
            recs = [json.loads(l) for l in r.stdout.strip().split("\n")]
            ok = (len(recs) == 3
                  and recs[0]["i"] == 0 and recs[0]["exit"] == 0 and "H1" in recs[0]["out"]
                  and recs[1]["exit"] == 1 and "not-found" in recs[1]["error"]
                  and recs[2]["exit"] == 0 and "New.md" in recs[2]["out"])
        except Exception:
            ok = False
        check(f"B1/{label} batch three ops one process", r.returncode == 0 and ok,
              r.stdout[:140] + r.stderr[:60])
        # writers refused per-op, batch survives
        ops = json.dumps({"cmd": "set", "args": ["Old", "status", "x"]}) + "\n" + \
              json.dumps({"cmd": "head", "args": ["Old.md"]}) + "\n"
        r = run(entry, "batch", stdin=ops)
        try:
            recs = [json.loads(l) for l in r.stdout.strip().split("\n")]
            ok = recs[0]["exit"] == 1 and "read" in recs[0]["error"] and recs[1]["exit"] == 0
        except Exception:
            ok = False
        check(f"B2/{label} batch refuses writers per-op", r.returncode == 0 and ok,
              r.stdout[:120])
        # malformed line: per-op error, batch continues
        r = run(entry, "batch", stdin='not json\n' + json.dumps({"cmd": "tags", "args": []}) + "\n")
        try:
            recs = [json.loads(l) for l in r.stdout.strip().split("\n")]
            ok = recs[0]["exit"] == 1 and recs[1]["exit"] == 0
        except Exception:
            ok = False
        check(f"B3/{label} malformed op line survives", r.returncode == 0 and ok, r.stdout[:120])
finally:
    shutil.rmtree(tv, ignore_errors=True)

print(f"\n{len(fails)} failures: {fails}" if fails else "\nALL PASS (batch+changed: 14)")
sys.exit(1 if fails else 0)
