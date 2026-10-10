#!/usr/bin/env python3
"""Line endings are data that belong to the line. A write may change bytes
only inside the span it edits; every other byte of the note is copied.

Each case is (fixture bytes, command, args, stdin, EXPECTED bytes). The
expected bytes are written by hand here — not derived from either engine —
so this suite is an oracle both engines are checked against, not a parity
rubber stamp. Standalone script: own check(), PASS/FAIL lines, exit 1 on any
FAIL. Runs every case through the python entry and, when built, the native
binary."""
import os, subprocess, sys, tempfile, shutil

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VV = [sys.executable, os.path.join(REPO, "src/vv.py")]
VR = [os.path.join(REPO, "vrust/target/release/vrust")]

MIXED = (b"---\r\ntype: note\r\n---\r\n"
         b"# One\r\nalpha\r\nbeta\r\n"
         b"# Two\nlf-a\nlf-b\n"
         b"# Three\r\ngamma\r\n")

# (name, fixture bytes, argv after the note name, stdin bytes or None, expected bytes)
CASES = [
    # --- appendsec: inserted line takes the terminator of the line above ---
    ("appendsec-into-lf-section", MIXED, ["appendsec", "H2", "new"], None,
     b"---\r\ntype: note\r\n---\r\n# One\r\nalpha\r\nbeta\r\n# Two\nlf-a\nlf-b\nnew\n# Three\r\ngamma\r\n"),
    ("appendsec-into-crlf-section", MIXED, ["appendsec", "H1", "new"], None,
     b"---\r\ntype: note\r\n---\r\n# One\r\nalpha\r\nbeta\r\nnew\r\n# Two\nlf-a\nlf-b\n# Three\r\ngamma\r\n"),
    ("appendsec-last-section-eof", MIXED, ["appendsec", "H3", "new"], None,
     b"---\r\ntype: note\r\n---\r\n# One\r\nalpha\r\nbeta\r\n# Two\nlf-a\nlf-b\n# Three\r\ngamma\r\nnew\r\n"),
    # --- all-LF and all-CRLF notes: byte-identical to 3.1 behaviour ---
    ("appendsec-all-lf", b"# A\nx\n## B\ny\n", ["appendsec", "H2", "z"], None,
     b"# A\nx\n## B\ny\nz\n"),
    ("appendsec-all-crlf", b"# A\r\nx\r\n## B\r\ny\r\n", ["appendsec", "H2", "z"], None,
     b"# A\r\nx\r\n## B\r\ny\r\nz\r\n"),
    # --- no trailing newline: the unterminated final line gains its neighbour's terminator, nothing else moves ---
    ("appendsec-no-trailing-nl", b"# A\r\nx\r\n## B\ny", ["appendsec", "H2", "z"], None,
     b"# A\r\nx\r\n## B\ny\nz"),
    # --- append (EOF): separator and terminator come from the LAST terminated line ---
    ("append-eof-mixed-last-crlf", MIXED, ["append", "tail"], None,
     MIXED + b"tail\r\n"),
    ("append-eof-last-lf", b"# A\r\nx\r\n# B\ny\n", ["append", "tail"], None,
     b"# A\r\nx\r\n# B\ny\ntail\n"),
    ("lone-cr-eof", b"# A\nx\r", ["append", "tail"], None,
     b"# A\nx\r\ntail\n"),
    # --- patch: unchanged head/tail keep their bytes; the changed line keeps the original line's terminator ---
    ("patch-mixed-middle",
     b"# One\r\nalpha\r\nbeta\ngamma\r\n", ["patch", "H1", "SHA8"], b"# One\nalpha\nBETA\ngamma\n",
     b"# One\r\nalpha\r\nBETA\ngamma\r\n"),
    ("patch-roundtrip-noop",
     b"# One\r\nalpha\r\nbeta\ngamma\r\n", ["patch", "H1", "SHA8"], b"# One\nalpha\nbeta\ngamma\n",
     b"# One\r\nalpha\r\nbeta\ngamma\r\n"),
    ("patch-grow-section",
     b"# One\r\nalpha\r\n# Two\nx\n", ["patch", "H1", "SHA8"], b"# One\nalpha\nadded\n",
     b"# One\r\nalpha\r\nadded\r\n# Two\nx\n"),
    # --- set / unset: only the edited key line changes; delimiters, other keys, body untouched ---
    ("set-replace-key", MIXED, ["set", "type", "todo"], None,
     MIXED.replace(b"type: note\r\n", b"type: todo\r\n")),
    ("set-add-key", MIXED, ["set", "status", "open"], None,
     MIXED.replace(b"type: note\r\n---\r\n", b"type: note\r\nstatus: open\r\n---\r\n")),
    ("set-fm-lf-body-crlf", b"---\ntype: note\n---\n# A\r\nx\r\n", ["set", "status", "open"], None,
     b"---\ntype: note\nstatus: open\n---\n# A\r\nx\r\n"),
    ("unset-key", b"---\r\ntype: note\r\nstatus: open\r\n---\r\n# A\nx\n", ["unset", "status"], None,
     b"---\r\ntype: note\r\n---\r\n# A\nx\n"),
    ("set-no-fm-crlf-body", b"# A\r\nx\r\n", ["set", "type", "note"], None,
     b"---\r\ntype: note\r\n---\r\n# A\r\nx\r\n"),
    # --- prepend: inserted line takes the terminator of the closing --- line (or the first line without fm) ---
    ("prepend-after-crlf-fm", b"---\r\ntype: note\r\n---\r\n# A\nx\n", ["prepend", "top"], None,
     b"---\r\ntype: note\r\n---\r\ntop\r\n# A\nx\n"),
    ("prepend-no-fm-lf", b"# A\nx\r\n", ["prepend", "top"], None,
     b"top\n# A\nx\r\n"),
]


def sha8_of(engine, vault, note, sid):
    """The sha8 the engine prints for a section — what a caller pastes into patch."""
    r = subprocess.run(engine + ["outline", note], capture_output=True, text=True,
                       env=dict(os.environ, VV_VAULT=vault, VV_NO_METRICS="1"))
    for line in r.stdout.splitlines():
        cols = line.split("\t")
        if cols and cols[0] == sid:
            return cols[4]
    raise AssertionError(f"no {sid} in outline: {r.stdout!r} {r.stderr!r}")


def run_case(engine, name, fixture, argv, stdin, expected):
    vault = tempfile.mkdtemp(prefix="vv-eol-")
    try:
        os.makedirs(os.path.join(vault, "Standups"))
        note = "N.md"
        with open(os.path.join(vault, note), "wb") as f:
            f.write(fixture)
        argv = list(argv)
        if argv[0] == "patch":
            argv[2] = sha8_of(engine, vault, note, argv[1])
        cmd = engine + [argv[0], note] + argv[1:]
        r = subprocess.run(cmd, input=stdin, capture_output=True,
                           env=dict(os.environ, VV_VAULT=vault, VV_NO_METRICS="1"))
        with open(os.path.join(vault, note), "rb") as f:
            got = f.read()
        ok = r.returncode == 0 and got == expected
        return ok, f"exit={r.returncode} stderr={r.stderr[:200]!r}\n   got={got!r}\n   exp={expected!r}"
    finally:
        shutil.rmtree(vault, ignore_errors=True)


def main():
    fails, n = [], 0
    engines = [("python", VV)]
    if os.path.exists(VR[0]):
        engines.append(("native", VR))
    else:
        print("SKIP native: vrust binary not built")
    for eng_name, eng in engines:
        for name, fixture, argv, stdin, expected in CASES:
            n += 1
            ok, info = run_case(eng, name, fixture, argv, stdin, expected)
            print(("PASS " if ok else "FAIL ") + f"{eng_name}: {name}" + ("" if ok else f"\n   {info}"))
            if not ok:
                fails.append(f"{eng_name}: {name}")
    if fails:
        print(f"\n{len(fails)} of {n} FAILED: {fails}")
        return 1
    print(f"ALL PASS (eol-preserve: {n})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
