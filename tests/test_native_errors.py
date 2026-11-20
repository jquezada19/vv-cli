#!/usr/bin/env python3
"""Refusals the native entry words itself, proven with the python entry out of
reach. The bytes of each ported refusal are pinned by the golden replay
(tests/test_golden.py); this suite pins what the goldens cannot see: that
python was never started, what the metrics row says, and the edges around the
hand-off itself.

A  `read` arity. `vv read` with no operand, or with more than three, is
   answered natively with python's own line and exit 1.
B  Arity for every command the native dispatcher serves, from one operand
   table: the count python reports, the hint (an unquoted TEXT or VALUE, a
   `--section` given to `append`, a malformed `set` KEY) and the runnable next
   step with the caller's own operands quoted into their slots.
G  A global flag (`--vault`, `--limit`) on the command line: python validates
   those before arity, so the native entry hands the call off instead.
M  The row a native refusal logs: exit code and kind, timed from process
   start, with none of the fields python records on success only.
U  An argument that is not valid UTF-8 is handed to python byte for byte
   instead of crashing the binary.
P  The post-stdin `patch` hand-off finds python the way every other hand-off
   does (VV_PYTHON, VV_PY_ENTRY) and says so when it cannot.
"""
import json, os, re, shlex, shutil, stat, subprocess, sys, tempfile, atexit, hashlib

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

# ---- B: arity across the table, differentially ----
# Every command, every operand count from 0 to 5, several operand shapes. The
# python engine decides what the answer is; when its answer is the arity
# refusal the native entry must give the same bytes without starting python,
# and otherwise must hand the call off untouched.
SHAPES = {
    "plain": ["Hub.md", "H2", "x", "y", "z"],
    "spaced": ["Area/Solo.md", "two words", "it's", "a b", "c"],
    "flags": ["Hub.md", "--section", "H2", "--k", "-x"],
    "bullet": ["Hub.md", "- item", "text", "more", "words"],
    "bad-key": ["Area/Solo.md", "a=b", "c", "d", "e"],
    "odd": ["x\x01y", "", "a \u2014 next: b", "caf\u00e9", "$(id)"],
    "dash-first": ["--weird", "Hub.md", "x", "y", "z"],
}
COMMANDS = ("outline read show head resolve search patch append appendsec set unset "
            "links backlinks orphans deadends board tags props").split()
import threading
_local = threading.local()
WRITERS = {"patch", "append", "appendsec", "set", "unset"}

def one_arity_case(job):
    """Run one argv through the oracle and the native entry and classify the
    pair. Each worker keeps one fixture copy per engine and replaces it only
    after a call that could have written: building a vault per case is the
    slow part of this loop."""
    cmd, shape, n, argv = job
    if not hasattr(_local, "a"):
        _local.a, _local.b = Box(), Box()
    a, b = _local.a, _local.b
    if os.path.exists(b.mark):
        os.remove(b.mark)
    want = oracle(a, argv)
    got = b.run(argv)
    started = b.started()
    # the boundary check's own wording; `append` with three operands is
    # refused later, by the command, with a line that looks alike
    is_arity = bool(re.match(rf"usage: {cmd} takes [0-9+-]+ positional args, got {n}\b",
                             want[2].decode("utf-8", "replace"))) and not (cmd == "append" and n == 3)
    if cmd in WRITERS and not is_arity:
        del _local.a, _local.b            # a write may have landed: start clean next time
    return cmd, shape, n, is_arity, want, got, started

jobs = [(cmd, shape, n, [cmd] + pool[:n])
        for cmd in COMMANDS for shape, pool in SHAPES.items() for n in range(0, 6)
        # numeric-flag refusals have their own suite
        if not (cmd == "search" and any(a in ("--k", "--w") for a in pool[:n]))]
import concurrent.futures
tried = refused = others = 0
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool_:
    for cmd, shape, n, is_arity, want, got, started in pool_.map(one_arity_case, jobs):
        if is_arity:
            tried += 1
            ok = got == want and not started
            refused += ok
            if not ok:
                check(f"B1[{cmd} {shape} {n}] the native entry gives python's arity refusal itself", False, (got, want))
        else:
            others += 1
            if re.match(rb"usage: \S+ takes [0-9+-]+ positional args", got[2]):
                check(f"B1x[{cmd} {shape} {n}] no native arity refusal where python gives none", False, (got, want))
check(f"B1 every arity refusal python gives ({tried}) is answered natively with the same bytes", refused == tried and tried >= 150,
      (refused, tried))
check(f"B1b no native arity refusal where python gives none ({others} other calls checked)",
      others >= 100 and not any(f.startswith("B1x") for f in fails), others)
for cmd, argv in (("tags", ["tags", "a", "b", "c"]), ("search", ["search"]), ("show", ["show", "Hub.md", "x", "y"])):
    b = Box()
    got = b.run(argv)
    check(f"B2[{cmd}] (control) a count python accepts is not turned into an arity refusal",
          b"takes" not in got[2], got)
b = Box()
got = b.run(["--limit", "3", "outline"])
check("B3 with a global flag the arity refusal is python's", b.started() and got[0] == 42, got)
b = Box()
got = b.run(["search"])
check("B4 a search with no query is python's usage line, given natively",
      got == oracle(b, ["search"]) and not b.started() and got[2].startswith(b"usage: search needs a query"), (got, oracle(b, ["search"])))
b = Box()
got = b.run(["--limit", "3", "search"])
check("B5 with a global flag that refusal is python's too", b.started() and got[0] == 42, got)
# the whole route, with the real python reachable: whichever entry is used and
# whoever delegates to whom, an empty query ends in python's usage line
for label, argv in (("bare", ["search"]), ("--files", ["search", "--files"]), ("--k", ["search", "--k", "3"]),
                    ("--limit", ["--limit", "3", "search"])):
    b = Box()
    want = oracle(b, argv)
    got = b.run(argv, VV_PY_ENTRY=VV)
    check(f"B6[{label}] an empty query through the native entry is python's answer", got == want, (got, want))
    got = b.run(argv, entry=[sys.executable, VV])
    check(f"B7[{label}] and through the python launcher with the scanner built", got == want, (got, want))

# ---- R: resolver refusals, differentially ----
# A vault with the shapes a reference can fail on: duplicate basenames,
# duplicate ids, names that need quoting or escaping, symlinks that leave the
# vault (typed, found by name, found by id, through a directory), dangling and
# looping links, and, in the "locked" variant, directories that cannot be
# listed. Python decides the answer; an `escape:`, `ambiguous:` or
# `refused: cannot prove` answer must come from the native entry itself with
# the same bytes and the same files, and whatever else the native entry
# answers without python must match too.
R_OUTSIDE = mkdtemp("vv-nerr-outside-")
with open(os.path.join(R_OUTSIDE, "secret.md"), "w") as f:
    f.write("# secret\n\n## S\n\nx\n")

def r_extend(vault, locked):
    def w(rel_, txt="# t\n\n## S\n\nbody\n"):
        os.makedirs(os.path.dirname(os.path.join(vault, rel_)), exist_ok=True)
        with open(os.path.join(vault, rel_), "w") as f_:
            f_.write(txt)
    for d in ("Many", "Many1", "Many2", "Many3", "Many4", "Many5"):
        w(d + "/Dupe.md")
    for n in ("alpha one", "alpha two", "alpha three", "alphabet"):
        w(f"Sub/{n}.md")
    w("Sub/it's.md"); w("Sub2/it's.md")
    w("Ctl/new\nline.md"); w("Ctl2/new\nline.md")
    w("Sep/a — next: b.md"); w("Sep2/a — next: b.md")
    w("Ids/777 - one.md"); w("Ids/888 - a.md"); w("Ids2/888 - b.md")
    w("#555.md"); w("Ids/556 - hash.md"); w("557.md")
    secret = os.path.join(R_OUTSIDE, "secret.md")
    os.symlink(secret, os.path.join(vault, "Esc.md"))
    os.symlink(secret, os.path.join(vault, "Sub", "EscName.md"))
    os.symlink(secret, os.path.join(vault, "Ids", "444 - esc.md"))
    os.symlink(R_OUTSIDE, os.path.join(vault, "OutDir"))
    os.symlink("/nonexistent/zzz.md", os.path.join(vault, "Dangling.md"))
    os.symlink("Loop.md", os.path.join(vault, "Loop.md"))
    os.symlink("Hub.md", os.path.join(vault, "InLink.md"))
    os.symlink("Area", os.path.join(vault, "AreaLink"))
    os.symlink("Area", os.path.join(vault, "DirNote.md"))
    if locked:
        w("Locked/in/x.md")
        os.makedirs(os.path.join(vault, "Zed"))
        os.chmod(os.path.join(vault, "Locked"), 0)
        os.chmod(os.path.join(vault, "Zed"), 0)

def r_snapshot(vault):
    out = {}
    for d, _ds, fs in os.walk(vault):
        for n in fs:
            p_ = os.path.join(d, n)
            out[os.path.relpath(p_, vault)] = ("link", os.readlink(p_)) if os.path.islink(p_) else open(p_, "rb").read()
    return out

R_REFS = ["Hub", "hub", "Twin", "twin.md", "Dupe", "Solo", "nope", "Nope/zzz", "alpha", "alph", "it's", "new\nline",
          "a — next: b", "777", "#777", "888", "#888", "#555", "#556", "557", "#557", "#557.md", "0", "#", "#x", "１２３",
          "101", "202", "303", "#303", "444", "#444", "Esc", "Esc.md", "EscName", "OutDir/secret", "OutDir/nope",
          "../x", "/etc/hosts", "/etc/nope", "Dangling", "Dangling.md", "Loop", "InLink", "AreaLink/Solo", "DirNote",
          "", ".", "..", "Area", "Area/../Hub", "Area/../../x", "Hub.md/x", "Locked/in/x", "Zed", "x\x01y"]
R_CMDS = {"resolve": lambda r: ["resolve", r], "read": lambda r: ["read", r, "H1"], "show": lambda r: ["show", r],
          "links": lambda r: ["links", r], "set": lambda r: ["set", r, "k", "v"],
          "appendsec": lambda r: ["appendsec", r, "H1", "txt"], "patch": lambda r: ["patch", r, "H1", "deadbeef"]}

def one_resolver_case(job):
    locked, cmd, ref = job
    argv = R_CMDS[cmd](ref)
    res = []
    for engine in ("python", "native"):
        b_ = Box()
        r_extend(b_.vault, locked)
        if engine == "python":
            out_ = oracle(b_, argv)
        else:
            out_ = b_.run(argv, stdin=b"new body\n")
        if locked:
            os.chmod(os.path.join(b_.vault, "Locked"), 0o755)
            os.chmod(os.path.join(b_.vault, "Zed"), 0o755)
        res.append((tuple(x.replace(b_.base.encode(), b"<BOX>") if isinstance(x, bytes) else x for x in out_),
                    r_snapshot(b_.vault), b_.started()))
    return locked, cmd, ref, res

r_jobs = ([(False, c, r) for c in ("resolve", "read", "set") for r in R_REFS]
          + [(False, c, r) for c in ("show", "links", "appendsec", "patch") for r in R_REFS[::4]]
          + [(True, c, r) for c in ("resolve", "set", "patch") for r in R_REFS[::3]])
r_kinds = {}
r_warned = [0, 0]
r_native_ok = set()
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool_:
    for locked, cmd, ref, ((want, want_files, _), (got, got_files, started)) in pool_.map(one_resolver_case, r_jobs):
        err_ = want[2].decode("utf-8", "replace")
        kind = err_.split(":")[0] if want[0] else "ok"
        if kind == "refused" and "cannot prove" not in err_:
            kind = "refused-other"
        label = f"{'locked ' if locked else ''}{cmd} {ref!r}"
        r_kinds.setdefault(kind, [0, 0])[1 if started else 0] += 1
        if want[2].startswith(b"warning:"):
            r_warned[1 if started else 0] += 1
        if not started and not locked and cmd == "resolve" and want[0] == 0:
            r_native_ok.add(ref)
        if started:
            if kind in ("escape", "ambiguous", "refused"):
                check(f"R1x[{label}] the native entry words python's {kind}: refusal itself", False, (got, want))
        elif (got, got_files) != (want, want_files):
            check(f"R2x[{label}] what the native entry answers without python is python's answer", False, (got, want))
check("R1 every escape:, ambiguous: and cannot-prove refusal python gives is answered natively "
      f"({ {k: v[0] for k, v in r_kinds.items()} })",
      not any(f.startswith("R1x") for f in fails)
      and all(r_kinds.get(k, [0, 0])[0] >= n for k, n in (("escape", 20), ("ambiguous", 15), ("refused", 10))), r_kinds)
check("R2 and every native answer equals python's, bytes and files", not any(f.startswith("R2x") for f in fails), r_kinds)
check("R3 a not-found with three names containing the miss is native; the similarity tier still hands off",
      r_kinds.get("not-found", [0, 0])[0] >= 3 and r_kinds.get("not-found", [0, 0])[1] >= 3, r_kinds)
check("R4 (control) references that resolve are still served natively", r_kinds.get("ok", [0, 0])[0] >= 20, r_kinds)
check("R6 a name, an id and the #id spelling each resolve without python",
      {"Solo", "777", "#777", "#557", "#555"} <= r_native_ok, sorted(r_native_ok))
check("R5 a read over a directory that cannot be listed is still python's (it warns and answers)",
      r_warned[0] == 0 and r_warned[1] >= 3, r_warned)

# ---- W: a malformed global flag never reaches a native command ----
# `--vault` with no value used to stay in the operand list, so
# `append NOTE --vault` appended the literal text; an odd `--limit` value was
# read natively where python refuses it. Both now reach python untouched.
def w_box():
    """A box with a note where each misread vault would look for one: the
    default vault (an empty value), a directory named `~` in the working
    directory, and the directory `link/../notes` names when the link is
    followed before the `..` is applied. A native write lands in one of them."""
    b_ = Box()
    os.makedirs(os.path.join(b_.base, "real", "sub"))
    os.symlink(os.path.join(b_.base, "real", "sub"), os.path.join(b_.base, "lnk"))
    b_.decoys = [os.path.join(top, "Area", "Solo.md") for top in (
        os.path.join(b_.home, "Documents", "Obsidian Vault"), os.path.join(b_.base, "~"),
        os.path.join(b_.base, "real", "notes"))]
    for d in b_.decoys:
        os.makedirs(os.path.dirname(d))
        with open(d, "w") as f_:
            f_.write("decoy\n")
    return b_

for label, argv in (("--vault with no value", ["append", "Area/Solo.md", "--vault"]),
                    ("--limit +1", ["--limit", "+1", "append", "Area/Solo.md", "x"]),
                    ("--limit with no value", ["append", "Area/Solo.md", "x", "--limit"]),
                    ("--limit 0", ["--limit", "0", "set", "Area/Solo.md", "k", "v"]),
                    ("--limit past 64 bits", ["--limit", "18446744073709551616", "append", "Area/Solo.md", "x"]),
                    ("--limit non-ASCII digit", ["--limit", "\u0663", "append", "Area/Solo.md", "x"]),
                    ("--limit zero-padded past python's digit limit", ["--limit", "0" * 4400 + "1", "append", "Area/Solo.md", "x"]),
                    ("--limit 19 digits", ["--limit", "1" * 19, "append", "Area/Solo.md", "x"]),
                    ("--vault empty", ["--vault", "", "append", "Area/Solo.md", "x"]),
                    ("--vault tilde", ["--vault", "~", "append", "Area/Solo.md", "x"]),
                    ("--vault tilde-slash", ["--vault", "~/nowhere-vv-test", "append", "Area/Solo.md", "x"]),
                    ("--vault not a directory", ["--vault", "/nonexistent-vv-vault", "tags"]),
                    ("--vault a file", ["--vault", "Hub.md", "tags"]),
                    # python normalises to a directory that does not exist; followed as
                    # written it exists, and a native `tags` there answers an empty success
                    ("--vault through a link and ..", ["--vault", "{base}/lnk/../notes", "tags"])):
    b = w_box()
    argv = [a.replace("{base}", b.base) for a in argv]
    before = open(os.path.join(b.vault, "Area", "Solo.md"), "rb").read()
    got = b.run(argv)
    check(f"W1[{label}] the call is handed to python with its argv intact, nothing written",
          got[0] == 42 and [a for a, _ in b.handed()] == [[x.encode() for x in argv]]
          and open(os.path.join(b.vault, "Area", "Solo.md"), "rb").read() == before
          and all(open(d).read() == "decoy\n" for d in b.decoys), (got, b.handed()))
    argv = [a.replace(b.base, "{base}") for a in argv]
    b = w_box()
    argv = [a.replace("{base}", b.base) for a in argv]
    want = oracle(b, argv)
    got = b.run(argv, VV_PY_ENTRY=VV)
    check(f"W2[{label}] and the caller gets python's answer", got == want, (got, want))
b = Box()
got = b.run(["--limit", "2", "orphans"])
check("W3 (control) a plain --limit is still taken natively, and applied",
      got[0] == 0 and not b.started() and got == oracle(b, ["--limit", "2", "orphans"]), got)
b = Box()
got = b.run(["--vault", b.vault, "outline", "Hub.md"], VV_VAULT="/nonexistent-vv-vault")
check("W4 (control) a --vault naming a real directory is still taken natively",
      got[0] == 0 and not b.started() and got[1].startswith(b"H0\t"), got)

# Operands are literal text to python, but the binary parses its command line
# again: a term that looks like a global flag, or a leading help flag, must
# stay in python rather than be re-read there.
for label, argv in (("a term named --limit", ["--limit", "1", "search", "--limit"]),
                    ("a second --limit pair", ["--limit", "1", "search", "alpha", "--limit", "1"]),
                    ("a term named --vault", ["search", "alpha", "--vault"]),
                    ("a phrase that splits into --limit 1 alpha", ["search", "--limit 1 alpha"]),
                    ("a phrase that splits into --help alpha", ["search", "--help alpha"])):
    b = Box()
    want = oracle(b, argv)
    got = b.run(argv, entry=[sys.executable, VV])
    check(f"W5[{label}] the python launcher with the scanner built gives pure python's answer", got == want, (got, want))
    got = b.run(argv, VV_PY_ENTRY=VV)
    check(f"W6[{label}] and so does the native entry", got == want, (got, want))

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

    # The predicate itself, on every host (the filesystem case below needs a
    # volume that accepts such a name; APFS does not).
    probe = ("import os, sys; sys.path.insert(0, os.path.join(sys.argv[1], 'src')); import vv_impl as v; "
             "print(v._native_argv_ok(['plain']), v._native_argv_ok([os.fsdecode(b'x\\xffy')]))")
    def predicate(vault):
        r = subprocess.run([sys.executable, "-c", probe, REPO], capture_output=True, text=True, timeout=60,
                           env={"PATH": os.environ.get("PATH", ""), "HOME": mkdtemp("vv-nerr-h-"),
                                "VV_VAULT": vault, "VV_NO_METRICS": "1"})
        return r.stdout.strip() or r.stderr.strip()[-200:]
    check("U9a python delegates a plain operand in a plain vault, never an undecodable operand",
          predicate(mkdtemp("vv-nerr-pv-")) == "True False", predicate(mkdtemp("vv-nerr-pv-")))
    check("U9b and delegates nothing when the vault path itself is undecodable",
          predicate(os.fsdecode(b"/tmp/v\xffault")) == "False False", predicate(os.fsdecode(b"/tmp/v\xffault")))

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
        check("U9c a search in a vault with an undecodable path is answered from that vault",
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
