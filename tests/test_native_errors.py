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
import json, os, shlex, shutil, stat, subprocess, sys, tempfile, atexit, hashlib

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

# A python entry that proves it was started: it appends what it was handed
# (each argument's bytes, and a hash of its stdin) to a file.
MARKED = os.path.join(mkdtemp("vv-nerr-entry-"), "entry.py")
with open(MARKED, "w") as f:
    f.write("import hashlib, json, os, sys\n"
            "rec = {'argv': [os.fsencode(a).hex() for a in sys.argv[1:]],\n"
            "       'stdin': hashlib.sha256(sys.stdin.buffer.read()).hexdigest()}\n"
            "open(os.environ['VV_NERR_MARK'], 'a').write(json.dumps(rec) + '\\n')\n"
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

    def handed(self):
        """What each python start received: [(argv as bytes, stdin sha256)]."""
        if not self.started():
            return []
        return [([bytes.fromhex(a) for a in r["argv"]], r["stdin"]) for r in map(json.loads, open(self.mark))]

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
    check(f"G1[{label}] python answers when a global flag is present, with the whole argv",
          got[0] == 42 and [a for a, _ in b.handed()] == [[x.encode() for x in argv]], (got, b.handed()))

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
    check("U2 python receives each argument byte for byte",
          [a for a, _ in b.handed()] == [[b"outline", b"x\xffy"]], b.handed())

    # A search is the one command python delegates BACK to the binary, so an
    # argument the binary declines must not start a second round. A wrapper
    # interpreter counts the starts and refuses past a cap, so a regression
    # is a red check, never a process storm.
    cap_dir = mkdtemp("vv-nerr-cap-")
    counter, capped = os.path.join(cap_dir, "count"), os.path.join(cap_dir, "py")
    with open(capped, "w") as f:
        f.write(f'#!/bin/sh\necho x >> {shlex.quote(counter)}\n'
                f'if [ "$(wc -l < {shlex.quote(counter)})" -gt 4 ]; then exit 99; fi\n'
                f'exec {shlex.quote(sys.executable)} "$@"\n')
    os.chmod(capped, 0o755)
    def starts():
        n = len(open(counter).read().split()) if os.path.exists(counter) else 0
        if os.path.exists(counter):
            os.remove(counter)
        return n
    for label, argv in (("one term", ["search", bad]), ("with a plain term", ["search", "alpha", bad]),
                        ("with a flag", ["search", bad, "--files"])):
        b = Box()
        want = oracle(b, argv)
        got = b.run(argv, VV_PY_ENTRY=VV, VV_PYTHON=capped)
        n = starts()
        check(f"U3[{label}] a non-UTF-8 search term is answered once, with python's answer",
              got == want and n == 1, (got, want, n))
        # VV_PYTHON too: a binary this python started would count its own hand-off
        got = b.run(argv, entry=[capped, VV], VV_PYTHON=capped)
        n = starts()
        check(f"U4[{label}] and the python launcher answers it without delegating",
              got == want and n == 1, (got, want, n))
    # The marker means "python started this process" only when it names the
    # parent's pid; this suite IS the parent of the binaries it runs.
    me = str(os.getpid())
    b = Box()
    got = b.run(["search", bad], VV_FROM_PY=me)
    check("U5 a binary that python called never hands the call back",
          got[0] == 1 and got[2].startswith(b"engine: ") and not b.started(), got)
    b = Box()
    got = b.run(["outline", "Nope"], VV_FROM_PY=me)
    check("U6 whatever the reason for declining", got[0] == 1 and got[2].startswith(b"engine: ") and not b.started(), got)
    b = Box()
    got = b.run(["search", "zzqq xxyy"], VV_FROM_PY=me)
    check("U7 (control) an ordinary miss is still answered for python",
          got[0] == 0 and got[1] == b"(0 of 0 matches)\n" and not b.started(), got)
    for label, value in (("another pid", "1"), ("empty", "")):
        b = Box()
        got = b.run(["outline", "Nope"], VV_FROM_PY=value)
        check(f"U8[{label}] a marker that does not name the parent changes nothing",
              got[0] == 42 and b.started(), got)

    # A vault whose path is not valid UTF-8: the binary cannot take it from
    # VV_VAULT, so python must scan it itself rather than let the binary
    # search its default vault.
    odd = os.path.join(mkdtemp("vv-nerr-oddvault-").encode(), b"v\xffault")
    try:
        os.makedirs(os.path.join(odd, b".obsidian"))
        with open(os.path.join(odd, b"Only.md"), "w") as f:
            f.write("# Only\n\nunique-token-here\n")
        made = True
    except OSError:
        made = False                    # a filesystem that refuses such a name
    if made:
        b = Box()
        e = dict(b.env, VV_VAULT=os.fsdecode(odd), VV_NO_METRICS="1")
        e.pop("VV_PY_ENTRY")
        r = subprocess.run([sys.executable, VV, "search", "unique-token-here", "--files"], capture_output=True,
                           env=e, timeout=120, cwd=b.base)
        check("U9 a search in a vault with an undecodable path is answered from that vault",
              r.returncode == 0 and r.stdout == b"Only.md\n(1 of 1 matches)\n", (r.returncode, r.stdout, r.stderr))

# ---- P: the post-stdin patch hand-off uses the shared launcher ----
# A patch whose body is not valid UTF-8 passes every pre-stdin check natively,
# then hands the consumed bytes to python: the one path through
# exec_python_with_stdin that needs no second writer.
def patch_handoff(**env):
    b = Box()
    rc, out, _ = b.run(["outline", "Hub.md"])
    sha = [l.split("\t")[-1] for l in out.decode().splitlines() if l.startswith("H2\t")][0]
    got = b.run(["patch", "Hub.md", "H2", sha], stdin=BODY, **env)
    return b, got, sha

BODY = b"## Details\n\n\xff\n"
b, got, sha = patch_handoff()
want_argv = [b"--vault", os.fsencode(b.vault), b"patch", b"Hub.md", b"H2", sha.encode()]
check("P1 the post-stdin hand-off reaches VV_PY_ENTRY with the consumed body and the vault",
      got[0] == 42 and b.handed() == [(want_argv, hashlib.sha256(BODY).hexdigest())], (got, b.handed()))
wrapper = os.path.join(mkdtemp("vv-nerr-wrap-"), "py")
with open(wrapper, "w") as f:
    f.write(f"#!/bin/sh\necho wrapped >> \"$VV_NERR_MARK.wrap\"\nexec {shlex.quote(sys.executable)} \"$@\"\n")
os.chmod(wrapper, os.stat(wrapper).st_mode | stat.S_IXUSR)
b, got, sha = patch_handoff(VV_PYTHON=wrapper)
check("P2 and honours VV_PYTHON", os.path.exists(b.mark + ".wrap") and b.started() and got[0] == 42, got)
missing = os.path.join(mkdtemp("vv-nerr-nopy-"), "python")
b, got, sha = patch_handoff(VV_PYTHON=missing)
check("P3 an unrunnable interpreter is the `engine:` refusal with its next step, exit 1",
      got[0] == 1 and got[2].decode() == f"engine: python engine unavailable ({missing} not runnable) \u2014 next: install python3, or set VV_PYTHON\n"
      and not b.started(), got)
check("P4 the vault is untouched when the hand-off cannot start",
      open(os.path.join(b.vault, "Hub.md"), "rb").read() == fixture.NOTES["Hub.md"].encode(), "Hub.md changed")
b, got, sha = patch_handoff(VV_FROM_PY=str(os.getpid()))
check("P5 the post-stdin launcher does not hand back to a python parent either",
      got[0] == 1 and got[2].startswith(b"engine: ") and not b.started(), got)
if os.name == "posix":
    # A read error part-way: a non-blocking pipe with some bytes in it and the
    # writer still open gives the bytes, then EAGAIN instead of end-of-file.
    b = Box()
    rc, out, _ = b.run(["outline", "Hub.md"])
    sha = [l.split("\t")[-1] for l in out.decode().splitlines() if l.startswith("H2\t")][0]
    rd, wr = os.pipe()
    os.set_blocking(rd, False)
    os.write(wr, b"## Details\n\nonly the first half")
    try:
        r = subprocess.run([VRUST, "patch", "Hub.md", "H2", sha], stdin=rd, capture_output=True, env=b.env,
                           timeout=120, cwd=b.base)
    finally:
        os.close(rd); os.close(wr)
    check("P6 a body that could not be read whole is refused, never applied or forwarded short",
          r.returncode == 1 and r.stderr.startswith(b"io: could not read the replacement body") and not b.started()
          and open(os.path.join(b.vault, "Hub.md"), "rb").read() == fixture.NOTES["Hub.md"].encode(),
          (r.returncode, r.stdout, r.stderr))

print(f"\n{len(fails)} failures" if fails else "\nALL PASS (native errors)")
sys.exit(1 if fails else 0)
