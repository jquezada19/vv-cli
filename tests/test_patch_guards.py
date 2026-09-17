#!/usr/bin/env python3
"""Task 9 — `patch` guards: whole-file CAS and the alias-safe frontmatter
refusal.

Two bugs, one command (`cmd_patch` in src/vv_impl.py, `cmd_patch` in
vrust/src/write.rs — both engines handle `patch` natively, so both change):

1. `find_sec` maps the outline's display label `(preamble)` (and bare
   `preamble`) to H0, but the frontmatter refusal tested the literal `sid`
   argument the caller typed, not the resolved section id — so
   `patch NOTE (preamble) ...` bypassed the refusal entirely and rewrote a
   note's YAML frontmatter as plain body text. Fixed by testing the
   resolved section's id (`s["id"] == "H0"` / `s.id == "H0"`).

2. Every other writer (set/unset/append/appendsec/daily-append) captures a
   whole-file signature (mtime_ns + size) before its read and passes it to
   `atomic_write` as `expect_sig`, so a second writer (Obsidian, most
   often) landing between the read and the write is caught instead of
   silently overwritten. `patch` alone skipped this — it had a
   section-level sha8 CAS (the `expect` argument) but no whole-file CAS, so
   a concurrent edit to a DIFFERENT part of the same file, one the
   section-level hash can't see, was unguarded. Fixed by capturing the
   signature before the read (Python: `file_sig(fp)` before `read_raw`;
   Rust: `file_sig(&fp)` before `fs::read`) and threading it through
   `atomic_write(..., expect_sig=_sig)` / `atomic_write(&fp, &new_text,
   Some(sig))`.

The second case's test patches section H2 ("## S") on G.md while a
concurrent write lands on a DIFFERENT line (the "# G" heading, H1) between
the outline read and the patch call — outside the H2 span, so H2's own
section-level sha8 is untouched and that CAS alone would let the patch
through even without a whole-file guard. The whole-file check is what
notices the file changed at all. On the native (rust) arm this trips
`atomic_write`'s `Some(sig)` mismatch, which returns `Outcome::Fallback` —
the established pattern (mirrors set/unset/append/appendsec): stdin is
piped through to a fresh python process, which re-reads the now-current
file, recomputes its own signature and section hash from those fresh
bytes, finds H2 still matches the caller's `expect`, and completes the
write. Nothing is lost either way: the concurrent heading edit and the new
section body both land. The python-only arm never sees an actual mid-flight
race here — outline and patch are two separate `vv` invocations in this
suite, so by the time cmd_patch's own `file_sig()` runs, the concurrent
write from `eng.write()` is already the file it reads; there's no window
inside a single python invocation for that edit to arrive late.

That is why a SECOND case (on H.md) spawns `patch` with stdin held open and
writes the competing edit while the command is blocked on the pipe, which
is a real mid-flight race for both engines and the case that actually fails
when the whole-file guard is removed. The two engines end that race
differently on purpose — python refuses `stale:` (exit 3, no patch), native
falls back to python and completes (exit 0, both edits) — and the code
there says why.

A first attempt at this second case tried to patch the SAME section whose
own heading line the concurrent edit touched (id H1, since H1's span
starts at its own heading line — confirmed by `vv outline`, not assumed).
That is not a whole-file-CAS scenario at all: H1's own section-level sha8
already differs after the edit, so the existing (pre-Task-9) section CAS
alone refuses it with `stale:` — exit 3, no write, regardless of whether a
whole-file signature exists. Patching H2 instead, while the concurrent
edit touches H1's heading line, is what actually isolates the whole-file
guard as the thing doing the catching.
"""
import os, sys, shutil, subprocess, tempfile, atexit, time

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

NOTES = {"F.md": "---\nk: v\n---\n# F\n\nbody\n", "G.md": "# G\n\n## S\n\nold\n",
         "H.md": "# H\n\n## S\n\nold\n"}

class Engine:
    def __init__(self, name, env):
        self.name, self.env = name, env
        self.vault = mkdtemp(f"vv-patchguards-{name}-vault-")
        self.journals = mkdtemp(f"vv-patchguards-{name}-journals-")
        self.index = mkdtemp(f"vv-patchguards-{name}-index-")
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

def next_of(stderr):
    """The next step — and an envelope-integrity assertion: exactly one
    separator per error line."""
    if stderr.count(" — next: ") != 1:
        return f"<separator count {stderr.count(' — next: ')}>"
    return stderr.rstrip().partition(" — next: ")[2]

def refused(eng, tag, name, args, want_prefix, want_next, exit_code=1, env=None, stdin=None):
    """Run args; assert exit, stderr shape, and a byte-identical vault+journal."""
    before = eng.snapshot()
    r = eng.run(*args, env=env, stdin=stdin)
    check(f"{tag}{name} exit {exit_code}", r.returncode == exit_code, f"rc={r.returncode} {r.stderr[:160]}")
    check(f"{tag}{name} message", r.stderr.startswith(want_prefix), r.stderr)
    if want_next is not None:
        check(f"{tag}{name} next", next_of(r.stderr) == want_next, r.stderr)
    check(f"{tag}{name} vault+journal untouched", eng.snapshot() == before,
          [k for k in set(before) | set(eng.snapshot()) if before.get(k) != eng.snapshot().get(k)][:4])
    return r

def sha8_of(eng, note, hid):
    """Parse `vv outline NOTE` for the sha8 of section `hid` — the outline
    line shape is `id\\tmarker\\ttitle\\tNB\\tsha8` (cmd_outline)."""
    r = eng.run("outline", note)
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if parts and parts[0] == hid:
            return parts[-1]
    raise AssertionError(f"{eng.name}: no section {hid} in `vv outline {note}`: {r.stdout!r} {r.stderr!r}")

engines = [Engine("python", {"VV_ENGINE": "python"})]
if os.path.exists(VRUST):
    engines.append(Engine("rust", {}))
else:
    print(f"SKIP native arm: {VRUST} not built (run_tests.sh builds it first, so the gate never skips)")

for eng in engines:
    # the alias must not bypass the frontmatter refusal
    refused(eng, "", "patch (preamble) with frontmatter refuses like H0",
            ["patch", "F", "(preamble)", sha8_of(eng, "F", "H0")],
            "refused: H0 contains frontmatter", "vv set/unset (patch would rewrite YAML as body)",
            stdin="x\n")

    # whole-file CAS: a concurrent write to a DIFFERENT section (H1, the "# G"
    # heading) between the outline read and the patch call — H2's own
    # section-level sha8 never sees it, so only a whole-file guard can catch
    # the file having moved. The write must still land, with BOTH the
    # concurrent edit and the new section body.
    h = sha8_of(eng, "G", "H2")
    eng.write("G.md", "# G changed\n\n## S\n\nold\n")
    r = eng.run("patch", "G", "H2", h, stdin="## S\n\nnew\n")
    check(f"{eng.name}: patch succeeds (edit landed before the read)",
          r.returncode == 0, f"rc={r.returncode} {r.stderr}")
    check(f"{eng.name}: patch keeps the earlier out-of-section edit",
          eng.read("G.md") == "# G changed\n\n## S\n\nnew\n", eng.read("G.md"))

    # ...and the same thing with the edit INSIDE the window. The case above
    # never opens one: `eng.run` hands `patch` a stdin pipe that is already
    # closed, so by the time the command starts, the competing write is
    # simply the file it reads — reverting the whole-file guard leaves it
    # green. Here `patch` is spawned with stdin held OPEN, which pins the
    # interleaving on the ordering both engines document: every check that
    # can refuse or fall back runs BEFORE stdin is consumed, so the process
    # has read the note and is blocked on the pipe when the competing write
    # lands. The sleep is the handshake — there is no output before the read
    # to wait on, and the alternative (inspecting the child's open file
    # descriptors) buys determinism this suite does not need; the wait is
    # generous relative to a read of a four-line note, and a machine slow
    # enough to miss it fails loudly rather than passing wrongly, because
    # the competing write would then precede the read and the patch would
    # land with the heading edit lost.
    #
    # THE TWO ENGINES END DIFFERENTLY, BY DESIGN:
    #   python  — its own signature is stale, `atomic_write` refuses:
    #             `stale:` exit 3, the note keeps the competing edit and
    #             does NOT get the patch. The caller re-reads and retries.
    #   native  — the same mismatch is `Outcome::Fallback`, not a refusal:
    #             the captured stdin bytes are piped to a fresh python
    #             process, which reads the now-current file, and since the
    #             competing edit is outside H2 the section sha8 still
    #             matches, so the patch lands, exit 0, with BOTH edits.
    hh = sha8_of(eng, "H", "H2")
    entry = [VRUST] if eng.name == "rust" else [sys.executable, VV]
    e = dict(os.environ, VV_VAULT=eng.vault, VV_NO_METRICS="1", VV_INDEX_ROOT=eng.index,
             VV_JOURNAL_ROOT=eng.journals, **eng.env)
    proc = subprocess.Popen([*entry, "patch", "H", "H2", hh], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=e)
    time.sleep(0.3)          # long enough for the note read; see the note above
    eng.write("H.md", "# H changed\n\n## S\n\nold\n")
    sout, serr = proc.communicate("## S\n\nnew\n", timeout=60)
    after = eng.read("H.md")
    if eng.name == "python":
        check(f"{eng.name}: mid-window edit refuses stale", proc.returncode == 3,
              f"rc={proc.returncode} {serr}")
        check(f"{eng.name}: mid-window refusal names the whole-file guard",
              serr.startswith("stale: H.md changed on disk since it was read"), serr)
        check(f"{eng.name}: mid-window refusal leaves the competing edit and no patch",
              after == "# H changed\n\n## S\n\nold\n", after)
    else:
        check(f"{eng.name}: mid-window mismatch falls back and completes",
              proc.returncode == 0, f"rc={proc.returncode} {serr}")
        check(f"{eng.name}: fallback keeps both the competing edit and the patch",
              after == "# H changed\n\n## S\n\nnew\n", after)

print(f"\n{len(fails)} failures" if fails else "\nALL PASS")
sys.exit(1 if fails else 0)
