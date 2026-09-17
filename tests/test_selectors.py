#!/usr/bin/env python3
"""Pin: a SEC operand may be a content sha8 or a unique title PREFIX, in BOTH
engines, and the metrics row says which selector resolved it.

Two weeks of real traffic showed agents addressing sections two ways the
resolver refused. `read NOTE <sha8>` is the outline's own 5th column handed
straight back -- the outline prints it, so it reads as an address. And a
partial heading (`Today` for `## Today (Tuesday)`) is what a caller types when
the heading carries a parenthetical it did not memorise. Both were correctly
refused and uselessly so.

The order is total and identical in both engines: id, the `(preamble)` alias,
a `#Heading` spelling, an unambiguous exact title, then content sha8, then a
unique prefix. sha8 sits BELOW exact title on purpose -- a section whose
heading happens to be 8 hex characters is reached by its title, which is what
someone typing it means. A tier that matches more than once refuses with its
own message rather than picking a winner, because silently addressing the
wrong section is a write-path bug waiting to happen.

Ids here are derived from `vv outline`, never hand-typed: the section numbering
is what the outline prints, and a test that hard-codes it pins its own
arithmetic instead of the tool's.
"""
import atexit, json, os, shutil, subprocess, sys, tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VV = os.path.join(REPO, "src", "vv.py")
VRUST = os.path.join(REPO, "vrust", "target", "release", "vrust")

_TMP = []
def mkdtemp(prefix):
    d = tempfile.mkdtemp(prefix=prefix); _TMP.append(d); return d
def _cleanup():
    for d in _TMP:
        shutil.rmtree(d, True)
atexit.register(_cleanup)

fails = []
checks_run = 0

def check(name, cond, detail=""):
    global checks_run
    checks_run += 1
    print(("PASS " if cond else "FAIL ") + name + (f"  [{str(detail)[:200]}]" if detail and not cond else ""))
    if not cond:
        fails.append(name)

# `Dup` twice with identical bodies: two sections with the SAME content sha8,
# which is the only way the sha8 tier can be ambiguous.
NOTES = {"N.md": "# N\n\n## Today (Tuesday)\n\n- a\n\n## Tomorrow\n\n- b\n\n"
                 "## Todo list\n\n- c\n\n## deadbeef\n\nhex title\n\n"
                 "## Dup\n\n- d\n\n## Dup\n\n- d\n",
         # P.md exercises the word-break preference on its own so N.md's
         # numbering -- which the messages above quote -- stays undisturbed.
         "P.md": "# P\n\n## Today (Tuesday)\n\n- a\n\n## Today's plan\n\n- b\n\n"
                 "## Alpha one\n\n- c\n\n## Alpha two\n\n- d\n",
         # U.md's two headings FOLD to the same text: `\u0130` (capital I with
         # dot) lowercases to TWO characters, so either spelling of the
         # selector prefixes both. Written as escapes because the second
         # heading's combining dot is invisible in a source listing.
         "U.md": "# U\n\n## \u0130tem one\n\n- a\n\n## i\u0307tem two\n\n- b\n"}


class Engine:
    def __init__(self, name, env):
        self.name, self.env = name, env
        self.vault = mkdtemp(f"vv-selectors-{name}-vault-")
        self.journals = mkdtemp(f"vv-selectors-{name}-journals-")
        self.index = mkdtemp(f"vv-selectors-{name}-index-")
        self.home = mkdtemp(f"vv-selectors-{name}-home-")
        os.makedirs(os.path.join(self.home, ".claude/metrics"))
        for relp, body in NOTES.items():
            p = os.path.join(self.vault, relp)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                f.write(body)

    def run(self, *args, metrics=False, env=None, stdin=""):
        e = dict(os.environ, VV_VAULT=self.vault, VV_NO_METRICS="1", VV_INDEX_ROOT=self.index,
                 VV_JOURNAL_ROOT=self.journals, **self.env, **(env or {}))
        if metrics:
            # the sink is keyed off HOME, and both suppressors must be gone or
            # the row is never written
            e.pop("VV_NO_METRICS", None); e.pop("VV_JOURNAL_ROOT", None)
            e.pop("VV_METRICS_SRC", None)
            e["HOME"] = self.home
        entry = [VRUST] if self.name == "rust" else [sys.executable, VV]
        # stdin is ALWAYS a closed pipe: an inherited open stdin would block the
        # suite forever.
        return subprocess.run([*entry, *args], capture_output=True, text=True, env=e,
                              input=stdin, timeout=60)

    def snapshot(self):
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

    def rows(self):
        log = os.path.join(self.home, ".claude/metrics/vv.jsonl")
        if not os.path.exists(log):
            return []
        with open(log) as f:
            return [json.loads(l) for l in f if l.strip()]

    def last_row_after(self, *args, stdin=""):
        r = self.run(*args, metrics=True, stdin=stdin)
        rows = self.rows()
        return (rows[-1] if rows else {}), r


def next_of(stderr):
    """The next step -- and an envelope assertion: exactly one separator."""
    if stderr.count(" — next: ") != 1:
        return f"<separator count {stderr.count(' — next: ')}>"
    return stderr.rstrip().partition(" — next: ")[2]


def refused(eng, name, args, want_prefix, want_next, exit_code=1):
    """Run args; assert exit, stderr shape, and a byte-identical vault+journal."""
    before = eng.snapshot()
    r = eng.run(*args)
    check(f"{eng.name}: {name} exit {exit_code}", r.returncode == exit_code,
          f"rc={r.returncode} {r.stderr[:160]}")
    check(f"{eng.name}: {name} message", r.stderr.startswith(want_prefix), r.stderr)
    if want_next is not None:
        check(f"{eng.name}: {name} next", next_of(r.stderr) == want_next, r.stderr)
    after = eng.snapshot()
    check(f"{eng.name}: {name} vault+journal untouched", after == before,
          [k for k in set(before) | set(after) if before.get(k) != after.get(k)][:4])
    return r


def outline_rows(eng, note="N"):
    """`vv outline` as dicts, so every Hn id in this file comes from the tool."""
    r = eng.run("outline", note)
    if r.returncode != 0:
        check(f"{eng.name}: outline {note} succeeds", False, r.stderr)
        return {}
    by_title = {}
    for line in r.stdout.splitlines():
        f = line.split("\t")
        by_title.setdefault(f[2], []).append({"id": f[0], "sha8": f[4]})
    return by_title


# stderr of the length-changing-fold refusals, per selector per engine: the
# two engines must word it byte for byte, not merely each refuse.
fold_stderr = {}

engines = [Engine("python", {"VV_ENGINE": "python"})]
if os.path.exists(VRUST):
    engines.append(Engine("rust", {}))
else:
    print(f"SKIP native arm: {VRUST} not built (run_tests.sh builds it first, so the gate never skips)")

for eng in engines:
    ol = outline_rows(eng)
    if not ol:
        continue
    today, tomorrow, todo = ol["Today (Tuesday)"][0], ol["Tomorrow"][0], ol["Todo list"][0]
    dup = ol["Dup"]
    check(f"{eng.name}: the two Dup sections really do share a sha8",
          len(dup) == 2 and dup[0]["sha8"] == dup[1]["sha8"], dup)

    # --- content sha8 -------------------------------------------------------
    r = eng.run("read", "N", today["sha8"])
    check(f"{eng.name}: sha8 selects the section", r.stdout.startswith("## Today (Tuesday)"),
          (r.returncode, r.stdout[:80], r.stderr[:120]))
    r_up = eng.run("read", "N", today["sha8"].upper())
    check(f"{eng.name}: sha8 is case-insensitive",
          r_up.returncode == 0 and r_up.stdout == r.stdout, (r_up.returncode, r_up.stderr[:120]))
    refused(eng, "identical sections share a sha8 → ambiguous", ["read", "N", dup[0]["sha8"]],
            f"ambiguous: 2 sections have content sha8 {dup[0]['sha8']} "
            f"({dup[0]['id']}, {dup[1]['id']})", "vv outline N")
    refused(eng, "unknown sha8 is not-found, never stale", ["read", "N", "01234567"],
            "not-found: no section 01234567", "vv outline N")

    # --- unique title prefix ------------------------------------------------
    r = eng.run("read", "N", "Today")
    check(f"{eng.name}: unique prefix at '('", r.stdout.startswith("## Today (Tuesday)"),
          (r.returncode, r.stdout[:80], r.stderr[:120]))
    refused(eng, "prefix 'Tod' hits Today and Todo list", ["read", "N", "Tod"],
            f"ambiguous: 2 sections match 'Tod' ({today['id']}, {todo['id']})", "vv outline N")
    # a two-character token is too short to be a prefix selector: it would make
    # almost any note ambiguous, so it is never tried and the miss is plain
    refused(eng, "a 2-char token is too short to be a prefix", ["read", "N", "To"],
            "not-found: no section To", "vv outline N")

    # A token that ends at a word break outranks one that stops mid-word, so
    # `Today` reaches `Today (Tuesday)` past `Today's plan` -- but when the
    # break singles out nobody (`Alpha one` / `Alpha two`) the refusal names
    # every prefix hit rather than picking one.
    pl = outline_rows(eng, "P")
    r = eng.run("read", "P", "Today")
    check(f"{eng.name}: a word-break prefix outranks a mid-word one",
          r.stdout.startswith("## Today (Tuesday)"),
          (r.returncode, r.stdout[:80], r.stderr[:120]))
    refused(eng, "two word-break hits are still ambiguous", ["read", "P", "Alpha"],
            f"ambiguous: 2 sections match 'Alpha' "
            f"({pl['Alpha one'][0]['id']}, {pl['Alpha two'][0]['id']})", "vv outline P")

    # --- a case fold that changes LENGTH ------------------------------------
    # The word break that ranks prefix hits is an OFFSET, and it has to be
    # measured on the folded text: `\u0130tem one` and `i\u0307tem two`
    # lowercase to the same five characters, so a boundary taken from the
    # original title at the raw selector's length lands on the wrong character
    # and hands one of them the win. Neither spelling singles out a section, so
    # both must be refused, and both engines must word the refusal the same.
    ul = outline_rows(eng, "U")
    fold_one, fold_two = ul["\u0130tem one"][0], ul["i\u0307tem two"][0]
    for tok in ("\u0130tem", "i\u0307tem"):
        rf = refused(eng, f"a length-changing fold stays ambiguous ({tok!r})",
                     ["read", "U", tok],
                     f"ambiguous: 2 sections match {tok!r} "
                     f"({fold_one['id']}, {fold_two['id']})", "vv outline U")
        fold_stderr.setdefault(tok, {})[eng.name] = rf.stderr

    # --- the tiers stay in order --------------------------------------------
    r = eng.run("read", "N", "deadbeef")
    check(f"{eng.name}: 8-hex TITLE wins over hash lookup", "hex title" in r.stdout,
          (r.returncode, r.stdout[:80], r.stderr[:120]))
    # the flag spelling changes the SINK's label, never the order: `--section`
    # is the positional operand, so an 8-hex TITLE still wins there, and the
    # `--hash` spelling of the same token reaches content only -- nothing holds
    # that content hash, so it is a plain miss.
    r = eng.run("read", "N", "--section", "deadbeef")
    check(f"{eng.name}: --section 8-hex still reaches the TITLE tier", "hex title" in r.stdout,
          (r.returncode, r.stdout[:80], r.stderr[:120]))
    refused(eng, "--hash of an 8-hex TITLE is a miss", ["read", "N", "--hash", "deadbeef"],
            "not-found: no section deadbeef", "vv outline N")
    r = eng.run("read", "N", "## Tomorrow")
    check(f"{eng.name}: '## Title' form", r.stdout.startswith("## Tomorrow"),
          (r.returncode, r.stdout[:80], r.stderr[:120]))

    # --- the SEC operand may also be spelled as a flag -----------------------
    # `read NOTE --section X` and `read NOTE --hash X` were typed repeatedly
    # and refused on arity alone: the flag spelling is what a caller reaches
    # for when the operand order is not in front of them. The flag
    # carries the SAME selector the positional does, so the three spellings
    # are one output, byte for byte.
    a = eng.run("read", "N", "Tomorrow")
    b = eng.run("read", "N", "--section", "Tomorrow")
    c = eng.run("read", "N", "--section=Tomorrow")
    check(f"{eng.name}: --section is the positional SEC",
          b.returncode == 0 and c.returncode == 0 and a.stdout == b.stdout == c.stdout,
          (b.returncode, b.stderr[:120], c.stderr[:120]))
    # --hash skips the id/title tiers: the caller is saying "this is content",
    # so the 8-hex TITLE that wins the positional form must NOT win here.
    d = eng.run("read", "N", "--hash", today["sha8"])
    check(f"{eng.name}: --hash forces the sha8 tier",
          d.returncode == 0 and d.stdout.startswith("## Today (Tuesday)"),
          (d.returncode, d.stdout[:80], d.stderr[:120]))
    hexsec = ol["deadbeef"][0]
    e = eng.run("read", "N", "--hash", hexsec["sha8"])
    check(f"{eng.name}: --hash reaches the section whose TITLE is 8 hex",
          e.returncode == 0 and "hex title" in e.stdout,
          (e.returncode, e.stdout[:80], e.stderr[:120]))
    refused(eng, "--hash with a non-hex value is usage", ["read", "N", "--hash", "Tomorrow"],
            "usage: --hash takes an 8-hex sha8", "vv outline N")
    refused(eng, "--hash with no value is usage", ["read", "N", "--hash"],
            "usage: --hash takes an 8-hex sha8", "vv outline N")
    refused(eng, "--section with no value is usage", ["read", "N", "--section"],
            "usage: --section takes a value", "vv outline N")
    refused(eng, "a positional SEC plus a flag is usage",
            ["read", "N", today["id"], "--section=Tomorrow"],
            "usage: read takes one section selector", "vv outline N")
    refused(eng, "two flag selectors are usage",
            ["read", "N", "--section=Tomorrow", "--hash=deadbeef"],
            "usage: read takes one section selector", "vv outline N")
    refused(eng, "an unknown flag is usage", ["read", "N", "--sec", "H2"],
            "usage: read has no --sec", "vv outline N")
    # Over the positional ceiling the ordinary arity refusal still answers, and
    # its next step is still the note's own outline.
    refused(eng, "past the positional ceiling it is an arity miss",
            ["read", "N", today["id"], "--section", "Tomorrow"],
            "usage: read takes 1-3 positional args, got 4", "vv outline N")

    # --- bare `read NOTE` is a budgeted show ---------------------------------
    # Asking for a whole note by the command that reads notes is not a usage
    # error; it is `show`, budget and continuation token included.
    s, r1 = eng.run("show", "N"), eng.run("read", "N")
    check(f"{eng.name}: read NOTE is show NOTE",
          r1.returncode == 0 and r1.stdout == s.stdout, (r1.returncode, r1.stderr[:120]))
    # The row keeps the CALLER's spelling -- `op: read`, because what the
    # affordance measures is the command that was typed -- and carries
    # `sel: bare` so the report can count the whole-note class apart from the
    # section selectors. The native entry does not serve this shape, so the row
    # says `python` on both entries by design.
    bare_row, r_bare = eng.last_row_after("read", "N")
    check(f"{eng.name}: bare read logs op=read", bare_row.get("op") == "read",
          (bare_row, r_bare.returncode, r_bare.stderr[:120]))
    check(f"{eng.name}: bare read logs sel=bare", bare_row.get("sel") == "bare",
          (bare_row, r_bare.returncode, r_bare.stderr[:120]))
    check(f"{eng.name}: bare read runs on python", bare_row.get("engine") == "python",
          (bare_row, r_bare.returncode, r_bare.stderr[:120]))

    # --- writers share the resolver -----------------------------------------
    r = eng.run("appendsec", "N", "Tomor", "- z")
    check(f"{eng.name}: appendsec by unique prefix",
          r.returncode == 0 and "- b\n- z\n" in eng.read("N.md"),
          (r.returncode, r.stderr[:120], eng.read("N.md")[:120]))

    # --- the selector kind reaches the metrics row --------------------------
    # `engine` is asserted beside every `sel`: the native entry falls back to
    # python on anything it does not handle, and a fallback still produces a
    # correct row -- so a `sel` check alone would stay green if the native
    # success path regressed into Outcome::Fallback, which is precisely the
    # half of this change that lives in rust.
    want_engine = "native" if eng.name == "rust" else "python"

    def sel_row(label, want_sel, *args, stdin=""):
        row, r = eng.last_row_after(*args, stdin=stdin)
        check(f"{eng.name}: {label} sel={want_sel}", row.get("sel") == want_sel,
              (row, r.returncode, r.stderr[:120]))
        check(f"{eng.name}: {label} sel={want_sel} ran on {want_engine}",
              row.get("engine") == want_engine, (row, r.returncode, r.stderr[:120]))
        return row

    for tok, kind in ((today["sha8"], "sha8"), ("Today", "prefix"),
                      (today["id"], "id"), ("Tomorrow", "title")):
        sel_row("read", kind, "read", "N", tok)

    # A flag-spelled selector is its own kind in the sink: the question it
    # answers is which SPELLING agents reach for, and folding it into the tier
    # that resolved it would make the flag forms invisible.
    sel_row("read --section title", "flag", "read", "N", "--section", "Tomorrow")
    sel_row("read --section sha8", "flag", "read", "N", "--section", today["sha8"])
    sel_row("read --hash", "flag", "read", "N", "--hash", today["sha8"])

    # the writers log their selector too, and they are separate native arms
    sel_row("appendsec", "prefix", "appendsec", "N", "Tomor", "- q")
    # patch's SEC is the selector; its THIRD operand is the CAS sha8. Feeding
    # the section's own content sha8 as the selector exercises the sha8 tier on
    # the write path -- off a FRESH outline, because the appendsec above moved
    # every hash it touched.
    fresh = outline_rows(eng)
    todo_now = fresh["Todo list"][0]
    sel_row("patch", "sha8", "patch", "N", todo_now["sha8"], todo_now["sha8"],
            stdin="- patched\n")
    check(f"{eng.name}: the patch actually landed", "- patched\n" in eng.read("N.md"),
          eng.read("N.md")[:200])

    # --- an unresolvable SEC keeps the whole TEXT in the next step ----------
    # `- via append` starts with a dash; treating that as a FLAG dropped it out
    # of the suggested command entirely, so the printed next step silently
    # changed what the caller had asked for.
    refused(eng, "an unresolvable 3-operand append quotes the full TEXT",
            ["append", "N", "Nope", "- via append"],
            "usage: append takes 2 positional args, got 3",
            "vv append N 'Nope - via append'")

for tok, by_engine in fold_stderr.items():
    if len(by_engine) > 1:
        vals = list(by_engine.values())
        check(f"both engines refuse {tok!r} byte for byte", vals[0] == vals[1], vals)

print(("ALL PASS (selectors: %d)" % checks_run) if not fails
      else "FAILURES: " + ", ".join(fails))
sys.exit(1 if fails else 0)
