# Sweep guards: making a false-clean zero impossible to report

A script that scans a corpus and extracts matches has two ways to return zero:
the corpus genuinely has none, or the sweep is broken. They print identically,
and the broken one reads as a reassuring negative result.

Six instances landed in this repo on 2026-08-27. Every one was caught by a
positive control; none by reading the code. So the control now lives inside the
sweep and runs every time.

## The instances

| # | What broke | What it reported |
|---|---|---|
| 1 | Extractor matched only `vv.py <verb>`, missing the native `vv <verb>` that had become the default entry | "156 operations" — a confident mix built on 53% of the data |
| 2 | argv sliced from the `vv` token, so `argv[0]` was the binary name and an `argv[0] != verb` guard rejected everything | "0 operations" |
| 3 | Relative paths handed to `xargs` after a cwd reset | "0 matches" from three separate greps |
| 4 | Parity test compared only the `==` header lines, waiving the body | Green, while 16 of 18 real inputs diverged in the body |
| 5 | Verb list hand-maintained, three verbs behind the tool (`lint`, `index`, `doctor`) | An "operation mix" silently missing whole command families |
| 6 | `pilot_report.py` swallowed `OSError` | "no vault ops logged" — indistinguishable from a deleted log file, on the script that decides the pilot's fate |

## The mechanisms

`bench/sweepguard.py`, used by every bench script:

- **`Funnel`** — stage counters (`files -> tool_uses -> vv_ops`). Makes a zero
  *attributable*: 0 ops with 15,000 tool-uses is a real negative; 0 ops with 0
  files is a broken sweep. Report-only by itself; `require()` is what fails.
- **`run_canary`** — the extractor must reproduce known cases *before* it is
  trusted on real data. The only mechanism that catches a healthy corpus with a
  wrong pattern, which was three of the six.
- **`RejectLog`** — why candidates were discarded. **Keeping nothing is an error,
  not a zero result**, and a single reason accounting for ~100% is a parse bug.
- **`require_recall`** — differential against a dumb high-recall baseline. A
  `>= 1` floor cannot catch a *partial* miss: instance 1 still returned 156.
- **`preflight_corpus`** — absolute + readable, checked before sweeping. Makes
  instance 3 unrepresentable rather than merely detectable.

`bench/vvops.py` holds the **one** vv-invocation extractor. Two scripts used to
carry their own copy; they drifted, and that drift *is* instance 1.

## Guards that could not fail

Every mechanism above has its own failure mode, and an adversarial review of
these guards found four real ones — each confirmed by running it against the
defect it claimed to catch:

- **A floor calibrated below the miss it was written for.** `require_recall`
  defaulted to 0.5; the real miss recovered 53% and sailed through. Now 0.8.
  Raise it, never lower it; if a baseline over-counts, narrow the *baseline*.
- **A baseline that under-counts the extractor it bounds.** The first
  high-recall regex missed `/path/vv.py` and `vv --vault ... verb`, making
  recall >100% — permanently green. Now pinned by a `baseline_dominates` canary
  over every case.
- **`0/0` treated as agreement.** If the baseline also found nothing, the
  differential established *nothing*; it now raises instead of returning.
- **A guard that cannot fail the process.** `pilot_report.py` printed a loud
  `ABORT` and exited 0, because `main()` was called instead of
  `sys.exit(main())`.

`tests/test_sweepguard.py` is the test-the-test: it feeds each guard the real
historical defect and asserts it raises, including a subprocess mutation that
reintroduces instance 1 into the shared extractor. If a guard is ever weakened,
one of those goes red.

## Canary hygiene

Write canary cases from the **spec**, never from what the extractor currently
returns — that converts the control into a tautology. A canary written in the
old `vv.py` form would have passed happily while the native form went uncounted.
The cases are the requirement ("both entry forms must be recovered"), and they
caught two bugs in this module's own first draft before it saw real data.

## Contamination: the false-clean inverted

`pilot_report.py` reported `adoption: 100%` over 118,726 operations — 99% of
which arrived in 104 minutes at >=120 ops/min, i.e. our own benchmark loops. A
number that looks like a triumph and measures the instrument is the same defect
wearing the opposite sign. Rate is the tell that needs no cooperation from the
logger: no interactive session sustains that. The report now separates the
populations and refuses to draw a conclusion when every row is machine-paced.

Its own failure mode is the fixed threshold: 119 ops/min of test traffic passes,
and a genuine 120-op burst is dropped. It is a heuristic, not provenance — the
durable fix is for the logger to mark its own test traffic.
