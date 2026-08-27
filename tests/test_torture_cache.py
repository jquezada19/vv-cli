#!/usr/bin/env python3
"""Torture: a damaged vvidx cache must NEVER produce a wrong answer.

Damage models: (a) truncation at every byte offset, (b) single-byte flips at
random offsets, (c) whole-record deletions, (d) footer tampering.
Oracle = python engine (VV_NO_INDEX=1). Native must match it byte-for-byte.
"""
import hashlib, os, random, shutil, subprocess, sys, tempfile

REPO = os.path.expanduser("~/Desktop/Git/vv-cli")
VR = os.path.join(REPO, "vrust/target/release/vrust")
VV = os.path.join(REPO, "src/vv.py")
rng = random.Random(int(os.environ.get("SEED", "4242")))

def _fnv(b: bytes) -> int:
    """Mirror of cache.rs fnv1a64 — used only to forge a VALID footer for the
    blindness control."""
    M = 0xFFFFFFFFFFFFFFFF
    h = (0x9e3779b97f4a7c15 ^ len(b)) & M
    n = len(b) - len(b) % 8
    for i in range(0, n, 8):
        h ^= (int.from_bytes(b[i:i+8], "little") * 0xff51afd7ed558ccd) & M
        h = ((h << 31 | h >> 33) & M) * 0xc4ceb9fe1a85ec53 & M
    for x in b[n:]:
        h = ((h ^ x) * 0x100000001b3) & M
    return h

def cache_of(v):
    k = hashlib.sha256(os.path.realpath(v).encode()).hexdigest()[:16]
    return os.path.expanduser(f"~/.cache/vv/index/{k}.vvidx")

def run(cmd, vault, py=False):
    env = dict(os.environ, VV_NO_METRICS="1", VV_VAULT=vault)
    if py:
        env["VV_NO_INDEX"] = "1"
        return subprocess.run([sys.executable, VV] + cmd, capture_output=True, env=env)
    return subprocess.run([VR] + cmd, capture_output=True, env=env)

def build(vault, n=40):
    names = [f"Note {i}" for i in range(n)] + ["My Spaced Note", "Héllo Wörld", "日本語ノート"]
    for i, nm in enumerate(names):
        tgts = rng.sample(names, k=min(4, len(names)))
        body = "\n".join(f"link to [[{t}]]" for t in tgts if t != nm)
        extra = f"\nmd link [{names[(i+1)%len(names)]}]({names[(i+1)%len(names)].replace(' ', '%20')}.md)\n"
        open(os.path.join(vault, nm + ".md"), "w").write(
            f"---\ntype: test\n---\n\n# {nm}\n\n{body}\n{extra}")
    return names

def main():
    vault = tempfile.mkdtemp(prefix="vv-torture-")
    names = build(vault, n=int(os.environ.get("TORTURE_NOTES", "18")))
    probes = [["backlinks", n] for n in rng.sample(names, 6)] + \
             [["links", n] for n in rng.sample(names, 4)] + [["orphans"]]
    truth = {}
    for p in probes:
        r = run(p, vault, py=True)
        truth[tuple(p)] = (r.returncode, r.stdout)
    # warm the cache
    run(["backlinks", names[0]], vault)
    cp = cache_of(vault)
    if not os.path.exists(cp):
        print("FAIL: no cache produced"); return 1
    orig = open(cp, "rb").read()
    print(f"cache {len(orig)} bytes; {len(probes)} probes; vault {len(names)} notes")

    bad = []
    def verify(label):
        for p in probes:
            r = run(p, vault)
            if (r.returncode, r.stdout) != truth[tuple(p)]:
                bad.append((label, p, r.stdout[:120], truth[tuple(p)][1][:120]))
                return

    # (a) truncation at EVERY offset
    for off in range(len(orig)):
        open(cp, "wb").write(orig[:off])
        verify(f"truncate@{off}")
        if bad: break
    print(f"(a) truncation: {len(orig)} offsets -> {'FAIL' if bad else 'clean'}")

    # (b) single-byte flips
    if not bad:
        for _ in range(400):
            off = rng.randrange(len(orig))
            b = bytearray(orig); b[off] ^= 1 << rng.randrange(7)
            open(cp, "wb").write(bytes(b))
            verify(f"flip@{off}")
            if bad: break
        print(f"(b) byte flips: 400 -> {'FAIL' if bad else 'clean'}")

    # (c) whole-record deletions
    if not bad:
        lines = orig.split(b"\n")
        for _ in range(200):
            i = rng.randrange(len(lines))
            open(cp, "wb").write(b"\n".join(lines[:i] + lines[i+1:]))
            verify(f"delline@{i}")
            if bad: break
        print(f"(c) record deletion: 200 -> {'FAIL' if bad else 'clean'}")

    # (d) footer tampering: rewrite the length/checksum to self-consistent-looking junk
    if not bad:
        end = orig.rfind(b"\nvvidx-end\t")
        for mangled in [b"vvidx-end\t0\t0000000000000000\n",
                        b"vvidx-end\t999999\tdeadbeefdeadbeef\n",
                        b"vvidx-end\tabc\tzz\n", b"vvidx-end\n", b""]:
            open(cp, "wb").write(orig[:end+1] + mangled)
            verify("footer:" + mangled[:24].decode(errors="replace"))
            if bad: break
        print(f"(d) footer tamper: 5 -> {'FAIL' if bad else 'clean'}")

    # positive control: an INTACT cache must still be used and correct
    open(cp, "wb").write(orig)
    verify("intact")

    # positive control #2 — the suite must not be blind. Drop one L row and
    # RE-STAMP a valid footer: the footer check now passes, so a live cache must
    # serve the damage and answer WRONG. If nothing diverges, the cache is not
    # being consulted and every "clean" above is meaningless.
    if not bad:
        end2 = orig.rfind(b"\nvvidx-end\t")
        lines = orig[:end2 + 1].split(b"\n")
        li = next((i for i, l in enumerate(lines) if l.startswith(b"L\t")), None)
        if li is None:
            print("FAIL control: cache holds no L rows"); return 1
        nb = b"\n".join(lines[:li] + lines[li + 1:])
        open(cp, "wb").write(nb + b"vvidx-end\t%d\t%016x\n" % (len(nb), _fnv(nb)))
        served = any(run(["backlinks", n], vault).stdout
                     != run(["backlinks", n], vault, py=True).stdout for n in names)
        print(f"(e) control (re-stamped damage served): {'yes' if served else 'NO'}")
        if not served:
            print("FAIL control: a checksum-valid corruption changed nothing -> suite is blind")
            shutil.rmtree(vault, ignore_errors=True); return 1
    shutil.rmtree(vault, ignore_errors=True)
    if bad:
        for b in bad[:5]:
            print("  FAIL", b[0], b[1], "\n    got:", b[2], "\n    want:", b[3])
        return 1
    print("ALL PASS (cache torture: no damaged cache produced a wrong answer)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
