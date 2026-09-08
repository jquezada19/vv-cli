# Changelog

Notable changes to `vv`. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html), where
the public API is the **CLI surface**: command names, flag names, output shape,
and exit codes. A change that makes existing output unparseable, or that
changes an exit code, is a major change.

## [Unreleased]

Affordance sweep of the five days after 2.0.1 (615 telemetry rows, 2026-09-03
→ 2026-09-07). Three fixes; each is pinned through both entries by
`tests/test_affordance.py`, and a seven-seat review round added a fourth.

### Changed
Exit-code transitions — CLI surface under this file's header. Whether the
next release is a MAJOR or treats these as bug fixes is the release PR's
call (a four-seat design review split 2–2 on exactly that). Measured by an
A/B of every listed input against 2.0.1:
- **0 → 1** — `move`/`rename`/`trash` with an extra positional (`vv move A B
  C Dest --apply`), a non-hex token after `--apply` (`--apply abc`), an
  unknown flag (`--force`, `-n`, `--apply=…`), `--apply` twice, or a flag in
  the FOLDER/NEWNAME slot (`vv move A --apply`). Two of these used to WRITE:
  the extra-positional form with `--apply` used note B as the destination
  folder (2026-09-07, four stray folders at the vault root), and the non-hex
  form dropped the plan id and applied UNBOUND. The rest exited 0 as a
  dry-run without writing.
- **3 → 1** — junk after a valid plan id (`--apply deadbeef C`): a stale-plan
  refusal is now a usage refusal (the tail is validated before the plan).
- **0 → 3** — an UPPERCASE 8-hex id that does not match the plan
  (`--apply DEADBEEF`): the id was lowercase-only, so uppercase was ignored
  and the apply went ahead unbound; it is now bound and `stale:`.
- **1 → 0** — a bare digit ref that names a unique `NNNNN - Title` note
  (`vv set 24995 status done`), and the `#NNNNN` spelling wherever `NNNNN`
  would have resolved (`'#24998'` → a literal `24998.md`). A script whose
  fallback branch fired on not-found now writes instead.
- **0 → 1** — a bare name or id whose unique hit is a symlink to a file
  OUTSIDE the vault is now `escape:` in both engines (Python refuses, the
  native resolver hands the case to Python). Typed paths were always
  contained; the walk-derived alias was not, and `set`/`append`/`patch`
  through it wrote outside the vault. Found in review (two seats).

### Fixed
- The relocate tail is a grammar (`--apply` optionally followed by ONE 8-hex
  plan id, nothing else), validated before the note resolves or a plan is
  printed, so a syntax error never reports as `not-found:` and a dry-run never
  previews the wrong move. Short flags (`-h`) count as flags. A caller token
  in the message is escaped (newlines, the `— next:` separator) so the error
  stays one line and the `--jsonl` envelope's `next` field cannot be
  hijacked; `die()` now splits the envelope from the right.
- Arity misses name a runnable next step derived from the command table —
  the caller's own operands, shell-quoted, in the slots they filled
  (`vv set A status VALUE`); surplus arguments with no flag among them are
  joined into a TEXT/VALUE last slot as one quoted argument (`vv append A
  'hello world'`) and otherwise dropped (`vv props status`, never `vv props
  'status Work Items'`); an optional-only command falls back to its bare
  form (`vv orphans`); `append … --section` points at `appendsec`; `patch`,
  like `read`, points at the note's outline. The generic "run vv with no
  args for the command list" pointer is gone. Generated synopsis text
  carries no bracket, redirect, or placeholder-in-brackets (a quoted operand
  may: `vv read '[x]'` is correct quoting). A name with a newline is never
  interpolated. `move NOTE` no longer advertises "2+" positionals.
- The table entry for `trash` says `[--apply [SHA8]]` — a bare `--apply` was
  always accepted; the synopsis overstated (pinned).

### Added
- Id-prefix resolution: a `NOTE` operand of ASCII digits (`24995`, or the
  quoted `'#24995'`) that matches no exact path or basename resolves to the
  unique note whose basename starts with `24995 - `. Exact delimiter (an
  en-dash, a missing space, or digits mid-name are not matches); exact match
  always wins, for both spellings (a literal `#24995` note wins the `#`
  spelling; a literal `24995.md` wins both); two candidates refuse
  `ambiguous:` with a runnable `next:`; an unreadable directory makes
  uniqueness — and absence — unprovable and the lookup is `refused:`,
  naming the directory. This is a filename convention, not link semantics —
  `[[24995]]` stays an unresolved link, and `new 24995` still creates
  `24995.md`.

## [2.0.1] — 2026-09-06

Patch release; also the first tag to exercise the bumped release workflow
(`actions/upload-artifact@v7`, `actions/download-artifact@v8`).

### Fixed
- `_ondisk` (folder-scope respelling) compared entry identity with
  `DirEntry.stat()`, which on Windows reports `st_ino == st_dev == 0` — so
  `samestat` never matched and every scoped `board`/`props`/`orphans` would
  refuse `not-found:` on a folder that exists (latent: no Windows CI; the
  lexical fallback removed in 2.0.0 had masked it). Identity now comes from
  `os.stat(entry.path)`. Pinned by `tests/test_ondisk_identity.py`, which
  simulates the zero-inode entries. Found in review, 2026-09-06.
- A permission failure while resolving a scoped folder is reported against the
  directory that could not be read (`refused: cannot read <dir> while resolving
  <folder>`), not as "cannot list <folder>" — the unreadable directory may be an
  ancestor, and the failing call may be the `stat`, not the listing.

## [2.0.0] — 2026-09-02

The affordance release: a refusal names the next command to run, and a
folder argument means the same thing to both engines. MAJOR because the
**Exit-code change** below moves five cases across three commands from
exit 0 to exit 1 (`props KEY <file>`, `orphans <file>`,
`orphans <missing>`, `orphans <skip dir>`, `board ../x`), which breaks a
script that tested for success — exit codes are part of the CLI surface
under this file's header. Precedent note: 1.1.0 shipped `new --template`
refusing an ambiguous prefix (exit 0 → 1) as a minor; the header rule
applies from this release forward.

Follow-ups from the shadow-pilot read-out (window 2026-08-26T21:06 →
2026-09-02) — the affordance class: vv was right and unhelpful at the same
time.

### Security
- `board FOLDER`'s folder argument now carries the same vault containment as
  every other path argument: `board ../x` is refused (`escape:`) in both
  engines. Before this it walked and printed frontmatter from outside the
  vault. Residual, pre-existing and unchanged here: a symlinked `.md` *file*
  inside a legitimately contained folder is still read by every folder-scoped
  enumerator (`board`/`props`/`orphans`) in both engines (directory
  symlinks are not followed).

### Fixed
- `board FOLDER status open` (a filter without `=`) died as a bare Python
  traceback — exit 1, no usage line, no `next:`, and no metrics row; it is
  now `usage: board filters are KEY=VALUE …` with a runnable `next:`.
- A case- or normalization-variant folder spelling on an insensitive
  filesystem (APFS default): `orphans SUB` or an NFC-spelled `orphans Café`
  over an NFD directory resolved but named no walked path and answered a
  silent `(0 orphans)`, and `orphans GRAPHIFY-OUT` dodged the skip-dir refusal
  into the same silent zero. The python engine now respells the resolved scope
  to its on-disk names, choosing each component by identity among the
  directory's non-symlink entries (candidates narrowed by NFC-casefold first;
  the narrowing can only fall through, never mis-select). The native engine's
  canonicalize (realpath(3) on Darwin) already answered the stored spelling —
  observed and pinned, not a documented contract.
  Known limit, pre-existing and an engine divergence: with a case-variant
  `VV_VAULT` root, the python engine refuses an absolute in-vault path (or a
  symlink whose target uses the stored spelling) as `escape:` — an
  over-refusal, it fails closed — while the native engine, which canonicalizes
  both sides, answers it. Spell `VV_VAULT` as it is on disk. (Index rows a
  scoped query stored under a caller-spelled prefix before this release stay
  until the next unscoped sync; answers are unaffected.)
- The `next:` a file-as-folder or skip-dir refusal prints is shell-quoted, so a
  path with a space is copy-runnable (board's and read's already were).

### Changed
- Environment contract: `VV_VAULT` is normalised lexically (like
  `os.path.normpath`) in BOTH engines before use, so a value containing `..`
  through a symlink names the lexical directory in both — previously python
  and the native engine could address different vaults (known lexical
  divergence: POSIX keeps a leading `//`, the native normalise collapses it
  to `/`). `VV_NO_INDEX` and
  `VV_INDEX_ROOT` now bind the native engine's link cache too (they were
  python-only; README documented them without an engine qualifier), and an
  exported-but-empty value means unset in both engines.
- `journal` (not a command) now hints `did you mean: doctor` — an alias table
  the edit-distance hint could never reach.
- `read NOTE` with no section points at `vv outline <that note>` instead of
  the generic usage line. (The read-out's other `read` bucket — wrong note
  names — already prints `did you mean:` suggestions and is unchanged.)
- `board .` / `board ""` / `props KEY .` / `orphans .` now cover the vault
  root in both engines (they returned zero rows before — `board` on the
  indexed path, `props` on both python paths, `orphans` on every path in
  both engines) and sync the index unscoped
  instead of reparsing every note per call. The `board` walk in both engines
  now skips the same generated directory the index skips (`graphify-out/`),
  so indexed, walk and native answers agree at the root. A skip dir named
  explicitly as the scope — at any depth, by any spelling — IS answered by
  `board`/`props` (its own notes), and `orphans` refuses it with a `next:`
  (those notes are outside the link graph; it printed a silent zero).
  **Exit-code change:**
  `props KEY <file>` and `orphans <file|missing>` are refused (exit 1) like
  `board <file>` in both engines (`props` used to retire that note's index
  row and answer a count; `orphans <missing>` answered a clean zero and
  `orphans <file>` listed the file itself as an orphan, both exit 0).
  `orphans <in-vault symlink>` now resolves the folder in both engines (it
  answered zero).
- Shadow harness (`bench/shadow.py`, `bench/shadow_report.py`): a legacy
  one-liner that *fails* (`grep` exit 2+, anything else non-zero — `grep`
  exit 1 is an answer) is recorded as `legacy-error` and reported as a
  harness error, never a disagreement, with no answer-set diff kept; rulings
  can be scoped to one `(op, args)` case with `--adjudicate … -- <args>`, an
  op-level ruling is labelled as reused, rulings are never window-filtered,
  and a sink of nothing but failed pairs aborts loudly instead of printing a
  clean zero. `VV_SHADOW_SINK` overrides the sink (tests only; banner printed).

## [1.1.0] — 2026-08-27

The agent-ergonomics release: byte budgets, structured output, invocation
amortization, a safe removal verb, and archives that actually work.

### Fixed
- **Size labels were characters, not bytes** — `outline`/`patch` under-reported
  multibyte sections in BOTH engines (parity agreed on the same wrong number);
  now true UTF-8 byte counts, pinned by hand-computed Unicode vectors.

### Added
- `--version` (byte-identical across entries); terse one-line no-args usage;
  one-suggestion command typo hints; help cleanup.
- `search --files` (paths only) and a global `--limit N` on every enumerator
  with an honest `(K of M …)` trailer; `search` folds `--limit` into `--k`.
- `--jsonl`: JSON Lines from the enumerators, `search`, and `lint` (`{"v":1}`
  first record, per-command fields, explicit `"truncated"` flag), structured
  `{"kind","message","next","exit"}` errors on stderr; `lint --quick --check`
  exits 1 on findings for CI. Measured 1.05–2.5× the bytes of the terse
  default — which is why it is opt-in.
- `batch` (JSONL read-ops on stdin, one process — measured 5.2× vs separate
  invocations) and `changed --since <epoch|ISO>`.
- `unresolved`, `templates` (ambiguity-marked), `prepend` (after-frontmatter,
  CAS-guarded); `new --template` refuses ambiguous prefixes and duplicated
  exact stems instead of silently picking one.
- `trash NOTE`: journaled removal to `.trash/` with a dry-run blast-radius
  report and plan digest — replaces the v1.0 "no delete" non-goal.
- `--generate man|complete-bash|complete-zsh|complete-fish`, rendered from a
  declarative command table gate-pinned to the dispatcher in both directions.

### Distribution
- Release archives bundle BOTH engines (the v1.0 binary lost most of its
  surface outside a checkout); python-entry resolution knows the archive
  layout, `VV_PYTHON`/`VV_PY_ENTRY` override, missing engines are grep-stable
  errors, and a VERSION skew between engines warns once. Four platforms
  (macOS arm64/x86_64, Linux x86_64/arm64), each archive smoke-tested unpacked
  before it ships, with man page + completions included.

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

[Unreleased]: https://github.com/jquezada19/vv-cli/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/jquezada19/vv-cli/releases/tag/v1.1.0
[1.0.0]: https://github.com/jquezada19/vv-cli/releases/tag/v1.0.0
