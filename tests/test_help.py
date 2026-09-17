#!/usr/bin/env python3
"""`vv <cmd> --help` / `-h` prints that command's COMMAND_TABLE line and exits
0 — before the positional-arity check, so a help request never needs real
operands. Read: `vv read --help` used to reach `_check_arity` first and answer
`usage: read takes 2 positional args, got 1` (measured 7 times in two weeks of
agent usage). Pinned through both entries — the native binary
(vrust/target/release/vrust) and `VV_ENGINE=python src/vv.py` — because every
native arm matches an exact arity, so a one-arg `--help` already falls back to
Python for every command; this test PROVES that fallback rather than assuming
it. `outline` and `backlinks` are included alongside the brief's six because
their native `run` matchers have a `args.len() == 1` arm (`outline NOTE`,
`backlinks NOTE`) — a resolve miss on the literal name `--help` returns
Fallback, so Python still answers, but that path is worth confirming
directly rather than by inference from the exact-arity rule alone.
"""
import os, sys, subprocess, tempfile, atexit, shutil

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VV = os.path.join(REPO, "src", "vv.py")
VRUST = os.path.join(REPO, "vrust", "target", "release", "vrust")
sys.path.insert(0, os.path.join(REPO, "src"))
import vv_impl  # noqa: E402  (import after sys.path insert, as other suites do)

_TMP = []
def mkdtemp(prefix):
    d = tempfile.mkdtemp(prefix=prefix); _TMP.append(d); return d
def _cleanup():
    for d in _TMP:
        shutil.rmtree(d, True)
atexit.register(_cleanup)

fails = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{str(detail)[:200]}]" if detail and not cond else ""))
    if not cond: fails.append(name)

NOTES = {
    "A.md": "# A\n",
}

class Engine:
    def __init__(self, name, env):
        self.name, self.env = name, env
        self.vault = mkdtemp(f"vv-help-{name}-vault-")
        self.journals = mkdtemp(f"vv-help-{name}-journals-")
        self.index = mkdtemp(f"vv-help-{name}-index-")
        for relp, body in NOTES.items():
            p = os.path.join(self.vault, relp)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                f.write(body)

    def run(self, *args, env=None):
        e = dict(os.environ, VV_VAULT=self.vault, VV_NO_METRICS="1", VV_INDEX_ROOT=self.index,
                 VV_JOURNAL_ROOT=self.journals, **self.env, **(env or {}))
        entry = [VRUST] if self.name == "rust" else [sys.executable, VV]
        # stdin closed: nothing here reads it, but an inherited open stdin
        # (a background runner's) could otherwise block the suite forever.
        return subprocess.run([*entry, *args], capture_output=True, text=True, env=e,
                              input="", timeout=60)

engines = [
    Engine("rust", {}),
    Engine("python", {"VV_ENGINE": "python"}),
]

# The expected synopsis is derived from the same COMMAND_TABLE the CLI renders
# from (imported directly), not hand-typed — so a table edit can't silently
# desync the test's expectation from the implementation.
COMMAND_TABLE_LINES = [f"vv {c['name']} {c['args']}".rstrip() for c in vv_impl.COMMAND_TABLE]

COMMANDS = ("read", "append", "appendsec", "patch", "daily-append", "move", "outline", "backlinks")

for eng in engines:
    for cmd in COMMANDS:
        r = eng.run(cmd, "--help")
        line = next(c for c in COMMAND_TABLE_LINES if c.startswith(f"vv {cmd} "))
        check(f"{eng.name}: {cmd} --help exits 0", r.returncode == 0, r.stderr)
        check(f"{eng.name}: {cmd} --help prints its synopsis", line in r.stdout, r.stdout)
        check(f"{eng.name}: {cmd} --help touches no note", "usage:" not in r.stderr, r.stderr)
    r = eng.run("read", "-h")
    check(f"{eng.name}: -h alias", r.returncode == 0, r.stderr)

print(f"\n{len(fails)} failed" if fails else "\nall passed")
sys.exit(1 if fails else 0)
