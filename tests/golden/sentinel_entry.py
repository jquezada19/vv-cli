#!/usr/bin/env python3
"""Stands in for src/vv.py during golden runs. A native call that hands off
lands here; it records exactly what it was handed (argv, stdin, the vault it
was pointed at) in the file named by VV_GOLDEN_MARK, says so, and exits 42.

The marker file, not the exit code, is what proves a hand-off happened: output
can be imitated, a file written by this process cannot.
"""
import hashlib, json, os, sys

mark = os.environ.get("VV_GOLDEN_MARK")
if mark:
    body = sys.stdin.buffer.read()
    with open(mark, "a", encoding="utf-8") as f:
        f.write(json.dumps({"argv": sys.argv[1:], "stdin_sha256": hashlib.sha256(body).hexdigest(),
                            "vault": os.environ.get("VV_VAULT", "")}, ensure_ascii=False) + "\n")
sys.stderr.write("HANDOFF\n")
sys.exit(42)
