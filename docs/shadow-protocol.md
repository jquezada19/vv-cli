# Paired-read shadow protocol

The harness runs a vault read **both ways** — vv and the old-fashioned way —
and records the pair, so the pilot closes on measured quality and speed rather
than on a handful of hand-paired tasks. In practice the 30 recorded pairs were
made in one session on 2026-08-27; day-to-day reads through the pilot went to
vv directly (the pilot register is honest about this: "30 pairs, unchanged
since 2026-08-27").

```
python3 bench/shadow.py <read-verb> [args...]     # stdout = vv's answer
python3 bench/shadow_report.py [since] [until]    # the read-out
```

`stdout` carries vv's output verbatim (it is the answer the caller consumes),
`stderr` carries a one-line comparison, and every read appends a record to
`~/.claude/metrics/vv-shadow.jsonl`.

**Writes are refused by the tool**, not by memory. Two tools writing one note is
how divergence starts, so `set`/`patch`/`rename`/`move`/`new`/`append*` exit
with an error telling you to run vv once, directly.

## Quality is not byte equality

vv and grep return different SHAPES for the same question, so comparing raw
output scores formatting. Each verb has a normaliser reducing both sides to the
same answer — a set of note paths, tag names, frontmatter values, or normalised
text — and the comparison is over that.

**A disagreement is not automatically vv being wrong.** For most of these
questions grep is the weaker instrument. Disagreements are recorded in full and
adjudicated case by case (`shadow.py --adjudicate <op> <who> <reason> [-- <args...>]`);
a ruling with `-- <args>` is scoped to that one `(op, args)` case, a ruling
without it is op-level — still honoured, but the report labels it *op-level
ruling reused* so the read-out can see a ruling covering more than one case.
Rulings are never window-filtered (they are usually made after the window, on
read-out day). The report refuses to call the checkpoint closed while any
disagreement remains unadjudicated.

**A failed legacy run is a harness error, not a disagreement.** When the
legacy one-liner *fails* — exits 2+ for `grep`, non-zero for anything else —
its output is not an answer, so the record is written with verdict
`legacy-error`, kept out of both the quality and the byte totals, and reported
under *harness errors*. `grep` exiting 1 ("no selected lines") is an answer
and is compared normally: it is exactly the "vv found, old way missed" class
the pairing exists to score. `VV_SHADOW_SINK` relocates the sink (tests only;
both scripts print a banner when it is set).

## The dominant failure mode: the instrument invents the difference

Five separate times, a reported "disagreement" turned out to be the harness:

| Symptom | Cause |
|---|---|
| `outline` always differed | the normaliser took the last tab column — vv's content **hash** — as the heading |
| `links` always differed | splitting `[[...]]` output on whitespace shredded multi-word targets (`New Note` → `New`, `Note`) |
| `search` always "legacy-superset" | vv **ranks and truncates** to `--k 5`; grep returns every hit |
| `resolve`/`board`/`props`/`tags` differed | `find`/`grep` walked `.claude/worktrees` (whole copies of the vault); `paths()` was run over vv output containing no paths; vv's `(N tags)` summary footer leaked into the answer set |
| 3 pairs scored `vv-superset` | the legacy one-liner had **exited 2** (a bad path) and printed nothing; the empty output was compared as an answer (found at the 2026-09-02 read-out; now `legacy-error`) |

So the harness is **versioned** (`HARNESS_VERSION`), and the report sets aside
records from older versions instead of pooling them — the same rule the pilot
note applies to latency rows either side of the index landing. A measurement
tool that reports differences it created is worse than no measurement.

Comparability rules that follow:
- the legacy side must cover **vv's corpus** (skip dot-directories, and `Sandbox/`
  + `graphify-out/` for search, which vv omits by design), or the comparison
  scores scope rather than correctness;
- `search` quality is compared at a widened `--k`, while **ms and bytes stay
  those of the default invocation** real usage issues;
- vv's summary footers are counts, not answers, and never enter the answer set.

## What 30 paired reads showed (v4 harness)

(As of the 2026-09-02 read-out: 27 of the 30 v4 records scored — the 3 with
a failed legacy side (`legacy_exit=2`) are reported separately as harness
errors, and the context bill becomes 62,256 B vs 277,511 B, still 4×. Later
records move these counts; the report is the source of truth.)

| | vv | old way |
|---|---|---|
| context bill | **64,333 B** as recorded (30 pairs) · **62,256 B** scored (27, after the harness-error exclusion) | 277,511 B (**4×** either way) |
| whole-vault ops | 3–30× faster (`resolve` 30×, `backlinks` 8×, `search` 7×, `props` 4×, `tags` 3×) | |
| single-note ops | roughly equal or slightly slower — vv pays process startup where grep reads one file | |

Every adjudicated disagreement was either **vv-correct** or a harness artifact.
No case yet where the old way was right:

- **backlinks** — `grep -rlF "[[CLAUDE"` prefix-matches `[[CLAUDE.md
  conventions]]`-shaped links (12 false positives) and cannot see `[CLAUDE.md](CLAUDE.md)`
  in AGENTS.md (1 false negative).
- **links** — grep counts wikilinks quoted in prose: `` `[[wikilinks]]` `` is
  inline code, `superseded_by: "[[New Note]]"` is a quoted YAML value.
- **props** — grep matched `type: experiment` inside a ```markdown fenced
  EXAMPLE in a template whose real frontmatter is `type: reference`.
- **outline** — vv reports an H0 `(preamble)` section, addressable by
  `vv read <note> H0`, which `grep '^#'` cannot see because it has no heading.

The pattern: the old way cannot tell a link from a mention of a link, a
frontmatter key from an example of one, or a note name from a prefix of one.
That is the quality difference, and it is structural rather than incidental.
