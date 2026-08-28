# vv — a fast, terse, agent-friendly CLI for Obsidian vaults

[![CI](https://github.com/jquezada19/vv-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/jquezada19/vv-cli/actions/workflows/ci.yml)
[![License: MIT OR Apache-2.0](https://img.shields.io/badge/license-MIT%20OR%20Apache--2.0-blue.svg)](#license)

`vv` (**Verified Vault**) reads, writes, and edits an Obsidian vault headlessly,
built for AI-agent workflows where **every output byte enters the model's
context**. Output is terse by contract, edits are hash-anchored compare-and-swap,
multi-file operations are journaled with classified rollback, and link semantics
are verified against the Obsidian app's own metadata cache — not guessed from the
docs.

Reading one section of a note costs **11× fewer bytes** than opening it. Search
returns **~1,200× fewer bytes** than `grep`. Flipping one frontmatter field costs
35 bytes instead of a 16 KB read-modify-write. That is the whole point:
[the numbers are below](#benchmarks), measured on a real 1,500-note vault.

## Demo

```console
$ vv outline Hub.md
H0	-	(preamble)	52B	69e4e87e
H1	#	Hub	72B	40631b2c
H2	##	Details	61B	71a265ae
H3	##	Second section	76B	31890858

$ vv read Hub.md H2
## Details

Every query needs a tenant check before it runs.

--sha8:71a265ae

$ printf '## Details\n\nRewritten body.\n' | vv patch Hub.md H2 71a265ae
patched H2 in Hub.md (61B -> 27B)

$ printf 'again\n' | vv patch Hub.md H2 71a265ae     # someone else moved first
stale: H2 is a658c3af, expected 71a265ae — re-outline
$ echo $?
3

$ vv rename Beta.md "Beta Renamed"
plan d351fd67: rename Beta.md -> Beta Renamed.md
files to rewrite: 1 (1 link occurrences)
  1	Hub.md
(dry-run — apply with: --apply, or --apply d351fd67 to bind to THIS plan)
```

Read a section, patch it against the hash you read, and every writer refuses if
the bytes moved underneath you. Refactors show their blast radius before they
touch anything.

## Install

Grab an archive from the [releases page](https://github.com/jquezada19/vv-cli/releases)
(macOS arm64/x86_64, Linux x86_64/arm64 — the binary AND the python engine
travel together; unpacking them apart loses most of the surface):

```
tar xzf vv-*-<target>.tar.gz && cd vv-*/
./vv --vault ~/path/to/YourVault outline "Some Note.md"
# put it on PATH keeping the layout: ln -s "$PWD/vv" /opt/homebrew/bin/vv
```

Or build from a checkout:

```
git clone https://github.com/jquezada19/vv-cli && cd vv-cli
(cd vrust && cargo build --release)
ln -s "$PWD/vrust/target/release/vrust" /opt/homebrew/bin/vv   # the native entry
vv --vault ~/path/to/YourVault outline "Some Note.md"
```

No Python dependencies; no crates. Skipping the Rust build (or the symlink)
leaves the pure-Python entry: `python3 src/vv.py ...` — same output on every
command, held identical by the parity gate, just slower.

Set `VV_VAULT` once (`export VV_VAULT=~/path/to/YourVault`) and drop the flag.

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
| `search TERMS [--k N] [--w C]` | ranked full-text: a note **named** for the query outranks mere mentions; a `dir/` term filters by path. Unquoted args are AND-ed terms; a **quoted arg is one phrase** — a zero-hit phrase whose words do co-occur prints a retry-unquoted hint instead of silence |

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
| `lint [--quick]` | broken links, memory-slug links, table-pipe render breaks (index-served) |
| `index [--rebuild]` | index status · force a full rebuild |
| `doctor` | vault / engine / git / journal / metrics status |
| `doctor --rollback` · `--discard` | resolve a pending journal (restores or drops backups) |

### Global flags, environment, exit codes

|  |  |
|---|---|
| `--vault PATH` / `VV_VAULT` | target vault (flag wins) |
| `--limit N` | cap any enumerator at N entries; the trailer turns into `(N of M …)` so truncation is never silent. `search` folds it into `--k` |
| `--jsonl` | opt-in JSON Lines from the enumerators, `search`, and `lint`: `{"v":1,"cmd":…}` first, one record per entry (`path`/`score`/`snippet`, `tag`+`count`, `value`+`count`, `name`+`status`+`type`), `{"total":N,"shown":K}` last; errors become `{"kind","message","next","exit"}` on stderr. Measured 1.05–2.5× the bytes of the default output on a real vault — which is why it is opt-in and the terse forms stay the default |
| `lint --quick --check` | exit 1 when there are findings — the CI form |
| `VV_ENGINE=rust\|python` | force an engine — the test gate runs both |
| `VV_NO_INDEX=1` / `VV_INDEX_ROOT` | disable the index · relocate it (tests) |
| exit `0 · 1 · 3 · 4 · 5` | ok · usage/not-found · stale hash or plan · dirty journal · not UTF-8 |
| errors | grep-stable: `kind: message — next: <command>` |

## Why not just read the files?

Measured before building: agents reading whole notes to change one line burned
~49% of their read bytes on edit-enablement alone. Section-addressed reads + CAS
patches cut per-edit context cost by 60–91%. And a vault is not plain text —
wikilinks resolve through duplicate-basename rules, escaped pipes, HTML comments,
and fence masking that `grep` silently gets wrong (see *Correctness*, below).

## Benchmarks

Why use this over `grep`/`cat` or the official `obsidian` CLI? Measured on a real
1,500-note vault (macOS, Apple Silicon, median of 5 runs; reproduce with
`python3 bench/bench.py` — re-measured 2026-08-27 through the DEFAULT (native)
entry, after the full-Rust rewrite; `VV_BENCH_ENTRY=python` benches the python
entry instead):

| task | shell (grep/cat) | obsidian CLI | vv |
|---|---|---|---|
| read ONE section of a note | 3 ms · 16,125 B (whole file) | 4 ms · 16,125 B (whole file) | 10 ms · **1,442 B** |
| search a common term | 5,857 ms · 3,505,090 B | 123 ms · 299,519 B | **36 ms** · **2,993 B** |
| flip one frontmatter field | 24 ms · 16,126 B round-trip | n/a headless-only | **6 ms** · **35 B** |
| backlinks of a hub note | 2,299 ms · 107,037 B | **5 ms** · 252 B | 24 ms · 266 B |

The column that matters for an agent is **bytes**: that's the context (token)
bill, paid on every operation, every session. Reading one section costs **11×
fewer bytes** than opening the note; search returns ~1,200× fewer bytes than
grep and ~100× fewer than the obsidian CLI at comparable latency; flipping a
frontmatter field costs 35 bytes against a 16 KB read-modify-write.

Honest caveats: `cat` still edges vv on a bare single-file read (3 vs 10 ms —
the residue is vv's resolve + section parse, no longer interpreter startup),
and the obsidian CLI still wins backlinks outright — the app holds a live
in-memory cache and its CLI is a 133 KB socket shim into it. vv's job is to be
*accurate and cheap in context* without needing the app open at all.

## How it works

Two implementations, one semantics: a std-only Rust binary (the default entry)
answers the common commands natively, and a stdlib-only Python implementation
remains the semantic authority — the native path execs it for everything it
doesn't handle and for any input it is unsure about. The two are held to
identical output by differential parity suites and to *correct* output by a
hand-authored expected-vector corpus plus a live-Obsidian oracle, so a shared
bug can't certify itself.

### Entry points

Since 2026-08-27 the default entry is the **native binary** (`vv` on PATH →
`vrust/target/release/vrust`): the four read commands, all graph commands, the
frontmatter/query commands, and the common writes (`set`/`unset`/`append`/
`appendsec`/`patch`) are answered natively in 3-26 ms; every other command —
and any input the native path is unsure about — execs `python3 src/vv.py`
with the original argv, so error text, suggestions, and exit codes are the
Python implementation's, byte-for-byte. `python3 src/vv.py` remains a fully
supported entry forever. Parity between the two is pinned by six differential
suites (~300 checks) in the test gate.

### The caches

Each entry keeps its own derived, disposable cache under `~/.cache/vv/index/`
— they never read or write each other's, because two writers on one cache is
how caches lie. The **native entry** keeps a TSV link cache (`.vvidx`) behind
`backlinks`/`orphans`/`deadends`; the **python entry** keeps a SQLite index
behind its graph and frontmatter commands and `lint --quick`. Both follow the
same freshness contract, described here in the SQLite index's terms.

The `.vvidx` cache additionally carries an **integrity footer** (body length +
checksum), because the freshness contract alone is not enough to make it safe.
Per-file `(mtime, size, ino)` equality proves each surviving record still
describes its file — it cannot prove that record's own link rows survived a
crash, so a record-aligned partial write would pass every structural check and
silently serve *missing links*. The footer makes that state detectable, which is
what lets the write skip `fsync` (a crash can lose the newest cache; it cannot
produce one that lies). Both halves are pinned by `tests/test_cache_integrity.py`
and were confirmed by disabling each and watching the suite fail. Format,
reproduction, and the measured case against backing this with SQLite instead:
[docs/cache-format.md](docs/cache-format.md).

The python entry's index is a **persistent SQLite cache** under
`~/.cache/vv/index/`, never inside the vault. Freshness is per-invocation, not
per-interval: every command stat-walks the vault (no file reads, ~10 ms), diffs
`(mtime_ns, size, inode)` per file by **equality** against the DB, and re-parses
only changed files — so a just-edited note is reflected on the very next
command, with no daemon and no file watcher. Git's "racily clean" rule
(re-hash anything whose mtime ties the index's own commit stamp) closes the
same-tick rewrite hole. Raw link targets are stored, never resolved
destinations — bare-link winners depend on the current duplicate-basename
population, so resolution happens at query time.

The index is an accelerator, not an authority: on any doubt (corruption, torn
read, version mismatch) vv falls back to the live scan and the DB is deleted
and rebuilt — never repaired, never served partially. Byte-parity between the
indexed and live paths is regression-tested on every accelerated command.

| | before | after |
|---|--:|--:|
| backlinks | 242 ms | 77 ms |
| orphans | 254 ms | 83 ms |
| impact | 260 ms | 101 ms |
| tags | 176 ms | 64 ms |
| `lint --quick` | 1,766 ms | 87 ms |
| search (parallel Rust scan, not the index) | 153 ms | 99 ms |

(The table above is the python entry's own before/after from 2026-08-27, kept
as the dated record; the native entry is faster still — see *Benchmarks*.)

`vv index` shows status; `vv index --rebuild` forces a rebuild; `VV_NO_INDEX=1`
disables the SQLite index (`bench/index_bench.py` measured the before/after).
The python entry's remaining floor is CPython startup, ~22 ms — which is why
the native binary became the default entry; the `.vvidx` cache self-heals the
same way (any doubt → live scan + rebuild, delete-don't-repair).

## Safety model

- **Containment** — every read and write path, including rename/move
  destinations, resolves through a realpath check that refuses to leave the vault.
- **CAS on every writer** — section patches carry a sha8 of the section they replace, and
  `set`/`unset`/`append`/`appendsec`/`daily-append` capture a `(mtime_ns, size)`
  signature at read and refuse with exit 3 if the file changed underneath. Obsidian is
  a second writer whenever the app is open, so this is a live case, not a hypothetical.
  One documented deviation under the native entry: a CAS conflict there falls back to
  python, which re-reads the latest bytes and applies — no update is ever lost, but
  during an active race the exit-3 warning is replaced by a clean retry;
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

The full gate (`./run_tests.sh`) runs eighteen suites — the originals on BOTH
python engines, six native-vs-python differential suites (~300 checks), the
phase-2 cache/patch pins, and three fuzz seeds — over 500 checks in all, with
seeded property/fuzz invariants (sections must partition every file; crash
injection at every write index must roll back byte-identically) and a read-only
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

## Design notes

- Output bytes are the cost function; every op logs `{op, ms, out_bytes, exit, kind, cf_bytes}` to
  `~/.claude/metrics/vv.jsonl` (best-effort, silent if absent).
- Two implementations of one semantics is a standing drift risk — accepted
  deliberately and held in check by the differential suites, the expected
  vectors, and the fallback contract (the native path answers only when sure;
  python authors every error surface). The maintenance rule that follows: a
  semantic change lands twice or not at all, and the gate is what proves the
  twice matched.
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

## Contributing

`vv` keeps two implementations of one semantics, so a semantic change lands
twice or not at all — see [CONTRIBUTING.md](CONTRIBUTING.md) for the gate, how
to run it against your own vault, and what a good change looks like. Bugs and
proposals go through the [issue templates](.github/ISSUE_TEMPLATE); security
issues go [privately](SECURITY.md).

## License

Dual-licensed under either of

- Apache License, Version 2.0 ([LICENSE-APACHE](LICENSE-APACHE))
- MIT license ([LICENSE-MIT](LICENSE-MIT))

at your option. Unless you explicitly state otherwise, any contribution
intentionally submitted for inclusion in this work by you shall be dual-licensed
as above, without any additional terms or conditions.
