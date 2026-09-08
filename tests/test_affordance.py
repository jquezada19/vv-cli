#!/usr/bin/env python3
"""Regressions for the 2026-09-07 affordance sweep (5 days of vv telemetry,
615 rows, after the 2.0.x releases).

Three defect classes, each pinned through BOTH entries (native binary via
VV_ENGINE=rust, and VV_ENGINE=python) in separate throwaway vaults, so one
engine's mutation can never stand in as the other's control:

A  Relocate tails. `cmd_move(ref, dest_folder, *args)` accepted extra
   positionals silently: on 2026-09-07 `vv move A B C D Dest --apply` used
   note B as the destination folder — four stray folders at the vault root,
   exit 0. A second silent path: `_plan_token` returned None for a non-hex
   token after --apply, so `--apply abc` (a typo'd plan id) degraded to an
   UNBOUND apply. Now: the tail is a grammar (`--apply` optionally followed by
   one 8-hex id, nothing else), validated BEFORE the note resolves or a plan is
   printed; a flag in an operand slot is refused; a valid-but-stale id is still
   exit 3 (the boundary between usage and stale is pinned).
B  Arity `next:` lines. ARITY_NEXT had one entry (read); every other command's
   arity miss ended with "run vv with no args for the command list" — one
   agent hit `append` four times in two minutes (5, 4, 1, 0 args) hunting for
   a --section flag. Now the next step is derived from COMMAND_TABLE: the
   caller's own operands interpolated, shell-quoted, no placeholders-in-
   brackets, no `<stdin` redirect hazard; every emitted `next:` splits under
   shlex and carries no shell metacharacters (B4 sweeps the whole table).
C  Id-prefix resolution. Notes named `NNNNN - Title.md` are the vault's
   work-item convention; `vv set 24995 status done` was not-found (44 rows in
   one second from one script). Now a bare ASCII-digit ref that matches no
   exact path/basename resolves to the UNIQUE note whose basename starts with
   `<digits> - ` (exact delimiter: en-dash, no-space, and mid-name are not
   matches). Deterministic, never fuzzy; exact match always wins; `#24995`
   is the same ref; the candidate is vault-contained (a symlink out is
   `escape:`); an unreadable directory makes uniqueness unprovable and the
   alias is refused; `[[24995]]` stays an unresolved LINK — link semantics
   are Obsidian's, CLI-operand semantics are vv's.

Checks marked "(control…)" pass at the PR base (origin/main) by design; every
other check fails at the base or with its fix reverted (mutation pass,
recorded in the PR).
"""
import os, sys, json, shlex, shutil, stat, subprocess, tempfile, atexit

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VV = os.path.join(REPO, "src", "vv.py")
VRUST = os.path.join(REPO, "vrust", "target", "release", "vrust")
sys.path.insert(0, os.path.join(REPO, "src"))

_TMP = []
def mkdtemp(prefix):
    d = tempfile.mkdtemp(prefix=prefix); _TMP.append(d); return d
_RESTORE = []   # (path, mode) to chmod back before rmtree
def _cleanup():
    for p, m in _RESTORE:
        try: os.chmod(p, m)
        except OSError: pass
    for d in _TMP:
        shutil.rmtree(d, True)
atexit.register(_cleanup)

fails = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{str(detail)[:200]}]" if detail and not cond else ""))
    if not cond: fails.append(name)

NOTES = {
    "A.md": "---\nstatus: open\n---\n# A\n\n[[B]]\n",
    "B.md": "# B\n",
    "C.md": "# C\n",
    "Work Items/24995 - Some title.md": "---\nstatus: open\n---\n# T\n",
    "Work Items/24996 - Other.md": "# T2\n",
    "Dup/24997 - X.md": "# X\n",
    "Dup2/24997 - Y.md": "# Y\n",
    "Lit/24998.md": "# stub\n",
    "Lit/24998 - Foo.md": "# titled\n",
    "Foo - Bar.md": "# FB\n",
    "Link.md": "# L\n\n[[24995]]\n",
    "Short/249 - Old.md": "# old\n",
    "Dash/25001 – EnDash.md": "# en\n",
    "Dash/25002-NoSpace.md": "# ns\n",
    "Dash/x 25003 - Mid.md": "# mid\n",
}

class Engine:
    def __init__(self, name, env):
        self.name, self.env = name, env
        self.vault = mkdtemp(f"vv-afford-{name}-vault-")
        self.journals = mkdtemp(f"vv-afford-{name}-journals-")
        self.index = mkdtemp(f"vv-afford-{name}-index-")
        for relp, body in NOTES.items():
            p = os.path.join(self.vault, relp)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                f.write(body)
        for d in ("Dest", "Dest2"):
            os.makedirs(os.path.join(self.vault, d))

    def run(self, *args, stdin=None):
        e = dict(os.environ, VV_VAULT=self.vault, VV_NO_METRICS="1", VV_INDEX_ROOT=self.index,
                 VV_JOURNAL_ROOT=self.journals, **self.env)
        e.pop("VV_NO_INDEX", None)
        # stdin is ALWAYS a closed pipe: `batch`/`patch` read it, and an inherited
        # open stdin (a background runner's) blocks the suite forever.
        return subprocess.run([sys.executable, VV, *args], capture_output=True, text=True, env=e,
                              input=stdin if stdin is not None else "", timeout=60)

    def snapshot(self):
        """Every file's bytes + every directory, vault and journal root. A
        refusal must leave BOTH byte-identical — not merely 'no folder created'."""
        snap = {}
        for root in (self.vault, self.journals):
            for dp, dns, fns in os.walk(root):
                for dn in dns:
                    snap[os.path.join(dp, dn) + "/"] = None
                for fn in fns:
                    p = os.path.join(dp, fn)
                    try:
                        with open(p, "rb") as f:
                            snap[p] = f.read()
                    except OSError:
                        snap[p] = "<unreadable>"
        return snap

    def read(self, relp):
        with open(os.path.join(self.vault, relp)) as f:
            return f.read()

def next_of(stderr):
    return stderr.rstrip().rpartition(" — next: ")[2]

def refused(eng, tag, name, args, want_prefix, want_next, exit_code=1):
    """Run args; assert exit, stderr shape, and a byte-identical vault+journal."""
    before = eng.snapshot()
    r = eng.run(*args)
    check(f"{tag}{name} exit {exit_code}", r.returncode == exit_code, f"rc={r.returncode} {r.stderr[:160]}")
    check(f"{tag}{name} message", r.stderr.startswith(want_prefix), r.stderr)
    if want_next is not None:
        check(f"{tag}{name} next", next_of(r.stderr) == want_next, r.stderr)
    check(f"{tag}{name} no plan printed", "plan " not in r.stdout, r.stdout)
    check(f"{tag}{name} vault+journal untouched", eng.snapshot() == before,
          [k for k in set(before) | set(eng.snapshot()) if before.get(k) != eng.snapshot().get(k)][:4])
    return r

def section_a(eng, tag):
    """A — relocate tails."""
    # A1: the incident, both forms (apply and dry-run), and the two siblings
    refused(eng, tag, "1a incident: move A B C Dest --apply", ["move", "A", "B", "C", "Dest", "--apply"],
            "usage: move takes NOTE FOLDER, got extra positional 'C' (one note per call)", "vv move NOTE FOLDER")
    check(f"{tag}1a' no stray folder named after note B", not os.path.isdir(os.path.join(eng.vault, "B")))
    refused(eng, tag, "1b dry-run with extras is refused too", ["move", "A", "B", "C"],
            "usage: move takes NOTE FOLDER, got extra positional 'C'", "vv move NOTE FOLDER")
    refused(eng, tag, "1c rename with extras", ["rename", "A", "A2", "junk", "--apply"],
            "usage: rename takes NOTE NEWNAME, got extra positional 'junk'", "vv rename NOTE NEWNAME")
    r = eng.run("rename", "A", "A2", "junk")
    check(f"{tag}1c'' rename refusal does not say 'one note per call'", "one note per call" not in r.stderr, r.stderr)
    refused(eng, tag, "1d trash with extras", ["trash", "C", "junk"],
            "usage: trash takes NOTE, got extra positional 'junk'", "vv trash NOTE")
    # A2: the --apply token grammar
    refused(eng, tag, "2a --apply abc is refused, not an unbound apply", ["move", "A", "Dest", "--apply", "abc"],
            "usage: --apply takes an 8-hex plan id, got 'abc'", "vv move A Dest")
    refused(eng, tag, "2a' eight non-hex chars are not a plan id", ["move", "A", "Dest", "--apply", "zzzzzzzz"],
            "usage: --apply takes an 8-hex plan id, got 'zzzzzzzz'", "vv move A Dest")
    refused(eng, tag, "2b junk after a valid id", ["move", "A", "Dest", "--apply", "deadbeef", "C"],
            "usage: move takes NOTE FOLDER, got extra positional 'C'", "vv move NOTE FOLDER")
    refused(eng, tag, "2c --apply twice", ["move", "A", "Dest", "--apply", "--apply"],
            "usage: --apply given twice", "vv move A Dest")
    refused(eng, tag, "2d unknown flag is named as a flag", ["move", "A", "Dest", "--apply=deadbeef"],
            "usage: move takes NOTE FOLDER [--apply [SHA8]], got unknown flag '--apply=deadbeef'", "vv move A Dest")
    refused(eng, tag, "2e flag in the FOLDER slot", ["move", "A", "--apply"],
            "usage: move takes NOTE FOLDER, got flag '--apply' where FOLDER was expected", "vv move NOTE FOLDER")
    refused(eng, tag, "2f flag in the NOTE slot", ["move", "--apply", "A", "Dest"],
            "usage: move takes NOTE FOLDER, got flag '--apply' where NOTE was expected", "vv move NOTE FOLDER")
    refused(eng, tag, "2g tail is validated before the note resolves", ["trash", "Missing", "--apply", "abc"],
            "usage: --apply takes an 8-hex plan id, got 'abc'", "vv trash Missing")
    refused(eng, tag, "2h quoted operands in the next step", ["move", "Work Items/24995 - Some title.md", "Dest", "--apply", "xyz"],
            "usage: --apply takes an 8-hex plan id, got 'xyz'", "vv move 'Work Items/24995 - Some title.md' Dest")
    # A3: controls — the documented forms still work, and stale stays exit 3
    r = eng.run("move", "C", "Dest")
    plan = r.stdout.split()[1].rstrip(":") if r.returncode == 0 and r.stdout.startswith("plan ") else ""
    check(f"{tag}3a dry-run prints a plan (control)", r.returncode == 0 and len(plan) == 8, r.stdout + r.stderr)
    r = eng.run("move", "C", "Dest", "--apply", "00000000")
    check(f"{tag}3b a valid-but-wrong id is still stale exit 3, never usage",
          r.returncode == 3 and r.stderr.startswith("stale:"), f"rc={r.returncode} {r.stderr}")
    check(f"{tag}3b' stale left the note in place", os.path.isfile(os.path.join(eng.vault, "C.md")))
    r = eng.run("move", "C", "Dest", "--apply", plan.upper())
    check(f"{tag}3c uppercase id is accepted and normalised (bound apply)",
          r.returncode == 0 and os.path.isfile(os.path.join(eng.vault, "Dest", "C.md")), r.stdout + r.stderr)
    r = eng.run("move", "Dest/C", "Dest2", "--apply")
    check(f"{tag}3d plain --apply still works (control)",
          r.returncode == 0 and os.path.isfile(os.path.join(eng.vault, "Dest2", "C.md")), r.stdout + r.stderr)
    r = eng.run("trash", "Dest2/C", "--apply")
    check(f"{tag}3e trash plain --apply still works — the table now says [--apply [SHA8]] (control: behaviour)",
          r.returncode == 0 and not os.path.exists(os.path.join(eng.vault, "Dest2", "C.md")), r.stdout + r.stderr)
    # A4: under-arity wording no longer contradicts the strict tail
    r = eng.run("move", "A")
    check(f"{tag}4a move A is a usage error", r.returncode == 1 and r.stderr.startswith("usage: move takes 2 positional args, got 1"), r.stderr)
    check(f"{tag}4b …without the '2+' wording", "2+" not in r.stderr, r.stderr)
    check(f"{tag}4c …and the next step interpolates the note", next_of(r.stderr) == "vv move A FOLDER", r.stderr)

def section_b(eng, tag):
    """B — arity next lines from the command table."""
    cases = [
        (["append"],                       "usage: append takes 2 positional args, got 0",  "vv append NOTE TEXT"),
        (["append", "A"],                  "usage: append takes 2 positional args, got 1",  "vv append A TEXT"),
        (["append", "A", "hello", "world"], "usage: append takes 2 positional args, got 3 (TEXT is one argument; quote it)", "vv append A 'hello world'"),
        (["append", "A", "--section", "X", "hi"], "usage: append takes 2 positional args, got 4 (append has no --section; append inside a section is appendsec)", "vv appendsec A SEC TEXT"),
        (["prepend", "A", "--section", "X", "y"], "usage: prepend takes 2 positional args, got 4", "vv prepend A TEXT"),
        (["set", "A", "status"],           "usage: set takes 3 positional args, got 2",     "vv set A status VALUE"),
        (["set", "A"],                     "usage: set takes 3 positional args, got 1",     "vv set A KEY VALUE"),
        (["set", "A", "status", "in", "progress"], "usage: set takes 3 positional args, got 4 (VALUE is one argument; quote it)", "vv set A status 'in progress'"),
        (["unset", "A"],                   "usage: unset takes 2 positional args, got 1",   "vv unset A KEY"),
        (["patch", "A"],                   "usage: patch takes 3 positional args, got 1",   "vv outline A"),
        (["patch"],                        "usage: patch takes 3 positional args, got 0",   "vv outline NOTE"),
        (["appendsec", "A"],               "usage: appendsec takes 3 positional args, got 1", "vv appendsec A SEC TEXT"),
        (["daily-append"],                 "usage: daily-append takes 1 positional args, got 0", "vv daily-append TEXT"),
        (["rename", "A"],                  "usage: rename takes 2 positional args, got 1",  "vv rename A NEWNAME"),
        (["read", "A"],                    "usage: read takes 2 positional args, got 1",    "vv outline A"),   # control: pre-existing special case
        (["read", "Work Items/24995 - Some title.md"], "usage: read takes 2 positional args, got 1", "vv outline 'Work Items/24995 - Some title.md'"),
    ]
    for args, prefix, nxt in cases:
        r = eng.run(*args)
        label = " ".join(args)
        check(f"{tag}1 `{label}` message", r.returncode == 1 and r.stderr.startswith(prefix), f"rc={r.returncode} {r.stderr}")
        check(f"{tag}1 `{label}` next", next_of(r.stderr) == nxt, r.stderr)
        check(f"{tag}1 `{label}` no traceback", "Traceback" not in r.stderr, r.stderr)
    r = eng.run("prepend", "A", "--section", "X", "y")
    check(f"{tag}2 prepend never points at appendsec (opposite end of the section)", "appendsec" not in r.stderr, r.stderr)
    # B3: the generic pointer is gone from every arity miss
    r = eng.run("appendsec", "A")
    check(f"{tag}3 generic 'run vv with no args' pointer is gone", "run vv with no args" not in r.stderr, r.stderr)
    # B4: sweep the whole table — every arity next is shell-splittable, metachar-free, and starts with `vv <cmd>`
    import vv_impl
    for c in vv_impl.COMMAND_TABLE:
        r = eng.run(c["name"])
        if not r.stderr.startswith(f"usage: {c['name']} takes "):
            continue   # 0 required operands, or its own usage grammar (search/new/show/...)
        nxt = next_of(r.stderr)
        try:
            parts = shlex.split(nxt)
        except ValueError as ex:
            parts = []
        check(f"{tag}4 `{c['name']}` next is shell-splittable", bool(parts), nxt)
        check(f"{tag}4 `{c['name']}` next has no shell metacharacters", not any(ch in nxt for ch in "<>[]|"), nxt)
        check(f"{tag}4 `{c['name']}` next starts with a vv command", parts[:1] == ["vv"] and len(parts) >= 2, nxt)
    # B5: batch inherits the same interpolated next (the arity check is shared)
    r = eng.run("batch", stdin=json.dumps({"cmd": "backlinks", "args": []}) + "\n")
    check(f"{tag}5 batch arity miss carries the table-derived next", "vv backlinks NOTE" in r.stdout + r.stderr, (r.stdout + r.stderr)[:300])

def section_c(eng, tag):
    """C — id-prefix resolution."""
    T = "Work Items/24995 - Some title.md"
    for ref in ("24995", "#24995", "24995.md"):
        r = eng.run("resolve", ref)
        check(f"{tag}1 resolve {ref!r} → the unique 'NNNNN - ' note", r.returncode == 0 and r.stdout.strip() == T, r.stdout + r.stderr)
    r = eng.run("head", "24995")
    check(f"{tag}1b head by id", r.returncode == 0 and "status: open" in r.stdout, r.stdout + r.stderr)
    r = eng.run("set", "24995", "status", "done")
    check(f"{tag}1c set by id writes the titled note", r.returncode == 0 and "status: done" in eng.read(T), r.stdout + r.stderr)
    r = eng.run("append", "24996", "hello by id")
    check(f"{tag}1d append by id", r.returncode == 0 and eng.read("Work Items/24996 - Other.md").endswith("hello by id\n"), r.stdout + r.stderr)
    r = eng.run("resolve", "Work Items/24995")
    check(f"{tag}1e a path-qualified id is NOT expanded (exact path or nothing)", r.returncode == 1 and r.stderr.startswith("not-found:"), r.stdout + r.stderr)
    # C2: the anchor is exact
    for ref, why in (("2499", "a prefix of the digits"), ("25001", "en-dash delimiter"), ("25002", "no-space delimiter"),
                     ("25003", "digits mid-name"), ("123abc", "digits plus junk"), ("24995-", "trailing junk"),
                     ("Foo", "non-digit ref with a 'Foo - Bar' note (control)")):
        r = eng.run("resolve", ref)
        check(f"{tag}2 {ref!r} does not resolve ({why})", r.returncode == 1 and r.stderr.startswith("not-found:"), r.stdout + r.stderr)
    r = eng.run("resolve", "249")
    check(f"{tag}2' a shorter id names ITS OWN note, by the rule", r.returncode == 0 and r.stdout.strip() == "Short/249 - Old.md", r.stdout + r.stderr)
    # C3: ambiguity refuses with a runnable next, and writes nothing
    before = eng.snapshot()
    r = eng.run("set", "24997", "status", "x")
    check(f"{tag}3a two notes share the id → ambiguous", r.returncode == 1
          and r.stderr.startswith("ambiguous: 24997 matches 2 notes: Dup/24997 - X.md | Dup2/24997 - Y.md"), r.stderr)
    check(f"{tag}3b …with a runnable next", next_of(r.stderr) == "vv resolve 'Dup/24997 - X.md'", r.stderr)
    check(f"{tag}3c …and neither file was written", eng.snapshot() == before)
    # C4: exact match always wins, for both spellings of the ref
    for ref in ("24998", "#24998"):
        r = eng.run("resolve", ref)
        check(f"{tag}4 exact basename beats the titled sibling for {ref!r}", r.returncode == 0 and r.stdout.strip() == "Lit/24998.md", r.stdout + r.stderr)
    # C5: link semantics did not move
    r = eng.run("unresolved")
    check(f"{tag}5 [[24995]] is still an unresolved LINK", r.returncode == 0 and "Link.md\t3\t24995" in r.stdout, r.stdout + r.stderr)
    # C6: not-found for an id with no note keeps did-you-mean (control), and no traceback
    r = eng.run("resolve", "99999")
    check(f"{tag}6 unknown id is not-found (control)", r.returncode == 1 and r.stderr.startswith("not-found: no note matches '99999'"), r.stderr)
    # C7: a symlinked candidate that escapes the vault is refused, and the outside file untouched
    outside = mkdtemp("vv-afford-outside-")
    ext = os.path.join(outside, "ext.md")
    with open(ext, "w") as f:
        f.write("# outside\n")
    os.makedirs(os.path.join(eng.vault, "Ext"), exist_ok=True)
    os.symlink(ext, os.path.join(eng.vault, "Ext", "24999 - External.md"))
    r = eng.run("set", "24999", "status", "x")
    with open(ext) as f:
        outside_after = f.read()
    check(f"{tag}7 escaping symlink candidate is refused", r.returncode == 1 and r.stderr.startswith("escape:"), f"rc={r.returncode} {r.stderr}")
    check(f"{tag}7' outside file untouched", outside_after == "# outside\n")
    # C8: uniqueness is unprovable under an unreadable directory → refused (POSIX, non-root only)
    if os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() != 0:
        hidden = os.path.join(eng.vault, "Hidden")
        os.makedirs(hidden); os.makedirs(os.path.join(eng.vault, "Vis"))
        with open(os.path.join(hidden, "25000 - H.md"), "w") as f: f.write("# h\n")
        with open(os.path.join(eng.vault, "Vis", "25000 - Seen.md"), "w") as f: f.write("# s\n")
        mode = os.stat(hidden).st_mode
        os.chmod(hidden, 0); _RESTORE.append((hidden, mode))
        r = eng.run("resolve", "25000")
        os.chmod(hidden, mode)
        check(f"{tag}8 unreadable directory → alias refused, not a false unique", r.returncode == 1 and r.stderr.startswith("refused: cannot prove id 25000 is unique"), f"rc={r.returncode} {r.stderr}")
        check(f"{tag}8' …naming the directory", "Hidden" in r.stderr, r.stderr)
        r = eng.run("resolve", "Vis/25000 - Seen")
        check(f"{tag}8'' an exact path still resolves under the same condition", r.returncode == 0, r.stdout + r.stderr)
    else:
        print(f"SKIP {tag}8 unreadable-directory pin (not POSIX or running as root)")
    # C9: create never expands an id (last: it changes what 24995 resolves to)
    r = eng.run("new", "Tmp/24995")
    check(f"{tag}9a new does not expand the id", r.returncode == 0 and os.path.isfile(os.path.join(eng.vault, "Tmp", "24995.md")), r.stdout + r.stderr)
    r = eng.run("resolve", "24995")
    check(f"{tag}9b …and the exact basename now wins over the titled note", r.stdout.strip() == "Tmp/24995.md", r.stdout + r.stderr)

engines = [Engine("python", {"VV_ENGINE": "python"})]
if os.path.exists(VRUST):
    engines.append(Engine("rust", {"VV_ENGINE": "rust"}))
else:
    print(f"SKIP native arm: {VRUST} not built (run_tests.sh builds it first, so the gate never skips)")

for eng in engines:
    tag = "P" if eng.name == "python" else "N"
    print(f"== engine: {eng.name}")
    section_a(eng, tag + "A")
    section_b(eng, tag + "B")
    section_c(eng, tag + "C")

if len(engines) == 2:
    # Parity: the three error grammars are byte-identical through both entries
    pairs = [("move", "A", "B", "C", "Dest", "--apply"), ("move", "A", "Dest", "--apply", "abc"),
             ("append", "A", "hello", "world"), ("set", "A", "status"), ("resolve", "24995"),
             ("resolve", "2499"), ("set", "24997", "status", "x")]
    py, rs = engines
    for p in pairs:
        a, b = py.run(*p), rs.run(*p)
        check(f"X parity `{' '.join(p)}`", (a.returncode, a.stdout, a.stderr) == (b.returncode, b.stdout, b.stderr),
              f"py=({a.returncode},{a.stderr[:80]!r}) rs=({b.returncode},{b.stderr[:80]!r})")

print(f"\n{len(fails)} failures" if fails else "\nALL PASS")
sys.exit(1 if fails else 0)
