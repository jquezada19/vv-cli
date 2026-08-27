# Changelog

Notable changes to `vv`. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html), where
the public API is the **CLI surface**: command names, flag names, output shape,
and exit codes. A change that makes existing output unparseable is a major change.

## [Unreleased]

## [1.0.0] — 2026-08-27

First public release. `vv` reads, writes, and refactors an Obsidian vault
headlessly, optimized so that every output byte is a token an agent has to pay
for.

### Added

**Read** — `outline` (section map with sha8 anchors), `read` (one section, by id
or heading), `show` (budgeted read with a hard UTF-8 byte ceiling and a
continuation token), `head`, `resolve`, and `search` (ranked full-text where a
note *named* for the query outranks mere mentions; `dir/` terms confine to a
folder; a quoted arg is one phrase, and a zero-hit phrase whose words do
co-occur prints a retry hint instead of silence).

**Write** — `patch` (section replace, compare-and-swap on the section's sha8),
`append`, `appendsec`, `set`/`unset` (frontmatter flips that leave the body
untouched), `new` (from a vault template), `daily-append`.

**Refactor** — link-aware `rename` and `move`, journaled with hash-manifested
backups. Dry-run by default: prints every link that will be rewritten plus a
plan digest, and `--apply <digest>` refuses to execute a plan that drifted since
you reviewed it.

**Graph & query** — `backlinks`, `links`, `impact`, `orphans`, `deadends`,
`board`, `tags`, `props`.

**Health** — `lint` (broken links, table-pipe render breaks), `index`, `doctor`,
and `doctor --rollback`/`--discard` to resolve a pending journal.

### Architecture

- **Two implementations, one semantics.** A std-only Rust binary is the default
  entry and answers the read, graph, frontmatter/query, and common write
  commands natively in 3–26 ms; it execs the stdlib-only Python implementation —
  the semantic authority — for everything it does not handle and for any input
  it is unsure about. `python3 src/vv.py` is a fully supported entry.
- **Zero dependencies** in both implementations: no crates, no PyPI packages.
- **Per-entry caches** under `~/.cache/vv/index/`, never inside the vault, never
  shared between entries. The native `.vvidx` link cache carries an integrity
  footer so a record-aligned partial write is detectable rather than silently
  serving missing links.

### Safety

- Every read and write path — `rename`/`move` destinations included — resolves
  through a realpath check that refuses to leave the vault.
- Compare-and-swap on every writer, because Obsidian is a second writer whenever
  the app is open.
- Journaled refactors recoverable after a hard crash (`os._exit`-level), with
  rollback that classifies each file first and never clobbers another writer's
  bytes — including an edit made *after* the crash.
- CRLF/LF, BOM, and EOF-newline preserved byte-for-byte; non-UTF-8 files refused
  rather than mangled.

### Verified

- Link semantics probed against Obsidian's own `metadataCache` on a live vault
  rather than assumed, then pinned three ways: an expected-vector corpus
  independent of both engines, differential engine-parity suites, and an opt-in
  oracle test that diffs `vv backlinks` against the running app (clean across
  1,400+ sampled note comparisons).
- `./run_tests.sh` runs eighteen suites — over 500 checks — including six
  native-vs-python differential suites (~300 checks), seeded property/fuzz
  invariants, crash injection at every write index, and a read-only verification
  pass over a real corpus.

### Known limits

Setext headings are not sections; `.canvas` and plugin link formats are refused
rather than guessed; angle-bracket markdown links are not rewritten by `rename`.
The frontmatter reader is YAML-*shaped*, not YAML — no block scalars, nested
maps, or anchors.

[Unreleased]: https://github.com/jquezada19/vv-cli/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/jquezada19/vv-cli/releases/tag/v1.0.0
