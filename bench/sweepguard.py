#!/usr/bin/env python3
"""Make a sweep fail LOUDLY instead of reporting a false-clean zero.

A sweep that scans a corpus and extracts matches has two ways to return zero:
the corpus genuinely has none, or the sweep is broken. They look identical in
the output, and the broken one reads as a reassuring negative result. Four
instances landed in this repo in a single day (2026-08-27):

  * a regex that matched only one of two CLI invocation forms -> "0 operations",
    read as "the tool is unused" (it was missing 47% of them)
  * an argv slice off by one token, so a guard rejected 100% of candidates
  * relative paths handed to xargs after a cwd reset -> 0 files seen, 3 greps
    all reporting 0 matches
  * a parity check that compared only header lines -> passed while 16 of 18
    real inputs diverged in the body it never looked at

Every one was caught by a positive control. None by reading the code. So the
control belongs INSIDE the sweep, running every time, not in a habit.

Two mechanisms, deliberately small:

  Funnel  — count each stage (corpus -> candidates -> parsed -> kept) and print
            them. A zero becomes ATTRIBUTABLE instead of merely final; "0 kept"
            with "0 files seen" is a broken sweep, "0 kept" with "1500 files
            seen" is a real negative.
  canary  — the extractor must recover known synthetic inputs BEFORE it is
            trusted on real ones. This is the only mechanism that catches a
            sweep whose corpus is healthy but whose PATTERN is wrong, which was
            three of the four cases above.

The canary's own failure mode is drift: it rots into a tautology if it is built
from the same assumption as the extractor. Write canary cases from the SPEC
("both invocation forms must be recovered"), never by pasting what the extractor
currently returns.
"""
import sys


def mark_bench(label="bench"):
    """Tag every vv invocation this process spawns as benchmark traffic.

    Sets VV_METRICS_SRC in os.environ so child processes inherit it, which is
    why it must run BEFORE any subprocess is launched. The pilot report then
    separates these rows by provenance instead of guessing from arrival rate.

    This covers the bench scripts. It CANNOT cover an ad-hoc measurement loop
    typed into a shell or a heredoc -- and that is exactly what produced the
    117,312 contaminating rows on 2026-08-27. The rate heuristic therefore stays
    as a backstop, and the pilot report shows how much machine-paced traffic
    arrived UNMARKED, so a forgotten mark is visible rather than silent.
    """
    import os
    os.environ["VV_METRICS_SRC"] = label
    return label


class SweepError(RuntimeError):
    """A sweep could not establish that it was working. Never a normal result."""


class Funnel:
    """Stage counters for one sweep. Print it and a zero is attributable."""

    def __init__(self, name, *stages):
        self.name = name
        self.stages = list(stages)
        self.counts = {s: 0 for s in stages}

    def bump(self, stage, n=1):
        if stage not in self.counts:
            raise SweepError(f"{self.name}: unknown funnel stage {stage!r}")
        self.counts[stage] += n

    def require(self, stage, minimum=1):
        """Abort when a stage is below its floor -- the sweep cannot report."""
        got = self.counts[stage]
        if got < minimum:
            raise SweepError(
                f"{self.name}: stage {stage!r} produced {got} (floor {minimum}). "
                f"The SWEEP is broken, not the corpus. Funnel: {self.render()}")
        return got

    def render(self):
        return " -> ".join(f"{s}={self.counts[s]}" for s in self.stages)

    def report(self, out=sys.stdout):
        print(f"  funnel[{self.name}]: {self.render()}", file=out)
        zeros = [s for s in self.stages if self.counts[s] == 0]
        if zeros:
            print(f"  NOTE: zero at {', '.join(zeros)} — a zero HERE means the sweep "
                  f"found nothing to work with, not that the corpus is clean.", file=out)


class RejectLog:
    """Why candidates were discarded. A UNANIMOUS reject reason is a parse bug.

    If every candidate dies on one guard, that is not "0 results" -- it is an
    exception the sweep failed to raise. Exactly incident #2: an `argv[0] != verb`
    guard rejected 100% of candidates and the script reported zero operations.
    """

    def __init__(self, name):
        self.name = name
        self.reasons = {}
        self.kept = 0

    def keep(self):
        self.kept += 1

    def reject(self, reason, sample=None):
        r = self.reasons.setdefault(reason, {"n": 0, "sample": None})
        r["n"] += 1
        if r["sample"] is None and sample is not None:
            r["sample"] = str(sample)[:160]

    def require_not_unanimous(self, threshold=0.98, min_candidates=20):
        total = self.kept + sum(v["n"] for v in self.reasons.values())
        if not self.reasons:
            return
        # Nothing kept at all is the strongest possible signal, and it does not
        # care how the rejections are spread or how few there were: 19 unanimous
        # rejects, or 20 split evenly across two reasons, both slipped past the
        # threshold test below until this was added.
        if self.kept == 0:
            top = sorted(self.reasons.items(), key=lambda kv: -kv[1]["n"])
            raise SweepError(
                f"{self.name}: 0 candidates kept out of {total}. A sweep that keeps "
                f"NOTHING is broken, not empty. reasons=" +
                ", ".join(f"{r}x{i['n']}" for r, i in top[:3]) +
                f"\n    sample: {top[0][1]['sample']!r}")
        if total < min_candidates:
            return
        top, info = max(self.reasons.items(), key=lambda kv: kv[1]["n"])
        if info["n"] / total >= threshold:
            raise SweepError(
                f"{self.name}: {info['n']}/{total} candidates rejected for ONE reason "
                f"({top!r}) — that is a parse bug, not an empty corpus.\n"
                f"    sample: {info['sample']!r}")

    def report(self, top=4, out=sys.stdout):
        if not self.reasons:
            return
        print(f"  rejects[{self.name}]: kept={self.kept}", file=out)
        for reason, info in sorted(self.reasons.items(), key=lambda kv: -kv[1]["n"])[:top]:
            print(f"    {info['n']:6d}  {reason}   e.g. {info['sample']!r}", file=out)


def require_recall(name, extracted, baseline, min_recall=0.8, baseline_desc=""):
    """Differential against a DUMB high-recall lower bound (e.g. a plain grep).

    A floor of >=1 cannot catch a PARTIAL miss: the extractor that lost 47% of
    operations still returned 156, comfortably non-zero. Only a comparison
    against an independent over-approximation catches that. The baseline must
    not share the broken plumbing -- if both walk the same empty file list, 0/0
    agrees perfectly and proves nothing.

    The default floor is 0.8, NOT 0.5: the real incident recovered 156 of 294
    (53%), which sails past a half floor. A floor calibrated below the miss it
    was written for is decoration. Raise it, never lower it, and if a baseline
    genuinely over-counts, narrow the BASELINE rather than dropping the floor.
    """
    if baseline <= 0:
        # 0/0 is not agreement, it is two broken things agreeing. If the
        # high-recall baseline found nothing either, the differential
        # established NOTHING and must not report success (flagged by an
        # adversarial review of this very guard, 2026-08-27).
        if extracted == 0:
            raise SweepError(
                f"{name}: extractor found 0 AND the high-recall baseline found 0. "
                f"That is not a clean corpus — it is a differential that proved "
                f"nothing, most likely because both share broken plumbing.")
        return                      # baseline under-counted; the dominance canary covers it
    recall = extracted / baseline
    if recall < min_recall:
        raise SweepError(
            f"{name}: extracted {extracted} but a high-recall baseline says >= {baseline} "
            f"({recall:.0%} recall, floor {min_recall:.0%}). The EXTRACTOR is missing "
            f"cases, not the corpus. baseline = {baseline_desc or 'independent count'}")
    return recall


def preflight_corpus(name, paths):
    """Class-3 killer: prove the corpus handle resolves BEFORE sweeping it.

    A relative path list read after a cwd change yields zero files and three
    greps all reporting "0 matches". Absolute + readable, checked here, makes
    that unrepresentable rather than merely detectable.
    """
    import os
    if not paths:
        raise SweepError(f"{name}: corpus is empty before the sweep even started")
    bad = [p for p in paths if not os.path.isabs(p)]
    if bad:
        raise SweepError(
            f"{name}: {len(bad)} corpus path(s) are RELATIVE (e.g. {bad[0]!r}). "
            f"They resolve against whatever cwd the sweep inherits — use absolute paths.")
    unreadable = [p for p in paths if not os.access(p, os.R_OK)]
    if unreadable:
        raise SweepError(
            f"{name}: {len(unreadable)} corpus path(s) unreadable from this cwd "
            f"(e.g. {unreadable[0]!r})")
    return len(paths)


def run_canary(name, fn, cases):
    """Positive control: `fn` must reproduce every (input, expected) case.

    Raises SweepError naming the first failure. Call this BEFORE the real sweep,
    every run -- a control that only runs when someone remembers is the control
    that was missing all four times.
    """
    for i, (inp, expected) in enumerate(cases):
        try:
            got = fn(inp)
        except Exception as e:                                   # noqa: BLE001
            raise SweepError(f"{name}: canary[{i}] raised {e!r} on {inp!r}") from e
        if got != expected:
            raise SweepError(
                f"{name}: canary[{i}] FAILED — the extractor is broken before it "
                f"touched real data.\n    input:    {inp!r}\n"
                f"    expected: {expected!r}\n    got:      {got!r}")
    return len(cases)
