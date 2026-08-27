#!/usr/bin/env python3
"""Ledger of real vv usage across the last N sessions, and a stress replay of it.

WHY a ledger: the unit suites test what we thought to test; the metrics sink
(~/.claude/metrics/vv.jsonl) is dominated by our own benchmark and harness
traffic (98% of it on 2026-08-26 came from four build hours). The transcripts
are the only record of what was ACTUALLY asked of the tool, so the operation mix
is recovered from them.

Two invocation forms must both be counted, or the ledger silently under-reports:
  python3 .../src/vv.py <verb> ...     (the entry before 2026-08-27)
  vv <verb> ...                        (the native entry, default since)

Stress replay:
  READS  run against the LIVE vault (read-only) through BOTH engines and are
         compared byte-for-byte -- stdout, stderr and exit code. Python is the
         oracle; any divergence is a native-engine bug.
  WRITES run against a disposable COPY, never the real vault.
A write that legitimately refuses (exit 3 stale hash, exit 1 not-found because
the note was since renamed) is reported apart from one that CRASHES.
"""
import argparse, collections, glob, json, os, re, shutil, statistics
import subprocess, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sweepguard as sg
import vvops

PROJ = os.path.expanduser("~/.claude/projects/-Users-jxq-Documents-Obsidian-Vault")
VAULT = os.path.expanduser("~/Documents/Obsidian Vault")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYENTRY = [sys.executable, os.path.join(REPO, "src/vv.py")]
NATIVE = [os.path.join(REPO, "vrust/target/release/vrust")]

READ_VERBS, WRITE_VERBS = vvops.READ_VERBS, vvops.WRITE_VERBS


def walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values(): yield from walk(v)
    elif isinstance(o, list):
        for v in o: yield from walk(v)


def extract(n_sessions):
    vvops.self_test()                       # canary before any real data
    files = sorted(glob.glob(os.path.join(PROJ, "*.jsonl")),
                   key=os.path.getmtime, reverse=True)[:n_sessions]
    sg.preflight_corpus("session_ledger.transcripts", files)
    funnel = sg.Funnel("ledger", "files", "tool_uses", "vv_ops")
    rej = sg.RejectLog("ledger-extract")
    baseline = 0
    ops, per_session, synthetic = [], collections.Counter(), set()
    for f in files:
        sid = os.path.basename(f)[:-6]
        raw = open(f, encoding="utf-8", errors="replace").read()
        if "claude-provenance" in raw and "synthetic=true" in raw:
            synthetic.add(sid)
        for ln in raw.splitlines():
            try: rec = json.loads(ln)
            except json.JSONDecodeError: continue
            for d in walk(rec):
                if not isinstance(d, dict) or d.get("type") != "tool_use": continue
                funnel.bump("tool_uses")
                inp = d.get("input") or {}
                if d.get("name") != "Bash" or not isinstance(inp, dict): continue
                cmd = inp.get("command") or ""
                if not isinstance(cmd, str) or "vv" not in cmd: continue
                if re.search(r"<<'?\w*EOF", cmd): continue      # heredoc that merely mentions vv
                baseline += len(vvops.LOOSE_RE.findall(cmd))
                found = vvops.parse_invocations(cmd)
                for o in found:
                    ops.append({**o, "session": sid})
                    per_session[sid] += 1
                    rej.keep()
                if not found and vvops.LOOSE_RE.search(cmd):
                    rej.reject("looked like a vv invocation but parsed to nothing", cmd)
    funnel.counts["files"] = len(files)
    funnel.counts["vv_ops"] = len(ops)
    funnel.require("files"); funnel.require("tool_uses")
    funnel.report()
    rej.require_not_unanimous()
    rej.report()
    sg.require_recall("ledger-extract", len(ops), baseline,
                      baseline_desc="loose command-position regex")
    return files, ops, per_session, synthetic

def run(engine, argv, vault, timeout=60):
    env = dict(os.environ, VV_NO_METRICS="1", VV_VAULT=vault)
    try:
        r = subprocess.run(engine + argv, capture_output=True, env=env,
                           cwd=vault, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return "TIMEOUT", b"", b""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", type=int, default=100)
    ap.add_argument("--limit-per-verb", type=int, default=25)
    ap.add_argument("--ledger-only", action="store_true")
    a = ap.parse_args()

    files, ops, per_session, synthetic = extract(a.sessions)
    if not files:
        sys.exit("no transcripts found")
    span = (time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(files[-1]))),
            time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(files[0]))))

    print("=" * 72)
    print(f"LEDGER — {len(files)} sessions, {span[0]} .. {span[1]}")
    print("=" * 72)
    print(f"sessions containing vv operations : {len(per_session)} of {len(files)}")
    print(f"sessions marked synthetic (arms)  : {len(synthetic)}")
    print(f"total vv operations recovered     : {len(ops)}")
    ent = collections.Counter(o["entry"] for o in ops)
    print(f"by entry point                    : " +
          ", ".join(f"{k}={v}" for k, v in ent.most_common()))

    verbs = collections.Counter(f"{o['cls']}:{o['verb']}" for o in ops)
    print("\noperation mix:")
    for k, v in verbs.most_common():
        print(f"  {k:22s} {v:5d}  {'#' * min(50, v)}")

    if per_session:
        vals = sorted(per_session.values())
        print(f"\nops per active session: median={statistics.median(vals):.0f} "
              f"max={vals[-1]} (session {per_session.most_common(1)[0][0][:8]})")
    if a.ledger_only:
        return 0

    # ---- stress replay -------------------------------------------------
    bycat = collections.defaultdict(list)
    for o in ops: bycat[o["verb"]].append(o)
    reads = [o for v in bycat for o in bycat[v][:a.limit_per_verb] if o["cls"] == "read"]
    writes = [o for v in bycat for o in bycat[v][:a.limit_per_verb] if o["cls"] == "write"]

    print("\n" + "=" * 72)
    print(f"STRESS — accuracy: {len(reads)} read ops, native vs python, byte-for-byte")
    print("=" * 72)
    div, errs, tn, tp = [], 0, [], []
    for o in reads:
        t0 = time.perf_counter(); n = run(NATIVE, o["argv"], VAULT)
        t1 = time.perf_counter(); p = run(PYENTRY, o["argv"], VAULT)
        t2 = time.perf_counter()
        tn.append((t1 - t0) * 1000); tp.append((t2 - t1) * 1000)
        if n == "TIMEOUT" or p == "TIMEOUT": errs += 1; continue
        if n != p:
            div.append((o["verb"], o["argv"], n, p))
    print(f"  identical : {len(reads) - len(div) - errs} / {len(reads)}")
    print(f"  divergent : {len(div)}")
    print(f"  timeouts  : {errs}")
    for verb, argv, n, p in div[:6]:
        print(f"\n  DIVERGENCE {verb}: {' '.join(argv)[:90]}")
        print(f"    native exit={n[0]} out={n[1][:110]!r}")
        print(f"    python exit={p[0]} out={p[1][:110]!r}")

    if tn:
        print(f"\n  speed over the SAME replayed ops (median of {len(tn)}):")
        print(f"    native {statistics.median(tn):7.2f} ms    python {statistics.median(tp):7.2f} ms"
              f"    speedup {statistics.median(tp)/max(statistics.median(tn),0.01):.1f}x")

    # ---- writes against a disposable copy ------------------------------
    if writes:
        sandbox = tempfile.mkdtemp(prefix="vv-ledger-")
        nfiles = 0
        for root, dirs, fs in os.walk(VAULT):
            dirs[:] = [d for d in dirs if d not in
                       (".git", ".obsidian", ".trash", "node_modules", "graphify-out", ".claude")]
            for fn in fs:
                if not fn.endswith(".md"): continue
                src = os.path.join(root, fn)
                dst = os.path.join(sandbox, os.path.relpath(src, VAULT))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst); nfiles += 1
        print("\n" + "=" * 72)
        print(f"STRESS — writes: {len(writes)} ops against a COPY ({nfiles} notes), never the vault")
        print("=" * 72)
        crash, refused, ok = [], 0, 0
        for o in writes:
            rc, out, err = run(NATIVE, o["argv"], sandbox)
            if rc == "TIMEOUT": crash.append((o, "timeout", b"")); continue
            if rc == 0: ok += 1
            elif rc in (1, 3, 4): refused += 1          # tool working: refusal/not-found/journal
            else: crash.append((o, rc, err))
        print(f"  succeeded          : {ok}")
        print(f"  refused cleanly    : {refused}   (exit 1/3/4 — the tool working, not a bug)")
        print(f"  crashed/unexpected : {len(crash)}")
        for o, rc, err in crash[:5]:
            print(f"    {o['verb']}: exit={rc} {err[:130]!r}")
        shutil.rmtree(sandbox, ignore_errors=True)

    bad = len(div) + errs + (len(crash) if writes else 0)
    print("\n" + ("LEDGER STRESS PASS" if bad == 0 else f"LEDGER STRESS: {bad} problem(s)"))
    return 1 if bad else 0

if __name__ == "__main__":
    sys.exit(main())
