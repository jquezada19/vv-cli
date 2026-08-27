#!/usr/bin/env python3
"""ONE vv-invocation extractor, shared by every bench script, with a canary.

Two scripts used to carry their own copy of this logic. They drifted: when the
default entry point became the native `vv` binary, bench/vault_ops_replay.py
kept matching only `vv.py <verb>` and silently lost 47% of all operations (156
recovered where 294 exist) while still printing a confident operation mix. A
duplicated extractor is a duplicated blind spot, so there is now exactly one.

CANARY_CASES below is the SPEC, not a transcript of current behaviour. Each case
states something the extractor must do; `self_test()` runs them all before any
real sweep. Never regenerate the expectations from the extractor's own output --
that converts the control into a tautology and is precisely how the miss above
survived review.
"""
import os
import re
import shlex

# Both invocation forms. `vv.py` may be quoted; the bare form must not match a
# longer word (`vvtest`), a path segment (`/x/vv`), or an assignment (`VV=...`).
VV_RE = re.compile(
    r"""(?:(?P<py>vv\.py)["']?|(?<![\w./=-])(?P<nat>vv))"""
    r"""\s+(?:--vault\s+\S+\s+)?"""       # a --vault PAIR may sit before the verb
    r"""(?P<verb>[a-z][a-z-]*)""")

# Deliberately DUMBER than VV_RE: a high-recall lower bound used only to prove
# the real extractor is not silently missing whole classes of invocation. It
# over-counts on purpose -- if it ever under-counts relative to VV_RE the
# differential is worthless. Independent of argv parsing, which is where two of
# the four historical misses actually happened.
# Command POSITION, not just the token: prose like "vv is unchanged" or
# "...whether vv handled it" matched a bare token baseline and inflated it to
# 453 against 257 real invocations, which would have made the recall floor fire
# forever and then be "tuned down" -- the exact death Kimi's panel note predicts
# for floors. Anchoring to start-of-line / ; / && / | / $( keeps the baseline
# high-recall for real commands while dropping English.
LOOSE_RE = re.compile(
    r"""(?:^|[\n;|&(]|\$\(|`)\s*"""
    r"""(?:\w+=\S+\s+)*"""                  # leading VAR=val assignments
    r"""(?:(?:python3?|\S*/python3?)\s+)?"""  # optional interpreter prefix
    r"""["']?(?:\S*/)?(?:vv\.py|vv)["']?\s+"""
    r"""(?:-{1,2}\S+\s+\S+\s+)*[a-z][a-z-]*""", re.M)

READ_VERBS = {"read", "outline", "show", "head", "resolve", "search", "backlinks",
              "links", "orphans", "deadends", "board", "props", "tags", "impact"}
WRITE_VERBS = {"set", "unset", "append", "appendsec", "patch", "daily-append",
               "rename", "move", "new"}


def _verbs_from_source():
    """The verb list, READ OFF THE TOOL, so it cannot drift from it.

    A hand-maintained copy had silently fallen three verbs behind (lint, index,
    doctor), so every ledger quietly omitted those command families while
    printing a confident "operation mix" -- the same false-clean shape as the
    entry-point miss, one level up. Deriving it removes the drift instead of
    correcting it once.
    """
    src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "src", "vv_impl.py")
    try:
        text = open(src, encoding="utf-8").read()
        i = text.index("CMDS = {")
        block = text[i:text.index("\n}", i)]
        found = set(re.findall(r'"([a-z][a-z-]*)"\s*:', block))
    except (OSError, ValueError):
        found = set()
    return found


SOURCE_VERBS = _verbs_from_source()
KNOWN_VERBS = SOURCE_VERBS or (READ_VERBS | WRITE_VERBS)


def classify(verb):
    if verb in READ_VERBS:
        return "read"
    if verb in WRITE_VERBS:
        return "write"
    return "other"          # lint / index / doctor: counted, never dropped


def _argv_from(cmd, verb_start):
    """Recover argv starting AT THE VERB (not at the `vv` token: starting there
    makes argv[0] the binary name, and an `argv[0] != verb` guard then rejects
    every candidate -- a measured 100% false-negative)."""
    tail = cmd[verb_start:].split("\n")[0]
    tail = re.sub(r"\s\d?>>?\s*\S+", " ", tail)        # redirections first
    tail = re.split(r"\s(?:;|&&|\|\||\||#)\s", tail)[0].strip()
    try:
        argv = shlex.split(tail)
    except ValueError:
        return None
    out, skip = [], False
    for a in argv:
        if skip:
            skip = False
            continue
        if a == "--vault":
            skip = True
            continue
        if a in (">", ">>", ";", "&&", "|"):
            continue
        out.append(a.rstrip(";"))
    return out or None


def parse_invocations(cmd, known_only=True):
    """Every vv invocation in one shell command, as argv lists (argv[0] = verb)."""
    if not isinstance(cmd, str) or "vv" not in cmd:
        return []
    out = []
    for m in VV_RE.finditer(cmd):
        verb = m.group("verb")
        if known_only and verb not in KNOWN_VERBS:
            continue
        argv = _argv_from(cmd, m.start("verb"))
        if not argv or argv[0] != verb:
            continue
        out.append({"argv": argv, "verb": verb,
                    "entry": "python" if m.group("py") else "native",
                    "cls": classify(verb)})
    return out


def _argvs(cmd):
    return [o["argv"] for o in parse_invocations(cmd)]


def _entries(cmd):
    return [o["entry"] for o in parse_invocations(cmd)]


# --- THE SPEC ------------------------------------------------------------
# Each case asserts a REQUIREMENT. Written from what the extractor must do.
CANARY_CASES = [
    # the two entry points -- the exact pair whose drift caused the 47% miss
    ('python3 /r/src/vv.py outline "A Note.md"', [["outline", "A Note.md"]]),
    ('vv outline "A Note.md"',                   [["outline", "A Note.md"]]),
    # --vault PAIR is dropped, not just the flag
    ("vv --vault /some/path search foo",         [["search", "foo"]]),
    ("python3 src/vv.py --vault /p board Work",  [["board", "Work"]]),
    # redirections and shell operators terminate the argv
    ("vv backlinks A.md > /dev/null",            [["backlinks", "A.md"]]),
    ("vv backlinks A.md 2>/dev/null",            [["backlinks", "A.md"]]),
    ("vv tags | head -3",                        [["tags"]]),
    ("vv props type ; echo done",                [["props", "type"]]),
    # several invocations in one command line
    ("vv board Work && vv tags",                 [["board", "Work"], ["tags"]]),
    # NOT invocations -- these must contribute nothing
    ("which vv",                                 []),
    ("VV=/x/src/vv.py echo hi",                  []),
    ("./vvtest outline A.md",                    []),
    ("cat /some/vv/outline.txt",                 []),
    ("vv frobnicate A.md",                       []),      # unknown verb
    # verbs that a hand-maintained list had silently dropped
    ("vv lint --quick",                          [["lint", "--quick"]]),
    ("vv index",                                 [["index"]]),
    ("vv doctor --rollback",                     [["doctor", "--rollback"]]),
    ("echo 'no invocation here'",                []),
]
ENTRY_CASES = [
    ('python3 /r/src/vv.py outline A.md', ["python"]),
    ('vv outline A.md',                   ["native"]),
    ('python3 src/vv.py read A.md && vv read B.md', ["python", "native"]),
]


def _loose_dominates(cmd):
    """The baseline must never count FEWER than the strict extractor."""
    return len(LOOSE_RE.findall(cmd)) >= len(parse_invocations(cmd))


def self_test():
    """Run the canary. Import-time-cheap; call before any real sweep."""
    from sweepguard import run_canary, SweepError
    # the derived verb list must actually have been derived
    if not SOURCE_VERBS:
        raise SweepError("vvops: could not read CMDS from src/vv_impl.py — the verb "
                         "list silently fell back to a hand-maintained copy that has "
                         "drifted before. Fix the parse rather than trusting it.")
    missing = (READ_VERBS | WRITE_VERBS) - SOURCE_VERBS
    if missing:
        raise SweepError(f"vvops: classified verbs absent from the tool: {sorted(missing)} "
                         f"— the classification is stale, or the parse is wrong.")
    n = run_canary("vvops.parse_invocations", _argvs, CANARY_CASES)
    n += run_canary("vvops.entry_detection", _entries, ENTRY_CASES)
    # The recall differential is only meaningful while the loose baseline is a
    # genuine OVER-approximation. First draft under-counted (it missed
    # `/path/vv.py` and `vv --vault ... verb`), which silently made recall >100%
    # and turned the guard into decoration -- a check that cannot fail.
    n += run_canary("vvops.baseline_dominates", _loose_dominates,
                    [(c, True) for c, _ in CANARY_CASES] +
                    [(c, True) for c, _ in ENTRY_CASES])
    return n


if __name__ == "__main__":
    print(f"vvops canary: {self_test()} cases PASS")
