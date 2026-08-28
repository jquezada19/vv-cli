#!/usr/bin/env python3
"""P3 pins: --jsonl structured output, structured errors, lint --check.

Contract (docs/roadmap-v1.1-spec.md P3):
  * one JSON object per line; FIRST record is {"v": 1, "cmd": <cmd>};
    LAST record is {"total": N, "shown": K}; entry records between carry
    per-command fields. Default (non---jsonl) output must be byte-unchanged —
    that is held by the existing suites, not re-pinned here.
  * under --jsonl, stderr errors are one JSON object: {kind, message, next, exit}.
  * lint --check exits nonzero when there are findings, 0 when clean.
  * the NATIVE entry hands any --jsonl invocation to python (schema has one
    author); search --jsonl must not shell back to the rust engine.
"""
import json, os, subprocess, sys, tempfile, shutil

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VR = os.path.join(REPO, "vrust/target/release/vrust")
VV = os.path.join(REPO, "src/vv.py")

fails = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond: fails.append(name)

tv = tempfile.mkdtemp(prefix="vv-jsonl-")
try:
    os.makedirs(os.path.join(tv, "Wk"))
    open(os.path.join(tv, "Wk/A.md"), "w").write("---\nstatus: open\ntype: t1\ntags: [alpha]\n---\n[[B]]\nneedle text\n")
    open(os.path.join(tv, "Wk/B.md"), "w").write("---\nstatus: done\ntype: t2\ntags: [alpha, beta]\n---\n[[A]]\nneedle too\n")
    env = dict(os.environ, VV_NO_METRICS="1", VV_VAULT=tv)

    def run(cmd, *args):
        return subprocess.run(cmd + list(args), capture_output=True, text=True, env=env)

    def parse_jsonl(stdout):
        return [json.loads(l) for l in stdout.strip().split("\n") if l]

    for entry_label, entry in (("python", [sys.executable, VV]), ("native", [VR])):
        # enumerator: backlinks
        r = run(entry, "backlinks", "A", "--jsonl")
        try:
            recs = parse_jsonl(r.stdout)
            ok = (recs[0] == {"v": 1, "cmd": "backlinks"}
                  and recs[-1] == {"total": 1, "shown": 1}
                  and recs[1] == {"path": "Wk/B.md"})
        except Exception as e:
            ok, recs = False, repr(e)
        check(f"J1/{entry_label} backlinks --jsonl shape", r.returncode == 0 and ok,
              f"{r.stdout[:120]}{r.stderr[:60]}")
        # search: must not echo rust text output; fields path+score
        r = run(entry, "search", "needle", "--jsonl")
        try:
            recs = parse_jsonl(r.stdout)
            ok = (recs[0]["cmd"] == "search" and recs[0]["v"] == 1
                  and all(set(x) >= {"path", "score"} for x in recs[1:-1])
                  and len(recs) - 2 == recs[-1]["shown"] == 2)
        except Exception as e:
            ok, recs = False, repr(e)
        check(f"J2/{entry_label} search --jsonl shape", r.returncode == 0 and ok,
              f"{r.stdout[:120]}{r.stderr[:60]}")
        # board / tags / props field schemas
        r = run(entry, "board", "Wk", "--jsonl")
        try:
            recs = parse_jsonl(r.stdout)
            ok = set(recs[1]) == {"name", "status", "type"} and recs[-1]["total"] == 2
        except Exception as e:
            ok = False
        check(f"J3/{entry_label} board --jsonl fields", r.returncode == 0 and ok, r.stdout[:120])
        r = run(entry, "tags", "--jsonl")
        try:
            recs = parse_jsonl(r.stdout)
            ok = set(recs[1]) == {"tag", "count"} and recs[-1]["total"] == 2
        except Exception as e:
            ok = False
        check(f"J4/{entry_label} tags --jsonl fields", r.returncode == 0 and ok, r.stdout[:120])
        r = run(entry, "props", "status", "--jsonl")
        try:
            recs = parse_jsonl(r.stdout)
            ok = set(recs[1]) == {"value", "count"} and recs[-1]["total"] == 2
        except Exception as e:
            ok = False
        check(f"J5/{entry_label} props --jsonl fields", r.returncode == 0 and ok, r.stdout[:120])
        # --limit composes: shown reflects the cap, total the truth
        r = run(entry, "tags", "--jsonl", "--limit", "1")
        try:
            recs = parse_jsonl(r.stdout)
            ok = recs[-1] == {"total": 2, "shown": 1}
        except Exception as e:
            ok = False
        check(f"J6/{entry_label} --jsonl + --limit trailer", ok, r.stdout[:120])
        # structured error on stderr, still one JSON object, exit preserved
        r = run(entry, "backlinks", "NoSuchNote", "--jsonl")
        try:
            e = json.loads(r.stderr.strip())
            ok = (e["exit"] == r.returncode == 1 and e["kind"] == "not-found"
                  and "next" in e and "message" in e)
        except Exception:
            ok = False
        check(f"J7/{entry_label} structured error", ok, r.stderr[:120])

    # lint --check: clean vault exits 0; a broken link makes it exit nonzero
    r = run([sys.executable, VV], "lint", "--quick", "--check")
    check("J8 lint --check clean exits 0", r.returncode == 0, r.stdout[:80] + r.stderr[:80])
    open(os.path.join(tv, "Wk/C.md"), "w").write("[[Does Not Exist Qq]]\n")
    r = run([sys.executable, VV], "lint", "--quick", "--check")
    check("J9 lint --check redes on findings", r.returncode == 1, f"exit={r.returncode} {r.stdout[:80]}")
    # and --jsonl diagnostics parse
    r = run([sys.executable, VV], "lint", "--quick", "--jsonl")
    try:
        recs = [json.loads(l) for l in r.stdout.strip().split("\n") if l]
        ok = recs[0]["cmd"] == "lint" and any("Does Not Exist Qq" in json.dumps(x) for x in recs)
    except Exception:
        ok = False
    check("J10 lint --jsonl diagnostics parse", ok, r.stdout[:120])
finally:
    shutil.rmtree(tv, ignore_errors=True)

print(f"\n{len(fails)} failures: {fails}" if fails else f"\nALL PASS (jsonl: 24)")
sys.exit(1 if fails else 0)
