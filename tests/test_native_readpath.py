#!/usr/bin/env python3
"""Native read path (E2, 2026-08-27): the vrust binary serves outline/read/
head/resolve itself and MUST be byte-identical (stdout+stderr+exit) to the
Python implementation; anything it can't handle execs Python, so error grammar
is Python-canonical by construction. Fixtures are specification pins for the
grammar corners the corpus may not contain (panel-prescribed 2026-08-27)."""
import os, subprocess, sys, tempfile, shutil

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VR = os.path.join(REPO, "vrust/target/release/vrust")
VV = os.path.join(REPO, "src/vv.py")

FIXTURES = {
 "bom-crlf.md": "﻿---\r\ntype: note\r\n---\r\n# H\r\nbody\r\n",
 "bom-lf.md": "﻿---\ntype: x\n---\n# A\nb\n",
 "no-trailing-nl.md": "# Top\nbody with no final newline",
 "heading-at-0.md": "# First line is heading\nx\n",
 "empty.md": "",
 "only-fm.md": "---\na: 1\n---\n",
 "unterminated-fm.md": "---\nnever closed\n# Real Heading\n",
 "heading-in-fence.md": "pre\n```\n# not a heading\n```\n# real\n",
 "tilde-fence.md": "~~~\n# masked\n~~~\n## ok\n",
 "nested-fence.md": "````\n```\n# still masked\n```\n````\n# free\n",
 "inline-triplet.md": "```code``` inline\n# heading after inline span\n",
 "dup-headings.md": "# Same\na\n# Same\nb\n",
 "seven-hashes.md": "####### not a heading\n###### yes heading\n",
 "hash-no-space.md": "#nospace\n# spaced\n",
 "unicode-title.md": "# Émojis \U0001f9ed and ünïcode\ncontent\n",
 "crlf-body.md": "# One\r\nline\r\n## Two\r\nmore\r\n",
 "indented-fence.md": "   ```\n# masked by 3-space fence\n   ```\n# ok\n",
 "empty-sections.md": "# A\n# B\n# C\n",
 "fm-dash-body.md": "---\nk: v\n---\ntext\n---\nmore\n",
 # mini graph for the --limit pins: L2 and L3 both link L1; L1 links both.
 "Wk/L1.md": "---\nstatus: open\ntype: t1\ntags: [alpha, beta]\n---\n[[L2]] [[L3]]\n",
 "Wk/L2.md": "---\nstatus: open\ntype: t1\ntags: [alpha]\n---\n[[L1]]\n",
 "Wk/L3.md": "---\nstatus: done\ntype: t2\ntags: [beta]\n---\n[[L1]]\n",
}

def main():
    if not os.path.exists(VR):
        print("SKIP: vrust binary not built"); return 0
    tv = tempfile.mkdtemp(prefix="vv-native-")
    fails = []
    try:
        for name, content in FIXTURES.items():
            fp = os.path.join(tv, name)
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            open(fp, "w", newline="").write(content)
        env = dict(os.environ, VV_NO_METRICS="1", VV_VAULT=tv)
        n = 0
        for name in sorted(FIXTURES):
            for args in (["outline", name], ["head", name], ["read", name, "H0"],
                         ["read", name, "H1"], ["read", name, "(preamble)"],
                         ["resolve", name[:-3]]):
                a = subprocess.run([VR] + args, capture_output=True, env=env)
                b = subprocess.run([sys.executable, VV] + args, capture_output=True, env=env)
                n += 1
                if (a.stdout, a.stderr, a.returncode) != (b.stdout, b.stderr, b.returncode):
                    fails.append((name, args, a.returncode, b.returncode))
        # error-path fallbacks must be python-canonical too
        for args in (["resolve", "No Such Note Qq"], ["read", "empty.md", "H7"],
                     ["outline", "definitely missing"]):
            a = subprocess.run([VR] + args, capture_output=True, env=env)
            b = subprocess.run([sys.executable, VV] + args, capture_output=True, env=env)
            n += 1
            if (a.stdout, a.stderr, a.returncode) != (b.stdout, b.stderr, b.returncode):
                fails.append(("errpath", args, a.returncode, b.returncode))
        # Absolute Unicode-size pin (2026-08-27): parity alone let both engines
        # agree on the same WRONG size (chars labeled B). The constant below is
        # hand-computed, independent of either engine: the section text is
        # "## Emoji\n\ncafe\u0301... " -> 23 chars; multibyte extras are
        # e-acute +1, rocket emoji +3, i-diaeresis +1 = 28 UTF-8 bytes.
        uni = "## Emoji\n\ncaf\u00e9 \U0001f680 na\u00efve\n"
        open(os.path.join(tv, "uni-size.md"), "w", newline="").write(uni)
        for label, cmd in (("rust", [VR]), ("python", [sys.executable, VV])):
            r = subprocess.run(cmd + ["outline", "uni-size.md"],
                               capture_output=True, text=True, env=env)
            n += 1
            if "\t28B\t" not in r.stdout:
                fails.append(("uni-size-pin", label, r.stdout.strip()[:80], "expected 28B"))
        # and the patch report's size labels, via a same-content round-trip
        sha = subprocess.run([VR, "outline", "uni-size.md"], capture_output=True,
                             text=True, env=env).stdout.split("\t")[4].strip()
        for label, cmd in (("rust", [VR]), ("python", [sys.executable, VV])):
            r = subprocess.run(cmd + ["patch", "uni-size.md", "H1", sha],
                               input=uni.rstrip("\n") + "\n", capture_output=True,
                               text=True, env=env)
            n += 1
            # 27, not 28: patch strips the ONE trailing newline the caller's
            # framing adds (documented in cmd_patch), so the new body is the
            # 28-byte section minus its terminator.
            if "(28B -> 27B)" not in r.stdout:
                fails.append(("uni-patch-pin", label, (r.stdout + r.stderr).strip()[:80],
                              "expected (28B -> 27B)"))
        # --- P1 surface pins (spec rev 2, 2026-08-27) --------------------
        # --version: identical bytes from both entries, matching VERSION file.
        ver = open(os.path.join(REPO, "VERSION")).read().strip()
        outs = []
        for label, cmd in (("rust", [VR]), ("python", [sys.executable, VV])):
            r = subprocess.run(cmd + ["--version"], capture_output=True, text=True, env=env)
            n += 1
            if r.returncode != 0 or r.stdout != f"vv {ver}\n" or r.stderr:
                fails.append(("version", label, r.returncode, (r.stdout + r.stderr)[:60]))
            outs.append(r.stdout)
        if len(set(outs)) != 1:
            fails.append(("version-parity", outs))
        # Cargo.toml must carry the same version (two files, one gate-pinned value).
        cargo = open(os.path.join(REPO, "vrust", "Cargo.toml")).read()
        n += 1
        if f'version = "{ver}"' not in cargo:
            fails.append(("version-cargo-skew", ver, "not in vrust/Cargo.toml"))
        # bare invocation: SAME terse usage from both entries — stderr, exit 1,
        # ONE line (an accidental no-args in an agent loop must not cost the
        # full help catalog).
        for label, cmd in (("rust", [VR]), ("python", [sys.executable, VV])):
            r = subprocess.run(cmd, capture_output=True, text=True, env=env)
            n += 1
            if (r.returncode != 1 or r.stdout != ""
                    or r.stderr.count("\n") != 1
                    or "next: vv --help" not in r.stderr
                    or "linkscan" in r.stderr):
                fails.append(("noargs", label, r.returncode, (r.stdout + r.stderr)[:80]))
        # help: -h == --help, no PROTOTYPE, identical across entries (rust execs python).
        houts = []
        for flag in ("--help", "-h"):
            for label, cmd in (("rust", [VR]), ("python", [sys.executable, VV])):
                r = subprocess.run(cmd + [flag], capture_output=True, text=True, env=env)
                n += 1
                if r.returncode != 0 or "PROTOTYPE" in r.stdout or "Read:" not in r.stdout:
                    fails.append(("help", label, flag, r.stdout[:60]))
                houts.append(r.stdout)
        if len(set(houts)) != 1:
            fails.append(("help-parity", "four help outputs differ"))
        # unknown command: grep-stable error + exactly one suggestion.
        for label, cmd in (("rust", [VR]), ("python", [sys.executable, VV])):
            r = subprocess.run(cmd + ["outlien"], capture_output=True, text=True, env=env)
            n += 1
            if (r.returncode != 1
                    or "unknown command outlien" not in r.stderr
                    or "(did you mean: outline)" not in r.stderr
                    or "next:" not in r.stderr):
                fails.append(("typo-suggest", label, (r.stdout + r.stderr)[:90]))
        # --- P2a: search --files — paths only, cheapest possible answer ----
        # Both engines, byte-identical; no "==" headers, no snippet bodies, the
        # standard count trailer intact. Uses the fixtures already on disk.
        fouts = []
        for label, cmd in (("rust", [VR]), ("python", [sys.executable, VV])):
            env2 = dict(env)
            if label == "python":
                env2["VV_ENGINE"] = "python"   # exercise the pure-python path, not the shell-out
            r = subprocess.run(cmd + ["search", "heading", "--files"],
                               capture_output=True, text=True, env=env2)
            n += 1
            body = r.stdout
            if (r.returncode != 0 or "== " in body or "score" in body
                    or "matches)" not in body
                    or not any(l.endswith(".md") for l in body.splitlines())):
                fails.append(("files-flag", label, r.returncode, body[:90]))
            fouts.append(body)
        if len(set(fouts)) != 1:
            fails.append(("files-parity", [o[:60] for o in fouts]))
        # --files respects --k like normal search: ask for 1, get 1 path + honest trailer
        r = subprocess.run([VR, "search", "heading", "--files", "--k", "1"],
                           capture_output=True, text=True, env=env)
        n += 1
        paths = [l for l in r.stdout.splitlines() if l.endswith(".md")]
        if len(paths) != 1 or "(1 of " not in r.stdout:
            fails.append(("files-k", r.stdout[:90]))
        # --- P2b: --limit on every enumerator ----------------------------
        # Shape: first N entries + "(N of M <noun>)" trailer when truncated;
        # byte-identical across engines; untruncated output is covered by the
        # existing suites and must not change.
        lim_cases = [
            (["backlinks", "L1", "--limit", "1"], "of 2 backlinks)"),
            (["links", "L1", "--limit", "1"], "of 2 links)"),
            (["orphans", "--limit", "1"], "orphans)"),
            (["deadends", "--limit", "1"], "deadends)"),
            (["board", "Wk", "--limit", "1"], "of 3 notes)"),
            (["tags", "--limit", "1"], "of 2 tags)"),  # alpha+beta distinct
            (["props", "status", "--limit", "1"], "notes with status)"),
        ]
        for cargs, want_trailer in lim_cases:
            pair = []
            for label, cmd in (("rust", [VR]), ("python", [sys.executable, VV])):
                r = subprocess.run(cmd + cargs, capture_output=True, text=True, env=env)
                n += 1
                lines = r.stdout.rstrip("\n").split("\n")
                # exactly 1 entry line + 1 trailer line, and an honest K-of-M
                if (r.returncode != 0 or len(lines) != 2
                        or want_trailer not in lines[-1]
                        or "(1 of " not in lines[-1]):
                    fails.append(("limit", label, cargs[0], r.stdout[:80] + r.stderr[:40]))
                pair.append(r.stdout)
            if pair[0] != pair[1]:
                fails.append(("limit-parity", cargs[0], [o[:50] for o in pair]))
        if fails:
            for f in fails[:8]:
                print("FAIL", f)
            print(f"\n{len(fails)} of {n} DIVERGED")
            return 1
        print(f"ALL PASS (native-readpath: {n})")
        return 0
    finally:
        shutil.rmtree(tv, ignore_errors=True)

if __name__ == "__main__":
    sys.exit(main())
