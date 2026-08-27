#!/usr/bin/env python3
"""Replay every vault operation the last N sessions performed, and check it still works.

WHY: today changed the writer path, the ranker, the record store, the linter and
the journal. Unit tests cover what we thought to test; this covers what we
actually DID -- the real operation mix, replayed against today's code.

READS run against the live vault (read-only, no mutation possible).
WRITES run against a disposable COPY of the vault, so a replayed `set`, `patch`
or `rename` can never touch real notes. A write that legitimately refuses (exit
3 stale hash / drifted plan, exit 1 not-found because the note has since been
renamed) is reported separately from one that CRASHES -- the first is the tool
working, the second is a regression.

Usage: vault_ops_replay.py [--sessions 50] [--limit-per-kind 40]
"""
import argparse, collections, glob, json, os, re, shutil, subprocess, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sweepguard as sg
import vvops

PROJ = os.path.expanduser("~/.claude/projects/-Users-jxq-Documents-Obsidian-Vault")
VAULT = os.path.expanduser("~/Documents/Obsidian Vault")
VV = os.path.expanduser("~/Desktop/Git/vv-cli/src/vv.py")
ASK = os.path.join(VAULT, ".claude/skills/vault-ask/vault_ask.py")

READ_VERBS = {"read", "outline", "show", "head", "resolve", "search", "backlinks",
              "links", "impact", "orphans", "deadends", "board", "props", "tags"}
WRITE_VERBS = {"set", "unset", "append", "appendsec", "patch", "daily-append",
               "rename", "move", "new"}


def walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from walk(v)


def extract(n_sessions):
    vvops.self_test()          # canary BEFORE any real data is touched
    files = sorted(glob.glob(os.path.join(PROJ, "*.jsonl")),
                   key=os.path.getmtime, reverse=True)[:n_sessions]
    sg.preflight_corpus("vault_ops_replay.transcripts", files)
    funnel = sg.Funnel("extract", "files", "tool_uses", "vv_ops")
    rej = sg.RejectLog("vv-extract")
    baseline = {"n": 0}
    ops = []
    for f in files:
        for ln in open(f, encoding="utf-8", errors="replace"):
            try:
                rec = json.loads(ln)
            except json.JSONDecodeError:
                continue
            for d in walk(rec):
                if not isinstance(d, dict) or d.get("type") != "tool_use":
                    continue
                funnel.bump("tool_uses")
                name, inp = d.get("name"), d.get("input") or {}
                if not isinstance(inp, dict):
                    continue
                if name == "Read":
                    p = inp.get("file_path") or ""
                    if p.endswith(".md") and VAULT in p:
                        ops.append({"cls": "read", "tool": "Read", "path": p})
                elif name in ("Edit", "Write", "MultiEdit"):
                    p = inp.get("file_path") or ""
                    if p.endswith(".md") and VAULT in p:
                        ops.append({"cls": "write", "tool": name, "path": p})
                elif name in ("Grep", "Glob"):
                    ops.append({"cls": "read", "tool": name,
                                "pattern": inp.get("pattern") or inp.get("glob") or ""})
                elif name == "Bash":
                    cmd = inp.get("command") or ""
                    if not isinstance(cmd, str):
                        continue
                    if re.search(r"<<'?\w*EOF|^\s*def |python3 - ", cmd):
                        continue          # a heredoc that merely MENTIONS vv
                    # ONE shared extractor (bench/vvops.py). This used to be a
                    # local copy matching only `vv.py <verb>`; when the native
                    # `vv` binary became the default entry it silently lost 47%
                    # of operations (156 recovered where 294 exist) and still
                    # printed a confident mix. Duplicated extractor, duplicated
                    # blind spot -- hence the single implementation + canary.
                    if "vv" in cmd:
                        baseline["n"] += len(vvops.LOOSE_RE.findall(cmd))
                    found = vvops.parse_invocations(cmd)
                    for o in found:
                        ops.append({"cls": o["cls"], "tool": "vv", "verb": o["verb"],
                                    "argv": o["argv"], "cmd": cmd})
                        rej.keep()
                    if not found and vvops.LOOSE_RE.search(cmd):
                        rej.reject("looked like a vv invocation but parsed to nothing", cmd)
                    if "vault_ask.py" in cmd:
                        ops.append({"cls": "read", "tool": "vault_ask", "cmd": cmd})
                    if re.search(r"record\.py\s+query", cmd):
                        ops.append({"cls": "read", "tool": "record", "cmd": cmd})
                    if re.search(r"\bobsidian\s+\w", cmd):
                        ops.append({"cls": "read", "tool": "obsidian", "cmd": cmd})
    funnel.counts["files"] = len(files)
    funnel.counts["vv_ops"] = sum(1 for o in ops if o.get("tool") == "vv")
    funnel.require("files")
    funnel.require("tool_uses")      # transcripts present but nothing parsed = broken reader
    funnel.report()
    rej.require_not_unanimous()
    rej.report()
    # dumb high-recall lower bound, independent of the extractor's parsing:
    # a >=1 floor cannot catch a PARTIAL miss (the real one still returned 156).
    sg.require_recall("vv-extract", funnel.counts["vv_ops"], baseline["n"],
                      baseline_desc="loose regex over the same commands")
    return ops


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", type=int, default=50)
    ap.add_argument("--limit-per-kind", type=int, default=40)
    a = ap.parse_args()

    ops = extract(a.sessions)
    kinds = collections.Counter(
        f"{o['cls']}:{o['tool']}" + (f".{o['verb']}" if o.get("verb") else "") for o in ops)
    print(f"scanned {a.sessions} sessions -> {len(ops)} vault operations")
    print("mix: " + ", ".join(f"{k}={v}" for k, v in kinds.most_common()))

    sandbox = tempfile.mkdtemp(prefix="vault-replay-")
    print(f"\ncopying markdown into a disposable sandbox for WRITE replay ({sandbox}) …")
    n = 0
    for root, dirs, files in os.walk(VAULT):
        dirs[:] = [d for d in dirs if d not in (".git", ".obsidian", ".trash",
                                                "node_modules", "graphify-out", ".claude")]
        for fn in files:
            if not fn.endswith(".md"):
                continue
            src = os.path.join(root, fn)
            dst = os.path.join(sandbox, os.path.relpath(src, VAULT))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            n += 1
    print(f"  {n} notes copied\n")

    env = dict(os.environ, VV_NO_METRICS="1",
               VV_JOURNAL_ROOT=tempfile.mkdtemp(prefix="vault-replay-j-"))
    results = collections.defaultdict(lambda: collections.Counter())
    failures = []
    seen = collections.Counter()

    for o in ops:
        key = f"{o['cls']}:{o['tool']}" + (f".{o['verb']}" if o.get("verb") else "")
        # Ephemeral fixtures: vv's own test runs create and destroy notes under
        # Sandbox/ inside a session. Replaying them against the live vault is a
        # guaranteed (and meaningless) not-found, which would swamp the signal.
        blob = json.dumps(o)
        if re.search(r"Sandbox/|/vvtest|/vv15test|/tmp/|vault-replay-", blob):
            results[key]["skipped-fixture"] += 1
            continue
        if seen[key] >= a.limit_per_kind:
            continue
        seen[key] += 1
        target = VAULT if o["cls"] == "read" else sandbox
        cmd = None

        if o["tool"] == "Read":
            rel = os.path.relpath(o["path"], VAULT)
            if os.path.exists(os.path.join(VAULT, rel)):
                results[key]["ok"] += 1
                continue
            # A note absent at its OLD path is usually relocated, not lost --
            # the Work Items reorg moved notes into In Flight/Backlog/Done
            # subfolders. Resolving by name separates "moved" (fine, and the
            # reason vv's rename is link-aware) from "actually gone" (a finding).
            base = os.path.basename(rel)
            found = None
            for r_, _, fs in os.walk(VAULT):
                if ".git" in r_ or "/.claude" in r_:
                    continue
                if base in fs:
                    found = os.path.relpath(os.path.join(r_, base), VAULT)
                    break
            if found:
                results[key]["relocated"] += 1
            else:
                results[key]["LOST"] += 1
                failures.append((key, rel, "note not found anywhere in the vault"))
            continue
        if o["tool"] in ("Edit", "Write", "MultiEdit"):
            rel = os.path.relpath(o["path"], VAULT)
            ok = os.path.exists(os.path.join(sandbox, rel))
            results[key]["ok" if ok else "gone"] += 1
            continue
        if o["tool"] in ("Grep", "Glob"):
            # NOT "ok": nothing was run. Counting an unexecuted pattern as a
            # success inflates coverage with operations the replay never tested.
            results[key]["not-executed"] += 1
            continue
        if o["tool"] == "vv":
            # argv comes from the SHARED extractor (bench/vvops.py), already
            # canaried. There used to be a second parser here (parse_vv_args)
            # doing the same job differently -- a duplicate extractor is a
            # duplicate blind spot, which is how the entry-point miss survived.
            argv = o.get("argv")
            if not argv:
                results[key]["unparsed"] += 1
                continue
            if argv[0] == "patch":       # needs stdin + a live hash; not replayable verbatim
                results[key]["skipped-stdin"] += 1
                continue
            cmd = [sys.executable, VV, "--vault", target] + argv
        elif o["tool"] == "vault_ask":
            m = re.search(r"vault_ask\.py[\"']?\s+(.*)", o["cmd"])
            q = None
            if m:
                mm = re.search(r'"([^"]{4,200})"', m.group(1))
                q = mm.group(1) if mm else None
            if not q:
                results[key]["unparsed"] += 1
                continue
            cmd = [sys.executable, ASK, "--vault", VAULT, "--top", "5", q]
        elif o["tool"] == "record":
            mm = re.search(r"--grep\s+(\S+)", o["cmd"])
            cmd = [sys.executable, os.path.expanduser("~/.claude/scripts/record.py"),
                   "query", "Users-jxq-Documents-Obsidian-Vault", "--limit", "3"]
            if mm:
                cmd += ["--grep", mm.group(1).strip("'\"")]
        elif o["tool"] == "obsidian":
            results[key]["not-executed"] += 1     # needs the live app
            continue

        if cmd is None:
            results[key]["unparsed"] += 1
            continue
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env)
        except subprocess.TimeoutExpired:
            results[key]["TIMEOUT"] += 1
            failures.append((key, " ".join(cmd[3:6]), "timeout"))
            continue
        if r.returncode == 0:
            results[key]["ok"] += 1
        elif r.returncode in (1, 3, 4, 5):
            results[key][f"refused-{r.returncode}"] += 1
        else:
            results[key][f"CRASH-{r.returncode}"] += 1
            failures.append((key, " ".join(cmd[3:7]), (r.stderr or "")[:120]))

    executed = sum(v for row in results.values() for kk, v in row.items()
                   if kk == "ok" or kk.startswith("refused-") or kk.startswith("CRASH")
                   or kk == "TIMEOUT")
    if executed == 0:
        sys.exit("replay: NOTHING was actually executed — every operation was "
                 "skipped, unparsed or not-executed. This is a broken replay, "
                 "not a clean run.")
    print(f"executed {executed} operation(s); the rest were not run\n")
    print("results by operation kind:")
    for k in sorted(results):
        row = results[k]
        total = sum(row.values())
        bad = sum(v for kk, v in row.items() if kk.startswith("CRASH") or kk == "TIMEOUT")
        flag = "  <-- REGRESSION" if bad else ""
        print(f"  {k:26} n={total:4}  " +
              ", ".join(f"{kk}={vv}" for kk, vv in row.most_common()) + flag)

    print(f"\ncrashes/timeouts: {len(failures)}")
    for k, what, why in failures[:15]:
        print(f"  [{k}] {what}: {why}")
    shutil.rmtree(sandbox, ignore_errors=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
