#!/usr/bin/env python3
"""write.rs parity (agent B, 2026-08-27): native set/unset/append/appendsec
MUST be byte-identical (stdout+stderr+exit, AND the resulting file bytes) to
the Python implementation. Modeled on tests/test_native_readpath.py.

Each case runs the SAME command against two disposable copies of a fixture
vault -- one via the vrust binary, one via `python3 src/vv.py` -- then diffs
stdout/stderr/exit and every file's bytes in both copies.

NOTE on the native binary still being wired for Fallback-only dispatch as of
this writing: main.rs's dispatcher already routes set/unset/append/appendsec
to write::run (see main.rs "set" | "unset" | ... => Some(write::run)), so
once write.rs lands this test exercises the real native path. If write::run
ever regresses to pure Fallback, this test still PASSES (Fallback execs
python, so native-vs-python is trivially identical) -- it becomes a
meaningful regression pin the moment native output diverges, not a green
rubber stamp before that. We do NOT special-case that: identical bytes is
identical bytes either way.
"""
import os, subprocess, sys, tempfile, shutil, stat

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VR = os.path.join(REPO, "vrust/target/release/vrust")
VV = os.path.join(REPO, "src/vv.py")

# fixture vault contents, keyed by relative path
FIXTURES = {
    "plain.md": "---\ntype: note\nstatus: open\n---\n# H\nbody\n",
    "crlf.md": "---\r\ntype: note\r\nkey: old\r\n---\r\n# H\r\nbody\r\nmore\r\n",
    "bom.md": "﻿---\ntype: note\n---\n# H\nbody\n",
    "no-trailing-nl.md": "---\ntype: note\n---\n# H\nbody, no final newline",
    "no-fm.md": "# Just a heading\nno frontmatter here\n",
    "block-scalar.md": "---\ndesc: >\n  multi\n  line\ntype: note\n---\n# H\nbody\n",
    "block-scalar-pipe.md": "---\nnotes: |\n  literal\n  block\n---\n# H\nx\n",
    "quoted-vals.md": '---\ntitle: "already quoted"\nflow: [a, b]\n---\n# H\nx\n',
    "empty-fm-val.md": "---\ndesc:\n  continued line\ntype: note\n---\n# H\nx\n",
    "no-body.md": "---\ntype: note\n---\n",
    "append-target.md": "# Sec\nfirst\n## Sub\nsecond\n",
    "append-target-crlf.md": "# Sec\r\nfirst\r\n## Sub\r\nsecond\r\n",
    "append-no-trailing-nl.md": "# Sec\nno newline at eof",
    "empty.md": "",
}

# (cmd, args...) cases exercised against copies of the fixture vault.
# args[0] is always the note name (bare, resolved like `vv resolve` would).
CASES = [
    # --- set: bare value, no quoting needed ---
    ("set", ["plain", "priority", "high"]),
    # --- set: value needing colon-space quoting ---
    ("set", ["plain", "description", "vv pilot: live"]),
    # --- set: value ending with colon ---
    ("set", ["plain", "note", "trailing:"]),
    # --- set: value with " #" ---
    ("set", ["plain", "tag", "a #b"]),
    # --- set: value starting with a YAML lead char ---
    ("set", ["plain", "k", "[bracket"]),
    ("set", ["plain", "k2", "#hash"]),
    ("set", ["plain", "k3", "'quote"]),
    ("set", ["plain", "k4", "@at"]),
    # --- set: leading/trailing whitespace ---
    ("set", ["plain", "padded", " spaced "]),
    # --- set: indicator-before-space ---
    ("set", ["plain", "dash", "- item"]),
    ("set", ["plain", "q", "? what"]),
    ("set", ["plain", "colon", ": x"]),
    # --- set: -1 style value must NOT be quoted (indicator only before space) ---
    ("set", ["plain", "num", "-1"]),
    # --- set: control char / tab ---
    ("set", ["plain", "tabbed", "a\tb"]),
    # --- set: already well-formed quoted / balanced flow: pass through ---
    ("set", ["quoted-vals", "title", '"already quoted"']),
    ("set", ["quoted-vals", "flow", "[a, b]"]),
    # --- set: empty value ---
    ("set", ["plain", "empty", ""]),
    # --- set: new key appended ---
    ("set", ["plain", "brand-new-key", "v"]),
    # --- set: existing key replaced ---
    ("set", ["plain", "status", "closed"]),
    # --- set: no frontmatter -> synthesize one ---
    ("set", ["no-fm", "type", "note"]),
    # --- set: CRLF file, byte preservation ---
    ("set", ["crlf", "key", "new"]),
    # --- set: BOM file ---
    ("set", ["bom", "type", "changed"]),
    # --- set: no trailing newline on file ---
    ("set", ["no-trailing-nl", "type", "changed"]),
    # --- set: block-scalar key must refuse (Fallback -> python error path) ---
    ("set", ["block-scalar", "desc", "oops"]),
    ("set", ["block-scalar-pipe", "notes", "oops"]),
    ("set", ["empty-fm-val", "desc", "oops"]),
    # --- set: file with no body after fm ---
    ("set", ["no-body", "type", "changed"]),
    # --- unset: existing key ---
    ("unset", ["plain", "status"]),
    # --- unset: missing key -> not-found fallback ---
    ("unset", ["plain", "doesnotexist"]),
    # --- unset: no frontmatter -> fallback ---
    ("unset", ["no-fm", "type"]),
    # --- unset: block-scalar key -> refuse ---
    ("unset", ["block-scalar", "desc"]),
    # --- unset: CRLF / BOM byte preservation ---
    ("unset", ["crlf", "key"]),
    ("unset", ["bom", "type"]),
    # --- append: normal file ending in newline ---
    ("append", ["plain", "new line text"]),
    # --- append: file with no trailing newline ---
    ("append", ["append-no-trailing-nl", "appended text"]),
    # --- append: empty file ---
    ("append", ["empty", "first line"]),
    # --- append: CRLF file ---
    ("append", ["append-target-crlf", "crlf appended"]),
    # --- appendsec: normal section ---
    ("appendsec", ["append-target", "H1", "sec text"]),
    ("appendsec", ["append-target", "H2", "sub text"]),
    # --- appendsec: CRLF file ---
    ("appendsec", ["append-target-crlf", "H1", "crlf sec text"]),
    # --- appendsec: missing section -> fallback ---
    ("appendsec", ["append-target", "H9", "text"]),
]


def make_vault(tag):
    tv = tempfile.mkdtemp(prefix=f"vv-write-{tag}-")
    for name, content in FIXTURES.items():
        with open(os.path.join(tv, name), "w", newline="", encoding="utf-8") as f:
            f.write(content)
    return tv


def snapshot(tv):
    out = {}
    for name in FIXTURES:
        p = os.path.join(tv, name)
        out[name] = open(p, "rb").read() if os.path.exists(p) else None
    return out


def run_case(binary_argv, note_and_rest, env):
    args = binary_argv + [note_and_rest[0]] + list(note_and_rest[1:])
    return subprocess.run(args, capture_output=True, env=env)


def main():
    have_native = os.path.exists(VR)
    if not have_native:
        print("SKIP: vrust binary not built (write.rs parity untestable natively; "
              "python-vs-python sanity still runs below)")

    fails = []
    n = 0
    for cmd, cargs in CASES:
        tv_native = make_vault("native")
        tv_py = make_vault("py")
        try:
            env_native = dict(os.environ, VV_NO_METRICS="1", VV_VAULT=tv_native,
                               VV_PY_ENTRY=VV, VV_JOURNAL_ROOT=os.path.join(tv_native, ".journals"))
            env_py = dict(os.environ, VV_NO_METRICS="1", VV_VAULT=tv_py,
                           VV_JOURNAL_ROOT=os.path.join(tv_py, ".journals"))

            if have_native:
                a = run_case([VR, cmd], cargs, env_native)
            else:
                # native binary absent: fall back to running python twice so the
                # harness still proves the CASES table + comparison logic is sound.
                a = run_case([sys.executable, VV, cmd], cargs, env_native)
            b = run_case([sys.executable, VV, cmd], cargs, env_py)
            n += 1
            snap_a = snapshot(tv_native)
            snap_b = snapshot(tv_py)
            if (a.stdout, a.stderr, a.returncode) != (b.stdout, b.stderr, b.returncode):
                fails.append((cmd, cargs, "stdio/exit differ",
                              (a.stdout, a.stderr, a.returncode),
                              (b.stdout, b.stderr, b.returncode)))
                continue
            if snap_a != snap_b:
                diffs = [k for k in FIXTURES if snap_a[k] != snap_b[k]]
                fails.append((cmd, cargs, f"file bytes differ: {diffs}", None, None))
        finally:
            shutil.rmtree(tv_native, ignore_errors=True)
            shutil.rmtree(tv_py, ignore_errors=True)

    # --- CAS conflict: pre-touch the file between read and write is hard to
    # race deterministically in-process, so instead pin the journal-gate path:
    # a pending journal must route native to Fallback (same bytes as python's
    # own exit-4 refusal).
    tv_native = make_vault("journal-native")
    tv_py = make_vault("journal-py")
    try:
        jroot_native = os.path.join(tv_native, ".journals")
        jroot_py = os.path.join(tv_py, ".journals")
        import hashlib
        for tv, jroot in ((tv_native, jroot_native), (tv_py, jroot_py)):
            vid = hashlib.sha256(os.path.realpath(tv).encode()).hexdigest()[:12]
            pending = os.path.join(jroot, vid, "somejournal")
            os.makedirs(pending, exist_ok=True)
        env_native = dict(os.environ, VV_NO_METRICS="1", VV_VAULT=tv_native,
                           VV_PY_ENTRY=VV, VV_JOURNAL_ROOT=jroot_native)
        env_py = dict(os.environ, VV_NO_METRICS="1", VV_VAULT=tv_py, VV_JOURNAL_ROOT=jroot_py)
        if have_native:
            a = run_case([VR, "set"], ["plain", "k", "v"], env_native)
        else:
            a = run_case([sys.executable, VV, "set"], ["plain", "k", "v"], env_native)
        b = run_case([sys.executable, VV, "set"], ["plain", "k", "v"], env_py)
        n += 1
        if (a.stdout, a.stderr, a.returncode) != (b.stdout, b.stderr, b.returncode):
            fails.append(("set", ["<journal-gate>"], "stdio/exit differ",
                          (a.stdout, a.stderr, a.returncode), (b.stdout, b.stderr, b.returncode)))
    finally:
        shutil.rmtree(tv_native, ignore_errors=True)
        shutil.rmtree(tv_py, ignore_errors=True)

    if fails:
        for f in fails[:10]:
            print("FAIL", f)
        print(f"\n{len(fails)} of {n} DIVERGED")
        return 1
    tag = "native-vs-python" if have_native else "python-vs-python sanity only"
    print(f"ALL PASS (write-parity, {tag}: {n})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
