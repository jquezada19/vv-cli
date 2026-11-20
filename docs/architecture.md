# Engines and caches

How `vv` is put together. The [README](../README.md) has the user-facing
contract; this page is for contributors and the curious.

## Two engines, one CLI

`vv` has two implementations of the same command line:

- a **native binary** (`vrust/`, Rust, no crates, `#![forbid(unsafe_code)]`),
  which is the default entry, and
- a **Python implementation** (`src/vv_impl.py`, standard library only), which
  is the reference: when the two disagree, Python's answer is the specified
  one.

The native entry answers a call itself when it has a handler for it and is
sure of the answer. Otherwise it starts `python3 src/vv.py` with the original
arguments and passes the result through. A hand-off prints no command output
of its own first. `VV_PYTHON` chooses the interpreter and `VV_PY_ENTRY` the
script; without either, the binary looks for `src/vv.py` next to itself.

Python, in turn, uses the binary as a scanner for `search` and link scans when
it is built (`VV_ENGINE=python` turns that off; `VV_ENGINE=rust` requires it).
A native process that Python started never hands the call back, so the two
cannot call each other in a loop.

### What the native entry answers

Success paths: `outline`, `read NOTE SEC`, `show`, `head`, `resolve`,
`search`, `links`, `backlinks`, `orphans`, `deadends`, `board`, `tags`,
`props`, `set`, `unset`, `append NOTE TEXT`, `appendsec` and `patch`. Some
forms of these still go to Python, including `read NOTE` with no section and
`append NOTE SEC TEXT`.

Refusals it words itself, in Python's exact bytes and exit codes:

- a wrong operand count for any of those commands, and a search with no query;
- a reference that leaves the vault (`escape:`), that names several notes
  (`ambiguous:`), or that a write cannot prove unique because a directory
  could not be listed (`refused:`);
- a miss when three or more note names contain the missed one (`not-found:`
  with a `did you mean:` line).

Still Python's, by hand-off: every other command; a miss that needs
similarity-ranked suggestions; section errors; `--jsonl`; `<command> --help`;
any `--limit` or `--vault` that is not in its plainest form; a read over a
directory that cannot be listed (it answers with a warning); and a bare note
name outside ASCII. That last one exists because names are matched lowercased
and the two engines' Unicode tables are different editions; exact paths
compare nothing and stay native.

The direction of travel is a single native engine with Python kept as the
test oracle. Each slice is ported only where golden vectors show the bytes
are Python's ([how it is tested](verification.md)).

## The caches

Each engine keeps its own derived, disposable cache, by default under
`~/.cache/vv/index/` (`VV_INDEX_ROOT` moves them, `VV_NO_INDEX=1` disables
them). They never read or write each other's.

- The **native** graph handlers (`backlinks`, `orphans`, `deadends`) use a TSV
  link cache, `.vvidx`. Its format, its integrity footer and the reasoning
  behind it are in [cache-format.md](cache-format.md).
- The **Python** indexed handlers (its graph and frontmatter commands and
  `lint --quick`) use a SQLite index. `vv index` reports on it and
  `vv index --rebuild` rebuilds it.

Both follow the same freshness rule. On each call the vault is listed and
each note's `(mtime_ns, size, inode)` is compared by equality with the cached
row; a note that is new or changed is parsed again, and a note stamped close
to the cache's own commit time is re-hashed, the way git handles a "racily
clean" file. Raw link targets are stored, not resolved destinations, because
which note a bare link names depends on which duplicate names exist now.

A cache is an accelerator, not an authority. Metadata equality is a heuristic,
and the footer detects accidental damage, not every possible inconsistency.
When a cache cannot be read or does not check out, the command falls back to a
live scan and the cache is rebuilt.

## Metrics

By default each instrumented call appends one line to
`~/.claude/metrics/vv.jsonl` when that directory exists:
`{op, ms, out_bytes, exit}` plus, when they apply, `kind` (the refusal),
`ref` (how the note was addressed), `sel` (how the section was addressed),
`cf_bytes` (the size of the whole note, for comparison) and `fb` (Python was
reached by a hand-off). `VV_NO_METRICS=1` turns it off. Nothing leaves the
machine.

## Design notes

- Output bytes are the cost function. The terse forms are the default and
  `--jsonl` is opt-in because it is larger.
- Two implementations of one CLI is a standing drift risk, accepted on purpose
  and held in check by the differential suites, the golden vectors and the
  hand-off contract.
- Patterns adapted from [sqlx](https://github.com/transact-rs/sqlx) (validate
  against the authoritative engine, dirty-state gates, checksummed
  bookkeeping, per-backend test matrices) and rustdoc's search (tiered
  matching, did-you-mean suggestions, expected-result harnesses).
