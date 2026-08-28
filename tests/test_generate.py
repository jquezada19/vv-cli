#!/usr/bin/env python3
"""P6b pins: --generate man|complete-bash|complete-zsh|complete-fish, driven by
a declarative command table (COMMAND_TABLE) that a drift guard pins to CMDS in
BOTH directions — a command added to one without the other fails the gate, so
generated docs cannot describe a surface the dispatcher doesn't have (or miss
one it does)."""
import os, re, subprocess, sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VR = os.path.join(REPO, "vrust/target/release/vrust")
VV = os.path.join(REPO, "src/vv.py")
sys.path.insert(0, os.path.join(REPO, "src"))
os.environ.setdefault("VV_NO_METRICS", "1")
import vv  # noqa: E402

fails = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond: fails.append(name)

# drift guard: table <-> dispatcher, both directions
table_names = {c["name"] for c in getattr(vv, "COMMAND_TABLE", [])}
cmd_names = set(vv.CMDS)
check("G0 table == CMDS (both directions)", table_names == cmd_names,
      f"only-table={sorted(table_names - cmd_names)} only-cmds={sorted(cmd_names - table_names)}")

env = dict(os.environ, VV_NO_METRICS="1")
def run(entry, *args):
    return subprocess.run(entry + list(args), capture_output=True, text=True, env=env)

for label, entry in (("python", [sys.executable, VV]), ("native", [VR])):
    r = run(entry, "--generate", "man")
    check(f"G1/{label} man is roff with every command",
          r.returncode == 0 and r.stdout.startswith(".TH VV 1")
          and all(f"\\fB{c}\\fR" in r.stdout for c in cmd_names), r.stdout[:80] + r.stderr[:60])
    for shell, needle in (("bash", "complete "), ("zsh", "#compdef vv"), ("fish", "complete -c vv")):
        r = run(entry, "--generate", f"complete-{shell}")
        check(f"G2/{label} {shell} completion covers every command",
              r.returncode == 0 and needle in r.stdout
              and all(re.search(rf"\b{re.escape(c)}\b", r.stdout) for c in cmd_names),
              r.stdout[:80] + r.stderr[:60])
    r = run(entry, "--generate", "nope")
    check(f"G3/{label} unknown kind is grep-stable",
          r.returncode == 1 and "usage:" in r.stderr and "next:" in r.stderr, r.stderr[:80])

print(f"\n{len(fails)} failures: {fails}" if fails else "\nALL PASS (generate: 11)")
sys.exit(1 if fails else 0)
