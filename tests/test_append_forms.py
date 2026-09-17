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
# Task 11 (unique-PREFIX section matching) hasn't landed: `_find_sec_or_none`
# still only matches an id, the `(preamble)` alias, or an EXACT title, so
# "Beta" does not yet resolve against a section titled "Beta (Two)". This
# case is written now, against the shape Task 11 will also dispatch through,
# and marked pending rather than deferred — Task 11 removes it from this set.
EXPECTED_PENDING = {"unique prefix SEC resolves (Task 11 semantics)"}

def check(name, cond, detail=""):
    if any(p in name for p in EXPECTED_PENDING):
        print(f"pending {name}")
        return
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
    check(f"{eng.name}: unique prefix SEC resolves (Task 11 semantics)", r.returncode == 0, r.stderr)

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

print(f"\n{len(fails)} failures" if fails else "\nALL PASS")
sys.exit(1 if fails else 0)
