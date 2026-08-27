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
1,500-note vault (macOS, Python 3.13, median of 5 runs; reproduce with
`python3 bench/bench.py` — re-measured 2026-08-26):

| task | shell (grep/cat) | obsidian CLI | vv |
|---|---|---|---|
| read ONE section of a note | 3 ms · 15,965 B (whole file) | 5 ms · 15,965 B (whole file) | 105 ms · **1,442 B** |
| search a common term | 5,800 ms · 3,505,090 B | 106 ms · 299,519 B | 152 ms · **2,993 B** |
| flip one frontmatter field | 23 ms · 15,966 B round-trip | n/a headless-only | 57 ms · **35 B** |
| backlinks of a hub note | 2,306 ms · 107,037 B | **6 ms** · 252 B | 238 ms · 266 B |

The column that matters for an agent is **bytes**: that's the context (token)
bill, paid on every operation, every session. Reading one section costs **11×
fewer bytes** than opening the note; search returns ~1,200× fewer bytes than
grep and ~100× fewer than the obsidian CLI at comparable latency; flipping a
frontmatter field costs 35 bytes against a 16 KB read-modify-write.

Honest caveats, unchanged by the re-measurement: `cat` beats vv on raw wall-time
for single files (vv pays ~30 ms of Python startup per call), and the obsidian
CLI wins backlinks outright — the app holds a live in-memory cache. vv's job is
to be *accurate and cheap in context* without needing the app open at all.

## Why not just read the files?

Measured before building: agents reading whole notes to change one line burned
~49% of their read bytes on edit-enablement alone. Section-addressed reads + CAS
patches cut per-edit context cost by 60–91%. And a vault is not plain text —
wikilinks resolve through duplicate-basename rules, escaped pipes, HTML comments,
and fence masking that `grep` silently gets wrong (see *Correctness*, below).

## Commands

`NOTE` is a vault-relative path or a bare name (wikilink-style resolution — a
failed lookup prints `did you mean:` suggestions).

### Read

| command | what it does |
|---|---|
| `outline NOTE` | section map: id · level · title · size · sha8 anchor |
| `read NOTE SEC` | one section — by outline id, or by heading title / `#Heading` / `(preamble)`; an ambiguous title refuses and names the ids |
| `show NOTE [--max-bytes N] [--from SEC]` | budgeted read with a continuation token; `--max-bytes` is a **hard ceiling in UTF-8 bytes**, and a single oversized section is truncated-and-marked rather than emitted whole |
| `head NOTE` · `resolve NAME` | frontmatter only · name → path |
| `search TERMS [--k N] [--w C]` | ranked full-text: a note **named** for the query outranks mere mentions; a `dir/` term filters by path |

### Write

| command | what it does |
|---|---|
| `patch NOTE SEC SHA8 <stdin` | replace one section, compare-and-swap on its sha8 — exit 3 = stale, re-outline |
| `appendsec NOTE SEC TEXT` · `append NOTE TEXT` | append inside a section · at end of note |
| `set NOTE KEY VALUE` · `unset NOTE KEY` | frontmatter field flip, body untouched |
| `new PATH [--template T] [--key v ...]` | create from a vault template |
| `daily-append TEXT` | append to today's daily note |

### Refactor (link-aware, journaled)

| command | what it does |
|---|---|
| `rename NOTE NEWNAME` | dry-run: prints every link that will be rewritten + a **plan digest** |
| `move NOTE FOLDER` | same, for folder moves; bare-name links are left alone |
| `... --apply` | execute the plan |
| `... --apply <digest>` | execute **exactly** the previewed plan — exit 3 if anything drifted since review |

### Graph & query

| command | what it does |
|---|---|
| `backlinks NOTE` · `links NOTE` | who links here · where this links |
| `impact NOTE` | blast radius before a refactor |
| `orphans [FOLDER]` · `deadends` | nothing links in · nothing links out |
| `board FOLDER [k=v ...]` | frontmatter table with filters |
| `tags [--counts]` · `props KEY [FOLDER]` | tag census · one field across notes |

### Health

| command | what it does |
|---|---|
| `lint [--quick]` | broken links, memory-slug links, table-pipe render breaks |
| `doctor` | vault / engine / git / journal / metrics status |
| `doctor --rollback` · `--discard` | resolve a pending journal (restores or drops backups) |

### Global flags, environment, exit codes

|  |  |
|---|---|
| `--vault PATH` / `VV_VAULT` | target vault (flag wins) |
| `VV_ENGINE=rust\|python` | force an engine — the test gate runs both |
| exit `0 · 1 · 3 · 4 · 5` | ok · usage/not-found · stale hash or plan · dirty journal · not UTF-8 |
| errors | grep-stable: `kind: message — next: <command>` |

## Safety model

- **Containment** — every read and write path, including rename/move
  destinations, resolves through a realpath check that refuses to leave the vault.
- **CAS on every writer** — section patches carry a sha8 of the section they replace, and
  `set`/`unset`/`append`/`appendsec`/`daily-append` capture a `(mtime_ns, size)`
  signature at read and refuse with exit 3 if the file changed underneath. Obsidian is
  a second writer whenever the app is open, so this is a live case, not a hypothetical;
  rename/move dry-runs print a plan digest over every affected file's bytes, and
  `--apply <digest>` refuses to execute a plan that drifted since review.
- **Journaled refactors, recoverable after a HARD crash** — rename/move backs up
  every file (sha256-manifested, vault-scoped) before writing, and the journal
  also persists the rename endpoints, the transaction phase, and a per-file hash
  of what this process wrote — each written before the step it describes, the
  manifest replaced atomically with `fsync`. So recovery works whether the
  process caught the failure or was killed outright: a kill between the rename
  and the commit no longer leaves the note at both ends. Rollback *classifies*
  each file first — bytes that are neither the original nor vv's own write belong
  to another writer and are never clobbered, including an edit made **after** the
  crash. A leftover journal blocks all writes (exit 4) until `doctor --rollback`
  or `doctor --discard`.
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

The full gate (`./run_tests.sh`) runs six suites on BOTH engines: 200+ checks,
seeded property/fuzz invariants (sections must partition every file; crash
injection at every write index must roll back byte-identically), and a read-only
verification pass over the real vault.

Fault injection covers two different kinds of failure, because they exercise
different code. Catchable faults (`VV_FAULT_AFTER`, `VV_FAULT_KIND=exit`) test
the in-process handler. `VV_FAULT_KILL_AFTER_RENAME` uses `os._exit` to bypass
handlers, `finally` blocks and `atexit` entirely — the only way to reach the
crash-recovery path, which every catchable injector had been quietly tidying up
before it could run.

### Replaying real usage

`bench/vault_ops_replay.py` replays every vault operation the last N sessions
actually performed — 586 from 50 sessions on its first run — against the current
code. Reads run against the live vault; **writes run against a disposable copy**,
so a replayed `set`/`patch`/`rename` can never touch a real note. It separates a
legitimate refusal from a crash, ephemeral test fixtures from real notes, and a
relocated note from a lost one.

Unit tests cover what you thought to test; this covers what you actually did. It
is what surfaced the section-addressing gap above: across 50 sessions, callers
guessed section addressing wrong four distinct ways, and every one was correctly
refused — the tool being right and unhelpful at the same time.

## Install

```
git clone https://github.com/jquezada19/vv-cli && cd vv-cli
(cd vrust && cargo build --release)   # optional: Rust engine for hot scans
python3 src/vv.py --vault ~/path/to/YourVault outline "Some Note.md"
```

No Python dependencies. Without the Rust engine everything still works on the
pure-Python fallback (same output, held identical by the parity gate).

## Design notes

- Output bytes are the cost function; every op logs `{op, ms, out_bytes, exit, kind, cf_bytes}` to
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

The frontmatter reader is **YAML-shaped, not YAML**. It handles plain scalars,
quoted values, flow lists (`[a, b]`) and dash lists; it does *not* interpret
block scalars (`|`, `>`), nested maps, or anchors, and it keeps surrounding
quotes verbatim. Probed against Obsidian's own parser across 16 cases: those
divergences are values we mis-*read*, not ones Obsidian rejects — with one
exception that matters, an unquoted `: ` in a value, which makes Obsidian reject
the whole block and silently drop the note from every Bases view. `set` quotes
that case on write, and the vault linter flags any pre-existing instance.
