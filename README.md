# vv — a fast, terse, agent-friendly CLI for Obsidian vaults

`vv` reads, writes, and edits an Obsidian vault headlessly, built for AI-agent
workflows where **every output byte enters the model's context**. Output is terse
by contract, edits are hash-anchored compare-and-swap, multi-file operations are
journaled with classified rollback, and link semantics are verified against the
Obsidian app's own metadata cache — not guessed from the docs.

Python orchestrator (stdlib only) + optional std-only Rust engine for the two hot
scans (search, link scan). Both lexers are held to identical output by a parity
suite and to *correct* output by a hand-authored expected-vector corpus, so a
shared bug can't certify itself.

## Benchmarks

Why use this over `grep`/`cat` or the official `obsidian` CLI? Measured on a real
1,495-note vault (macOS, Python 3.13, median of 5 runs; reproduce with
`python3 bench/bench.py`):

| task | shell (grep/cat) | obsidian CLI | vv |
|---|---|---|---|
| read ONE section of a note | 2 ms · 4,643 B (whole file) | 3 ms · 4,643 B (whole file) | 69 ms · **1,728 B** |
| search a common term | 3,610 ms · 3,240,649 B | 24 ms · 286,065 B | 118 ms · **2,993 B** |
| flip one frontmatter field | 17 ms · 4,644 B round-trip | n/a headless-only | 41 ms · **44 B** |
| backlinks of a hub note | 1,722 ms · 74,591 B | **4 ms** · 2,144 B | 179 ms · 2,159 B |

The column that matters for an agent is **bytes**: that's the context (token)
bill, paid on every operation, every session. On search vv returns ~1,000× fewer
bytes than grep and ~95× fewer than the obsidian CLI at comparable latency.
Honest caveats: `cat` beats vv on raw wall-time for single files (vv pays ~30 ms
of Python startup per call), and the obsidian CLI wins backlinks latency
outright — the app holds a live in-memory cache. vv's job is to be *accurate and
cheap in context* without needing the app open at all.

## Why not just read the files?

Measured before building: agents reading whole notes to change one line burned
~49% of their read bytes on edit-enablement alone. Section-addressed reads + CAS
patches cut per-edit context cost by 60–91%. And a vault is not plain text —
wikilinks resolve through duplicate-basename rules, escaped pipes, HTML comments,
and fence masking that `grep` silently gets wrong (see *Correctness*, below).

## Commands

```
Read:    outline NOTE · read NOTE SEC · head NOTE · resolve NAME
         show NOTE [--max-bytes N] [--from SEC]      # budgeted read, continuation tokens
         search TERMS [--k N] [--w C]                # name-match ranked; "dir/" terms filter by path
Write:   patch NOTE SEC SHA8 <stdin                  # compare-and-swap; exit 3 = stale, re-outline
         appendsec NOTE SEC TEXT · append NOTE TEXT
         set NOTE KEY VALUE · unset NOTE KEY · new PATH [--template T] [--key v ...]
Graph:   backlinks NOTE · links NOTE · orphans [FOLDER] · deadends · impact NOTE
Refactor: rename NOTE NEWNAME [--apply [PLAN]]       # link-aware; dry-run prints a plan digest;
          move NOTE FOLDER [--apply [PLAN]]          #   --apply <digest> executes EXACTLY that plan or exits stale
Query:   board FOLDER [k=v ...] · tags [--counts] · props KEY [FOLDER]
Daily:   daily-append TEXT
Health:  lint [--quick] · doctor [--rollback | --discard]
```

`NOTE` is a vault-relative path or a bare name (wikilink-style resolution; a
failed lookup suggests near-misses). Global: `--vault PATH` or `VV_VAULT`.
`VV_ENGINE=rust|python` forces an engine (tests run both). Exit codes: `0` ok ·
`1` usage/not-found · `3` stale hash or stale plan · `4` dirty journal · `5` not
UTF-8. Errors are grep-stable: `kind: message — next: <command>`.

## Safety model

- **Containment** — every read and write path, including rename/move
  destinations, resolves through a realpath check that refuses to leave the vault.
- **CAS everywhere** — section patches carry a sha8 of the section they replace;
  rename/move dry-runs print a plan digest over every affected file's bytes, and
  `--apply <digest>` refuses to execute a plan that drifted since review.
- **Journaled refactors** — rename/move backs up every file (sha256-manifested,
  vault-scoped) before writing. A crash mid-apply rolls back byte-identically —
  including on Ctrl-C and non-UTF-8 surprises. Rollback *classifies* each file
  first: bytes that are neither the original nor vv's own write belong to another
  writer and are never clobbered. A leftover journal blocks all writes (exit 4)
  until `doctor --rollback` or `doctor --discard`.
- **Line endings & encoding** — CRLF/LF, BOM, and EOF-newline are preserved
  byte-for-byte. Non-UTF-8 files are refused, never mangled.

## Correctness

Link semantics were **probed against Obsidian's `metadataCache`** on a live
vault rather than assumed: duplicate basenames resolve same-folder-first then
shortest-path; `[[Note\|alias]]` table escapes; one trailing backslash consumed
per boundary; links inside `<!-- -->` are inert while `%%` comments stay real;
an open comment owns fence markers; a backtick fence's info string may not
contain backticks; aliases may contain `]`. Each probe is pinned three ways:
an expected-vector corpus (independent of both engines), an engine parity suite,
and an opt-in oracle test (`tests/oracle_obsidian.py`) that diffs `vv backlinks`
against the running app — clean across 1,400+ sampled note comparisons.

The full gate (`./run_tests.sh`) runs six suites on BOTH engines: 190+ checks,
seeded property/fuzz invariants (sections must partition every file; crash
injection at every write index must roll back byte-identically), and a read-only
verification pass over the real vault.

## Install

```
git clone https://github.com/jquezada19/vv-cli && cd vv-cli
(cd vrust && cargo build --release)   # optional: Rust engine for hot scans
python3 src/vv.py --vault ~/path/to/YourVault outline "Some Note.md"
```

No Python dependencies. Without the Rust engine everything still works on the
pure-Python fallback (same output, held identical by the parity gate).

## Design notes

- Output bytes are the cost function; every op logs `{op, ms, out_bytes}` to
  `~/.claude/metrics/vv.jsonl` (best-effort, silent if absent).
- Two implementations of one lexer is a standing drift risk; the mitigations are
  the parity suite, the expected vectors, and `VV_ENGINE` running the whole
  command suite on both. Semantics (what a link *means*) live only in Python.
- Patterns adapted from [sqlx](https://github.com/transact-rs/sqlx) (validate
  against the authoritative engine; dirty-state gates; checksummed bookkeeping;
  per-backend test matrices) and rustdoc's search internals (tiered matching,
  did-you-mean suggestions, expected-result test harnesses).

## Known limits

Setext (`===`-underlined) headings are not sections; `.canvas` and plugin link
formats are refused rather than guessed; angle-bracket markdown links
(`[x](<file.md>)`) are not rewritten by rename (documented, not silent).
