#!/usr/bin/env python3
"""Refusals the native entry words itself, proven with the python entry out of
reach. The bytes of each ported refusal are pinned by the golden replay
(tests/test_golden.py); this suite pins what the goldens cannot see: that
python was never started, what the metrics row says, and the edges around the
hand-off itself.

A  `read` arity. `vv read` with no operand, or with more than three, is
   answered natively with python's own line and exit 1.
G  A global flag (`--vault`, `--limit`) on the command line: python validates
   those before arity, so the native entry hands the call off instead.
M  The row a native refusal logs: exit code and kind, timed from process
   start, with none of the fields python records on success only.
U  An argument that is not valid UTF-8 is handed to python byte for byte
   instead of crashing the binary.
P  The post-stdin `patch` hand-off finds python the way every other hand-off
   does (VV_PYTHON, VV_PY_ENTRY) and says so when it cannot.
"""
import json, os, shutil, stat, subprocess, sys, tempfile, atexit, hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
VV = os.path.join(REPO, "src", "vv.py")
VRUST = os.path.join(REPO, "vrust", "target", "release", "vrust")
sys.path.insert(0, os.path.join(HERE, "golden"))
import fixture                      # noqa: E402

_TMP = []
def mkdtemp(prefix):
    d = os.path.realpath(tempfile.mkdtemp(prefix=prefix)); _TMP.append(d); return d
atexit.register(lambda: [shutil.rmtree(d, True) for d in _TMP])

fails = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{str(detail)[:300]}]" if detail and not cond else ""))
    if not cond: fails.append(name)

if not os.path.exists(VRUST):
    print("FAIL vrust/target/release/vrust is not built")
    sys.exit(1)

# A python entry that proves it was started: it appends its argv to a file.
MARKED = os.path.join(mkdtemp("vv-nerr-entry-"), "entry.py")
with open(MARKED, "w") as f:
    f.write("import json, os, sys\n"
            "open(os.environ['VV_NERR_MARK'], 'ab').write((json.dumps(os.fsencode(' '.join(sys.argv[1:])).hex()) + '\\n').encode())\n"
            "sys.stderr.write('HANDOFF\\n'); sys.exit(42)\n")

class Box:
    def __init__(self, metrics=False):
        self.base = mkdtemp("vv-nerr-")
        self.vault = os.path.join(self.base, "vault")
        fixture.build(self.vault)
        self.home = os.path.join(self.base, "home")
        os.makedirs(os.path.join(self.home, ".claude", "metrics"))
        self.mark = os.path.join(self.base, "started")
        self.env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": self.home, "TZ": "UTC",
                    "VV_VAULT": self.vault, "VV_INDEX_ROOT": os.path.join(self.base, "index"),
                    "VV_PY_ENTRY": MARKED, "VV_NERR_MARK": self.mark}
        if not metrics:
            self.env["VV_NO_METRICS"] = "1"

    def run(self, argv, stdin=b"", entry=None, **env):
        e = dict(self.env, **env)
        r = subprocess.run((entry or [VRUST]) + argv, input=stdin, capture_output=True, env=e, timeout=120, cwd=self.base)
        return r.returncode, r.stdout, r.stderr

    def started(self):
        return os.path.exists(self.mark)

    def rows(self):
        p = os.path.join(self.home, ".claude", "metrics", "vv.jsonl")
        return [json.loads(l) for l in open(p)] if os.path.exists(p) else []


def oracle(box, argv):
    e = dict(box.env, VV_ENGINE="python", VV_NO_METRICS="1")
    r = subprocess.run([sys.executable, VV] + argv, capture_output=True, env=e, timeout=120, cwd=box.base)
    return r.returncode, r.stdout, r.stderr

# ---- A: read arity, python unreachable ----
for label, argv in (("no operand", ["read"]),
                    ("four operands", ["read", "Hub.md", "a", "b", "c"]),
                    ("a quoted operand", ["read", "it's a note", "b", "c", "d"]),
                    ("an empty operand", ["read", "", "b", "c", "d"]),
                    ("a control character", ["read", "x\x01y", "b", "c", "d"]),
                    ("the separator in an operand", ["read", "a \u2014 next: b", "x", "y", "z"]),
                    ("a non-ASCII operand", ["read", "Caf\u00e9", "b", "c", "d"]),
                    ("a flag-shaped operand", ["read", "--weird", "b", "c", "d"])):
    b = Box()
    got = b.run(argv)
    check(f"A1[{label}] the native entry answers without starting python", not b.started() and got[0] == 1, got)
    check(f"A2[{label}] with python's own bytes", got == oracle(b, argv), (got, oracle(b, argv)))

b = Box()
got = b.run(["read", "Hub.md", "H2"])
check("A3 (control) a well-formed read is still served", got[0] == 0 and not b.started(), got)
b = Box()
got = b.run(["read", "Hub.md", "H1", "H2"])
check("A4 (control) a three-operand refusal that is not arity still hands off", b.started() and got[0] == 42, got)

# ---- G: a global flag hands the refusal off ----
for label, argv in (("--limit", ["--limit", "2", "read"]), ("--vault", ["read", "--vault", "/nonexistent-vault"]),
                    ("--vault value-less", ["read", "a", "b", "c", "d", "--vault"])):
    b = Box()
    got = b.run(argv)
    check(f"G1[{label}] python answers when a global flag is present", b.started() and got[0] == 42, got)

# ---- M: the metrics row of a native refusal ----
b = Box(metrics=True)
b.run(["read"])
rows = b.rows()
check("M1 exactly one row", len(rows) == 1, rows)
if rows:
    r = rows[0]
    line = "usage: read takes 1-3 positional args, got 0 \u2014 next: vv outline NOTE\n"
    check("M2 it names the op, the exit code, the kind and the engine",
          (r.get("op"), r.get("exit"), r.get("kind"), r.get("engine")) == ("read", 1, "usage", "native"), r)
    check("M3 out_bytes is the refusal line's UTF-8 length", r.get("out_bytes") == len(line.encode("utf-8")), r)
    check("M4 no success-only fields", not ({"cf_bytes", "ref", "sel", "fb"} & set(r)), r)
    check("M5 ms is a small non-negative number", isinstance(r.get("ms"), int) and 0 <= r["ms"] < 60_000, r)
b = Box(metrics=True)
b.run(["read"], VV_NO_METRICS="1")
check("M6 (control) VV_NO_METRICS suppresses the row", b.rows() == [], b.rows())

# ---- U: non-UTF-8 argv ----
if os.name == "posix":
    b = Box()
    bad = os.fsdecode(b"x\xffy")
    try:
        got = b.run(["outline", bad])
        ok = True
    except (UnicodeEncodeError, ValueError, OSError) as ex:
        got, ok = repr(ex), False
    check("U1 an undecodable argument does not crash the binary", ok and got[0] == 42 and b"panicked" not in got[2], got)
    if ok and b.started():
        seen = [bytes.fromhex(json.loads(l)) for l in open(b.mark, "rb")]
        check("U2 python receives the argument byte for byte", seen == [b"outline x\xffy"], seen)
    else:
        check("U2 python receives the argument byte for byte", False, "python was not started")

# ---- P: the post-stdin patch hand-off uses the shared launcher ----
# A patch whose body is not valid UTF-8 passes every pre-stdin check natively,
# then hands the consumed bytes to python: the one path through
# exec_python_with_stdin that needs no second writer.
def patch_handoff(**env):
    b = Box()
    rc, out, _ = b.run(["outline", "Hub.md"])
    sha = [l.split("\t")[-1] for l in out.decode().splitlines() if l.startswith("H2\t")][0]
    got = b.run(["patch", "Hub.md", "H2", sha], stdin=b"## Details\n\n\xff\n", **env)
    return b, got

b, got = patch_handoff()
check("P1 the post-stdin hand-off reaches VV_PY_ENTRY", b.started() and got[0] == 42, got)
wrapper = os.path.join(mkdtemp("vv-nerr-wrap-"), "py")
with open(wrapper, "w") as f:
    f.write(f"#!/bin/sh\necho wrapped >> \"$VV_NERR_MARK.wrap\"\nexec {sys.executable} \"$@\"\n")
os.chmod(wrapper, os.stat(wrapper).st_mode | stat.S_IXUSR)
b, got = patch_handoff(VV_PYTHON=wrapper)
check("P2 and honours VV_PYTHON", os.path.exists(b.mark + ".wrap") and b.started() and got[0] == 42, got)
b, got = patch_handoff(VV_PYTHON="/nonexistent/python")
check("P3 an unrunnable interpreter is an `engine:` refusal, exit 1",
      got[0] == 1 and got[2].startswith(b"engine: python engine unavailable (/nonexistent/python not runnable)"), got)
b, got = patch_handoff(VV_PY_ENTRY=os.path.join(mkdtemp("vv-nerr-skew-"), "src", "vv.py"))
check("P4 (control) the entry path is passed through as given", got[0] != 0 and not b.started(), got)

print(f"\n{len(fails)} failures" if fails else "\nALL PASS (native errors)")
sys.exit(1 if fails else 0)
