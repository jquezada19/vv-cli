#!/usr/bin/env python3
"""Regression tests for adversarial-panel findings (Codex + Grok + Gemini + Kimi, 2026-08-26).
Each test names the finding it pins. Runs against a throwaway vault."""
import subprocess, sys, os, shutil, tempfile, glob

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VV = os.path.join(REPO, "src", "vv.py")
VAULT = tempfile.mkdtemp(prefix="vv-panel-vault-")
OUTSIDE = tempfile.mkdtemp(prefix="vv-panel-outside-")

def run(*args, stdin=None, env_extra=None):
    env = dict(os.environ, VV_VAULT=VAULT)
    if env_extra:
        env.update(env_extra)
    return subprocess.run([sys.executable, VV, *args], capture_output=True, text=True,
                          input=stdin, env=env)

fails = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{str(detail)[:150]}]" if detail and not cond else ""))
    if not cond: fails.append(name)

def w(name, text):
    fp = os.path.join(VAULT, name)
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    open(fp, "w", newline="").write(text)
    return fp

# K1/G10: path containment — abs path and .. escape must refuse before any write
outside_file = os.path.join(OUTSIDE, "victim.md")
open(outside_file, "w").write("## V\noriginal\n")
r = run("set", outside_