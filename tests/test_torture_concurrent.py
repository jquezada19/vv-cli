#!/usr/bin/env python3
"""Torture: concurrent readers and writers must never yield a wrong answer.

Readers and writers are interleaved in the submit order so they genuinely
overlap — a pool that queues every reader ahead of every writer is not a
concurrency test, and the overlap control below fails the run if that happens.

What this suite does NOT cover, stated because a guard that cannot fail is
worse than an absent one: the native engine writes no journal for `set` (only
python's rename/move --apply journal), so a leftover-journal or exit-4 check
here would be structurally incapable of failing. Journal recovery is covered by
the write-parity suite instead.
"""
import os, random, shutil, subprocess, sys, tempfile, threading, time
from concurrent.futures import ThreadPoolExecutor

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VR = os.path.join(REPO, "vrust/target/release/vrust")
VV = os.path.join(REPO, "src/vv.py")
TIMEOUT = int(os.environ.get("TORTURE_TIMEOUT", "60"))
WORKERS = int(os.environ.get("TORTURE_WORKERS", "16"))
rng = random.Random(int(os.environ.get("SEED", "77")))

JR = tempfile.mkdtemp(prefix="vv-conc-journals-")
HOME = tempfile.mkdtemp(prefix="vv-conc-home-")


def run(cmd, vault, py=False):
    env = dict(os.environ, VV_NO_METRICS="1", VV_VAULT=vault, VV_JOURNAL_ROOT=JR,
               HOME=HOME)
    if py:
        # VV_ENGINE=python is the load-bearing part: cmd_search delegates to the
        # native binary whenever use_rust() holds, so without it the "oracle"
        # would exec the very engine under test and the comparison is a tautology.
        env["VV_ENGINE"] = "python"
        env["VV_NO_INDEX"] = "1"
        return subprocess.run([sys.executable, VV] + cmd, capture_output=True,
                              text=True, env=env, timeout=TIMEOUT)
    return subprocess.run([VR] + cmd, capture_output=True, text=True, env=env,
                          timeout=TIMEOUT)


def main():
    if not os.path.exists(VR):
        print("SKIP: binary not built")
        for d in (JR, HOME):
            shutil.rmtree(d, ignore_errors=True)
        return 0
    vault = tempfile.mkdtemp(prefix="vv-conc-")
    try:
        os.makedirs(os.path.join(vault, "Notes"))
        names = [f"N{i}" for i in range(60)]
        for i, n in enumerate(names):
            with open(os.path.join(vault, "Notes", n + ".md"), "w") as f:
                f.write(f"---\ntype: test\nstatus: open\n---\n\n# {n}\n\n"
                        f"[[{names[(i + 1) % 60]}]] [[{names[(i + 7) % 60]}]]\n")

        # Every entry is a real invocation — a verb that only ever prints a usage
        # error would compare two identical error strings and prove nothing.
        READS = [["backlinks", "N3"], ["links", "N10"], ["orphans"],
                 ["search", "N3"], ["outline", "N20"], ["tags"],
                 ["board", "Notes"], ["read", "N40", "N40"]]
        pre = {}
        for c in READS:
            r = run(c, vault)
            if r.returncode != 0:
                print(f"FAIL setup: read verb {' '.join(c)} exits {r.returncode}: "
                      f"{r.stderr.strip()[:120]}")
                return 1
            pre[tuple(c)] = r.stdout

        # Which verbs can a `set status` write actually move? Measured, not
        # assumed — and measured against EVERY note, not one. Flipping a single
        # note misclassified `search` as invariant: its snippets embed note
        # bodies, so it moves only when a note inside the result set changes.
        # The verbs that still do not move have a constant correct answer for the
        # whole storm, which is what makes a during-storm assertion possible.
        for n in names:
            run(["set", n, "status", "done"], vault)
        invariant = {k for k in pre if run(list(k), vault).stdout == pre[k]}
        for n in names:
            run(["set", n, "status", "open"], vault)
        for c in READS:                       # re-baseline after restoring
            pre[tuple(c)] = run(c, vault).stdout
        print("  status-invariant verbs (asserted during the storm): "
              + ", ".join(sorted(" ".join(k) for k in invariant)))
        print("  status-dependent verbs (exit code only): "
              + ", ".join(sorted(" ".join(k) for k in pre if k not in invariant)))
        if not invariant:
            print("FAIL setup: no status-invariant read verb; nothing can be "
                  "asserted during the storm")
            return 1

        # Values exclude "open" so any landed write is detectable by the control
        # below; a plan built up front keeps the shared RNG off the worker threads
        # (random.Random is not thread-safe).
        plan = {i: (names[rng.randrange(60)], rng.choice(["next", "done"]))
                for i in range(300)}
        errs, refused, lock = [], [], threading.Lock()
        rspan, wspan = [], []

        def note(bucket, t0, t1):
            with lock:
                bucket.append((t0, t1))

        def reader(i):
            c = READS[i % len(READS)]
            t0 = time.monotonic()
            r = run(c, vault)
            note(rspan, t0, time.monotonic())
            if r.returncode not in (0, 1, 2, 3):
                with lock:
                    errs.append(("bad-exit", c, r.returncode, r.stderr[:120]))
            elif tuple(c) in invariant and r.stdout != pre[tuple(c)]:
                # A verb no write can move returned a third answer mid-storm.
                with lock:
                    errs.append(("in-storm-divergence", c, r.stdout[:120]))

        def writer(i):
            n, val = plan[i]
            t0 = time.monotonic()
            r = run(["set", n, "status", val], vault)
            note(wspan, t0, time.monotonic())
            with lock:
                if r.returncode == 3:
                    # Lost the compare-and-swap race. Native `set` returns
                    # Outcome::Fallback on a CAS mismatch (vrust/src/write.rs) and
                    # re-execs python, which re-reads a fresh signature and
                    # usually wins — so exit 3 means a SECOND lost race inside the
                    # fallback window. Rare, correct, and not a failure. Counted
                    # and reported; deliberately NOT asserted on, because a guard
                    # keyed on a rare race would be one that never fires.
                    refused.append(n)
                elif r.returncode != 0:
                    errs.append(("write-fail", n, r.returncode, r.stderr[:120]))

        # Interleave: one writer every fifth job, so writes land throughout the
        # storm instead of after every read has already finished.
        jobs = []
        for i in range(300):
            jobs.append((writer, i) if i % 5 == 4 else (reader, i))
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futs = [ex.submit(fn, i) for fn, i in jobs]
            for f in futs:
                f.result()

        # Post-storm: every read verb must agree with the python oracle on the
        # final state.
        final = []
        for c in READS:
            a = run(c, vault).stdout
            b = run(c, vault, py=True).stdout
            if a != b:
                final.append((c, a[:120], b[:120]))

        # Controls — each one fails the suite, none can pass vacuously.
        changed = sum(1 for n in names
                      if "status: open" not in
                      open(os.path.join(vault, "Notes", n + ".md")).read())
        if changed == 0:
            errs.append(("control-writes", "no write landed; the storm was a no-op"))
        # Each read must overlap an INDIVIDUAL writer's span. The union hull
        # (min start .. max end) spans essentially the whole run, so a fully
        # serialized execution scored ~92% against it — a guard that cannot fail.
        overlap = sum(1 for t0, t1 in rspan
                      if any(t1 > ws and t0 < we for ws, we in wspan))
        if overlap < len(rspan) // 4:
            errs.append(("control-overlap",
                         f"only {overlap}/{len(rspan)} reads overlapped a writer; "
                         f"the storm was not concurrent"))

        print(f"{len(jobs)} ops ({len(rspan)} read / {len(wspan)} write, "
              f"{WORKERS} workers)")
        print(f"  reads overlapping the write window: {overlap}/{len(rspan)}")
        print(f"  notes actually mutated: {changed}   "
              f"writes refused by the CAS guard: {len(refused)}")
        print(f"  runtime errors: {len(errs)}   post-storm divergence: {len(final)}")
        for e in errs[:5]:
            print("FAIL", e)
        for f in final[:5]:
            print("FAIL divergence", f)
        if errs or final:
            return 1
        print("ALL PASS (concurrency torture)")
        return 0
    finally:
        for d in (vault, JR, HOME):
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
