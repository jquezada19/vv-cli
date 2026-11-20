#!/usr/bin/env python3
"""Regressions for six write-path defects, each of which exited 0 while writing
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
O  `move` to another folder left the moved note's own relative markdown links
   anchored at the old folder, and reported the verification clean.
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
        r = subprocess.run(self.entry + list(args), capture_output=True, text=True, env=e, input=stdin)
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

    # ---- O: move re-anchors the moved note's own relative markdown links ----
    body = ("[a](./Bee.md) [b](Bee.md) [c](../A/Bee.md) [root](A/Bee.md) [gone](./Missing.md)\n"
            "`[code](./Bee.md)`\n```\n[fenced](./Bee.md)\n```\n[[Bee]] [sp](./Has%20Space.md)\n")
    e.fresh({"A/Mover.md": body, "A/Bee.md": "b\n", "A/Has Space.md": "s\n", "C/keep.md": "k\n"})
    rc, out, err = e.run("move", "A/Mover.md", "C")
    check(f"O1[{n}] the dry-run reports the outgoing links", "outgoing relative links to re-anchor: 3" in out, out)
    check(f"O2[{n}] (control) the dry-run writes nothing", e.get("A/Mover.md") == body)
    rc, out, err = e.run("move", "A/Mover.md", "C", "--apply", plan_id(out) or "0")
    want = ("[a](../A/Bee.md) [b](../A/Bee.md) [c](../A/Bee.md) [root](A/Bee.md) [gone](./Missing.md)\n"
            "`[code](./Bee.md)`\n```\n[fenced](./Bee.md)\n```\n[[Bee]] [sp](../A/Has%20Space.md)\n")
    check(f"O3[{n}] links resolve from the new folder; inert and root-form text untouched",
          rc == 0 and e.has("C/Mover.md") and e.get("C/Mover.md") == want,
          (rc, err, e.has("C/Mover.md") and e.get("C/Mover.md")))
    e.fresh({"A/Mover.md": "[a](./Bee.md)\n", "A/Bee.md": "b\n"})
    rc, out, err = e.run("rename", "A/Mover.md", "Renamed", "--apply")
    check(f"O4[{n}] (control) a same-folder rename leaves relative links alone",
          rc == 0 and e.get("A/Renamed.md") == "[a](./Bee.md)\n", (rc, out, err))
    e.fresh({"A/Mover.md": "[me](./Mover.md) [b](./Bee.md)\n", "A/Bee.md": "b\n", "C/keep.md": "k\n"})
    rc, out, err = e.run("move", "A/Mover.md", "C", "--apply")
    check(f"O5[{n}] a self link is anchored at the new folder",
          rc == 0 and e.has("C/Mover.md") and e.get("C/Mover.md") == "[me](Mover.md) [b](../A/Bee.md)\n",
          (rc, err, e.has("C/Mover.md") and e.get("C/Mover.md")))
    e.fresh({"A/Mover.md": "[u](./B\u00e9e.md) [e](./B%C3%A9e.md)\n", "A/B\u00e9e.md": "b\n", "C D/keep.md": "k\n"})
    rc, out, err = e.run("move", "A/Mover.md", "C D", "--apply")
    check(f"O7[{n}] an unencoded target stays unencoded, an encoded one stays encoded",
          rc == 0 and e.has("C D/Mover.md") and e.get("C D/Mover.md") == "[u](../A/B\u00e9e.md) [e](../A/B%C3%A9e.md)\n",
          (rc, err, e.has("C D/Mover.md") and e.get("C D/Mover.md")))
    e.fresh({"C D/Mover.md": "[u](./Bee.md)\n", "C D/Bee.md": "b\n", "A/keep.md": "k\n"})
    rc, out, err = e.run("move", "C D/Mover.md", "A", "--apply")
    check(f"O8[{n}] a new path that needs encoding is encoded",
          rc == 0 and e.has("A/Mover.md") and e.get("A/Mover.md") == "[u](../C%20D/Bee.md)\n",
          (rc, err, e.has("A/Mover.md") and e.get("A/Mover.md")))
    e.fresh({"A/Mover.md": "[a](./Bee.md)\n", "A/Bee.md": "b\n", "C/keep.md": "k\n"})
    rc, out, err = e.run("move", "A/Mover.md", "C")
    d = plan_id(out)
    e.put("A/Mover.md", "[a](./Bee.md)\n")          # same bytes: the digest must still hold
    rc, out, err = e.run("move", "A/Mover.md", "C", "--apply", d or "0")
    check(f"O6[{n}] (control) unchanged source bytes keep the digest valid", rc == 0, (rc, out, err))

    # ---- S: search numeric flags ----
    e.fresh({"A.md": "alpha beta\n"})
    for bad in (("--k", "banana"), ("--w", "x"), ("--k", "-1"), ("--k",)):
        rc, out, err = e.run("search", "alpha", *bad)
        check(f"S1[{n},{' '.join(bad)}] usage refusal, no traceback, runnable next",
              rc == 1 and "Traceback" not in err and err.startswith("usage: search") and "next: vv search --help" in err,
              (rc, out, err))
    rc, out, err = e.run("search", "alpha", "--k", "1", "--w", "20")
    check(f"S2[{n}] (control) numeric values still search", rc == 0 and "A.md" in out, (rc, out, err))

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

print(f"\n{len(fails)} failures" if fails else "\nall write-defect checks passed")
sys.exit(1 if fails else 0)
