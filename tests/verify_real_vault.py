#!/usr/bin/env python3
"""Corpus verification against a REAL vault. Read-only toward the vault:
notes are copied to a temp vault before any write is attempted.

  1. structure    — sections partition every note exactly (no lost/duplicated lines)
  2. round-trip   — read->patch of every section reproduces the file byte-for-byte (in-process)
  3. end-to-end   — same, through real CLI subprocesses, on a random sample
  4. parser       — no crashes; non-UTF-8 notes reported, not fatal

Usage: python3 tests/verify_real_vault.py [--sample N]
Honors VV_VAULT; defaults to the configured vault.
"""
import sys, os, subprocess, tempfile, shutil, random, time, argparse

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "src"))
import vv  # noqa: E402

VV = os.path.join(REPO, "src", "vv.py")

ap = argparse.ArgumentParser()
ap.add_argument("--sample", type=int, default=40, help="notes for the end-to-end CLI pass")
args = ap.parse_args()

fails = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond: fails.append(name)

print(f"vault: {vv.VAULT}")

# ---- 1 & 2: structure + in-process round-trip over every note ----
def patch_result(text, s, stdin_body):
    lines, secs = vv.parse(text)
    body = stdin_body.replace("\r\n", "\n")
    if body.endswith("\n"):
        body = body[:-1]
    bl = [] if (body == "" and s["end"] == s["start"]) else body.split("\n")
    if vv.eol_of("\n".join(lines)) == "\r\n":
        bl = [b + "\r" for b in bl]
        if s["end"] == len(lines) and lines and not lines[-1].endswith("\r") and lines[-1] != "":
            bl[-1] = bl[-1].rstrip("\r")
    return "\n".join(lines[:s["start"]] + bl + lines[s["end"]:])

bad_struct, bad_rt, unreadable, parse_err = [], [], [], []
n_notes = n_secs = 0
t0 = time.perf_counter()
for fp in vv.md_files():
    try:
        text = vv.read_raw(fp)
    except SystemExit:
        unreadable.append(vv.rel(fp)); continue
    try:
        lines, secs = vv.parse(text)
        cover = []
        for s in secs:
            cover.extend(range(s["start"], s["end"]))
        if sorted(cover) != list(range(len(lines))):
            bad_struct.append(vv.rel(fp))
        list(vv.link_targets_in(text))
    except Exception as e:
        parse_err.append(f"{vv.rel(fp)}: {e!r}"[:100]); continue
    n_notes += 1
    for i, s in enumerate(secs):
        if s["start"] == s["end"]:
            continue
        if i == 0 and lines and lines[0].lstrip(vv.BOM).rstrip("\r") == "---":
            continue  # H0-with-frontmatter is guarded (use set/unset)
        n_secs += 1
        if patch_result(text, s, vv.sec_text(lines, s) + "\n") != text:
            bad_rt.append(f"{vv.rel(fp)}#{s['id']}")
el = time.perf_counter() - t0
print(f"scanned {n_notes} notes / {n_secs} sections in {el:.1f}s ({el/max(n_notes,1)*1000:.2f}ms per note)")
check("structure: sections partition every note", not bad_struct, f"{len(bad_struct)}: {bad_struct[:3]}")
check("round-trip: every section byte-identical", not bad_rt, f"{len(bad_rt)}: {bad_rt[:3]}")
check("parser: no crashes", not parse_err, f"{len(parse_err)}: {parse_err[:2]}")
if unreadable:
    print(f"note: {len(unreadable)} non-UTF-8 note(s) reported cleanly, not fatal: {unreadable[:3]}")

# ---- 3: end-to-end through the real CLI on copies ----
real = list(vv.md_files())
random.Random(11).shuffle(real)
tmp = tempfile.mkdtemp(prefix="vv-verify-")
def run(*a, stdin=None):
    return subprocess.run([sys.executable, VV, *a], capture_output=True, text=True,
                          input=stdin, env=dict(os.environ, VV_VAULT=tmp))
bad_e2e, n_e2e = [], 0
t0 = time.perf_counter()
for idx, src in enumerate(real[: args.sample]):
    name = "N%04d.md" % idx
    shutil.copyfile(src, os.path.join(tmp, name))
    target = os.path.join(tmp, name)
    original = open(target, newline="", encoding="utf-8", errors="replace").read()
    r = run("outline", name)
    if r.returncode != 0:
        continue
    for row in [l.split("\t") for l in r.stdout.strip().split("\n") if l]:
        rr = run("read", name, row[0])
        if rr.returncode != 0:
            continue
        body = rr.stdout[: rr.stdout.rfind("\n--sha8:")]
        pr = run("patch", name, row[0], row[4], stdin=body + "\n")
        if pr.returncode == 1 and "frontmatter" in pr.stderr:
            continue
        n_e2e += 1
        if open(target, newline="", encoding="utf-8", errors="replace").read() != original:
            bad_e2e.append(f"{os.path.basename(src)}#{row[0]}")
            shutil.copyfile(src, target)
shutil.rmtree(tmp, ignore_errors=True)
print(f"end-to-end: {n_e2e} sections through real CLI in {time.perf_counter()-t0:.0f}s")
check("end-to-end: no byte differences", not bad_e2e, f"{len(bad_e2e)}: {bad_e2e[:3]}")

print(f"\n{len(fails)} failures: {fails}" if fails else "\nREAL-VAULT VERIFICATION PASS")
sys.exit(1 if fails else 0)
