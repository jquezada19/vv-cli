#!/usr/bin/env python3
"""Generate the golden vectors: what the python engine answers for every case
in cases.py, and how the native entry serves that case today.

    python3 tests/golden/generate.py            # rewrite tests/golden/*.jsonl
    python3 tests/golden/generate.py --check    # exit 1 if a checked-in file differs

Each case runs from a fresh copy of the frozen fixture under an explicit
environment (nothing inherited), twice:

  oracle  `python3 src/vv.py` with VV_ENGINE=python — never through the binary,
          so no recorded answer came from native code.
  native  the built binary with VV_PY_ENTRY pointed at sentinel_entry.py,
          which records what it was handed in a marker file. The marker, not
          the output, says a hand-off happened; a clean hand-off also forwards
          the caller's argv and stdin unchanged, prints nothing of its own and
          leaves the vault alone. Anything else is an error here, never a
          recorded answer.

`served` is what was OBSERVED: `handoff`, or `native` when the binary's own
answer equals the oracle's in stdout, stderr, exit code and resulting files. A
native answer that differs is a pre-existing divergence between the engines: it
is written to known-divergences.jsonl with both answers, and repairing it is a
deliberate edit to that file.
"""
import base64, hashlib, json, os, re, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
VV = os.path.join(REPO, "src", "vv.py")
VRUST = os.path.join(REPO, "vrust", "target", "release", "vrust")
SENTINEL = os.path.join(HERE, "sentinel_entry.py")
DIVERGENCES = "known-divergences.jsonl"
TIMEOUT = 120

sys.path.insert(0, HERE)
import fixture                      # noqa: E402
from cases import CASES, ORACLE_CRASHES   # noqa: E402


def field(b):
    """Bytes as a JSON value: text when valid UTF-8, else base64."""
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return {"b64": base64.b64encode(b).decode("ascii")}


def unfield(v):
    return base64.b64decode(v["b64"]) if isinstance(v, dict) else v.encode("utf-8")


def snapshot(root):
    """Every file under the vault, dot-directories included (.trash is where a
    removal lands). A symlink is recorded as its target, never followed."""
    out = {}
    for d, dirs, names in os.walk(root):
        dirs.sort()
        for n in sorted(names + [x for x in dirs if os.path.islink(os.path.join(d, x))]):
            p = os.path.join(d, n)
            rel = os.path.relpath(p, root)
            if os.path.islink(p):
                out[rel] = b"symlink -> " + os.fsencode(os.readlink(p))
            elif os.path.isfile(p):
                with open(p, "rb") as f:
                    out[rel] = f.read()
        dirs[:] = [x for x in dirs if not os.path.islink(os.path.join(d, x))]
    return out


class Sandbox:
    """One fresh fixture copy plus the directories that must sit outside it."""
    def __init__(self):
        # realpath: on macOS the temp root is a symlink, and python prints
        # some paths resolved and some as given — one spelling keeps `{VAULT}`
        # substitution exact
        self.base = os.path.realpath(tempfile.mkdtemp(prefix="vv-golden-"))
        self.vault = os.path.join(self.base, "vault")
        fixture.build(self.vault)
        self.before = snapshot(self.vault)
        self.home = os.path.join(self.base, "home")
        os.makedirs(self.home)

    def env(self, **extra):
        e = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": self.home, "TZ": "UTC", "LC_ALL": "C",
             "VV_VAULT": self.vault, "VV_INDEX_ROOT": os.path.join(self.base, "index"),
             "VV_JOURNAL_ROOT": os.path.join(self.base, "journals"), "VV_NO_METRICS": "1"}
        e.update(extra)
        return e

    def run(self, entry, argv, stdin, **env):
        # cwd is the sandbox: a relative write lands where it is thrown away
        r = subprocess.run(entry + argv, input=(stdin or "").encode("utf-8"), capture_output=True,
                           env=self.env(**env), timeout=TIMEOUT, cwd=self.base)
        sub = lambda b: b.replace(self.vault.encode(), b"{VAULT}")
        after = snapshot(self.vault)
        files = {p: field(after[p]) for p in sorted(after) if self.before.get(p) != after[p]}
        files.update({p: None for p in sorted(self.before) if p not in after})
        return {"exit": r.returncode, "stdout": field(sub(r.stdout)), "stderr": field(sub(r.stderr)), "files": files}

    def run_native(self, argv, stdin):
        """(result, handoff): the binary's result, and whether it handed off.
        Raises SystemExit on a hand-off that is not clean."""
        mark = os.path.join(self.base, "handoff.mark")
        got = self.run([VRUST], argv, stdin, VV_PY_ENTRY=SENTINEL, VV_GOLDEN_MARK=mark)
        if not os.path.exists(mark):
            return got, False
        seen = [json.loads(l) for l in open(mark, encoding="utf-8")]
        problems = []
        if len(seen) != 1:
            problems.append(f"python was started {len(seen)} times")
        # the patch hand-off rebuilds argv as `--vault <vault> patch …`; a
        # caller-side --vault pair is otherwise forwarded as given
        strip = lambda a: [x for i, x in enumerate(a) if x != "--vault" and (i == 0 or a[i - 1] != "--vault")]
        if strip(seen[0]["argv"]) != strip(argv):
            problems.append(f"argv was not forwarded as given: {seen[0]['argv']}")
        if seen[0]["stdin_sha256"] != hashlib.sha256((stdin or "").encode("utf-8")).hexdigest():
            problems.append("stdin was not forwarded as given")
        if os.path.realpath(seen[0]["vault"] or self.vault) != self.vault:
            problems.append(f"the vault changed on the way: {seen[0]['vault']}")
        if got["exit"] != 42 or got["stderr"] != "HANDOFF\n" or got["stdout"] != "":
            problems.append(f"the native entry printed or exited on its own: {got['exit']} {got['stdout']!r} {got['stderr']!r}")
        if got["files"]:
            problems.append(f"the vault was changed before the hand-off: {sorted(got['files'])}")
        if problems:
            raise SystemExit("FAIL dirty hand-off for " + " ".join(argv) + ": " + "; ".join(problems))
        return got, True

    def close(self):
        shutil.rmtree(self.base, True)


_sha_cache = {}
def resolve_placeholders(argv):
    def sha8(m):
        note, sec = m.group(1), m.group(2)
        if (note, sec) not in _sha_cache:
            sb = Sandbox()
            try:
                r = sb.run([sys.executable, VV], ["outline", note], None, VV_ENGINE="python")
            finally:
                sb.close()
            if r["exit"] != 0:
                raise SystemExit(f"generate: outline {note} failed: {r}")
            rows = [l.split("\t") for l in r["stdout"].splitlines()]
            hit = [c for c in rows if c and (c[0] == sec or (len(c) > 2 and c[2] == sec))]
            if len(hit) != 1:
                raise SystemExit(f"generate: cannot resolve sha8 of {note} {sec}: {r}")
            _sha_cache[(note, sec)] = hit[0][-1]
        return _sha_cache[(note, sec)]
    return [re.sub(r"\{sha8 ([^ }]+) ([^}]+)\}", sha8, a) for a in argv]


def record(cmd, cid, argv, stdin):
    argv = resolve_placeholders(argv)
    sb = Sandbox()
    try:
        oracle = sb.run([sys.executable, VV], argv, stdin, VV_ENGINE="python")
    finally:
        sb.close()
    if "Traceback (most recent call last)" in str(oracle["stderr"]):
        raise SystemExit(f"FAIL {cmd}/{cid}: the python engine crashed on {argv} — a traceback is a defect, "
                         "not an answer to freeze; fix it or drop the case")
    sb = Sandbox()
    try:
        native, handed_off = sb.run_native(argv, stdin)
    finally:
        sb.close()
    rec = {"id": f"{cmd}/{cid}", "argv": argv, "stdin": stdin}
    if handed_off:
        return dict(rec, served="handoff", **oracle), None
    if native == oracle:
        return dict(rec, served="native", **oracle), None
    return dict(rec, served="divergent", **oracle), dict(rec, note="", oracle=oracle, native=native)


def dump(rows):
    return "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)


def generate():
    """{filename: text} for every golden file."""
    if not os.path.exists(VRUST):
        raise SystemExit("generate: vrust/target/release/vrust is not built — next: (cd vrust && cargo build --release)")
    notes = {}
    old = os.path.join(HERE, DIVERGENCES)
    if os.path.exists(old):
        for line in open(old, encoding="utf-8"):
            if line.strip():
                r = json.loads(line)
                notes[r["id"]] = r.get("note", "")
    files, divergent = {}, []
    for cmd, cases in CASES.items():
        seen, rows = set(), []
        for cid, argv, stdin in cases:
            if cid in seen:
                raise SystemExit(f"generate: duplicate case id {cmd}/{cid}")
            seen.add(cid)
            rec, div = record(cmd, cid, argv, stdin)
            rows.append(rec)
            if div:
                div["note"] = notes.get(div["id"], "")
                divergent.append(div)
        files[f"{cmd}.jsonl"] = dump(rows)
    files[DIVERGENCES] = dump(divergent)
    for argv in ORACLE_CRASHES:
        sb = Sandbox()
        try:
            r = sb.run([sys.executable, VV], argv, None, VV_ENGINE="python")
        finally:
            sb.close()
        if "Traceback (most recent call last)" not in str(r["stderr"]):
            raise SystemExit(f"FAIL {argv} no longer crashes the python engine — next: move it from ORACLE_CRASHES into CASES")
    return files


def main():
    try:
        files = generate()
    except subprocess.TimeoutExpired as e:
        print(f"FAIL golden: a case did not finish within {TIMEOUT}s: {e.cmd}")
        sys.exit(1)
    if "--check" in sys.argv:
        bad = []
        for name, text in files.items():
            p = os.path.join(HERE, name)
            cur = open(p, encoding="utf-8").read() if os.path.exists(p) else None
            if cur != text:
                bad.append(name)
        stray = sorted(n for n in os.listdir(HERE) if n.endswith(".jsonl") and n not in files)
        for n in bad:
            print(f"FAIL tests/golden/{n} differs from what the engines answer now — next: python3 tests/golden/generate.py, then review the diff")
        for n in stray:
            print(f"FAIL tests/golden/{n} has no command in cases.py")
        n_cases = sum(len(c) for c in CASES.values())
        print(f"golden fresh: {'FAIL' if bad or stray else 'ALL PASS'} ({n_cases} cases, {len(files)} files)")
        sys.exit(1 if bad or stray else 0)
    for name, text in files.items():
        with open(os.path.join(HERE, name), "w", encoding="utf-8") as f:
            f.write(text)
    counts = {}
    for name, text in files.items():
        if name == DIVERGENCES:
            continue
        for line in text.splitlines():
            s = json.loads(line)["served"]
            counts[s] = counts.get(s, 0) + 1
    print("wrote", len(files), "files:", ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))


if __name__ == "__main__":
    main()
