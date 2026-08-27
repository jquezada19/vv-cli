#!/usr/bin/env python3
"""Torture: concurrent readers and writers must never yield a wrong answer.

Readers and writers are interleaved in the submit order, and the control
measures how much engine work actually happened at once: total child CPU time
divided by the storm's wall time. Anything that serializes execution — a
one-worker pool, a lock, an engine-level file lock — drives that to ~1.0 and
fails the run.

This is the third instrument tried here, and the reason for the change is worth
keeping. Timing each operation in the parent counted a thread's WAIT as
execution, so a fully serialized storm scored 100%. Counting children at spawn
counted a process blocked on a mutex as running, so a global lock scored higher
than the honest run. CPU time cannot be faked either way: a blocked process
burns none.

Its remaining limit, stated rather than claimed away: this proves engine
processes did work simultaneously, not that their vault accesses interleaved at
the byte level. Observing that would need instrumentation inside the engine.

What this suite does NOT cover, stated because a guard that cannot fail is
worse than an absent one: the native engine writes no journal for `set` (only
python's rename/move --apply journal), so a leftover-journal or exit-4 check
here would be structurally incapable of failing. Journal recovery is covered by
the write-parity suite instead.
"""
import os, random, resource, shutil, subprocess, sys, tempfile, threading, time
from concurrent.futures import ThreadPoolExecutor

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VR = os.path.join(REPO, "vrust/target/release/vrust")
VV = os.path.join(REPO, "src/vv.py")
TIMEOUT = int(os.environ.get("TORTURE_TIMEOUT", "60"))
WORKERS = int(os.environ.get("TORTURE_WORKERS", "16"))
rng = random.Random(int(os.environ.get("SEED", "77")))

JR = tempfile.mkdtemp(prefix="vv-conc-journals-")
HOME = tempfile.mkdtemp(prefix="vv-conc-home-")


# Concurrency is measured as child CPU time divided by wall time — the average
# number of engine processes doing work at once. Every parent-side proxy tried
# before this was defeated: timing spans counted a thread's wait as execution,
# and counting at spawn counted a child blocked on a mutex as running. CPU time
# cannot be faked that way, because a blocked process burns none. Measured on
# this machine: 10.0 honest, 0.9 under a global mutex, 0.9 at one worker.
# The floor is CALIBRATED IN-RUN rather than fixed, because achievable
# parallelism depends on the runner's core count: a fixed 1.5 that is generous
# here (14 cores) could redden a healthy 2-core CI box. The suite measures its
# own serial baseline, then requires the storm to beat it by this factor.
PARALLELISM_MARGIN = float(os.environ.get("TORTURE_PARALLELISM_MARGIN", "1.4"))


def _parallelism(fn):
    """Average engine processes doing work at once: child CPU time / wall."""
    r0 = resource.getrusage(resource.RUSAGE_CHILDREN)
    t0 = time.monotonic()
    fn()
    wall = time.monotonic() - t0
    r1 = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu = (r1.ru_utime - r0.ru_utime) + (r1.ru_stime - r0.ru_stime)
    return (cpu / wall) if wall > 0 else 0.0


def run(cmd, vault, py=False):
    env = dict(os.environ, VV_NO_METRICS="1", VV_VAULT=vault, VV_JOURNAL_ROOT=JR,
               HOME=HOME)
    if py:
        # VV_ENGINE=python is the load-bearing part: cmd_search delegates to the
        # native binary whenever use_rust() holds, so without it the "oracle"
        # would exec the very engine under test and the comparison is a tautology.
        env["VV_ENGINE"] = "python"
        env["VV_NO_INDEX"] = "1"
    argv = ([sys.executable, VV] + cmd) if py else ([VR] + cmd)
    return subprocess.run(argv, capture_output=True, text=True, env=env,
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
            with open(os.path.join(vault, "Notes", n + ".md"), "w",
                      encoding="utf-8") as f:
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
        # The verbs that still do not move under the storm's own value set have a
        # constant correct answer for its duration, which is what makes a
        # during-storm assertion possible.
        # Mixed, not uniform: the storm writes both `next` and `done`, and a verb
        # invariant to an all-`done` flip could still be sensitive to a mixed
        # state — which would fail intermittently mid-storm and look like an
        # engine bug. Classify against the same value set the storm uses.
        for i, n in enumerate(names):
            r = run(["set", n, "status", ["next", "done"][i % 2]], vault)
            if r.returncode != 0:
                print(f"FAIL setup: classifying flip on {n} exited "
                      f"{r.returncode}: {r.stderr.strip()[:120]}")
                return 1
        invariant = {k for k in pre if run(list(k), vault).stdout == pre[k]}
        for n in names:
            r = run(["set", n, "status", "open"], vault)
            if r.returncode != 0:
                # An unchecked restore leaves notes off-baseline, and the
                # mutation control below then counts that residue as if the storm
                # had written it — which let the control pass on zero writes.
                print(f"FAIL setup: restoring {n} exited {r.returncode}: "
                      f"{r.stderr.strip()[:120]}")
                return 1
        for c in READS:                       # re-baseline after restoring
            pre[tuple(c)] = run(c, vault).stdout
        def _statuses():
            out = {}
            for n in names:
                with open(os.path.join(vault, "Notes", n + ".md"),
                          encoding="utf-8") as f:
                    out[n] = f.read()
            return out
        baseline = _statuses()
        print("  status-invariant verbs (asserted during the storm): "
              + ", ".join(sorted(" ".join(k) for k in invariant)))
        print("  status-dependent verbs (exit code only): "
              + ", ".join(sorted(" ".join(k) for k in pre if k not in invariant)))
        if not invariant:
            print("FAIL setup: no status-invariant read verb; nothing can be "
                  "asserted during the storm")
            return 1
        # At least one asserted invariant must carry real content. `orphans` and
        # `tags` print "(0 orphans)" / "(0 tags)" on this fixture, and an
        # assertion satisfied by an engine that returns empty unconditionally is
        # not an assertion.
        if not any(any(n in pre[k] for n in names) for k in invariant):
            print("FAIL setup: every status-invariant verb returns content-free "
                  "output; an engine returning empty would satisfy all of them")
            return 1

        # Values exclude "open" so any landed write is detectable by the control
        # below; a plan built up front keeps the shared RNG off the worker threads
        # (random.Random is not thread-safe).
        plan = {i: (names[rng.randrange(60)], rng.choice(["next", "done"]))
                for i in range(300)}
        errs, refused, lock = [], [], threading.Lock()
        nreads, nwrites = [0], [0]

        def reader(i):
            c = READS[i % len(READS)]
            r = run(c, vault)
            with lock:
                nreads[0] += 1
            if r.returncode != 0:
                # Setup already proved every one of these verbs exits 0 on this
                # fixture, so a nonzero exit mid-storm is a real failure. The
                # earlier 0-3 tolerance let `search`/`outline`/`board` fail
                # repeatedly while the suite printed "runtime errors: 0".
                with lock:
                    errs.append(("bad-exit", c, r.returncode, r.stderr[:120]))
            elif tuple(c) in invariant and r.stdout != pre[tuple(c)]:
                # A verb no write can move returned a third answer mid-storm.
                with lock:
                    errs.append(("in-storm-divergence", c, r.stdout[:120]))

        def writer(i):
            n, val = plan[i]
            r = run(["set", n, "status", val], vault)
            with lock:
                nwrites[0] += 1
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
        # Serial baseline on this machine, this run: the same work with no
        # concurrency at all. On any box this lands near 1.0, which is exactly
        # what a serialized storm would also produce.
        serial = _parallelism(lambda: [run(READS[i % len(READS)], vault)
                                       for i in range(8)])
        floor = serial * PARALLELISM_MARGIN

        _r0 = resource.getrusage(resource.RUSAGE_CHILDREN)
        _t0 = time.monotonic()
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futs = [ex.submit(fn, i) for fn, i in jobs]
            for f in futs:
                f.result()
        _wall = time.monotonic() - _t0
        _r1 = resource.getrusage(resource.RUSAGE_CHILDREN)
        _cpu = ((_r1.ru_utime - _r0.ru_utime) + (_r1.ru_stime - _r0.ru_stime))
        parallelism = (_cpu / _wall) if _wall > 0 else 0.0

        # Post-storm: every read verb must agree with the python oracle on the
        # final state.
        final = []
        for c in READS:
            a = run(c, vault)
            b = run(c, vault, py=True)
            if (a.returncode, a.stdout) != (b.returncode, b.stdout):
                final.append((c, a.stdout[:120], b.stdout[:120]))
            elif a.returncode != 0:
                final.append((c, f"both engines exit {a.returncode}", ""))

        # Controls — each one fails the suite, none can pass vacuously.
        after = _statuses()
        changed = sum(1 for n in names if after[n] != baseline[n])
        if changed == 0:
            errs.append(("control-writes", "no write landed; the storm was a no-op"))
        # How often was a read process alive at the same moment as a write
        # process? Counted at spawn time, so any serialization below this harness
        # drives it to 0. Two earlier versions of this control measured
        # parent-side spans and passed a fully serialized storm at ~100%.
        if (os.cpu_count() or 1) < 2:
            print("  NOTE: single-core host — the parallelism control cannot "
                  "distinguish concurrency from serialization here, so it is "
                  "not asserted on this run")
        elif parallelism < floor:
            errs.append(("control-parallelism",
                         f"engine parallelism {parallelism:.2f} < {floor:.2f} "
                         f"(serial baseline {serial:.2f} x {PARALLELISM_MARGIN}); "
                         f"the storm was not concurrent"))

        print(f"{len(jobs)} ops ({nreads[0]} read / {nwrites[0]} write, "
              f"{WORKERS} workers)")
        print(f"  engine parallelism (child cpu / wall): {parallelism:.2f}"
              f"   serial baseline {serial:.2f}   floor {floor:.2f}")
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
