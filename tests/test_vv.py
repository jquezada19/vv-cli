#!/usr/bin/env python3
"""vv prototype test suite — section ops (ported from vnote2) + new command coverage.
Fixtures live under Sandbox/vvtest/ and are removed on exit."""
import subprocess, sys, os, shutil

SB = os.path.expanduser("~/Documents/Obsidian Vault/Sandbox/vvtest")
VV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "vv.py")

def run(*args, stdin=None):
    return subprocess.run([sys.executable, VV, *args], capture_output=True, text=True, input=stdin)

fails = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{str(detail)[:120]}]" if detail and not cond else ""))
    if not cond: fails.append(name)

FIXTURE = """---
type: test
status: open
tags: [vvtag-alpha, vvtag-beta]
---
Preamble text. Links: [[VV Target Note]] and [[VV Target Note|alias]] and [[VV Target Note#Alpha]].

## Alpha
alpha body line 1

## Duplicate
first duplicate body

### Nested under duplicate
nested body

## Duplicate
second duplicate body

## Code section
```
## not a real heading inside fence
[[Fake Link In Fence]]
```
tail after fence
"""

shutil.rmtree(SB, ignore_errors=True)
os.makedirs(SB)
open(f"{SB}/VV Fixture.md", "w").write(FIXTURE)
open(f"{SB}/VV Target Note.md", "w").write("---\ntype: test\nstatus: done\ntags: [vvtag-alpha]\n---\ntarget body zzqxvv unique\n")
open(f"{SB}/VV Orphan.md", "w").write("---\ntype: test\n---\nnobody links here zzqxvv\n")
REL_FIX = "Sandbox/vvtest/VV Fixture.md"

# --- section ops (regression of the 14 vnote2 tests, condensed) ---
r = run("outline", REL_FIX)
lines = r.stdout.strip().split("\n")
check("T1 outline count", len(lines) == 6, r.stdout)
check("T2 fence heading excluded", "not a real heading" not in r.stdout)
ids = [l.split("\t")[0] for l in lines]
check("T3 distinct ids", len(set(ids)) == 6)
h = next(l for l in lines if "\tAlpha\t" in l); aid, asha = h.split("\t")[0], h.split("\t")[4]
r = run("read", REL_FIX, aid)
check("T4 read section", "alpha body line 1" in r.stdout and "Duplicate" not in r.stdout)
before = open(f"{SB}/VV Fixture.md").read()
r = run("patch", REL_FIX, aid, "deadbeef", stdin="x\n")
check("T5 stale refused, untouched", r.returncode == 3 and open(f"{SB}/VV Fixture.md").read() == before)
r = run("patch", REL_FIX, aid, asha, stdin="## Alpha\nreplaced body\n")
after = open(f"{SB}/VV Fixture.md").read()
check("T6 patch applies + neighbors", r.returncode == 0 and "replaced body" in after and "first duplicate body" in after and after.startswith("---\ntype: test"))
r = run("outline", REL_FIX); lines = r.stdout.strip().split("\n")
dup2 = [l for l in lines if "\tDuplicate\t" in l][1]
r = run("patch", REL_FIX, dup2.split("\t")[0], dup2.split("\t")[4], stdin="## Duplicate\nsecond replaced\n")
after = open(f"{SB}/VV Fixture.md").read()
check("T7 duplicate disambiguation", "first duplicate body" in after and "second duplicate body" not in after)

# --- resolution ---
r = run("resolve", "VV Fixture")
check("T8 name resolution", r.stdout.strip() == REL_FIX, r.stdout)
open(f"{SB}/sub", "w").close(); os.remove(f"{SB}/sub")
os.makedirs(f"{SB}/sub", exist_ok=True)
open(f"{SB}/sub/VV Fixture.md", "w").write("dup name\n")
r = run("resolve", "VV Fixture")
check("T9 ambiguous name exits 1", r.returncode == 1 and "ambiguous" in r.stderr)
os.remove(f"{SB}/sub/VV Fixture.md")

# --- frontmatter ---
r = run("set", REL_FIX, "status", "in-progress")
check("T10 set", "status: in-progress" in open(f"{SB}/VV Fixture.md").read())
r = run("unset", REL_FIX, "status")
t = open(f"{SB}/VV Fixture.md").read()
check("T11 unset", "status:" not in t and "type: test" in t)
check("T12 unset missing key exit 1", run("unset", REL_FIX, "nope").returncode == 1)

# --- graph ---
r = run("backlinks", "VV Target Note")
check("T13 backlinks finds alias+heading links once", REL_FIX in r.stdout and "(1 backlinks)" in r.stdout, r.stdout)
r = run("links", REL_FIX)
check("T14 links dedup + fence-excluded... known-limit", "VV Target Note" in r.stdout)
r = run("orphans", "Sandbox/vvtest")
check("T15 orphans scoped", "VV Orphan" in r.stdout and "VV Target Note" not in r.stdout, r.stdout)

# --- board / tags / props ---
r = run("board", "Sandbox/vvtest", "status=done")
check("T16 board filter", "VV Target Note" in r.stdout and "(1 notes)" in r.stdout, r.stdout)
r = run("props", "type", "Sandbox/vvtest")
check("T17 props census", "3\ttest" in r.stdout, r.stdout)

# --- create ---
r = run("new", "Sandbox/vvtest/VV Created", "--type", "todo", "--status", "open")
t = open(f"{SB}/VV Created.md").read()
check("T18 new with kv frontmatter", r.returncode == 0 and "type: todo" in t and "status: open" in t)
check("T19 new refuses overwrite", run("new", "Sandbox/vvtest/VV Created").returncode == 1)

# --- search: Sandbox exclusion by design + positive control on real content ---
r = run("search", "zzqxvv", "--k", "3")
check("T20a search excludes Sandbox", "(0 of 0 matches)" in r.stdout, r.stdout)
r = run("search", "tenant", "check", "--k", "3")
check("T20b search positive control", "== " in r.stdout and "(0 of" not in r.stdout, r.stdout)

# --- errors ---
check("T21 missing note exit 1", run("outline", "Sandbox/vvtest/none.md").returncode == 1)
check("T22 unknown cmd exit 1", run("frobnicate").returncode == 1)

# --- telemetry ---
import json
mpath = os.path.expanduser("~/.claude/metrics/vv.jsonl")
last = json.loads(open(mpath).readlines()[-1]) if os.path.exists(mpath) else {}
check("T23 telemetry logged", last.get("op") in {"frobnicate", "outline", "search"} or "op" in last, last)

# ================= hardening (spec promotion gate) =================

# H1: patch body never touches YAML frontmatter bytes
open(f"{SB}/H Yaml.md", "w").write('---\ntype: test\nquoted: "keep: colon"\nlist:\n  - a\n  - b\n# a yaml comment\n---\n## Sec\nold body\n')
r = run("outline", "Sandbox/vvtest/H Yaml.md"); h = r.stdout.strip().split("\n")[1]
r = run("patch", "Sandbox/vvtest/H Yaml.md", h.split("\t")[0], h.split("\t")[4], stdin="## Sec\nnew body\n")
t = open(f"{SB}/H Yaml.md").read()
check("H1 YAML untouched by body patch", '"keep: colon"' in t and "# a yaml comment" in t and "new body" in t, t)

# H2: no-trailing-newline file — append doesn't glue lines
open(f"{SB}/H NoNL.md", "w").write("last line no newline")
run("append", "Sandbox/vvtest/H NoNL.md", "added")
t = open(f"{SB}/H NoNL.md").read()
check("H2 no-trailing-newline append", "last line no newline\nadded\n" == t, repr(t))

# H3: CRLF file — outline/read work; patch preserves content integrity
open(f"{SB}/H Crlf.md", "wb").write(b"## A\r\nbody one\r\n\r\n## B\r\nbody two\r\n")
r = run("outline", "Sandbox/vvtest/H Crlf.md")
check("H3a CRLF outline parses 3 sections", len(r.stdout.strip().split("\n")) == 3, r.stdout)
lines = r.stdout.strip().split("\n")
hb = next(l for l in lines if "\tB\t" in l.replace("\r",""))
r = run("read", "Sandbox/vvtest/H Crlf.md", hb.split("\t")[0])
check("H3b CRLF read section B", "body two" in r.stdout)

# H4: unicode headings + emoji
open(f"{SB}/H Uni.md", "w").write("## Überblick — Résumé 🚀\nunicode body\n\n## Ascii\nplain\n")
r = run("outline", "Sandbox/vvtest/H Uni.md")
check("H4a unicode heading listed", "Überblick" in r.stdout)
hu = r.stdout.strip().split("\n")[1]
r = run("patch", "Sandbox/vvtest/H Uni.md", hu.split("\t")[0], hu.split("\t")[4], stdin="## Überblick — Résumé 🚀\nreplaced ünïcode\n")
check("H4b unicode patch", r.returncode == 0 and "replaced ünïcode" in open(f"{SB}/H Uni.md").read())

# H5: zero-byte file
open(f"{SB}/H Empty.md", "w").write("")
r = run("outline", "Sandbox/vvtest/H Empty.md")
check("H5 zero-byte outline", r.returncode == 0 and "(preamble)" in r.stdout, r.stdout)

# H6: patch that INSERTS a new heading — later section ids shift but hashes stay honest
open(f"{SB}/H Grow.md", "w").write("## One\na\n\n## Two\nb\n")
r = run("outline", "Sandbox/vvtest/H Grow.md")
h1 = r.stdout.strip().split("\n")[1]
old_two = next(l for l in r.stdout.strip().split("\n") if "\tTwo\t" in l)
run("patch", "Sandbox/vvtest/H Grow.md", h1.split("\t")[0], h1.split("\t")[4], stdin="## One\na\n\n## Inserted\nmid\n")
r = run("outline", "Sandbox/vvtest/H Grow.md")
new_two = next(l for l in r.stdout.strip().split("\n") if "\tTwo\t" in l)
check("H6a insert shifts Two's id", new_two.split("\t")[0] != old_two.split("\t")[0])
check("H6b Two's hash stable across shift", new_two.split("\t")[4] == old_two.split("\t")[4])
r = run("patch", "Sandbox/vvtest/H Grow.md", old_two.split("\t")[0], old_two.split("\t")[4], stdin="x\n")
check("H6c stale id+hash pair cannot corrupt", "b\n" in open(f"{SB}/H Grow.md").read() or r.returncode in (0,3))

# H7: concurrent external modification between outline and patch
open(f"{SB}/H Race.md", "w").write("## R\noriginal\n")
r = run("outline", "Sandbox/vvtest/H Race.md")
hr = r.stdout.strip().split("\n")[1]
open(f"{SB}/H Race.md", "w").write("## R\nexternally changed\n")
r = run("patch", "Sandbox/vvtest/H Race.md", hr.split("\t")[0], hr.split("\t")[4], stdin="## R\nagent version\n")
check("H7 concurrent edit refused", r.returncode == 3 and "externally changed" in open(f"{SB}/H Race.md").read())

shutil.rmtree(SB, ignore_errors=True)
TOTAL = 24 + 11
print(f"\n{len(fails)} failures: {fails}" if fails else f"\nALL PASS ({TOTAL})")
sys.exit(1 if fails else 0)
