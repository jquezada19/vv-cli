#!/usr/bin/env python3
"""Torture: a damaged vvidx cache must NEVER produce a wrong answer.

Damage models: truncation at every byte offset, single-byte flips (every one of
the eight bits, at sampled offsets), whole-record deletion, and footer tampering.
After each damage the engine is compared against an oracle.

Three things this suite is careful about, each learned from a review that caught
it claiming coverage it did not have:

  * The engine REPAIRS a cache it detects as damaged (cache.rs rebuild ->
    write_cache), so one damage survives exactly one probe. The sweep therefore
    re-damages before every single probe.

  * Not every verb consults the cache. Native `links` resolves the note directly
    and never calls cache::links_map (graph.rs cmd_links), so damage cannot move
    its answer — rotating damage onto it tests nothing while counting as
    coverage. The cache-consuming probes are DERIVED below, not assumed.

  * A sweep that reports "clean" is worthless if the cache is never consulted.
    The derivation doubles as the blindness control: it plants a checksum-VALID
    corruption, which the integrity check accepts, so a live cache must serve it
    and answer wrong. If no probe moves, this suite proves nothing and fails.

Stated honestly, because the halves exercise different paths: (a)-(d) plant
caches the footer REJECTS, so they prove the engine never answers wrongly from
damage — it is free to rebuild rather than read. Only the forged-footer control,
on the accepted path, proves the cache is consulted at all.

The oracle is the python engine, forced with VV_ENGINE=python. Its independence
from the cache comes from live-walking the vault, not from VV_NO_INDEX (which
only disables python's own SQLite index). Known limit: the oracle shells out to
the native link lexer, so this is an oracle for the CACHE layer only — a lexer
bug is invisible to it.
"""
import hashlib, os, random, shutil, subprocess, sys, tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VR = os.path.join(REPO, "vrust/target/release/vrust")
VV = os.path.join(REPO, "src/vv.py")
TIMEOUT = int(os.environ.get("TORTURE_TIMEOUT", "60"))
rng = random.Random(int(os.environ.get("SEED", "4242")))

# Redirected HOME: the native engine derives its cache path from HOME and does
# NOT honor VV_INDEX_ROOT (only src/vv_impl.py does), so HOME is the one knob
# that keeps both engines off the runner's real ~/.cache/vv. VV_INDEX_ROOT is
# deliberately NOT set — setting it re-enables python's SQLite index in
# CAS-fallback children, a path production would not take here.
HOME = tempfile.mkdtemp(prefix="vv-torture-home-")
JR = tempfile.mkdtemp(prefix="vv-torture-journals-")


def _fnv(b: bytes) -> int:
    """Mirror of fnv1a64 in vrust/src/cache.rs. Duplicated on purpose: forging a
    footer the Rust side ACCEPTS is the only way to prove the cache is live.
    Re-sync if the cache hash changes — the control says so explicitly if it
    drifts, rather than blaming the cache."""
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
    return os.path.join(HOME, ".cache/vv/index", f"{k}.vvidx")


def run(cmd, vault, py=False):
    env = dict(os.environ, VV_NO_METRICS="1", VV_VAULT=vault, HOME=HOME,
               VV_JOURNAL_ROOT=JR)
    if py:
        env["VV_ENGINE"] = "python"
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


def _cleanup(*dirs):
    for d in dirs:
        shutil.rmtree(d, ignore_errors=True)


def main():
    if not os.path.exists(VR):
        print("SKIP: binary not built")
        _cleanup(HOME, JR)
        return 0
    fails, notes = [], int(os.environ.get("TORTURE_NOTES", "18"))
    vault = tempfile.mkdtemp(prefix="vv-torture-")
    try:
        names = build(vault, notes)
        candidates = ([["backlinks", n] for n in rng.sample(names, 6)]
                      + [["links", n] for n in rng.sample(names, 4)] + [["orphans"]])
        truth = {}
        for p in candidates:
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
        print(f"cache {len(orig)} bytes; vault {len(names)} notes")

        # --- Derive the cache-consuming probes; this IS the blindness control.
        victim = None
        for li, l in enumerate(body_lines):
            if not l.startswith(b"L\t"):
                continue
            f = l.split(b"\t")
            # Wiki rows only: a markdown row's percent-encoded target is a
            # different raw string from its wiki twin's, so a redundant row can
            # look unique and removing it would change no answer — a false
            # accusation against the cache.
            if len(f) < 4 or f[2] != b"w":
                continue
            twin = [x for j, x in enumerate(body_lines)
                    if j != li and x.startswith(b"L\t")
                    and x.split(b"\t")[1:2] == f[1:2] and x.split(b"\t")[3:4] == f[3:4]]
            if not twin:
                victim = li
                break
        if victim is None:
            print("FAIL control: no non-redundant wiki L row to forge with")
            return 1
        # Drop EVERY link row, not just the victim's. Removing one row only moves
        # the backlinks of that row's target, so a one-row forgery measures
        # "affected by this damage", not "consults the cache" — it classified 1
        # of 11 probes as a consumer when the true answer is every backlinks and
        # orphans probe. A cache with no link rows must move every verb that
        # reads link data, and must not move one that resolves notes directly.
        kept = [l for l in body_lines if not l.startswith(b"L\t")]
        nb = b"\n".join(kept)
        forged = nb + b"vvidx-end\t%d\t%016x\n" % (len(nb), _fnv(nb))
        # The victim row is still located above: it is what proves a
        # non-redundant link EXISTS to lose, so an empty-link cache is a real
        # corruption of this fixture rather than a no-op.

        consumers = []
        for pr in candidates:
            with open(cp, "wb") as f:
                f.write(forged)
            a = run(pr, vault)
            b = run(pr, vault, py=True)
            # A clean native exit is required: an engine that errors before
            # touching the cache also "differs" from the oracle, and counting
            # that as consumption lets a crash masquerade as coverage.
            if a.returncode == 0 and (a.returncode, a.stdout) != (b.returncode, b.stdout):
                consumers.append(pr)
        accepted = open(cp, "rb").read() == forged

        print("    cache-consuming probes: "
              + (", ".join(" ".join(p) for p in consumers) or "NONE"))
        print("    probes that ignore the cache (excluded): "
              + (", ".join(" ".join(p) for p in candidates if p not in consumers) or "none"))
        print(f"(e) control (re-stamped damage served): {'yes' if consumers else 'NO'}"
              f"   [forged footer {'accepted' if accepted else 'REJECTED'}]")
        if not consumers and not accepted:
            print("FAIL control: the forged footer was rejected -> _fnv in this "
                  "file has drifted from fnv1a64 in vrust/src/cache.rs; re-sync "
                  "the mirror. This is NOT evidence about the cache being used.")
            return 1
        if not consumers:
            print("FAIL control: a checksum-valid corruption was accepted and "
                  "changed nothing -> the cache is not consulted and this suite "
                  "proves nothing")
            return 1
        probes = consumers

        seen = {}

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

        # (a) truncation at EVERY byte offset
        done = 0
        for off in range(len(orig)):
            done += 1
            if not damaged(orig[:off], probes[off % len(probes)], f"truncate@{off}"):
                break
        print(f"(a) truncation: {done} of {len(orig)} offsets -> "
              f"{'DIVERGED' if fails else 'clean'}")

        # (b) single-byte flips — every one of the eight bits, distinct offsets
        if not fails:
            done, offs = 0, rng.sample(range(len(orig)), min(50, len(orig)))
            for i, off in enumerate(offs):
                for bit in range(8):
                    b = bytearray(orig)
                    b[off] ^= 1 << bit
                    done += 1
                    if not damaged(bytes(b), probes[(i + bit) % len(probes)],
                                   f"flip@{off}:bit{bit}"):
                        break
                if fails:
                    break
            print(f"(b) byte flips: {done} flips over {len(offs)} distinct offsets "
                  f"x 8 bits -> {'DIVERGED' if fails else 'clean'}")

        # (c) whole-record deletion — real F/L records only, never the footer
        if not fails:
            recs = [i for i, l in enumerate(body_lines) if l[:2] in (b"F\t", b"L\t")]
            done, pick = 0, rng.sample(recs, min(200, len(recs)))
            for i, li in enumerate(pick):
                keep = b"\n".join(body_lines[:li] + body_lines[li + 1:])
                done += 1
                if not damaged(keep + orig[end + 1:], probes[i % len(probes)],
                               f"delrec@{li}"):
                    break
            print(f"(c) record deletion: {done} of {len(pick)} records -> "
                  f"{'DIVERGED' if fails else 'clean'}")

        # (d) footer tampering — a present-but-wrong footer, not truncation
        if not fails:
            tampers = [b"vvidx-end\t0\t0000000000000000\n",
                       b"vvidx-end\t999999\tdeadbeefdeadbeef\n",
                       b"vvidx-end\tabc\tzz\n",
                       b"vvidx-end\n",
                       b"vvidx-end\t%d\t%016x\n" % (len(orig[:end + 1]) - 1,
                                                    _fnv(orig[:end + 1]))]
            done = 0
            for i, m in enumerate(tampers):
                done += 1
                if not damaged(orig[:end + 1] + m, probes[i % len(probes)],
                               "footer:" + m[:24].decode(errors="replace")):
                    break
            print(f"(d) footer tamper: {done} of {len(tampers)} -> "
                  f"{'DIVERGED' if fails else 'clean'}")

        obs = ", ".join(f"{' '.join(k)}={v}" for k, v in sorted(seen.items()))
        print(f"    damage instances observed per probe: {obs}")

        if fails:
            for lbl, probe, got, want in fails[:5]:
                print(f"FAIL {lbl} probe={' '.join(probe)}")
                print(f"     got:  {got}")
                print(f"     want: {want}")
            return 1
        print("ALL PASS (cache torture: no damaged cache produced a wrong answer)")
        return 0
    finally:
        _cleanup(vault, HOME, JR)


if __name__ == "__main__":
    sys.exit(main())
