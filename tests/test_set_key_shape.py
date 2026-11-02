#!/usr/bin/env python3
"""Regressions for operands parsed into the wrong shape by `set` and `new`.

The incident (2026-10-31, two vault todos): `vv set T description="a b"
updated=2026-10-31` exited 0 on BOTH engines having taken `description=a b`
as the KEY and `updated=2026-10-31` as the VALUE — it appended the frontmatter
line `description=a b: updated=2026-10-31`. Same class through `new`:
`vv new X --template Task --key description=x` wrote `key: description=x`
(the usage text's own `--key` placeholder became a property).

Each check runs through BOTH entries — the native binary
(vrust/target/release/vrust) and `VV_ENGINE=python src/vv.py` — in separate
throwaway vaults, so one engine's mutation can never stand in as the other's
control (the tests/test_affordance.py pattern). A third arm runs the native
binary with the Python fallback made impossible (VV_PY_ENTRY at a sentinel):
the native `set` must DECLINE a malformed key, never write it.

S  `set` KEY shape. A KEY is written verbatim before `: `, so a KEY carrying
   `=`, `:`, whitespace, a control character, or a leading YAML indicator
   (`-`, `#`, `[`, …) is refused with a usage error before the note is even
   resolved, and the `next:` line is the runnable one-key form derived from
   the caller's own operands where it can be (`k=v` → `k v`, `--k v` → `k v`).
   An arity miss whose KEY slot is malformed gets the same derived next step —
   the old one (`vv set T a=1 'b=2 c=3'`) pointed at the junk-writing form.
   `unset` stays permissive on purpose: it is how a junk line is removed.
N  `new` flag shape. The flag IS the property name (`--status open`): a flag
   name that is not a bare key (`--status=done`, `--'a b'`), the literal
   `--key` placeholder, a flag with no value or whose value is itself a flag,
   an extra positional, and `--template` with no name are usage refusals (two
   of these were a StopIteration traceback). A derived `next:` carries every
   flag already accepted (`new` is not idempotent) and never offers a pair
   that would itself be refused or consumed as a global flag.

Cases marked "(control)" pass at the PR base (origin/main) by design; every
other numbered case has a check that fails there (sub-checks such as "no
traceback" or "exit 1" can pass at the base on their own).
"""
import os, sys, shutil, subprocess, tempfile, atexit

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VV = os.path.join(REPO, "src", "vv.py")
VRUST = os.path.join(REPO, "vrust", "target", "release", "vrust")

_TMP = []
def mkdtemp(prefix):
    d = tempfile.mkdtemp(prefix=prefix); _TMP.append(d); return d
atexit.register(lambda: [shutil.rmtree(d, True) for d in _TMP])

fails = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{str(detail)[:240]}]" if detail and not cond else ""))
    if not cond: fails.append(name)

T_BODY = '---\ntype: todo\ndescription: ""\n---\n\n# T\n'
NOTES = {
    "T.md": T_BODY,
    "Junk.md": "---\ntype: todo\n--status: done\ndescription=a b: updated=x\n---\n# J\n",
    "Templates/Task.md": "---\ntype: todo\nstatus: open\n---\n# Task\n",
}

class Engine:
    def __init__(self, name, env, entry):
        self.name, self.env, self.entry = name, env, entry
        self.vault = mkdtemp(f"vv-keyshape-{name}-vault-")
        self.journals = mkdtemp(f"vv-keyshape-{name}-journals-")
        self.index = mkdtemp(f"vv-keyshape-{name}-index-")
        os.makedirs(os.path.join(self.vault, ".obsidian"))
        for relp, body in NOTES.items():
            p = os.path.join(self.vault, relp)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                f.write(body)

    def run(self, *args, stdin=None):
        e = dict(os.environ, VV_VAULT=self.vault, VV_NO_METRICS="1", VV_INDEX_ROOT=self.index,
                 VV_JOURNAL_ROOT=self.journals, **self.env)
        return subprocess.run([*self.entry, *args], capture_output=True, text=True, env=e,
                              input=stdin if stdin is not None else "", timeout=60)

    def snapshot(self):
        snap = {}
        for root in (self.vault, self.journals):
            for dp, dns, fns in os.walk(root):
                for dn in dns:
                    snap[os.path.join(dp, dn) + "/"] = None
                for fn in fns:
                    with open(os.path.join(dp, fn), "rb") as f:
                        snap[os.path.join(dp, fn)] = f.read()
        return snap

    def read(self, relp):
        try:
            with open(os.path.join(self.vault, relp)) as f:
                return f.read()
        except OSError as ex:
            return f"<unreadable: {ex}>"

    def reset_t(self):
        with open(os.path.join(self.vault, "T.md"), "w") as f:
            f.write(T_BODY)

def next_of(stderr):
    if stderr.count(" — next: ") != 1:
        return f"<separator count {stderr.count(' — next: ')}>"
    return stderr.rstrip().partition(" — next: ")[2]

def refused(eng, tag, name, args, want_prefix, want_next):
    before = eng.snapshot()
    r = eng.run(*args)
    check(f"{tag}{name} exit 1", r.returncode == 1, f"rc={r.returncode} {r.stdout[:80]} {r.stderr[:160]}")
    check(f"{tag}{name} message", r.stderr.startswith(want_prefix), r.stderr)
    check(f"{tag}{name} next", next_of(r.stderr) == want_next, r.stderr)
    check(f"{tag}{name} no traceback", "Traceback" not in r.stderr, r.stderr[-200:])
    check(f"{tag}{name} vault+journal untouched", eng.snapshot() == before,
          [k for k in set(before) | set(eng.snapshot()) if before.get(k) != eng.snapshot().get(k)][:4])
    return r

SET_BAD = "usage: set KEY must be a bare frontmatter key, got "
SET_HINT = " (KEY and VALUE are separate arguments; one key per call)"

def section_s(eng, tag):
    """S — set KEY shape."""
    refused(eng, tag, "1 incident: set T description=a b updated=x", ["set", "T", "description=a b", "updated=2026-10-31"],
            SET_BAD + "'description=a b'" + SET_HINT, "vv set T description 'a b'")
    check(f"{tag}1' no junk line in T", "description=a b" not in eng.read("T.md"), eng.read("T.md"))
    refused(eng, tag, "2 --KEY VALUE spelling", ["set", "T", "--status", "done"],
            SET_BAD + "--status", "vv set T status done")
    refused(eng, tag, "3 --KEY=VALUE spelling", ["set", "T", "--status=done", "x"],
            SET_BAD + "--status=done", "vv set T status done")
    refused(eng, tag, "4 whitespace in KEY", ["set", "T", "my key", "v"],
            SET_BAD + "'my key'", "vv set T KEY v")
    refused(eng, tag, "5 colon in KEY", ["set", "T", "a:", "v"],
            SET_BAD + "a:", "vv set T KEY v")
    refused(eng, tag, "6 leading # (a YAML comment)", ["set", "T", "#tag", "v"],
            SET_BAD + "'#tag'", "vv set T KEY v")
    refused(eng, tag, "7 empty KEY", ["set", "T", "", "v"],
            SET_BAD + "''", "vv set T KEY v")
    refused(eng, tag, "8 tab in KEY", ["set", "T", "a\tb", "v"],
            SET_BAD + "KEY", "vv set T KEY v")
    refused(eng, tag, "9 leading [ (a flow sequence)", ["set", "T", "[a]", "v"],
            SET_BAD + "'[a]'", "vv set T KEY v")
    refused(eng, tag, "10 KEY is checked before the note resolves", ["set", "Missing", "a=b", "v"],
            SET_BAD + "a=b", "vv set Missing a b")
    refused(eng, tag, "10' a flag in the NOTE slot still derives the key", ["set", "--foo", "a=b", "v"],
            SET_BAD + "a=b", "vv set NOTE a b")
    refused(eng, tag, "11 k= derives an empty value", ["set", "T", "status=", "v"],
            SET_BAD + "status=", "vv set T status ''")
    # arity misses whose KEY slot is malformed: the derived next step, not a junk-writing one
    refused(eng, tag, "12 two operands k=v", ["set", "T", "status=done"],
            "usage: set takes 3 positional args, got 2 (KEY and VALUE are separate arguments; one key per call)", "vv set T status done")
    refused(eng, tag, "13 k=v pairs", ["set", "T", "a=1", "b=2", "c=3"],
            "usage: set takes 3 positional args, got 4 (KEY and VALUE are separate arguments; one key per call)", "vv set T a 1")
    refused(eng, tag, "14 bare --KEY", ["set", "T", "--status"],
            "usage: set takes 3 positional args, got 2 (KEY and VALUE are separate arguments; one key per call)", "vv set T status VALUE")
    refused(eng, tag, "15 unquoted VALUE (control)", ["set", "T", "description", "a", "b"],
            "usage: set takes 3 positional args, got 4 (VALUE is one argument; quote it)", "vv set T description 'a b'")
    # controls: well-formed keys still write, both engines
    for name, key, val, line in [("17 plain key (control)", "status", "done", "status: done"),
                                 ("18 dotted/underscored key (control)", "my_key.v2", "x", "my_key.v2: x"),
                                 ("19 non-ASCII key (control)", "clé", "v", "clé: v"),
                                 ("20 = inside the VALUE (control)", "url", "https://x/?a=b", "url: https://x/?a=b"),
                                 ("21 existing key replaced (control)", "description", "a b", "description: a b")]:
        eng.reset_t()
        r = eng.run("set", "T", key, val)
        t = eng.read("T.md")
        check(f"{tag}{name}", r.returncode == 0 and line in t.split("\n") and t.count("---") == 2, f"rc={r.returncode} {r.stderr[:120]} {t!r}")
    eng.reset_t()
    # unset stays permissive: it is how the junk an old vv wrote gets removed
    r1 = eng.run("unset", "Junk", "--status")
    r2 = eng.run("unset", "Junk", "description=a b")
    j = eng.read("Junk.md")
    check(f"{tag}22 unset removes a junk-key line (control)", r1.returncode == 0 and r2.returncode == 0 and j == "---\ntype: todo\n---\n# J\n", f"{r1.stderr} {r2.stderr} {j!r}")

def section_n(eng, tag):
    """N — new flag shape (python-only command; the native entry execs python)."""
    refused(eng, tag, "1 incident: --key placeholder", ["new", "X", "--template", "Task", "--key", "description=x"],
            "usage: new takes --KEY VALUE (the flag is the property name), got --key", "vv new X --template Task --description x")
    refused(eng, tag, "1' --key with a plain value", ["new", "X1", "--key", "status"],
            "usage: new takes --KEY VALUE (the flag is the property name), got --key", "vv new X1 --KEY VALUE")
    refused(eng, tag, "2 --KEY=VALUE", ["new", "Y2", "--status=done"],
            "usage: new takes --KEY VALUE (the flag is the property name), got --status=done", "vv new Y2 --status done")
    refused(eng, tag, "3 flag with no value (was a traceback)", ["new", "Y3", "--status"],
            "usage: new flag --status needs a value", "vv new Y3 --status VALUE")
    refused(eng, tag, "4 flag whose value is a flag", ["new", "Y4", "--status", "--type", "t"],
            "usage: new flag --status needs a value", "vv new Y4 --status VALUE")
    refused(eng, tag, "5 whitespace in a flag name", ["new", "Y5", "--my key", "v"],
            "usage: new takes --KEY VALUE (the flag is the property name), got '--my key'", "vv new Y5 --KEY VALUE")
    refused(eng, tag, "6 extra positional", ["new", "Y6", "extra"],
            "usage: new takes PATH, got extra positional 'extra'", "vv new Y6")
    refused(eng, tag, "7 --template with no name (was a traceback)", ["new", "Y7", "--template"],
            "usage: new flag --template needs a value", "vv new Y7 --template NAME")
    refused(eng, tag, "11 next keeps the flags already given (new is not idempotent)",
            ["new", "X8", "--template", "Task", "--owner", "jeff", "--status=done"],
            "usage: new takes --KEY VALUE", "vv new X8 --template Task --owner jeff --status done")
    refused(eng, tag, "12 --key template=X is not derived into a template pick", ["new", "X9", "--key", "template=Foo"],
            "usage: new takes --KEY VALUE", "vv new X9 --KEY VALUE")
    refused(eng, tag, "15 a derived global-flag name is not offered", ["new", "X12", "--key", "limit=5"],
            "usage: new takes --KEY VALUE", "vv new X12 --KEY VALUE")
    refused(eng, tag, "15' …nor --vault", ["new", "X12", "--vault=elsewhere"],
            "usage: new takes --KEY VALUE", "vv new X12 --KEY VALUE")
    refused(eng, tag, "16 a derived value that is a flag is not offered", ["new", "X13", "--status=--flag"],
            "usage: new takes --KEY VALUE", "vv new X13 --KEY VALUE")
    refused(eng, tag, "17 --key key=x does not derive --key", ["new", "X14", "--key", "key=x"],
            "usage: new takes --KEY VALUE", "vv new X14 --KEY VALUE")
    with open(os.path.join(eng.vault, "Templates", "Block.md"), "w") as f:
        f.write("---\ndescription: |\n  first\n  second\n---\n# B\n")
    r = eng.run("new", "X15", "--template", "Block", "--description", "|")
    check(f"{tag}18 a block-scalar header override is written as given (control)",
          r.returncode == 0 and "description: |\n  first" in eng.read("X15.md"), r.stderr + eng.read("X15.md"))
    r = eng.run("new", "X10", "--note", "--verbose flag")
    check(f"{tag}13 a value starting with -- that has a space is a value (control)",
          r.returncode == 0 and "note: --verbose flag" in eng.read("X10.md"), r.stderr + eng.read("X10.md"))
    r = eng.run("new", "X11", "--priority", "-1")
    check(f"{tag}14 a single-dash value is a value (control)", r.returncode == 0 and "priority: -1" in eng.read("X11.md"), r.stderr + eng.read("X11.md"))
    r = eng.run("new", "W", "--template", "Task", "--status", "done", "--owner", "jeff")
    w = eng.read("W.md")
    check(f"{tag}10 well-formed flags still work (control)", r.returncode == 0 and "status: done" in w and "owner: jeff" in w, r.stderr + w)

engines = [Engine("python", {"VV_ENGINE": "python"}, [sys.executable, VV])]
if os.path.exists(VRUST):
    engines.append(Engine("rust", {}, [VRUST]))
else:
    print(f"SKIP native arm: {VRUST} not built (run_tests.sh builds it first, so the gate never skips)")

for eng in engines:
    tag = "P" if eng.name == "python" else "N"
    print(f"== engine: {eng.name}")
    section_s(eng, tag + "S")
    section_n(eng, tag + "N")

if os.path.exists(VRUST):
    # Native-only: with the fallback impossible, a malformed KEY must reach the
    # sentinel (the binary declined) and never be written by the native path.
    print("== engine: native-only (python fallback is a sentinel)")
    sdir = mkdtemp("vv-keyshape-sentinel-")
    sentinel = os.path.join(sdir, "fallback_sentinel.py")
    with open(sentinel, "w") as f:
        f.write("import sys\nprint('FALLBACK_SENTINEL')\nsys.exit(42)\n")
    nat = Engine("native", {"VV_PY_ENTRY": sentinel}, [VRUST])
    for name, args in [("1 k=v KEY", ["set", "T", "description=a b", "updated=x"]),
                       ("2 --KEY", ["set", "T", "--status", "done"]),
                       ("3 whitespace KEY", ["set", "T", "my key", "v"]),
                       ("4 leading #", ["set", "T", "#tag", "v"]),
                       ("6 non-ASCII key (valid; python writes it)", ["set", "T", "clé", "v"])]:
        before = nat.snapshot()
        r = nat.run(*args)
        check(f"NO{name}: native declines (sentinel reached)", r.returncode == 42 and "FALLBACK_SENTINEL" in r.stdout, f"rc={r.returncode} {r.stdout[:80]} {r.stderr[:80]}")
        check(f"NO{name}: nothing written", nat.snapshot() == before)
    r = nat.run("set", "T", "status", "done")
    check("NO5 plain key is still served natively (control)", r.returncode == 0 and "status: done" in nat.read("T.md"), f"rc={r.returncode} {r.stdout[:80]}")

if len(engines) == 2:
    py, rs = engines
    for p in [("set", "T", "description=a b", "updated=x"), ("set", "T", "--status", "done"),
              ("set", "T", "status=done"), ("new", "Q", "--key", "a=b")]:
        a, b = py.run(*p), rs.run(*p)
        check(f"X parity `{' '.join(p)}`", (a.returncode, a.stdout, a.stderr) == (b.returncode, b.stdout, b.stderr),
              f"py=({a.returncode},{a.stderr[:80]!r}) rs=({b.returncode},{b.stderr[:80]!r})")

print(f"\n{len(fails)} failures" if fails else "\nALL PASS")
sys.exit(1 if fails else 0)
