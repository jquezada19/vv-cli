#!/usr/bin/env python3
"""Three-way vault-access benchmark: raw shell (grep/cat), the official `obsidian`
CLI (needs the app running), and vv.

Two costs per task, because for an AI-agent workflow BOTH matter:
  bytes — stdout the agent must carry as context (the token bill)
  ms    — median wall time of N runs

Tasks are the four operations an agent actually performs against a vault:
  section-read   "give me one section of a note"
  search         "find notes about X"
  fm-set         "flip one frontmatter field"
  backlinks      "what links here"

Run:  python3 bench/bench.py [--note "Some Note.md"] [--term keyword] [--runs 5]
The fm-set task writes to a COPY of the note in the system tempdir, never the
vault. Reported numbers depend on your vault's size; the point is the ratio.
"""
import argparse, os, shutil, statistics, subprocess, sys, tempfile, time

import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import sweepguard as _sg
_sg.mark_bench("bench")   # tag this run's vv rows as benchmark traffic

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Default entry since 2026-08-27: the native binary (falls back to python
# itself). VV_BENCH_ENTRY=python measures the python entry instead.
VV = ([sys.executable, os.path.join(REPO, "src", "vv.py")]
      if os.environ.get("VV_BENCH_ENTRY") == "python"
      else [os.path.join(REPO, "vrust", "target", "release", "vrust")])
VAULT = os.environ.get("VV_VAULT") or os.path.expanduser("~/Documents/Obsidian Vault")

def timed(cmd, runs, stdin=None, env=None):
    """(median_ms, stdout_bytes) — bytes from the first run, time = median."""
    times, out = [], b""
    e = dict(os.environ, **(env or {}))
    for i in range(runs):
        t0 = time.perf_counter()
        r = subprocess.run(cmd, capture_output=True, input=stdin, env=e)
        times.append((time.perf_counter() - t0) * 1000)
        if i == 0:
            out = r.stdout
    return statistics.median(times), len(out)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--note", default="CLAUDE.md")
    ap.add_argument("--term", default="tenant")
    ap.add_argument("--runs", type=int, default=5)
    a = ap.parse_args()
    note_path = os.path.join(VAULT, a.note)
    if not os.path.isfile(note_path):
        sys.exit(f"note not found: {note_path}")
    name = os.path.basename(a.note)[:-3]
    have_obs = shutil.which("obsidian") is not None
    if not have_obs:
        print("(obsidian CLI not installed — its column will be skipped)")

    rows = []
    def row(task, approach, cmd, **kw):
        ms, nbytes = timed(cmd, a.runs, **kw)
        rows.append((task, approach, ms, nbytes))

    # -- section-read: get ONE section of the note ---------------------------
    row("section-read", "shell", ["cat", note_path])          # whole file is the only granularity
    if have_obs:
        row("section-read", "obsidian", ["obsidian", "read", f"path={a.note}"])  # also whole file
    # vv: outline (pick a section) + read it — both calls counted
    r = subprocess.run(VV + ["outline", a.note], capture_output=True, text=True)
    sec = (r.stdout.strip().split("\n")[1:] or ["H0"])[0].split("\t")[0]
    ms1, b1 = timed(VV + ["outline", a.note], a.runs)
    ms2, b2 = timed(VV + ["read", a.note, sec], a.runs)
    rows.append(("section-read", "vv", ms1 + ms2, b1 + b2))

    # -- search --------------------------------------------------------------
    row("search", "shell", ["grep", "-rni", "--include=*.md", a.term, VAULT])
    if have_obs:
        row("search", "obsidian", ["obsidian", "search:context", f"query={a.term}"])
    row("search", "vv", VV + ["search", a.term])

    # -- fm-set: flip one frontmatter field (on a COPY, outside the vault) ---
    tmp = tempfile.mkdtemp(prefix="vv-bench-")
    shutil.copy2(note_path, os.path.join(tmp, name + ".md"))
    # shell analog of an agent edit: read the whole file, then write it back
    row("fm-set", "shell", ["python3", "-c",
        f"t=open({os.path.join(tmp, name + '.md')!r}).read();"
        f"print(t);open({os.path.join(tmp, name + '.md')!r},'w').write(t)"])
    # obsidian property:set only works on notes inside a vault the app has open — skipped
    row("fm-set", "vv", VV + ["--vault", tmp, "set", name + ".md", "bench-status", "done"])
    shutil.rmtree(tmp, ignore_errors=True)

    # -- backlinks -----------------------------------------------------------
    row("backlinks", "shell", ["grep", "-rlF", "--include=*.md", f"[[{name}", VAULT])
    if have_obs:
        row("backlinks", "obsidian", ["obsidian", "backlinks", f"path={a.note}"])
    row("backlinks", "vv", VV + ["backlinks", a.note])

    print(f"\nvault: {VAULT}")
    n_notes = 0
    for dp, dirs, fs in os.walk(VAULT):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        n_notes += sum(1 for f in fs if f.endswith(".md"))
    if n_notes < 10:
        # A benchmark over an empty or wrong-pathed vault still prints plausible
        # millisecond figures; the corpus size is the only tell, so assert it
        # rather than printing it and hoping someone notices.
        sys.exit(f"bench: only {n_notes} notes under {VAULT} — the vault path is "
                 f"wrong or empty. Timings against this corpus are meaningless.")
    print(f"notes: {n_notes} · note: {a.note} · term: '{a.term}' · runs: {a.runs} (median)\n")
    print(f"{'task':<14}{'approach':<10}{'ms':>8}{'bytes':>10}")
    for task, approach, ms, nbytes in rows:
        print(f"{task:<14}{approach:<10}{ms:>8.0f}{nbytes:>10}")

if __name__ == "__main__":
    main()
