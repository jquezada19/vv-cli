#!/usr/bin/env python3
"""Torture: a damaged vvidx cache must NEVER produce a wrong answer.

Damage models: truncation at every byte offset, single-byte flips (all eight
bits), whole-record deletion, and footer tampering. After each damage the cache
is compared against the python engine, which is held off the index entirely
(VV_NO_INDEX=1) so it is a genuine oracle.

Two properties this suite is careful about, both learned from review:

  * The engine REPAIRS a cache it detects as damaged (cache.rs rebuild/
    write_cache). So one damage survives exactly one probe — every later probe
    would read a healed cache. The sweep therefore re-damages before every
    single probe, and rotates which probe sees the damage, instead of claiming
    coverage it does not have.

  * A sweep that reports "clean" is worthless if the cache is never consulted.
    The control forges a checksum-VALID corruption: the integrity check then
    passes, the damage is served, and the answer must go wrong. No divergence
    means the suite is blind, and that fails the run.

    Stated honestly, because the two halves exercise different paths: (a)-(d)
    plant caches the footer REJECTS, so they prove the engine never answers
    wrongly from damage — it is free to rebuild rather than read. Only (e), on
    the accepted path, proves the cache is consulted at all. Neither claim
    covers the other, and this suite asserts both separately.
"""
import hashlib, os, random, shutil, subprocess, sys, tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VR = os.path.join(REPO, "vrust/target/release/vrust")
VV = os.path.join(REPO, "src/vv.py")
TIMEOUT = int(os.environ.get("TORTURE_TIMEOUT", "60"))
rng = random.Random(int(os.environ.get("SEED", "4242")))

# Redirected HOME: the native engine derives its cache path from HOME and does
# NOT honor VV_INDEX_ROOT (only src/vv_impl.py does), so HOME is the one knob
# that keeps both engines off the runner's real ~/.cache/vv.
HOME = tempfile.mkdtemp(prefix="vv-torture-home-")
JR = tempfile.mkdtemp(prefix="vv-torture-journals-")
INDEX = os.path.join(HOME, ".cache/vv/index")


def _fnv(b: bytes) -> int:
    """Mirror of fnv1a64 in vrust/src/cache.rs. Duplicated on purpose: forging a
    footer the Rust side ACCEPTS is the only way to prove the cache is live.
    Re-sync if the cache hash changes — control (e) fails loudly if it drifts."""
    M = 0xFFFFFFFFFFFFFFFF
    h = (0x9E3779B97F4A7C15 ^ len(b)) & M
    n = len(b) - len(b) % 8
    for i in range(0, n, 8):
        h ^= (int.from_bytes(b[i:i + 8], "little") * 0xFF51AFD7ED558CCD) & M
        h = ((h << 31 | h >> 33) & M) * 0xC4CEB9FE1A85EC53 & M
    for x in b[n:]:
        h = ((h ^ x) * 0x100000001B3) & M
    return h


def cache_of(vault):
    k = hashlib.sha256(os.path.realpath(vault).encode()).hexdigest()[:16]
    return os.path.join(INDEX, f"{k}.vvidx")


def run(cmd, vault, py=False):
    env = dict(os.environ, VV_NO_METRICS="1", VV_VAULT=vault, HOME=HOME,
               VV_JOURNAL_ROOT=JR, VV_INDEX_ROOT=INDEX)
    if py:
        env["VV_NO_INDEX"] = "1"
        return subprocess.run([sys.executable, VV] + cmd, capture_output=True,
                              env=env, timeout=TIMEOUT)
    return subprocess.run([VR] + cmd, capture_output=True, env=env, timeout=TIMEOUT)


def build(vault, n):
    names = [f"Note {i}" for i in range(n)] + ["My Spaced Note", "Héllo Wörld", "日本語ノート"]
    for i, nm in enumerate(names):
        tgts = [t for t in rng.sample(names, k=min(4, len(names))) if t != nm]
        body = "\n".join(f"link to [[{t}]]" for t in tgts)
        nxt = names[(i + 1) % len(names)]
        extra = f"\nmd link [{nxt}]({nxt.replace(' ', '%20')}.md)\n"
        with open(os.path.join(vault, nm + ".md"), "w") as f:
            f.write(f"---\ntype: test\n---\n\n# {nm}\n\n{body}\n{extra}")
    return names


def main():
    if not os.path.exists(VR):
        print("SKIP: binary not built")
        return 0
    fails, notes = [], int(os.environ.get("TORTURE_NOTES", "18"))
    vault = tempfile.mkdtemp(prefix="vv-torture-")
    try:
        names = build(vault, notes)
        probes = ([["backlinks", n] for n in rng.sample(names, 6)]
                  + [["links", n] for n in rng.sample(names, 4)] + [["orphans"]])
        # Oracle: the python engine with the index disabled. Captured BEFORE any
        # damage; the vault never changes, so these stay valid all run.
        truth = {}
        for p in probes:
            r = run(p, vault, py=True)
            truth[tuple(p)] = (r.returncode, r.stdout)

        run(["backlinks", names[0]], vault)          # warm
        cp = cache_of(vault)
        if not os.path.exists(cp):
            print("FAIL: no cache produced")
            return 1
        orig = open(cp, "rb").read()
        end = orig.rfind(b"\nvvidx-end\t")
        body_lines = orig[:end + 1].split(b"\n")
        print(f"cache {len(orig)} bytes; {len(probes)} probes; vault {len(names)} notes")

        seen = {}   # probe -> how many damage instances that probe actually observed

        def damaged(payload, probe, label):
            """Write `payload`, run ONE probe against it, compare to the oracle.
            One damage survives one probe: the engine repairs what it rejects."""
            with open(cp, "wb") as f:
                f.write(payload)
            r = run(probe, vault)
            seen[tuple(probe)] = seen.get(tuple(probe), 0) + 1
            if (r.returncode, r.stdout) != truth[tuple(probe)]:
                fails.append((label, probe, r.stdout[:160], truth[tuple(probe)][1][:160]))
                return False
            return True

        # (a) truncation at EVERY byte offset, rotating which probe sees it
        done = 0
        for off in range(len(orig)):
            done += 1
            if not damaged(orig[:off], probes[off % len(probes)], f"truncate@{off}"):
                break
        print(f"(a) truncation: {done} of {len(orig)} offsets -> "
              f"{'DIVERGED' if fails else 'clean'}")

        # (b) single-byte flips — all EIGHT bits, distinct offsets
        if not fails:
            done = 0
            for i, off in enumerate(rng.sample(range(len(orig)), min(400, len(orig)))):
                b = bytearray(orig)
                b[off] ^= 1 << rng.randrange(8)
                done += 1
                if not damaged(bytes(b), probes[i % len(probes)], f"flip@{off}"):
                    break
            print(f"(b) byte flips: {done} distinct offsets, all 8 bits -> "
                  f"{'DIVERGED' if fails else 'clean'}")

        # (c) whole-record deletion — real F/L records only, never the footer
        if not fails:
            recs = [i for i, l in enumerate(body_lines) if l[:2] in (b"F\t", b"L\t")]
            done = 0
            for i, li in enumerate(rng.sample(recs, min(200, len(recs)))):
                keep = b"\n".join(body_lines[:li] + body_lines[li + 1:])
                done += 1
                if not damaged(keep + orig[end + 1:], probes[i % len(probes)],
                               f"delrec@{li}"):
                    break
            print(f"(c) record deletion: {done} of {len(recs)} records -> "
                  f"{'DIVERGED' if fails else 'clean'}")

        # (d) footer tampering — a present-but-wrong footer, not truncation
        if not fails:
            tampers = [b"vvidx-end\t0\t0000000000000000\n",
                       b"vvidx-end\t999999\tdeadbeefdeadbeef\n",
                       b"vvidx-end\tabc\tzz\n",
                       b"vvidx-end\n",
                       b"vvidx-end\t%d\t%016x\n" % (len(orig[:end + 1]) - 1,
                                                    _fnv(orig[:end + 1]))]
            for i, m in enumerate(tampers):
                if not damaged(orig[:end + 1] + m, probes[i % len(probes)],
                               "footer:" + m[:24].decode(errors="replace")):
                    break
            print(f"(d) footer tamper: {len(tampers)} -> "
                  f"{'DIVERGED' if fails else 'clean'}")

        obs = ", ".join(f"{' '.join(k)}={v}" for k, v in sorted(seen.items()))
        print(f"    damage instances observed per probe: {obs}")

        # (e) BLINDNESS CONTROL — runs even after a divergence, because a failing
        # sweep still needs to prove the sweep itself was looking at anything.
        # Drop a NON-REDUNDANT link row and re-stamp a matching footer: the
        # integrity check passes, so a live cache must serve the damage and
        # answer wrong. No divergence => this suite is blind.
        victim = None
        for li, l in enumerate(body_lines):
            if not l.startswith(b"L\t"):
                continue
            f = l.split(b"\t")
            if len(f) < 4:
                continue
            twin = [x for j, x in enumerate(body_lines)
                    if j != li and x.startswith(b"L\t")
                    and x.split(b"\t")[1:2] == f[1:2] and x.split(b"\t")[3:4] == f[3:4]]
            if not twin:            # removing it must actually change an answer
                victim = li
                break
        if victim is None:
            print("FAIL control: no non-redundant L row to forge with")
            return 1
        nb = b"\n".join(body_lines[:victim] + body_lines[victim + 1:])
        forged = nb + b"vvidx-end\t%d\t%016x\n" % (len(nb), _fnv(nb))
        with open(cp, "wb") as f:
            f.write(forged)
        served = any(run(["backlinks", n], vault).stdout
                     != run(["backlinks", n], vault, py=True).stdout for n in names)
        # Three states, not two. "Not served" has two very different causes, and
        # reporting them identically sends the next reader after the wrong bug:
        # the engine REWRITES a cache it rejects, so if the forged file is gone
        # from disk the footer was refused — which means _fnv has drifted from
        # cache.rs fnv1a64 (that function has been re-tuned for speed before),
        # not that the cache is unused.
        accepted = open(cp, "rb").read() == forged
        print(f"(e) control (re-stamped damage served): {'yes' if served else 'NO'}"
              f"   [forged footer {'accepted' if accepted else 'REJECTED'}]")
        if not served and not accepted:
            print("FAIL control: the forged footer was rejected -> _fnv in this "
                  "file has drifted from fnv1a64 in vrust/src/cache.rs; re-sync "
                  "the mirror. This is NOT evidence about the cache being used.")
            return 1
        if not served:
            print("FAIL control: a checksum-valid corruption was accepted and "
                  "changed nothing -> the cache is not consulted and this suite "
                  "proves nothing")
            return 1

        if fails:
            for lbl, probe, got, want in fails[:5]:
                print(f"FAIL {lbl} probe={' '.join(probe)}")
                print(f"     got:  {got}")
                print(f"     want: {want}")
            return 1
        print("ALL PASS (cache torture: no damaged cache produced a wrong answer)")
        return 0
    finally:
        for d in (vault, HOME, JR):
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
