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
                 "## Alpha one\n\n- c\n\n## Alpha two\n\n- d\n"}


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

    def run(self, *args, metrics=False, env=None):
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
                              input="", timeout=60)

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

    def last_row_after(self, *args):
        self.run(*args, metrics=True)
        r = self.rows()
        return r[-1] if r else {}


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

    # --- the tiers stay in order --------------------------------------------
    r = eng.run("read", "N", "deadbeef")
    check(f"{eng.name}: 8-hex TITLE wins over hash lookup", "hex title" in r.stdout,
          (r.returncode, r.stdout[:80], r.stderr[:120]))
    r = eng.run("read", "N", "## Tomorrow")
    check(f"{eng.name}: '## Title' form", r.stdout.startswith("## Tomorrow"),
          (r.returncode, r.stdout[:80], r.stderr[:120]))

    # --- writers share the resolver -----------------------------------------
    r = eng.run("appendsec", "N", "Tomor", "- z")
    check(f"{eng.name}: appendsec by unique prefix",
          r.returncode == 0 and "- b\n- z\n" in eng.read("N.md"),
          (r.returncode, r.stderr[:120], eng.read("N.md")[:120]))

    # --- the selector kind reaches the metrics row --------------------------
    for tok, kind in ((today["sha8"], "sha8"), ("Today", "prefix"),
                      (today["id"], "id"), ("Tomorrow", "title")):
        row = eng.last_row_after("read", "N", tok)
        check(f"{eng.name}: sel={kind}", row.get("sel") == kind, row)

    # --- an unresolvable SEC keeps the whole TEXT in the next step ----------
    # `- via append` starts with a dash; treating that as a FLAG dropped it out
    # of the suggested command entirely, so the printed next step silently
    # changed what the caller had asked for.
    refused(eng, "an unresolvable 3-operand append quotes the full TEXT",
            ["append", "N", "Nope", "- via append"],
            "usage: append takes 2 positional args, got 3",
            "vv append N 'Nope - via append'")

print(("ALL PASS (selectors: %d)" % checks_run) if not fails
      else "FAILURES: " + ", ".join(fails))
sys.exit(1 if fails else 0)
