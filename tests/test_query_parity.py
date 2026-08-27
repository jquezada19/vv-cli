#!/usr/bin/env python3
"""query.rs (agent C, 2026-08-27): native `board`/`tags`/`props`/`show` MUST be
byte-identical (stdout+stderr+exit) to `python3 src/vv.py <same args>`.

NOTE on wiring: vv.py's CMDS dispatch does NOT yet route board/tags/props/show
through the vrust binary (only search/linkscan check use_rust() today — see
vv_impl.py:852,975). This test therefore compares the `vrust` binary invoked
DIRECTLY against `python3 vv.py` invoked directly, exactly like
test_native_readpath.py does for outline/read/head/resolve. It is meaningful
right now (it pins query.rs's own behavior against the python reference); it
becomes meaningful for END-TO-END `vv board ...` calls only once vv_impl.py's
CMDS table is taught to exec vrust for these four commands the way cmd_search
already does — that wiring is out of scope for this module."""
import os, subprocess, sys, tempfile, shutil

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VR = os.path.join(REPO, "vrust/target/release/vrust")
VV = os.path.join(REPO, "src/vv.py")

FIXTURES = {
    "root-note.md": '---\nstatus: active\ntype: note\ntags: alpha, beta/one\n---\n# Root\nbody\n',
    "quoted-vals.md": '---\nstatus: "active"\ntype: \'note\'\ntags: gamma\n---\n# Q\nbody\n',
    "no-fm.md": "# No Frontmatter\njust body\n",
    "empty-fm.md": "---\n---\n# Empty FM\nbody\n",
    "unterminated-fm.md": "---\nstatus: never-closed\n# heading\n",
    "crlf-note.md": "---\r\nstatus: done\r\ntype: crlf\r\ntags: delta\r\n---\r\n# C\r\nbody\r\n",
    "unicode-tags.md": "---\ntags: café, naïve\n---\n# U\nbody\n",
    "sub/child-a.md": "---\nstatus: active\ntype: child\ntags: alpha\n---\n# Child A\nbody\n",
    "sub/child-b.md": "---\nstatus: blocked\ntype: child\n---\n# Child B\nbody\n",
    "sub/deep/leaf.md": "---\nstatus: active\ntype: leaf\ntags: alpha/one, beta\n---\n# Leaf\nbody\n",
    ".hidden/skip-me.md": "---\nstatus: active\n---\n# Skip\nbody\n",
    "multibyte-show.md": ("---\nk: v\n---\n" + "# H1\n" + ("é" * 50) + "\n"
                          + "## H2\n" + ("body " * 50) + "\n"),
    "empty-key-val.md": "---\nstatus: \ntags: \n---\n# E\nbody\n",
}


def run(binpath, args, env):
    if binpath is VV:
        return subprocess.run([sys.executable, VV] + args, capture_output=True, env=env)
    return subprocess.run([binpath] + args, capture_output=True, env=env)


def cmp(vault_env, args, fails, n):
    n[0] += 1
    a = run(VR, args, vault_env)
    b = run(VV, args, vault_env)
    if (a.stdout, a.stderr, a.returncode) != (b.stdout, b.stderr, b.returncode):
        fails.append((args, a.returncode, b.returncode, a.stdout[:200], b.stdout[:200]))


def main():
    if not os.path.exists(VR):
        print("SKIP: vrust binary not built"); return 0
    tv = tempfile.mkdtemp(prefix="vv-query-")
    fails = []
    n = [0]
    try:
        for name, content in FIXTURES.items():
            fp = os.path.join(tv, name)
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            open(fp, "w", newline="").write(content)
        env = dict(os.environ, VV_NO_METRICS="1", VV_VAULT=tv, VV_JOURNAL_ROOT="1")

        # board: happy paths, filters, nested folder, missing folder, bad filter
        cmp(env, ["board", "."], fails, n)
        cmp(env, ["board", "sub"], fails, n)
        cmp(env, ["board", "sub", "status=active"], fails, n)
        cmp(env, ["board", "sub", "type=child", "status=active"], fails, n)
        cmp(env, ["board", "no-such-folder"], fails, n)
        cmp(env, ["board", ".", "badfilter"], fails, n)
        cmp(env, ["board"], fails, n)  # arity: missing folder

        # tags: bare, --counts, non-ASCII fallback path
        cmp(env, ["tags"], fails, n)
        cmp(env, ["tags", "--counts"], fails, n)

        # props: whole vault, folder-scoped, missing key, empty-string values
        cmp(env, ["props", "status"], fails, n)
        cmp(env, ["props", "type"], fails, n)
        cmp(env, ["props", "status", "sub"], fails, n)
        cmp(env, ["props", "tags"], fails, n)
        cmp(env, ["props", "nonexistent-key"], fails, n)
        cmp(env, ["props"], fails, n)  # arity: missing key

        # show: defaults, --from, --max-bytes boundaries around a multibyte char
        cmp(env, ["show", "multibyte-show"], fails, n)
        cmp(env, ["show", "multibyte-show", "--from", "H2"], fails, n)
        for mb in (1, 2, 3, 5, 8, 10, 20, 30, 40, 45, 50, 60, 100, 4000):
            cmp(env, ["show", "multibyte-show", "--max-bytes", str(mb)], fails, n)
        cmp(env, ["show", "no-fm"], fails, n)
        cmp(env, ["show", "crlf-note"], fails, n)
        cmp(env, ["show", "missing-note-xyz"], fails, n)
        cmp(env, ["show", "multibyte-show", "--max-bytes", "not-a-number"], fails, n)

        if fails:
            for f in fails[:12]:
                print("FAIL", f)
            print(f"\n{len(fails)} of {n[0]} DIVERGED")
            return 1
        print(f"ALL PASS (query-parity: {n[0]})")
        return 0
    finally:
        shutil.rmtree(tv, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
