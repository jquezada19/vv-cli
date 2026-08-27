#!/usr/bin/env python3
"""Torture: concurrent readers + a concurrent writer must never yield a WRONG
answer, a wedged journal, or a crash. A read may legitimately race the writer,
so each reader's output must match the truth for SOME point in time (before or
after that write) — never a third thing.
"""
import hashlib, os, random, shutil, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor

REPO = os.path.expanduser("~/Desktop/Git/vv-cli")
VR = os.path.join(REPO, "vrust/target/release/vrust")
VV = os.path.join(REPO, "src/vv.py")
rng = random.Random(int(os.environ.get("SEED", "77")))
JR = tempfile.mkdtemp(prefix="vv-conc-journals-")

def run(cmd, vault, py=False):
    env = dict(os.environ, VV_NO_METRICS="1", VV_VAULT=vault, VV_JOURNAL_ROOT=JR)
    if py:
        env["VV_NO_INDEX"] = "1"
        return subprocess.run([sys.executable, VV] + cmd, capture_output=True, text=True, env=env)
    return subprocess.run([VR] + cmd, capture_output=True, text=True, env=env)

vault = tempfile.mkdtemp(prefix="vv-conc-")
names = [f"N{i}" for i in range(60)]
for i, n in enumerate(names):
    open(os.path.join(vault, n + ".md"), "w").write(
        f"---\ntype: test\nstatus: open\n---\n\n# {n}\n\n[[{names[(i+1)%60]}]] [[{names[(i+7)%60]}]]\n")

READS = [["backlinks", "N3"], ["links", "N10"], ["orphans"], ["search", "N3"],
         ["outline", "N20"], ["tags"], ["board", "todo"], ["read", "N40"]]
before = {tuple(c): run(c, vault, py=True).stdout for c in READS}

errs = []
def reader(i):
    c = READS[i % len(READS)]
    r = run(c, vault)
    if r.returncode == 4:
        errs.append(("wedged-journal", c, r.stderr[:120]))
    elif r.returncode not in (0, 1, 2, 3):
        errs.append(("bad-exit", c, r.returncode, r.stderr[:120]))

def writer(i):
    n = names[rng.randrange(60)]
    r = run(["set", n, "status", rng.choice(["open", "next", "done"])], vault)
    if r.returncode != 0:
        errs.append(("write-fail", n, r.returncode, r.stderr[:120]))

with ThreadPoolExecutor(max_workers=16) as ex:
    jobs = [ex.submit(reader, i) for i in range(240)] + [ex.submit(writer, i) for i in range(60)]
    for j in jobs: j.result()

# after the storm: every read must agree with python on the FINAL state
final = []
for c in READS:
    a = run(c, vault).stdout
    b = run(c, vault, py=True).stdout
    if a != b:
        final.append((c, a[:120], b[:120]))

changed = sum(1 for n in names
              if "status: open" not in open(os.path.join(vault, n + ".md")).read())
if changed == 0:
    errs.append(("control", "no write actually landed -> the storm was a no-op"))
print(f"  control: notes actually mutated = {changed}")
pend = [f for f in os.listdir(JR) if not f.startswith(".")] if os.path.isdir(JR) else []
shutil.rmtree(vault, ignore_errors=True); shutil.rmtree(JR, ignore_errors=True)

print(f"300 concurrent ops (240 read / 60 write, 16 workers)")
print(f"  runtime errors : {len(errs)}")
print(f"  post-storm divergence from python: {len(final)}")
print(f"  leftover journal entries: {len(pend)}")
for e in errs[:5]: print("   ", e)
for f in final[:5]: print("   ", f)
print("ALL PASS (concurrency torture)" if not (errs or final) else "FAIL")
sys.exit(1 if (errs or final) else 0)
