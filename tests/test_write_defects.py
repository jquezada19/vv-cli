#!/usr/bin/env python3
"""Regressions for five write-path defects, each of which exited 0 while writing
bytes its contract does not describe (or, for the last, died in a traceback).

P  `patch` on the preamble of a note whose frontmatter opens with a BOM. The
   refusal compared the first line to `---` literally, so the BOM hid the
   frontmatter and the patch replaced the YAML with body text.
E  `set` on an EMPTY frontmatter block (`---` directly followed by `---`). The
   block was not recognised and a second block was stacked on top of it.
   `new` with such a template silently dropped the property.
N  `new --KEY VALUE` matched `KEY:` anywhere in the template, so a body line
   that began with the key was rewritten and no property was created.
D  The rename/move/trash plan digest did not cover the source note's own
   bytes: a plan reviewed against one version applied to another.
S  `search --k`/`--w` with a missing or non-numeric value: a traceback from
   the python entry, a silent default from the native one.

Each check runs through BOTH entries in separate throwaway vaults (the
tests/test_set_key_shape.py pattern). A third arm runs the native binary with
the python fallback made impossible, for the cases the native entry answers
itself: it must produce the same bytes or decline, never the old ones.

Cases marked "(control)" pass at the PR base by design; every other case has a
check that fails there.
"""
import os, sys, shutil, subprocess, tempfile, atexit, re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VV = os.path.join(REPO, "src", "vv.py")
VRUST = os.path.join(REPO, "vrust", "target", "release", "vrust")
BOM = "﻿"

_TMP = []
def mkdtemp(prefix):
    d = tempfile.mkdtemp(prefix=prefix); _TMP.append(d); return d
atexit.register(lambda: [shutil.rmtree(d, True) for d in _TMP])

fails = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{str(detail)[:300]}]" if detail and not cond else ""))
    if not cond: fails.append(name)

class Engine:
    def __init__(self, name, env, entry):
        self.name, self.env, self.entry = name, env, entry

    def fresh(self, notes):
        """A new vault per case: no case can read another's leftovers."""
        self.vault = mkdtemp(f"vv-wdef-{self.name}-vault-")
        self.journals = mkdtemp(f"vv-wdef-{self.name}-journals-")
        self.index = mkdtemp(f"vv-wdef-{self.name}-index-")
        os.makedirs(os.path.join(self.vault, ".obsidian"))
        for relp, body in notes.items():
            self.put(relp, body)

    def put(self, relp, body):
        p = os.path.join(self.vault, relp)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", newline="") as f:
            f.write(body)

    def get(self, relp):
        with open(os.path.join(self.vault, relp), newline="") as f:
            return f.read()

    def has(self, relp):
        return os.path.exists(os.path.join(self.vault, relp))

    def run(self, *args, stdin=None):
        e = dict(os.environ, VV_VAULT=self.vault, VV_NO_METRICS="1", VV_INDEX_ROOT=self.index,
                 VV_JOURNAL_ROOT=self.journals, **self.env)
        # bounded: a hang must be a red check, never a stuck gate
        r = subprocess.run(self.entry + list(args), capture_output=True, text=True, env=e, input=stdin, timeout=120)
        return r.returncode, r.stdout, r.stderr

ENGINES = [Engine("python", {"VV_ENGINE": "python"}, [sys.executable, VV])]
NATIVE_ONLY = None
if os.path.exists(VRUST):
    ENGINES.append(Engine("native", {}, [VRUST]))
    # the python entry is unreachable: a fallback fails on the missing entry
    NATIVE_ONLY = Engine("native-nofallback", {"VV_PY_ENTRY": "/nonexistent/vv.py"}, [VRUST])
else:
    print("SKIP native arms (vrust/target/release/vrust not built)")

def plan_id(out):
    m = re.search(r"^plan ([0-9a-f]{8}):", out, re.M)
    return m.group(1) if m else None

def h0_hash(e, note):
    rc, out, _ = e.run("outline", note)
    for line in out.splitlines():
        cols = line.split("\t")
        if cols and cols[0] == "H0":
            return cols[-1]
    return None

for e in ENGINES:
    n = e.name

    # ---- P: patch must refuse a BOM-prefixed frontmatter preamble ----
    src = BOM + "---\nk: v\n---\n# H\n"
    e.fresh({"N.md": src})
    h = h0_hash(e, "N.md")
    rc, out, err = e.run("patch", "N.md", "H0", h or "00000000", stdin="gone\n")
    check(f"P1[{n}] patch H0 on BOM frontmatter is refused", rc == 1 and "refused" in err, (rc, out, err))
    check(f"P2[{n}] the note is byte-identical", e.get("N.md") == src, repr(e.get("N.md")))
    src = "---\nk: v\n---\n# H\n"
    e.fresh({"N.md": src})
    rc, out, err = e.run("patch", "N.md", "H0", h0_hash(e, "N.md") or "0", stdin="gone\n")
    check(f"P3[{n}] (control) BOM-free frontmatter preamble is refused", rc == 1 and e.get("N.md") == src, (rc, err))
    for label, lead in (("plain", ""), ("bom", BOM)):
        src = lead + "---\n# H\n"                 # no closing delimiter: a rule, not frontmatter
        e.fresh({"N.md": src})
        rc, out, err = e.run("patch", "N.md", "H0", h0_hash(e, "N.md") or "0", stdin=lead + "intro\n")
        check(f"P5[{n},{label}] an unterminated `---` preamble is body and patches",
              rc == 0 and e.get("N.md") == lead + "intro\n# H\n", (rc, err, repr(e.get("N.md"))))
    src = BOM + "intro\n# H\nbody\n"
    e.fresh({"N.md": src})
    rc, out, err = e.run("patch", "N.md", "H0", h0_hash(e, "N.md") or "0", stdin=BOM + "new intro\n")
    check(f"P4[{n}] (control) a BOM note WITHOUT frontmatter still patches", rc == 0, (rc, err))

    # ---- E: set / new into an empty frontmatter block ----
    for label, eol in (("lf", "\n"), ("crlf", "\r\n")):
        src = f"---{eol}---{eol}body{eol}"
        e.fresh({"E.md": src})
        rc, out, err = e.run("set", "E.md", "k", "v")
        want = f"---{eol}k: v{eol}---{eol}body{eol}"
        check(f"E1[{n},{label}] set fills the empty block", rc == 0 and e.get("E.md") == want, repr(e.get("E.md")))
    e.fresh({"E.md": "---\n---"})
    rc, out, err = e.run("set", "E.md", "k", "v")
    check(f"E2[{n}] empty block at end of file", rc == 0 and e.get("E.md") == "---\nk: v\n---", repr(e.get("E.md")))
    e.fresh({"E.md": "---\n---\nstatus: BODY\n---\n"})
    rc, out, err = e.run("set", "E.md", "k", "v")
    check(f"E6[{n}] a later `---` in the body does not hide the empty block",
          rc == 0 and e.get("E.md") == "---\nk: v\n---\nstatus: BODY\n---\n", repr(e.get("E.md")))
    e.fresh({"Templates/T.md": "---\n---\nstatus: BODY\n---\n"})
    rc, out, err = e.run("new", "X", "--template", "T", "--status", "done")
    check(f"E7[{n}] new with that template writes the property, body untouched",
          rc == 0 and e.has("X.md") and e.get("X.md") == "---\nstatus: done\n---\nstatus: BODY\n---\n",
          (rc, err, e.has("X.md") and repr(e.get("X.md"))))
    e.fresh({"E.md": "---\n---oops\nbody\n"})
    rc, out, err = e.run("set", "E.md", "k", "v")
    check(f"E3[{n}] (control) `---oops` is not a closing delimiter",
          rc == 0 and e.get("E.md") == "---\nk: v\n---\n---\n---oops\nbody\n", repr(e.get("E.md")))
    e.fresh({"E.md": "---\n---\nbody\n"})
    rc, out, err = e.run("unset", "E.md", "k")
    check(f"E4[{n}] (control) unset on an empty block is a not-found, note untouched",
          rc == 1 and e.get("E.md") == "---\n---\nbody\n", (rc, err))
    e.fresh({"Templates/Empty.md": "---\n---\n# T\n"})
    rc, out, err = e.run("new", "X", "--template", "Empty", "--status", "done")
    check(f"E5[{n}] new writes the property into an empty template block",
          rc == 0 and e.has("X.md") and e.get("X.md") == "---\nstatus: done\n---\n# T\n",
          (rc, err, e.has("X.md") and repr(e.get("X.md"))))

    # ---- N: new properties are frontmatter lines, never body lines ----
    e.fresh({"Templates/T.md": "---\ntype: t\n---\nstatus: BODY\n"})
    rc, out, err = e.run("new", "X", "--template", "T", "--status", "done")
    check(f"N1[{n}] body line survives, property is created",
          rc == 0 and e.get("X.md") == "---\ntype: t\nstatus: done\n---\nstatus: BODY\n", repr(e.has("X.md") and e.get("X.md")))
    e.fresh({"Templates/T.md": "status: BODY\n# no frontmatter\n"})
    rc, out, err = e.run("new", "X", "--template", "T", "--status", "done")
    check(f"N2[{n}] a template without frontmatter gains a block, body untouched",
          rc == 0 and e.get("X.md") == "---\nstatus: done\n---\nstatus: BODY\n# no frontmatter\n",
          repr(e.has("X.md") and e.get("X.md")))
    e.fresh({"Templates/T.md": "---\r\ntype: t\r\nstatus: open\r\n---\r\n# T\r\n"})
    rc, out, err = e.run("new", "X", "--template", "T", "--status", "done", "--owner", "me")
    check(f"N3[{n}] (control) replace + insert keep CRLF",
          rc == 0 and e.get("X.md") == "---\r\ntype: t\r\nstatus: done\r\nowner: me\r\n---\r\n# T\r\n",
          repr(e.has("X.md") and e.get("X.md")))

    e.fresh({"Templates/T.md": "---\na: 0\n---\n"})
    rc, out, err = e.run("new", "X", "--template", "T", "--a", "1\nb: 2", "--b", "3")
    check(f"N4[{n}] (control) a line an earlier VALUE introduced is visible to a later key",
          rc == 0 and e.get("X.md") == "---\na: 1\nb: 3\n---\n", repr(e.has("X.md") and e.get("X.md")))

    e.fresh({"Templates/T.md": "---\nstatus: draft\n"})
    rc, out, err = e.run("new", "X", "--template", "T", "--status", "done")
    check(f"N5[{n}] an unterminated template block is refused, nothing created",
          rc == 1 and err.startswith("refused: template frontmatter is unterminated") and not e.has("X.md"), (rc, out, err))
    rc, out, err = e.run("new", "X", "--template", "T")
    check(f"N6[{n}] (control) the same template without property flags still creates",
          rc == 0 and e.get("X.md") == "---\nstatus: draft\n", (rc, err))

    # ---- D: the plan digest covers the source note's bytes ----
    for cmd, args in (("rename", ("A/Src.md", "Dst")), ("move", ("A/Src.md", "C")), ("trash", ("A/Src.md",))):
        e.fresh({"A/Src.md": "alpha\n", "C/keep.md": "k\n"})
        rc, out, err = e.run(cmd, *args)
        d = plan_id(out)
        e.put("A/Src.md", "ENTIRELY DIFFERENT\n")
        rc, out, err = e.run(cmd, *args, "--apply", d or "00000000")
        check(f"D1[{n},{cmd}] a digest reviewed against other source bytes is stale",
              d is not None and rc == 3 and e.has("A/Src.md"), (d, rc, out, err))
        e.fresh({"A/Src.md": "alpha\n", "C/keep.md": "k\n"})
        rc, out, err = e.run(cmd, *args)
        rc, out, err = e.run(cmd, *args, "--apply", plan_id(out) or "00000000")
        check(f"D2[{n},{cmd}] (control) a fresh digest applies", rc == 0 and not e.has("A/Src.md"), (rc, out, err))

    # ---- S: search numeric flags ----
    e.fresh({"A.md": "alpha beta\n"})
    for bad in (("--k", "banana"), ("--w", "x"), ("--k", "-1"), ("--k",), ("--k", "1" * 19),
                ("--k", "0" * 4400), ("--k", "18446744073709551616", "--k", "1"), ("--k", "1_0"), ("--k", "+")):
        rc, out, err = e.run("search", "alpha", *bad)
        check(f"S1[{n},{' '.join(bad)[:24]}] usage refusal, no traceback, runnable next",
              rc == 1 and "Traceback" not in err and err.startswith("usage: search") and "next: vv search --help" in err,
              (rc, out, err))
    rc, out, err = e.run("search", "alpha", "--k", "1", "--w", "20")
    check(f"S2[{n}] (control) numeric values still search", rc == 0 and "A.md" in out, (rc, out, err))
    rc, out, err = e.run("search", "alpha", "--k", "+1", "--w", "999999999999999999")
    check(f"S3[{n}] (control) a leading + and an 18-digit value are accepted", rc == 0 and "A.md" in out, (rc, out, err))

if NATIVE_ONLY is not None:
    e = NATIVE_ONLY
    for label, eol in (("lf", "\n"), ("crlf", "\r\n")):
        src = f"---{eol}---{eol}body{eol}"
        e.fresh({"E.md": src})
        rc, out, err = e.run("set", "E.md", "k", "v")
        check(f"X1[{label}] native answers set-into-empty-block itself",
              rc == 0 and e.get("E.md") == f"---{eol}k: v{eol}---{eol}body{eol}", (rc, err, repr(e.get("E.md"))))
    src = BOM + "---\nk: v\n---\n# H\n"
    e.fresh({"N.md": src})
    rc, out, err = e.run("patch", "N.md", "H0", h0_hash(e, "N.md") or "0", stdin="gone\n")
    check("X2 native declines the BOM frontmatter patch, never writes it",
          rc != 0 and "vv.py" in err and e.get("N.md") == src, (rc, err, repr(e.get("N.md"))))
    e.fresh({"A.md": "alpha\n"})
    rc, out, err = e.run("search", "alpha", "--k", "banana")
    check("X3 native declines a non-numeric --k, never defaults it", rc != 0 and "vv.py" in err and out == "", (rc, out, err))

if os.path.exists(VRUST):
    # The native scanner, invoked the way python delegates to it (VV_FROM_PY),
    # must answer an unparseable value itself: handing back to python is the
    # hop that would bounce between the engines without bound.
    v = mkdtemp("vv-wdef-frompy-")
    open(os.path.join(v, "A.md"), "w").write("alpha\n")
    r = subprocess.run([VRUST, "search", "alpha", "--k", "-1"], capture_output=True, text=True, timeout=60,
                       env=dict(os.environ, VV_VAULT=v, VV_NO_METRICS="1", VV_FROM_PY="1",
                                VV_PY_ENTRY="/nonexistent/vv.py"))
    check("X4 a delegated native search never hands an unparseable value back",
          r.returncode == 1 and r.stderr.startswith("engine: search --k") and "vv.py" not in r.stderr,
          (r.returncode, r.stdout, r.stderr))

print(f"\n{len(fails)} failures" if fails else "\nall write-defect checks passed")
sys.exit(1 if fails else 0)
