#!/usr/bin/env python3
"""Task 7 — 3-arg `append NOTE SEC TEXT` dispatches to `appendsec` when SEC
resolves.

`cmd_append` used to be arity-2 only; a caller who typed `vv append NOTE SEC
TEXT` (a section append) got the "TEXT is one argument; quote it" usage
error, which is right for `hello world` but wrong for `Alpha "- new"` — SEC
resolved to a real section and the caller's intent was appendsec, not a
quoting mistake. Now a resolving 3rd operand dispatches to `cmd_appendsec`
(reported under its own op, `appended to <Hn> in <note>`); an unresolvable
one keeps the quoting refusal, now naming appendsec as the alternative.

Native `cmd_append` (vrust/src/write.rs) returns Fallback for any
`args.len() != 2`, so this dispatch is Python-only — but the suite still
runs BOTH entries (the native binary and `VV_ENGINE=python`), so the native
arm proves the fallback itself reaches the same Python code, not just that
the CLI script does.

Task 8 (extended into this file rather than a new one) — `daily-append`
used to append unconditionally at EOF, landing in whatever section happened
to be last instead of "Today", the section its own name promises.
`write.rs` has no `daily-append` arm and `main.rs` routes it to a Python
fallback, so this is Python-only too; both entries still run for the same
reason as Task 7's cases.
"""
import os, sys, shutil, subprocess, tempfile, atexit

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VV = os.path.join(REPO, "src", "vv.py")
VRUST = os.path.join(REPO, "vrust", "target", "release", "vrust")
sys.path.insert(0, os.path.join(REPO, "src"))

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
    if not cond:
        fails.append(name)

STANDUP = ("---\ndate: 2026-09-20\n---\n# Standup 2026-09-20\n\n## Yesterday (Friday)\n\n- y\n\n"
           "## Today (Saturday)\n\n- t1\n\n### Sub\n\n- sub\n\n## Blockers / Needs\n\n- b\n")

NOTES = {"A.md": "# A\n\n## Alpha\n\na\n\n## Beta (Two)\n\nb\n", "B.md": "# B\nline\n",
         "Dup.md": "# D\n\n## Same\n\nx\n\n## Same\n\ny\n\n## Other\n\nz\n",
         "Standups/Standup 2026-09-20.md": STANDUP}

class Engine:
    def __init__(self, name, env):
        self.name, self.env = name, env
        self.vault = mkdtemp(f"vv-appendforms-{name}-vault-")
        self.journals = mkdtemp(f"vv-appendforms-{name}-journals-")
        self.index = mkdtemp(f"vv-appendforms-{name}-index-")
        for relp, body in NOTES.items():
            p = os.path.join(self.vault, relp)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                f.write(body)

    def run(self, *args, stdin=None, env=None):
        e = dict(os.environ, VV_VAULT=self.vault, VV_NO_METRICS="1", VV_INDEX_ROOT=self.index,
                 VV_JOURNAL_ROOT=self.journals, **self.env, **(env or {}))
        entry = [VRUST] if self.name == "rust" else [sys.executable, VV]
        # stdin is ALWAYS a closed pipe: an inherited open stdin (a background
        # runner's) would block the suite forever.
        return subprocess.run([*entry, *args], capture_output=True, text=True, env=e,
                              input=stdin if stdin is not None else "", timeout=60)

    def snapshot(self):
        """Every file's bytes + every directory, vault and journal root — a
        refusal must leave BOTH byte-identical."""
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

    def write(self, relp, text):
        p = os.path.join(self.vault, relp)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(text.encode("utf-8") if isinstance(text, str) else text)

    def read_bytes(self, relp):
        with open(os.path.join(self.vault, relp), "rb") as f:
            return f.read()

def next_of(stderr):
    """The next step — and an envelope-integrity assertion: exactly one
    separator per error line."""
    if stderr.count(" — next: ") != 1:
        return f"<separator count {stderr.count(' — next: ')}>"
    return stderr.rstrip().partition(" — next: ")[2]

def refused(eng, tag, name, args, want_prefix, want_next, exit_code=1, env=None):
    """Run args; assert exit, stderr shape, and a byte-identical vault+journal."""
    before = eng.snapshot()
    r = eng.run(*args, env=env)
    check(f"{tag}{name} exit {exit_code}", r.returncode == exit_code, f"rc={r.returncode} {r.stderr[:160]}")
    check(f"{tag}{name} message", r.stderr.startswith(want_prefix), r.stderr)
    if want_next is not None:
        check(f"{tag}{name} next", next_of(r.stderr) == want_next, r.stderr)
    check(f"{tag}{name} vault+journal untouched", eng.snapshot() == before,
          [k for k in set(before) | set(eng.snapshot()) if before.get(k) != eng.snapshot().get(k)][:4])
    return r

engines = [Engine("python", {"VV_ENGINE": "python"})]
if os.path.exists(VRUST):
    engines.append(Engine("rust", {}))
else:
    print(f"SKIP native arm: {VRUST} not built (run_tests.sh builds it first, so the gate never skips)")

for eng in engines:
    r = eng.run("append", "A", "Alpha", "- new")
    after = eng.read("A.md")
    check(f"{eng.name}: 3-arg append with resolving SEC lands inside the section",
          r.returncode == 0 and after == "# A\n\n## Alpha\n\na\n- new\n\n## Beta (Two)\n\nb\n", after)
    # H2, not H1: "# A" is itself the note's first heading (`vv outline A`
    # confirms it — H1 A / H2 Alpha / H3 Beta (Two)), so the SECOND heading,
    # Alpha, is H2. The canonical id is what's echoed, not the caller's SEC
    # spelling, so the message names the section outline agrees with.
    check(f"{eng.name}: reports as appendsec", r.stdout.strip() == "appended to H2 in A.md", r.stdout)

    r = eng.run("append", "A", "Beta", "- x")
    check(f"{eng.name}: unique prefix SEC resolves", r.returncode == 0, r.stderr)

    refused(eng, "", "unresolvable SEC keeps the quoting usage error", ["append", "B", "hello", "world"],
            "usage: append takes 2 positional args, got 3 (TEXT is one argument; quote it; a section append is appendsec)",
            "vv append B 'hello world'")

    r = eng.run("append", "B", "- eof")
    check(f"{eng.name}: 2-arg append still EOF", eng.read("B.md").endswith("line\n- eof\n"), eng.read("B.md"))

    # Deferred from this task's review: two sections sharing a title still
    # refuse ambiguous through the 3-arg append→appendsec dispatch, and the
    # vault is untouched — not just "not the wrong section".
    refused(eng, "", "duplicate section titles refuse ambiguous append",
            ["append", "Dup", "Same", "- x"],
            "ambiguous: 2 sections are titled 'Same' (H2, H3)", "vv outline Dup")

# --- the delegation window is one guarded snapshot -------------------------
# `cmd_append`'s 3-operand form resolves SEC itself and then calls
# `cmd_appendsec`, which RE-READS the note. Between those two reads another
# writer (Obsidian, a peer session) can insert a heading: ids number every
# heading in document order, so an inserted heading above the resolved
# section shifts every id below it and the canonical `Hn` handed over a
# moment ago now names a DIFFERENT section. `cmd_appendsec`'s own
# compare-and-swap cannot see it — it captures its signature AFTER the
# intervening write, so the signature it compares against is already the
# post-edit file.
#
# The window is closed by handing the signature captured before the FIRST
# read through to the delegated write, so the whole dispatch is one guarded
# snapshot: the resolve and the write either see the same bytes or the write
# is refused with `stale:` (exit 3). Delegating the caller's original
# selector string instead would only cover a caller who typed a TITLE (a
# caller who typed `H2` would still be re-resolving a stale id), and it would
# change the reported id away from the canonical one the native engine prints
# for the same call.
#
# The race cannot be staged from outside the process — both reads happen
# inside one `vv` invocation — so it is staged from inside: a child python
# process imports vv_impl, monkeypatches `cmd_appendsec` to record the
# delegated selector and to perform the competing edit before calling the
# real function, and runs `cmd_append` directly. That makes the interleaving
# exact rather than timing-dependent.
UNIT_RACE = r"""
import os, sys
sys.path.insert(0, sys.argv[1])
import vv_impl
note = os.path.join(os.environ["VV_VAULT"], "Race.md")
real, seen = vv_impl.cmd_appendsec, {}

def spy(ref, sid, text, *a, **kw):
    seen["sid"] = sid
    # the competing writer, landing in the window: "## Intruder" above the
    # resolved section pushes Alpha from H2 to H3
    with open(note, "w") as f:
        f.write("# R\n\n## Intruder\n\ni\n\n## Alpha\n\na\n\n## Beta\n\nb\n")
    return real(ref, sid, text, *a, **kw)

vv_impl.cmd_appendsec = spy
try:
    vv_impl.cmd_append("Race", "Alpha", "- x")
    rc = 0
except SystemExit as e:
    rc = e.code
print("DELEGATED_SID=%s" % seen.get("sid"))
print("EXIT=%s" % rc)
with open(note) as f:
    sys.stdout.write("FILE<<%s>>" % f.read())
"""

for eng in engines:
    if eng.name != "python":
        continue   # a unit-level call into vv_impl; the entry point is not what is under test
    eng.write("Race.md", "# R\n\n## Alpha\n\na\n\n## Beta\n\nb\n")
    env = dict(os.environ, VV_VAULT=eng.vault, VV_NO_METRICS="1", VV_INDEX_ROOT=eng.index,
               VV_JOURNAL_ROOT=eng.journals, VV_ENGINE="python")
    r = subprocess.run([sys.executable, "-c", UNIT_RACE, os.path.join(REPO, "src")],
                       capture_output=True, text=True, env=env, input="", timeout=60)
    body = r.stdout.partition("FILE<<")[2].rpartition(">>")[0]
    check("delegation carries the selector resolved on the guarded snapshot",
          "DELEGATED_SID=H2" in r.stdout, r.stdout)
    check("a heading inserted inside the delegation window refuses stale",
          "EXIT=3" in r.stdout and r.stderr.startswith("stale:"), r.stdout + r.stderr)
    check("the refused delegation writes nothing",
          body == "# R\n\n## Intruder\n\ni\n\n## Alpha\n\na\n\n## Beta\n\nb\n", body)

# --- Task 8: daily-append lands at the end of the Today section ------------
# cmd_daily_append used to append unconditionally at EOF, which lands inside
# whatever section happens to be last (usually "Blockers / Needs") instead of
# inside "Today", the section the command's own name promises. VV_TODAY is a
# test-only override next to datetime.date.today() so the suite can pin a
# fixed date instead of racing the real day.
for eng in engines:
    env = {"VV_TODAY": "2026-09-20"}
    r = eng.run("daily-append", "- t2", env=env)
    after = eng.read("Standups/Standup 2026-09-20.md")
    check(f"{eng.name}: lands after the last line of Today incl. its H3",
          "- sub\n- t2\n\n## Blockers" in after, after)
    # H3, not H2: ids number EVERY heading in document order regardless of
    # level (same rule Task 7's test documents for "# A" itself being H1) —
    # "# Standup 2026-09-20" is H1, "## Yesterday (Friday)" is H2, so the
    # THIRD heading, Today, is H3.
    check(f"{eng.name}: reports the landing",
          r.stdout.strip() == "appended to H3 (Today (Saturday)) in Standups/Standup 2026-09-20.md", r.stdout)

    # bold-label template shape (no "## Today…" H2) → EOF with an explicit report
    eng.write("Standups/Standup 2026-09-21.md", "# S\n\n## 🧍 Standup\n\n**Today** _(after 7 AM)_\n- a\n")
    r = eng.run("daily-append", "- z", env={"VV_TODAY": "2026-09-21"})
    check(f"{eng.name}: no Today heading → EOF and says so",
          r.returncode == 0 and r.stdout.strip().endswith("(no Today section — appended at end)"), r.stdout)

    # two dated notes → ambiguous, no write
    eng.write("Standups/Standup 2026-09-22.md", "# a\n")
    eng.write("Standups/Standup 2026-09-22 draft.md", "# b\n")
    refused(eng, "", "two notes for the date refuse", ["daily-append", "x"],
            "ambiguous: 2 standup notes for 2026-09-22", "vv search 2026-09-22 --files",
            env={"VV_TODAY": "2026-09-22"})

    # CRLF preserved
    eng.write("Standups/Standup 2026-09-23.md",
              "# S\r\n\r\n## Today (Tue)\r\n\r\n- a\r\n\r\n## Blockers / Needs\r\n\r\n- b\r\n")
    eng.run("daily-append", "- c", env={"VV_TODAY": "2026-09-23"})
    want = b"# S\r\n\r\n## Today (Tue)\r\n\r\n- a\r\n- c\r\n\r\n## Blockers / Needs\r\n\r\n- b\r\n"
    got = eng.read_bytes("Standups/Standup 2026-09-23.md")
    check(f"{eng.name}: CRLF kept", got == want, got)

    # Deferred from Task 9's review: no standup note at all for the date —
    # not-found, no write, and the next step's path operand goes through
    # _q(...) like every other writer's next step (an ISO date has no
    # spaces/control chars, so the quoted form is byte-identical to the
    # hand-written literal this replaced).
    refused(eng, "", "daily-append with no standup note for the date refuses",
            ["daily-append", "x"],
            "not-found: no standup note for 2026-09-24 under Standups/",
            "vv new 'Standups/Standup 2026-09-24' --template 'Daily Standup'",
            env={"VV_TODAY": "2026-09-24"})

    # Deferred from Task 9's review: one standup note, but two headings both
    # matching the "Today" regex (level 2 -- the regex only looks at H2s) —
    # ambiguous, no write. Ids number every heading in document order, so
    # with no heading between them the ids are H2 and H3 here, not "two
    # Today ids" in the abstract.
    eng.write("Standups/Standup 2026-09-25.md",
              "# S\n\n## Today (First)\n\n- a\n\n## Today (Second)\n\n- b\n")
    refused(eng, "", "two Today H2s in one standup refuse ambiguous",
            ["daily-append", "x"],
            "ambiguous: 2 Today sections (H2, H3)",
            "vv outline 'Standups/Standup 2026-09-25.md'",
            env={"VV_TODAY": "2026-09-25"})

print(f"\n{len(fails)} failures" if fails else "\nALL PASS")
sys.exit(1 if fails else 0)
