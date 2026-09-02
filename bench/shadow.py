#!/usr/bin/env python3
"""Run one vault READ both ways -- vv and the old-fashioned way -- and compare.

Pilot protocol upgrade (Jeff, 2026-08-27): every vault read during the shadow
window runs BOTH ways so the 2026-09-02 checkpoint can close on measured quality
and speed rather than on a handful of paired tasks.

  stdout  <- vv's output, verbatim. This is the answer the caller consumes.
  stderr  <- a one-line comparison.
  sink    <- ~/.claude/metrics/vv-shadow.jsonl, one record per read.

WRITES ARE REFUSED, by design. Two tools writing the same note is how you get
divergence, and the pilot note already says "reads only -- never pair a write".
That rule is enforced here rather than remembered.

QUALITY IS NOT BYTE EQUALITY. vv and grep return different SHAPES for the same
question, so comparing raw output would score formatting, not correctness. Each
verb therefore has a normaliser reducing both outputs to the same answer (a set
of note paths, or normalised text), and the comparison is over that.

A disagreement is NOT automatically vv being wrong -- for most of these
questions grep is the weaker instrument (it cannot resolve a wikilink to a path,
does not skip code fences, and happily matches frontmatter). Disagreements are
therefore RECORDED IN FULL for adjudication, never auto-scored into a verdict.
"""
import json, os, re, subprocess, sys, time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VAULT = os.environ.get("VV_VAULT") or os.path.expanduser("~/Documents/Obsidian Vault")
VV = os.path.join(REPO, "vrust/target/release/vrust")
SINK = os.environ.get("VV_SHADOW_SINK") or os.path.expanduser("~/.claude/metrics/vv-shadow.jsonl")  # override: tests only

# Bump whenever a NORMALISER, a legacy analog, or the comparison logic changes.
# Records made by an earlier version measured a different instrument and must
# not be pooled with these -- v1's normalisers scooped vv's content hash into
# the answer set, shredded multi-word link targets on whitespace, compared
# vv's ranked top-5 against every grep hit, and let `find`/`grep` walk
# .claude/worktrees (whole copies of the vault). Every one of those produced a
# confident "disagreement" the harness had manufactured.
HARNESS_VERSION = 4

WRITE_VERBS = {"set", "unset", "append", "appendsec", "patch", "daily-append",
               "rename", "move", "new", "index", "doctor"}


# vv closes list output with a summary line — "(24 links)", "(1010 tags)",
# "(8 backlinks)". It is a count, not an answer, and every normaliser that
# forgot to drop it silently added tokens like "(1010" to the answer set and
# reported a superset that did not exist.
FOOTER = re.compile(r"^\(\d[\d,]*\s+[a-z ]+\)$", re.I)


def is_footer(line):
    return bool(FOOTER.match(line.strip()))


def sh(argv, shell=False):
    t = time.perf_counter()
    r = subprocess.run(argv, capture_output=True, text=True, cwd=VAULT, shell=shell)
    return (time.perf_counter() - t) * 1000, r.stdout, r.returncode


# --- normalisers: both sides reduced to the SAME answer ---------------------
def paths(text):
    """Vault-relative .md paths mentioned, as a set."""
    out = set()
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r"^==\s+(.*?)\s+\(score", line)      # vv search
        if m:
            line = m.group(1)
        line = line.split(":")[0].strip()                  # grep -n / -H prefixes
        if not line.endswith(".md"):
            continue
        if line.startswith(VAULT):
            line = os.path.relpath(line, VAULT)
        out.add(line.lstrip("./"))
    return out


def headings(text):
    """Heading TEXT from either shape.

    vv:   'H2\t##\tTitle\t1069B\t21140087'   (tab columns; the LAST is a hash)
    grep: '7:## Title'                        (line-number prefix)
    Taking split("\t")[-1] scooped vv's content hash into the answer set and
    reported a difference the normaliser had manufactured.
    """
    out = set()
    for l in text.splitlines():
        l = l.strip()
        if not l or is_footer(l):
            continue
        if "\t" in l:                       # vv: title is the 3rd column
            parts = l.split("\t")
            l = parts[2] if len(parts) > 2 else parts[-1]
        else:                               # grep: strip 'NN:' then leading #s
            l = re.sub(r"^\d+:", "", l)
        out.add(re.sub(r"^[#\s]+", "", l).strip())
    return out


def linkset(text):
    """Link targets, LINE by line -- splitting on whitespace shreds any
    multi-word target ('New Note' became {'New','Note'})."""
    out = set()
    for l in text.splitlines():
        l = l.strip()
        if not l or is_footer(l):
            continue
        l = l.strip("[]")
        out.add(l.split("|")[0].split("#")[0].strip())
    return out - {""}


def words(text):
    return re.sub(r"\s+", " ", text).strip()


def tagset(text):
    """Tag names from either shape: vv prints one tag per line; grep prints the
    raw `tags: [a, b, c]` frontmatter line."""
    out = set()
    for l in text.splitlines():
        l = l.strip()
        if not l or is_footer(l):
            continue
        body = l.split(":", 1)[1] if l.lower().startswith("tags:") else l
        for w in re.split(r"[\s,]+", body):
            w = w.strip("[]\"'#").strip()
            if w:
                out.add(w.lower())
    return out


def valueset(text):
    """Frontmatter VALUES. vv emits `count<TAB>value`; grep emits `key: value`."""
    out = set()
    for l in text.splitlines():
        l = l.strip()
        if not l or is_footer(l):
            continue
        if "\t" in l:                       # vv: count TAB value
            out.add(l.split("\t")[-1].strip().strip("\"'").lower())
        elif ":" in l:                      # grep: key: value
            out.add(l.split(":", 1)[1].strip().strip("\"'").lower())
    return out - {""}


def noteset(text):
    """Note NAMES. vv board emits `status<TAB>type<TAB>name`; grep emits paths."""
    out = set()
    for l in text.splitlines():
        l = l.strip()
        if not l or is_footer(l):
            continue
        if "\t" in l:
            out.add(l.split("\t")[-1].strip())
        else:
            p = l.split(":")[0].strip()
            if p.endswith(".md"):
                out.add(os.path.basename(p)[:-3])
    return out - {""}


def resolve_l(args):
    # -not -path '*/.*': `find` descends .claude/worktrees, which holds entire
    # copies of the vault, so an unfiltered find "resolves" a note to five
    # different files. vv's walk skips dot-directories; the legacy side must
    # cover the same corpus or the comparison scores scope, not correctness.
    return ["find", VAULT, "-name",
            f"{os.path.basename(args[0]).removesuffix('.md')}.md",
            "-not", "-path", "*/.*"]


# verb -> (legacy argv builder, normaliser, needs_shell)
LEGACY = {
    "read":      (lambda a: ["cat", os.path.join(VAULT, a[0])], words, False),
    "outline":   (lambda a: ["grep", "-n", "^#", os.path.join(VAULT, a[0])], headings, False),
    "head":      (lambda a: ["awk", "NR==1&&$0!=\"---\"{exit} NR>1&&$0==\"---\"{exit} NR>1",
                             os.path.join(VAULT, a[0])],
                  lambda t: {l.split(":", 1)[0].strip() for l in t.splitlines()
                             if ":" in l and not l.startswith(" ")}, False),
    "resolve":   (resolve_l, paths, False),
    "search":    (lambda a: ["grep", "-rilF", "--include=*.md", "--exclude-dir=.*",
                             "--exclude-dir=Sandbox", "--exclude-dir=graphify-out",
                             a[0], VAULT], paths, False),
    "backlinks": (lambda a: ["grep", "-rlF", "--include=*.md", "--exclude-dir=.*",
                             f"[[{os.path.basename(a[0]).removesuffix('.md')}", VAULT], paths, False),
    "links":     (lambda a: ["grep", "-o", r"\[\[[^]]*\]\]", os.path.join(VAULT, a[0])],
                  linkset, False),
    # vv aggregates; grep lists raw lines. Normalise BOTH to the set of VALUES,
    # or the comparison scores output shape. (v2 ran `paths` over board output
    # that contains no paths at all, so vv's side was always the empty set.)
    "tags":      (lambda a: ["grep", "-rh", "--include=*.md", "--exclude-dir=.*",
                             "^tags:", VAULT], tagset, False),
    "props":     (lambda a: ["grep", "-rh", "--include=*.md", "--exclude-dir=.*",
                             f"^{a[0]}:", VAULT], valueset, False),
    "board":     (lambda a: ["grep", "-rl", "--include=*.md", "--exclude-dir=.*",
                             "^type:", os.path.join(VAULT, a[0])], noteset, False),
    "deadends":  (None, paths, False),      # no honest one-liner; vv-only, recorded as such
    "orphans":   (None, paths, False),
    "impact":    (None, paths, False),
    "show":      (lambda a: ["cat", os.path.join(VAULT, a[0])], words, False),
}


def adjudicate(argv):
    """Record who was RIGHT on a disagreement, with the evidence.

    Agreement rate alone cannot close the checkpoint: for most of these
    questions grep is the weaker instrument, so a disagreement is usually the
    old way being wrong. That has to be established per case and written down,
    not assumed in either direction.
    """
    # `-- <args...>` scopes the ruling to one (op, args) case. Without it the
    # ruling is op-level, which the report honours but labels as reused: the
    # 2026-09-02 read-out found op-keyed rulings silently covering every case
    # of that op, so a second disagreement never looked unadjudicated.
    case_args = None
    if "--" in argv:
        i = argv.index("--")
        argv, case_args = argv[:i], argv[i + 1:]
    if len(argv) < 3:
        sys.exit("usage: shadow.py --adjudicate <op> <vv-correct|legacy-correct|"
                 "both-defensible|unresolved> <reason...> [-- <args...>]")
    op, who, reason = argv[0], argv[1], " ".join(argv[2:])
    if who not in ("vv-correct", "legacy-correct", "both-defensible", "unresolved"):
        sys.exit(f"shadow: unknown adjudication {who!r}")
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": "adjudication",
           "op": op, "who": who, "reason": reason}
    if case_args is not None:
        rec["args"] = case_args
    os.makedirs(os.path.dirname(SINK), exist_ok=True)
    with open(SINK, "a") as f:
        f.write(json.dumps(rec) + "\n")
    print(f"adjudicated {op}: {who} — {reason}")
    return 0


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: shadow.py <read-verb> [args...]   (writes are refused)\n"
                 "       shadow.py --adjudicate <op> <who> <reason>")
    if sys.argv[1] == "--adjudicate":
        return adjudicate(sys.argv[2:])
    verb, args = sys.argv[1], sys.argv[2:]

    if verb in WRITE_VERBS:
        sys.exit(f"shadow: '{verb}' MUTATES. Reads are paired; writes are not — two "
                 f"tools writing one note is how divergence starts. Run it once "
                 f"through vv directly.")
    if verb not in LEGACY:
        sys.exit(f"shadow: no legacy equivalent defined for '{verb}'. Add one to "
                 f"LEGACY, or run vv directly and note why in the friction log.")

    build, norm, shell = LEGACY[verb]

    vv_ms, vv_out, vv_rc = sh([VV, verb] + list(args))
    quality_out = vv_out
    wide = None
    if verb == "search" and "--k" not in args:
        # vv ranks and truncates to --k (default 5); grep returns EVERY hit, so
        # comparing them scores the truncation, not the retrieval. Widen for the
        # ANSWER-SET comparison only -- ms and bytes above stay those of the
        # invocation real usage actually issues.
        _, quality_out, _ = sh([VV, verb] + list(args) + ["--k", "500"])
        wide = "quality compared at --k 500; ms/bytes are the default --k 5 call"
    sys.stdout.write(vv_out)                       # the answer the caller uses
    sys.stdout.flush()

    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "hv": HARNESS_VERSION,
           "op": verb, "args": args,
           "vv_argv_note": wide,
           "vv_ms": round(vv_ms, 1), "vv_bytes": len(vv_out.encode()), "vv_exit": vv_rc}

    if build is None:
        rec.update({"legacy": "none", "verdict": "vv-only",
                    "note": "no honest shell one-liner for this question"})
        print(f"shadow[{verb}]: vv-only ({vv_ms:.0f} ms, {rec['vv_bytes']} B) — "
              f"no legacy equivalent", file=sys.stderr)
    else:
        try:
            lg_ms, lg_out, lg_rc = sh(build(args), shell=shell)
        except Exception as e:                                        # noqa: BLE001
            lg_ms, lg_out, lg_rc = 0.0, "", -1
            rec["legacy_error"] = str(e)[:120]
        a, b = norm(quality_out), norm(lg_out)
        if lg_rc != 0:
            # The legacy one-liner FAILED. Whatever it printed is not an answer,
            # so comparing it scores the harness, not the tool (3 pairs in the
            # pilot week landed as "vv-superset" with legacy_exit=2). Recorded
            # so the report can count it; never a disagreement.
            verdict = "legacy-error"
        elif a == b:
            verdict = "match"
        elif isinstance(a, set) and isinstance(b, set):
            # subset/superset is only meaningful for ANSWER SETS. Applying `>`
            # to the text normalisers compared strings LEXICOGRAPHICALLY and
            # emitted confident "vv-superset" verdicts with an empty diff --
            # a false reading manufactured by the measuring instrument.
            verdict = ("vv-superset" if a > b else
                       "legacy-superset" if b > a else "differ")
        else:
            verdict = "differ"
        rec.update({"legacy_ms": round(lg_ms, 1), "legacy_bytes": len(lg_out.encode()),
                    "legacy_exit": lg_rc, "verdict": verdict,
                    "vv_only": (sorted(a - b)[:12]
                                if isinstance(a, set) and isinstance(b, set) else None),
                    "legacy_only": (sorted(b - a)[:12]
                                    if isinstance(a, set) and isinstance(b, set) else None),
                    "n_vv": len(a) if isinstance(a, set) else None,
                    "n_legacy": len(b) if isinstance(b, set) else None})
        ratio = (lg_ms / vv_ms) if vv_ms else 0
        bratio = (rec["legacy_bytes"] / rec["vv_bytes"]) if rec["vv_bytes"] else 0
        print(f"shadow[{verb}]: {verdict} | vv {vv_ms:.0f}ms/{rec['vv_bytes']}B  "
              f"legacy {lg_ms:.0f}ms/{rec['legacy_bytes']}B  "
              f"({ratio:.1f}x slower, {bratio:.1f}x bytes)", file=sys.stderr)
        if verdict != "match":
            print(f"  vv-only={rec['vv_only']}\n  legacy-only={rec['legacy_only']}",
                  file=sys.stderr)

    try:
        os.makedirs(os.path.dirname(SINK), exist_ok=True)
        with open(SINK, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass
    return vv_rc


if __name__ == "__main__":
    sys.exit(main())
