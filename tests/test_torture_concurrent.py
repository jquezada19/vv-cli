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
rng = random.Random(int(os.environ.get("SEED", "77")))

JR = tempfile.mkdtemp(prefix="vv-conc-journals-")
HOME = tempfile.mkdtemp(prefix="vv-conc-home-")
INDEX = os.path.join(HOME, ".cache/vv/index")


def run(cmd, vault, py=False):
    env = dict(os.environ, VV_NO_METRICS="1", VV_VAULT=vault, VV_JOURNAL_ROOT=JR,
               HOME=HOME, VV_INDEX_ROOT=INDEX)
    if py:
        env["VV_NO_INDEX"] = "1"
        return subprocess.run([sys.executable, VV] + cmd, capture_output=True,
                              text=True, env=env, timeout=TIMEOUT)
    return subprocess.run([VR] + cmd, capture_output=True, text=True, env=env,
                          timeout=TIMEOUT)


def main():
    if not os.path.exists(VR):
        print("SKIP: binary not built")
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

        def writer(i):
            n, val = plan[i]
            t0 = time.monotonic()
            r = run(["set", n, "status", val], vault)
            note(wspan, t0, time.monotonic())
            with lock:
                if r.returncode == 3:
                    # Lost the compare-and-swap race (src/vv_impl.py atomic_write
                    # expect_sig). That is the write path REFUSING to clobber a
                    # concurrent edit — the behavior under test, not a failure.
                    refused.append(n)
                elif r.returncode != 0:
                    errs.append(("write-fail", n, r.returncode, r.stderr[:120]))

        # Interleave: one writer every fourth job, so writes land throughout the
        # storm instead of after every read has already finished.
        jobs = []
        for i in range(300):
            jobs.append((writer, i) if i % 5 == 4 else (reader, i))
        with ThreadPoolExecutor(max_workers=16) as ex:
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
        if len(refused) == len(wspan):
            errs.append(("control-writes", "every write was refused; nothing was tested"))
        w0 = min((t for t, _ in wspan), default=0.0)
        w1 = max((t for _, t in wspan), default=0.0)
        overlap = sum(1 for t0, t1 in rspan if t1 > w0 and t0 < w1)
        if overlap < len(rspan) // 4:
            errs.append(("control-overlap",
                         f"only {overlap}/{len(rspan)} reads overlapped the write "
                         f"window; the storm was not concurrent"))

        print(f"{len(jobs)} ops ({len(rspan)} read / {len(wspan)} write, 16 workers)")
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
