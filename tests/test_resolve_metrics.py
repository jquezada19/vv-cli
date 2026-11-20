#!/usr/bin/env python3
"""Pins for the resolve fast path and what the metrics row says about it.

I  The id rule in the native entry. Bare digits name the one note whose
   basename starts `<digits> - `. The native entry used to find no exact
   basename, hand the invocation to python, and python walked the vault a
   second time. It now answers the one-candidate case from its own walk and
   still leaves zero candidates, several candidates and `#id` to python.
Z  Zero-hit search. The native scan handed every zero-hit query to python,
   which scanned again to rediscover the zero. Python is now reached only for
   a quoted multi-word argument (the one case the phrase hint can arise), and
   is told the scan already came back empty.
M  The metrics row. `ref` says how the NOTE was addressed (path, name, id);
   a row python writes after a native hand-off carries `fb: 1` and an `ms`
   measured from the native process start.

Each resolve/search check runs through the python entry and the native binary
in separate throwaway vaults; an arm with the python fallback made unreachable
proves which answers the native entry gives by itself.
"""
import os, sys, json, shutil, subprocess, tempfile, atexit, datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VV = os.path.join(REPO, "src", "vv.py")
VRUST = os.path.join(REPO, "vrust", "target", "release", "vrust")

_TMP = []
def mkdtemp(prefix):
    d = tempfile.mkdtemp(prefix=prefix); _TMP.append(d); return d
atexit.register(lambda: [shutil.rmtree(d, True) for d in _TMP])

fails = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{str(detail)[:300]}]" if detail and not cond else ""))
    if not cond: fails.append(name)

NOTES = {
    "Work/24995 - Disney follow-ups.md": "# A\n\nbody a\n",
    "Work/31000 - First.md": "# one\n",
    "Other/31000 - Second.md": "# two\n",
    "Work/42.md": "# exact name wins\n",
    "Work/42 - Also an id.md": "# id form\n",
    "Work/Plain Note.md": "# P\n\nalpha beta\n",
    "Top.md": "# T\n",
}

class Engine:
    def __init__(self, name, env, entry, metrics=False):
        self.name, self.entry = name, entry
        self.vault = mkdtemp(f"vv-resm-{name}-vault-")
        self.home = mkdtemp(f"vv-resm-{name}-home-")
        os.makedirs(os.path.join(self.vault, ".obsidian"))
        os.makedirs(os.path.join(self.home, ".claude", "metrics"))
        for relp, body in NOTES.items():
            p = os.path.join(self.vault, relp)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                f.write(body)
        base = {k: v for k, v in os.environ.items()
                if k not in ("VV_ENGINE", "VV_JOURNAL_ROOT", "VV_NO_METRICS", "VV_METRICS_SRC", "VV_NATIVE_T0_NS")}
        self.env = dict(base, HOME=self.home, VV_VAULT=self.vault,
                        VV_INDEX_ROOT=mkdtemp(f"vv-resm-{name}-index-"), **env)
        if not metrics:
            self.env["VV_NO_METRICS"] = "1"

    def run(self, *args, env=None):
        r = subprocess.run(self.entry + list(args), capture_output=True, text=True, timeout=120,
                           env=dict(self.env, **(env or {})))
        return r.returncode, r.stdout, r.stderr

    def rows(self):
        p = os.path.join(self.home, ".claude", "metrics", "vv.jsonl")
        return [json.loads(l) for l in open(p)] if os.path.exists(p) else []

HAVE_NATIVE = os.path.exists(VRUST)
ENGINES = [Engine("python", {"VV_ENGINE": "python"}, [sys.executable, VV])]
if HAVE_NATIVE:
    ENGINES.append(Engine("native", {}, [VRUST]))
else:
    print("SKIP native arms (vrust/target/release/vrust not built)")

# ---- I / Z: both entries agree ----
for e in ENGINES:
    n = e.name
    rc, out, err = e.run("resolve", "24995")
    check(f"I1[{n}] a bare id resolves to its note", rc == 0 and out.strip() == "Work/24995 - Disney follow-ups.md", (rc, out, err))
    rc, out, err = e.run("outline", "24995")
    check(f"I2[{n}] a command reads through a bare id", rc == 0 and "H1" in out, (rc, out, err))
    rc, out, err = e.run("resolve", "31000")
    check(f"I3[{n}] two candidates are an ambiguous refusal naming both",
          rc == 1 and err.startswith("ambiguous: 31000 matches 2 notes") and "First" in err and "Second" in err, (rc, out, err))
    rc, out, err = e.run("resolve", "42")
    check(f"I4[{n}] an exact basename outranks the id rule", rc == 0 and out.strip() == "Work/42.md", (rc, out, err))
    rc, out, err = e.run("resolve", "77777")
    check(f"I5[{n}] no candidate is a not-found", rc == 1 and err.startswith("not-found:"), (rc, out, err))
    rc, out, err = e.run("resolve", "#24995")
    check(f"I6[{n}] the #id spelling resolves", rc == 0 and out.strip() == "Work/24995 - Disney follow-ups.md", (rc, out, err))
    rc, out, err = e.run("search", "zzqqxxnotaword")
    check(f"Z1[{n}] a one-word miss answers the zero", rc == 0 and out == "(0 of 0 matches)\n", (rc, out, err))
    rc, out, err = e.run("search", "alpha zzqq")
    check(f"Z2[{n}] a phrase miss whose words do not all occur prints no hint",
          rc == 0 and out == "(0 of 0 matches)\n", (rc, out, err))
    rc, out, err = e.run("search", "alpha beta")
    check(f"Z3[{n}] a phrase that is present still matches", rc == 0 and "Plain Note.md" in out, (rc, out, err))
    rc, out, err = e.run("search", "beta alpha")
    check(f"Z4[{n}] a phrase that misses while its words match apart prints the hint",
          rc == 0 and out.startswith("(0 of 0 matches)\n") and "hint:" in out and "retry unquoted" in out, (rc, out, err))

    rc, out, err = e.run("search", "beta\x1calpha")
    check(f"Z7[{n}] a phrase split by U+001C counts as multi-word, as python's split() has it",
          rc == 0 and "hint:" in out, (rc, out, err))
    outside = mkdtemp("vv-resm-outside-")
    with open(os.path.join(outside, "x.md"), "w") as f:
        f.write("# outside\n")
    os.symlink(os.path.join(outside, "x.md"), os.path.join(e.vault, "88"))
    with open(os.path.join(e.vault, "Work", "88 - Good.md"), "w") as f:
        f.write("# good\n")
    rc, out, err = e.run("resolve", "88")
    check(f"I12[{n}] a typed path that escapes the vault is refused, not re-read as an id",
          rc == 1 and err.startswith("escape:") and out == "", (rc, out, err))
    os.remove(os.path.join(e.vault, "88")); os.remove(os.path.join(e.vault, "Work", "88 - Good.md"))

if HAVE_NATIVE:
    # ---- the native entry by itself: python unreachable ----
    solo = Engine("native-solo", {"VV_PY_ENTRY": "/nonexistent/vv.py"}, [VRUST])
    rc, out, err = solo.run("resolve", "24995")
    check("I7 the native entry answers a one-candidate id itself",
          rc == 0 and out.strip() == "Work/24995 - Disney follow-ups.md" and "vv.py" not in err, (rc, out, err))
    rc, out, err = solo.run("outline", "24995")
    check("I8 and reads through it", rc == 0 and "H1" in out, (rc, out, err))
    rc, out, err = solo.run("resolve", "31000")
    check("I9 (control) several candidates are python's to word", rc != 0 and "vv.py" in err, (rc, out, err))
    rc, out, err = solo.run("resolve", "42")
    check("I10 (control) an exact basename still resolves natively", rc == 0 and out.strip() == "Work/42.md", (rc, out, err))
    os.symlink("/nonexistent/target.md", os.path.join(solo.vault, "Work", "55555 - Dangling.md"))
    rc, out, err = solo.run("resolve", "55555")
    check("I11 a dangling symlink is never answered natively", rc != 0 and out == "", (rc, out, err))
    rc, out, err = solo.run("search", "zzqqxxnotaword")
    check("Z5 the native entry answers a one-word miss itself",
          rc == 0 and out == "(0 of 0 matches)\n" and "vv.py" not in err, (rc, out, err))
    rc, out, err = solo.run("search", "beta alpha")
    check("Z6 (control) a quoted multi-word miss is handed to python for the hint", rc != 0 and "vv.py" in err, (rc, out, err))

    # ---- M: the metrics row ----
    m = Engine("native-metrics", {}, [VRUST], metrics=True)
    before = datetime.datetime.now().replace(microsecond=0)
    m.run("outline", "Work/Plain Note.md")
    m.run("outline", "Plain Note")
    m.run("outline", "24995")
    after = datetime.datetime.now()
    rows = m.rows()
    check("M1 one row per native call", len(rows) == 3 and all(r.get("engine") == "native" for r in rows), rows)
    check("M2 ref names how the note was addressed",
          [r.get("ref") for r in rows] == ["path", "name", "id"], [r.get("ref") for r in rows])
    try:
        stamps = [datetime.datetime.fromisoformat(r["ts"]) for r in rows]
        ok = len(stamps) == 3 and all(
            before - datetime.timedelta(seconds=2) <= s_ <= after + datetime.timedelta(seconds=2) for s_ in stamps)
    except (ValueError, KeyError) as ex:
        stamps, ok = repr(ex), False
    check("M3 (control) the native timestamp is local wall-clock time", ok, (before, stamps, after))

    # a hand-off: python writes the row, flagged, timed from the native start
    n0 = len(m.rows())
    rc, out, err = m.run("resolve", "31000")              # ambiguous: python's answer
    new = m.rows()[n0:]
    check("M8 a hand-off writes exactly one row, by python, flagged fb",
          len(new) == 1 and new[0].get("engine") == "python" and new[0].get("fb") == 1, new)
    n0 = len(m.rows())
    rc, out, err = m.run("search", "zzqqxxnotaword")
    new = m.rows()[n0:]
    check("M9 a one-word miss writes exactly one native row", len(new) == 1 and new[0].get("engine") == "native", new)

    # python invoked directly is not a hand-off; only a recent start time is believed
    import time
    p = Engine("python-metrics", {"VV_ENGINE": "python"}, [sys.executable, VV], metrics=True)
    p.run("outline", "24995")
    p.run("outline", "Top", env={"VV_NATIVE_T0_NS": "12"})
    p.run("outline", "Top", env={"VV_NATIVE_T0_NS": "1000000000000000000"})        # 2001
    p.run("outline", "Top", env={"VV_NATIVE_T0_NS": str(time.time_ns() - 3600 * 10**9)})   # an hour ago
    p.run("outline", "Top", env={"VV_NATIVE_T0_NS": str(time.time_ns() + 3600 * 10**9)})   # the future
    p.run("outline", "Top", env={"VV_NATIVE_T0_NS": str(time.time_ns() - 2 * 10**9)})      # two seconds ago
    r = p.rows()
    check("M10 a direct python call carries ref and no fb", len(r) == 6 and r[0].get("ref") == "id" and "fb" not in r[0], r)
    check("M11 a stale, future or malformed start time is not believed",
          len(r) == 6 and all("fb" not in x and 0 <= x["ms"] < 30_000 for x in r[1:5]), r[1:5])
    check("M12 a recent start time is: the row is flagged and its ms starts there",
          len(r) == 6 and r[5].get("fb") == 1 and 2_000 <= r[5]["ms"] < 30_000, r[5:])

    # a zero-flag that did not come from this process's own parent is never believed
    z = Engine("python-launcher", {}, [sys.executable, VV])
    for forged in ("1", str(os.getpid() + 1)):
        rc, out, err = z.run("search", "alpha", env={"VV_NATIVE_ZERO": forged})
        check(f"Z8[{forged == '1' and 'literal' or 'pid'}] an inherited zero-flag cannot turn a hit into a miss",
              rc == 0 and "Plain Note.md" in out, (rc, out, err))

print(f"\n{len(fails)} failures" if fails else "\nall resolve/metrics checks passed")
sys.exit(1 if fails else 0)
