#!/usr/bin/env python3
"""P5a pins: unresolved, prepend, templates (+ new's ambiguity refusal).

  * unresolved — wiki links whose target resolves to no note: `from  line
    target` TSV, --limit/--jsonl aware. Resolution matches the resolver the
    graph commands use: last-segment stem in the basename index, or an exact
    vault-relative path; Templates/ count as resolvable (lint's rule).
  * prepend NOTE TEXT — inserted AFTER frontmatter (after the BOM when there is
    no frontmatter; body-only files with an unterminated fm block are body).
    CAS-guarded like append; CRLF and EOF-newline preserved byte-for-byte.
  * templates — lists template stems; new --template refuses an AMBIGUOUS
    prefix instead of silently taking the first lexicographic hit.
"""
import json, os, subprocess, sys, tempfile, shutil

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VR = os.path.join(REPO, "vrust/target/release/vrust")
VV = os.path.join(REPO, "src/vv.py")

fails = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond: fails.append(name)

tv = tempfile.mkdtemp(prefix="vv-p5a-")
try:
    os.makedirs(os.path.join(tv, "Templates"))
    open(os.path.join(tv, "Templates/todo.md"), "w").write("---\ntype: todo\n---\n")
    open(os.path.join(tv, "Templates/todo-weekly.md"), "w").write("---\ntype: todo\n---\nweekly\n")
    open(os.path.join(tv, "Good.md"), "w").write("# G\nok\n")
    open(os.path.join(tv, "Src.md"), "w").write("# S\n[[Good]] then [[Missing One]]\nand [[Sub/Missing Two]]\nand [[todo]]\n")
    open(os.path.join(tv, "FM.md"), "w", newline="").write("---\ntype: x\n---\nbody line\n")
    open(os.path.join(tv, "NoFM.md"), "w", newline="").write("just body\n")
    open(os.path.join(tv, "Crlf.md"), "wb").write(b"---\r\ntype: x\r\n---\r\nbody\r\n")
    env = dict(os.environ, VV_NO_METRICS="1", VV_VAULT=tv)

    def run(cmd, *args, stdin=None):
        return subprocess.run(cmd + list(args), capture_output=True, text=True, env=env, input=stdin)

    for label, entry in (("python", [sys.executable, VV]), ("native", [VR])):
        # unresolved: exactly the two missing targets, from+line+target, sorted by from/line
        r = run(entry, "unresolved")
        lines = r.stdout.strip().split("\n")
        check(f"U1/{label} unresolved finds exactly the broken pair",
              r.returncode == 0 and "(2 unresolved)" in lines[-1]
              and lines[0] == "Src.md\t2\tMissing One" and lines[1] == "Src.md\t3\tSub/Missing Two",
              r.stdout[:120] + r.stderr[:60])
        # template link resolves (lint's rule), Good resolves, so neither appears
        check(f"U2/{label} resolvable targets absent", "todo" not in r.stdout and "Good" not in r.stdout, r.stdout[:80])
        r = run(entry, "unresolved", "--jsonl")
        try:
            recs = [json.loads(l) for l in r.stdout.strip().split("\n")]
            ok = recs[1] == {"from": "Src.md", "line": 2, "target": "Missing One"} and recs[-1]["total"] == 2
        except Exception:
            ok = False
        check(f"U3/{label} unresolved --jsonl", ok, r.stdout[:120])

        # templates: stems, sorted
        r = run(entry, "templates")
        check(f"T1/{label} templates lists stems",
              r.returncode == 0 and r.stdout.splitlines()[:2] == ["todo", "todo-weekly"]
              and "(2 templates)" in r.stdout, r.stdout[:80])
        # new --template ambiguous prefix refused, exact match still works
        r = run(entry, "new", "Zz1", "--template", "todo-w")
        check(f"T2/{label} unambiguous prefix ok", r.returncode == 0 and os.path.exists(os.path.join(tv, "Zz1.md")), r.stdout + r.stderr[:80])
        os.remove(os.path.join(tv, "Zz1.md"))
        r = run(entry, "new", "Zz2", "--template", "tod")
        check(f"T3/{label} ambiguous prefix refused",
              r.returncode == 1 and "ambiguous" in r.stderr and "todo-weekly" in r.stderr
              and not os.path.exists(os.path.join(tv, "Zz2.md")), r.stderr[:100])
        r = run(entry, "new", "Zz3", "--template", "todo")
        check(f"T4/{label} exact match beats prefix ambiguity",
              r.returncode == 0 and "weekly" not in open(os.path.join(tv, "Zz3.md")).read(), r.stderr[:80])
        os.remove(os.path.join(tv, "Zz3.md"))
        # duplicated EXACT stem across subfolders: refused, and the listing marks it
        os.makedirs(os.path.join(tv, "Templates/sub"), exist_ok=True)
        open(os.path.join(tv, "Templates/sub/todo.md"), "w").write("---\ntype: sub\n---\n")
        r = run(entry, "new", "Zz4", "--template", "todo")
        check(f"T5/{label} duplicated exact stem refused",
              r.returncode == 1 and "ambiguous" in r.stderr and "sub/todo" in r.stderr
              and not os.path.exists(os.path.join(tv, "Zz4.md")), r.stderr[:100])
        r = run(entry, "templates")
        check(f"T6/{label} duplicate stems carry ambiguity markers",
              r.stdout.count("(ambiguous:") == 2 and "sub/todo" in r.stdout, r.stdout[:120])
        shutil.rmtree(os.path.join(tv, "Templates/sub"))

        # prepend: after fm; body-only at top; CRLF preserved
        for name, before, expect in (
            ("FM.md", "---\ntype: x\n---\nbody line\n", "---\ntype: x\n---\nNEW\nbody line\n"),
            ("NoFM.md", "just body\n", "NEW\njust body\n"),
        ):
            open(os.path.join(tv, name), "w", newline="").write(before)
            r = run(entry, "prepend", name, "NEW")
            got = open(os.path.join(tv, name), newline="").read()
            check(f"P1/{label} prepend {name}", r.returncode == 0 and got == expect, repr(got))
        open(os.path.join(tv, "Crlf.md"), "wb").write(b"---\r\ntype: x\r\n---\r\nbody\r\n")
        r = run(entry, "prepend", "Crlf.md", "NEW")
        got = open(os.path.join(tv, "Crlf.md"), "rb").read()
        check(f"P2/{label} prepend CRLF byte-exact",
              got == b"---\r\ntype: x\r\n---\r\nNEW\r\nbody\r\n", repr(got))
finally:
    shutil.rmtree(tv, ignore_errors=True)

print(f"\n{len(fails)} failures: {fails}" if fails else "\nALL PASS (surface5: 24)")
sys.exit(1 if fails else 0)
