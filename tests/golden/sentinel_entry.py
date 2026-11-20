#!/usr/bin/env python3
"""Stands in for src/vv.py during golden runs. A native call that hands off
lands here; it records exactly what it was handed (argv, stdin, the vault it
was pointed at) in the file named by VV_GOLDEN_MARK, says so, and exits with
the code the harness chose for this run (VV_GOLDEN_EXIT), so a launcher that
does not pass python's exit status through is caught.

The marker file, not the output, is what proves the entry was reached.
"""
import hashlib, json, os, sys

mark = os.environ.get("VV_GOLDEN_MARK")
if mark:
    body = sys.stdin.buffer.read()
    with open(mark, "a", encoding="utf-8") as f:
        f.write(json.dumps({"argv": sys.argv[1:], "stdin_sha256": hashlib.sha256(body).hexdigest(),
                            "vault": os.environ.get("VV_VAULT", "")}, ensure_ascii=False) + "\n")
sys.stderr.write("HANDOFF\n")
sys.exit(int(os.environ.get("VV_GOLDEN_EXIT", "42")))
