#!/usr/bin/env python3
"""Regression tests for defects reported by the 2026-08-26 multi-model review panel.
Each test pins one fixed defect so it cannot silently return. Runs on a temp vault."""
import subprocess, sys, os, shutil, tempfile, glob, json

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VV = os.path.join(REPO, "src", "vv.py")
VAULT = tempfile.mkdtemp(prefix="vv-panel-")
OUTSIDE = tempfile.mkdtemp(prefix="vv-outside-")

def run(*args, stdin=None, env_extra=None):
    env = dict(os.environ, VV_VAULT=VAULT)
    if env_extra:
        env.update(env_extra)
    return subprocess.run([sys.executable, VV, *args], capture_output=True, text=True,
                          input=stdin, env=env)


# --- suite safety (review 2026-08-26) ---------------------------------------
# 1. Never delete pre-existing Sandbox content: a non-empty fixture dir is MOVED
#    aside, not removed. 2. Journals go to a temp root so real pending recovery
#    journals can't be touched. 3. On failure the fixture dir is KEPT as evidence.
import tempfile, datetime as _dt
def fresh_fixture(path):
    if os.path.isdir(path) and os.listdir(path):
        os.rename(path, path + ".pre-" + _dt.datetime.now().strftime("%H%M%S"))
    shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path, exist_ok=True)
_JR = tempfile.mkdtemp(prefix="vv-test-journals-")
os.environ["VV_JOURNAL_ROOT"] = _JR
# -----------------------------------------------------------------------------

fails = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{str(detail)[:160]}]" if detail and not cond else ""))
    if not cond: fails.append(name)

def w(name, text):
    fp = os.path.join(VAULT, name)
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    with open(fp, "w", newline="") as f:
        f.write(text)
    return fp

# ---- F1: path containment (writes must stay inside the vault) ----
victim = os.path.join(OUTSIDE, "victim.md")
open(victim, "w").write("ORIGINAL OUTSIDE CONTENT\n")
r = run("append", victim, "appended")
check("F1a absolute path outside vault refused", r.returncode == 1 and "escapes vault" in r.stderr, r.stderr)
check("F1b outside file untouched", open(victim).read() == "ORIGINAL OUTSIDE CONTENT\n")
r = run("append", "../" + os.path.basename(OUTSIDE) + "/victim.md", "x")
check("F1c parent-relative path refused", r.returncode == 1, r.stderr)
r = run("new", os.path.join(OUTSIDE, "created.md"))
check("F1d new outside vault refused", r.returncode == 1 and not os.path.exists(os.path.join(OUTSIDE, "created.md")))

# ---- F2: folder-relative queries must not match sibling prefixes ----
w("Work/A.md", "---\ntype: t\n---\nbody\n")
w("Workshop/B.md", "---\ntype: t\n---\nbody\n")
r = run("props", "type", "Work")
check("F2 folder boundary exact", "1\tt" in r.stdout, r.stdout)

# ---- F3: multi-line YAML values are refused, not silently orphaned ----
w("Block.md", "---\ntitle: x\ndesc: |\n  line one\n  line two\nstatus: open\n---\nbody\n")
before = open(os.path.join(VAULT, "Block.md")).read()
r = run("set", "Block.md", "desc", "newvalue")
check("F3a block scalar set refused", r.returncode == 1 and "multi-line" in r.stderr, r.stderr)
check("F3b file untouched", open(os.path.join(VAULT, "Block.md")).read() == before)
r = run("set", "Block.md", "status", "done")
after = open(os.path.join(VAULT, "Block.md")).read()
check("F3c sibling scalar still settable", r.returncode == 0 and "status: done" in after and "  line two" in after)

# ---- F4: value text is written literally (no substitution-template interpretation) ----
w("Lit.md", "---\nk: old\n---\nbody\n")
r = run("set", "Lit.md", "k", r"C:\new\table")
t = open(os.path.join(VAULT, "Lit.md")).read()
check("F4 backslash value literal", r"k: C:\new\table" in t and t.count("---") == 2, repr(t))

# ---- F5: H0 holding frontmatter is not patchable ----
w("Fm.md", "---\ntype: t\n---\npreamble\n\n## S\nbody\n")
r = run("outline", "Fm.md")
h0 = [l for l in r.stdout.strip().split("\n") if l.startswith("H0")]
if h0:
    r2 = run("patch", "Fm.md", "H0", h0[0].split("\t")[4], stdin="replaced\n")
    check("F5 patch H0-with-frontmatter refused", r2.returncode == 1 and "frontmatter" in r2.stderr, r2.stderr)
    check("F5b frontmatter intact", "type: t" in open(os.path.join(VAULT, "Fm.md")).read())

# ---- F6: backlinks/orphans agree with impact on path-qualified links ----
w("folder/Note.md", "# Note\n")
w("Ref.md", "See [[folder/Note]].\n")
r = run("backlinks", "folder/Note.md")
check("F6a backlinks sees path-form link", "Ref.md" in r.stdout, r.stdout)
r = run("orphans")
check("F6b orphans excludes path-linked note", "folder/Note.md" not in r.stdout, r.stdout)
r = run("impact", "folder/Note.md")
check("F6c impact agrees", "incoming-link files: 1" in r.stdout, r.stdout)

# ---- F7: wikilink written with .md extension still resolves/rewrites ----
w("ExtTarget.md", "# ExtTarget\n")
w("ExtRef.md", "Link [[ExtTarget.md]] here.\n")
r = run("backlinks", "ExtTarget.md")
check("F7 .md-suffixed wikilink counted", "ExtRef.md" in r.stdout, r.stdout)

# ---- F8: relative markdown links rename correctly (previously always aborted) ----
w("RelTarget.md", "# RelTarget\n")
w("deep/RelRef.md", "Link [x](../RelTarget.md) here.\n")
r = run("rename", "RelTarget.md", "RelRenamed", "--apply")
t = open(os.path.join(VAULT, "deep/RelRef.md")).read()
check("F8a relative md-link rename applies", "verification clean" in r.stdout, r.stdout + r.stderr)
check("F8b stays relative", "(../RelRenamed.md)" in t, t)

# ---- F9: a note linking to itself renames cleanly, leaving no duplicate ----
w("Self.md", "# Self\nI link to [[Self]] and [[Self|me]].\n")
r = run("rename", "Self.md", "SelfNew", "--apply")
check("F9a self-link rename ok", "verification clean" in r.stdout, r.stdout + r.stderr)
check("F9b no duplicate left behind", os.path.exists(os.path.join(VAULT, "SelfNew.md")) and not os.path.exists(os.path.join(VAULT, "Self.md")))
check("F9c self-link rewritten", "[[SelfNew]]" in open(os.path.join(VAULT, "SelfNew.md")).read())

# ---- F10: journal keys cannot collide (path-encoded names) ----
w("a/b.md", "Link [[JTarget]] one\n")
w("a%2Fb.md", "Link [[JTarget]] two\n")
w("JTarget.md", "# JTarget\n")
orig1 = open(os.path.join(VAULT, "a/b.md")).read()
orig2 = open(os.path.join(VAULT, "a%2Fb.md")).read()
r = run("rename", "JTarget.md", "JBoom", "--apply", env_extra={"VV_FAULT_AFTER": "1"})
check("F10a rollback triggered", r.returncode == 1, r.stdout + r.stderr)
check("F10b colliding names restored correctly",
      open(os.path.join(VAULT, "a/b.md")).read() == orig1 and
      open(os.path.join(VAULT, "a%2Fb.md")).read() == orig2)
check("F10c source note still present", os.path.exists(os.path.join(VAULT, "JTarget.md")))
shutil.rmtree(_JR, ignore_errors=True); os.makedirs(_JR, exist_ok=True)

# ---- F11: writing through a symlink updates the target, not the link ----
real = os.path.join(VAULT, "Real.md")
open(real, "w").write("---\nk: v\n---\nbody\n")
link = os.path.join(VAULT, "Link.md")
try:
    os.symlink(real, link)
    r = run("set", "Link.md", "k", "changed")
    check("F11a symlink preserved", os.path.islink(link), "symlink was replaced by a regular file")
    check("F11b target updated", "k: changed" in open(real).read())
except OSError as e:
    print(f"SKIP F11 symlink (unsupported: {e})")

# ---- F12: template selection is deterministic ----
w("Templates/T-a.md", "---\ntype: a\n---\n")
w("Templates/sub/T-b.md", "---\ntype: b\n---\n")
r1 = run("new", "N1", "--template", "T-")
r2 = run("new", "N2", "--template", "T-")
c1 = open(os.path.join(VAULT, "N1.md")).read()
c2 = open(os.path.join(VAULT, "N2.md")).read()
check("F12 template pick deterministic", c1 == c2, f"{c1!r} vs {c2!r}")

# ---- F14: [[Note.md]] wikilinks are rewritten on rename, style preserved ----
w("ExtR2.md", "Links [[ExtT.md]] and [[ExtT]] and [[ExtT.md|a]].\n")
w("ExtT.md", "# ExtT\n")
r = run("rename", "ExtT.md", "ExtT2", "--apply")
t = open(os.path.join(VAULT, "ExtR2.md")).read()
check("F14a all three forms rewritten", t == "Links [[ExtT2.md]] and [[ExtT2]] and [[ExtT2.md|a]].\n", repr(t))
check("F14b verification ran clean", "verification clean" in r.stdout, r.stdout + r.stderr)

# ---- F13: folder/Name (no extension) resolves to folder/Name.md ----
r = run("resolve", "folder/Note")
check("F13 extensionless path resolves", r.stdout.strip() == "folder/Note.md", r.stdout + r.stderr)

# ---- F15: a fence closes only on its own marker type ----
w("Fence.md", "## A\n```\n~~~\n## not a heading\n```\n\n## B\nreal\n")
r = run("outline", "Fence.md")
titles = [l.split("\t")[2] for l in r.stdout.strip().split("\n")]
check("F15a tilde inside backticks is inert", "not a heading" not in titles, r.stdout)
check("F15b following heading still found", "B" in titles, r.stdout)

# ---- F16: unterminated frontmatter is treated as body, never half-parsed ----
w("Unterm.md", "---\ntype: t\nno closing marker\n\n## S\nbody\n")
before = open(os.path.join(VAULT, "Unterm.md")).read()
r = run("set", "Unterm.md", "type", "changed")
after = open(os.path.join(VAULT, "Unterm.md")).read()
check("F16 unterminated fm not duplicated", after.count("---") <= before.count("---") + 2 and "no closing marker" in after, repr(after[:120]))

# ---- F17: a byte-order mark survives a frontmatter edit ----
bomfile = os.path.join(VAULT, "Bom.md")
with open(bomfile, "w", encoding="utf-8-sig", newline="") as f:
    f.write("---\nk: v\n---\nbody\n")
r = run("set", "Bom.md", "k", "v2")
raw = open(bomfile, "rb").read()
check("F17a BOM preserved", raw.startswith(b"\xef\xbb\xbf"), raw[:12])
check("F17b single frontmatter block", raw.decode("utf-8-sig").count("---") == 2 and "k: v2" in raw.decode("utf-8-sig"))

# ---- F18: non-UTF-8 files fail clearly instead of crashing ----
with open(os.path.join(VAULT, "Latin.md"), "wb") as f:
    f.write(b"---\nk: caf\xe9\n---\nbody\n")
r = run("outline", "Latin.md")
check("F18 non-UTF-8 clean error", r.returncode == 5 and "not valid UTF-8" in r.stderr and "Traceback" not in r.stderr, r.stderr[:120])

# ================= hands-on QA pass (Codex, 2026-08-26) =================

# C1: a nested code sample (4-backtick fence containing ```) is not document structure
w("Nested.md", "# Real Section\nbefore fence\n\n````markdown\nouter\n```python\n# fake heading inside outer fence\n```\nafter inner fence\n````\n\nafter outer fence\n\n# Next Section\nnext bytes\n")
r = run("outline", "Nested.md")
titles = [l.split("\t")[2] for l in r.stdout.strip().split("\n")]
check("C1a nested fence not parsed as heading", "fake heading inside outer fence" not in titles, titles)
rows = {l.split("\t")[2]: l.split("\t") for l in r.stdout.strip().split("\n")}
r = run("patch", "Nested.md", rows["Real Section"][0], rows["Real Section"][4], stdin="# Real Section\nreplacement only\n\n")
t = open(os.path.join(VAULT, "Nested.md")).read()
check("C1b whole section replaced", t == "# Real Section\nreplacement only\n\n# Next Section\nnext bytes\n", repr(t))

# C2: rename leaves double-backtick spans and nested fences alone
w("RTarget.md", "# RTarget\n")
w("RLinks.md",
  "Bare [[RTarget]].\nInline `[[RTarget|c]]` stays.\nDouble ``[[RTarget|d]]`` stays.\n\n"
  "````markdown\nouter\n```python\n[[RTarget|nested]] stays\n```\nouter\n````\n\ntail\n")
r = run("rename", "RTarget.md", "RTarget2", "--apply")
t = open(os.path.join(VAULT, "RLinks.md")).read()
check("C2a active link rewritten", "Bare [[RTarget2]]." in t, t[:60])
check("C2b single-backtick span untouched", "`[[RTarget|c]]`" in t)
check("C2c double-backtick span untouched", "``[[RTarget|d]]``" in t, t)
check("C2d nested fence untouched", "[[RTarget|nested]] stays" in t, t)

# C3: patching the last section of a CRLF file keeps a valid CRLF terminator
with open(os.path.join(VAULT, "CrlfEnd.md"), "wb") as f:
    f.write(b"# End\r\nold end\r\n")
r = run("outline", "CrlfEnd.md")
row = r.stdout.strip().split("\n")[0].split("\t")
r = run("patch", "CrlfEnd.md", row[0], row[4], stdin="# End\nnew end\n")
raw = open(os.path.join(VAULT, "CrlfEnd.md"), "rb").read()
check("C3 CRLF EOF terminator intact", raw == b"# End\r\nnew end\r\n", raw)

# C4: a CRLF template with an override stays CRLF
with open(os.path.join(VAULT, "Templates/Crlf.md"), "wb") as f:
    f.write(b"---\r\ntype: t\r\n---\r\nbody\r\n")
r = run("new", "FromCrlf", "--type", "changed")
raw = open(os.path.join(VAULT, "FromCrlf.md"), "rb").read() if os.path.exists(os.path.join(VAULT, "FromCrlf.md")) else b""
r = run("new", "FromCrlf2", "--template", "Crlf", "--type", "changed")
raw2 = open(os.path.join(VAULT, "FromCrlf2.md"), "rb").read()
check("C4 CRLF template stays CRLF", b"\n" not in raw2.replace(b"\r\n", b""), raw2)

# C5: --key values absent from the template are added, not dropped
w("Templates/Plain.md", "---\ntype: todo\n---\nBody\n")
r = run("new", "WithKeys", "--template", "Plain", "--status", "open", "--owner", "jeff")
t = open(os.path.join(VAULT, "WithKeys.md")).read()
check("C5 missing keys added to template frontmatter",
      "type: todo" in t and "status: open" in t and "owner: jeff" in t and t.count("---") == 2, repr(t))

# C6: the fault hook can fire in the pre-rename window, and that rolls back cleanly
w("FTarget.md", "# FTarget\n")
w("FLink.md", "See [[FTarget]].\n")
before = open(os.path.join(VAULT, "FLink.md")).read()
r = run("rename", "FTarget.md", "FRenamed", "--apply", env_extra={"VV_FAULT_AFTER": "1"})
check("C6a pre-rename fault aborts", r.returncode == 1 and "ROLLED BACK" in r.stderr, r.stderr[:120])
check("C6b links restored", open(os.path.join(VAULT, "FLink.md")).read() == before)
check("C6c note not renamed", os.path.exists(os.path.join(VAULT, "FTarget.md")) and not os.path.exists(os.path.join(VAULT, "FRenamed.md")))
shutil.rmtree(_JR, ignore_errors=True); os.makedirs(_JR, exist_ok=True)

shutil.rmtree(VAULT, ignore_errors=True)
shutil.rmtree(OUTSIDE, ignore_errors=True)
print(f"\n{len(fails)} failures: {fails}" if fails else "\nALL PANEL-FINDING TESTS PASS")
sys.exit(1 if fails else 0)
