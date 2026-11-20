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
arguments and passes the result through. (One exception: a `patch` that has
already read its body from stdin resends the body and names the vault
explicitly.) A hand-off prints no command output of its own first, though it
can print a warning when the binary and the Python files report different
versions. `VV_PYTHON` chooses the interpreter and `VV_PY_ENTRY` the script;
without either, the binary looks for `src/vv.py` in the archive layout and the
checkout layout.

Python, in turn, can use a built binary in a checkout as a scanner for
searches and for link scans that have no index (`VV_ENGINE=python` turns that
off). A native process that Python started never hands the call back, so the
two cannot call each other in a loop.

### What the native entry answers

Success paths: `outline`, `read NOTE SEC`, `show`, `head`, `resolve`,
`search`, `links`, `backlinks`, `orphans`, `deadends`, `board`, `tags`,
`props`, `set`, `unset`, `append NOTE TEXT`, `appendsec` and `patch`, plus
`--version` and a bare `vv`. Some forms of these still go to Python, including
`read NOTE` with no section and `append NOTE SEC TEXT`.

Refusals it words itself, in Python's exact bytes and exit codes, when the
call carries no global flag:

- a wrong operand count for any of those commands, and a search with no query;
- a reference that leaves the vault (`escape:`), that names several notes
  (`ambiguous:`), or that a write cannot prove unique because a directory
  could not be listed (`refused:`);
- a miss when three or more distinct note names contain the last part of the
  missed one (`not-found:` with a `did you mean:` line).

Still Python's, by hand-off: every other command; a miss that needs
similarity-ranked suggestions; section errors; `--jsonl`; `<command> --help`;
any `--limit` or `--vault` that is not in its plainest form; a note lookup by
name over a directory that cannot be listed (it answers with a warning); and a
bare note name outside ASCII. Native `search` is not in that list: it answers
from the directories it could read. That last one exists because names are matched lowercased
and the two engines' Unicode tables are different editions; exact paths
compare nothing and stay native.

The direction of travel is a single native engine with Python kept as the
test oracle. Golden vectors classify each covered case as a native match, a
hand-off, or an explicitly recorded native difference
([how it is tested](verification.md)).

## The caches

Each engine keeps its own derived, disposable cache, by default under
`~/.cache/vv/index/` (`VV_INDEX_ROOT` moves them, `VV_NO_INDEX=1` disables
them). They never read or write each other's.

- The **native** graph handlers (`backlinks`, `orphans`, `deadends`) use a TSV
  link cache, `.vvidx`. Its format, its integrity footer and the reasoning
  behind it are in [cache-format.md](cache-format.md).
- The **Python** indexed handlers (its graph and frontmatter queries and
  `lint --quick`; `links` reads its one note directly) use a SQLite index.
  `vv index` syncs it and reports on it, and `vv index --rebuild` recreates
  it.

Both follow the same freshness rule. A command that uses a cache lists the
scope it needs (the vault, or one folder for a folder-scoped query) and
compares each note's `(mtime_ns, size, inode)` by equality with the cached
row; a note that is new or changed is parsed again, and a note stamped close
to the cache's own commit time is re-hashed, the way git handles a "racily
clean" file. Raw link targets are stored, not resolved destinations, because
which note a bare link names depends on which duplicate names exist now.

A cache is an accelerator, not an authority. Metadata equality is a heuristic,
and the footer detects accidental damage, not every possible inconsistency.
When the native cache does not check out it is rebuilt on the spot. When the
SQLite index fails, Python answers from a live scan for that run, sets the
database aside, and a later successful sync builds a new one.

## Metrics

By default an instrumented call appends a line, best effort, to
`~/.claude/metrics/vv.jsonl` when that directory exists: `ts`, `ver`,
`engine`, `op`, `ms`, `out_bytes` and `exit`, plus, when they apply, `kind`
(the refusal), `ref` (how the note was addressed), `sel` (how the section was
addressed), `cf_bytes` (the size of the notes the call resolved, for
comparison), `fb` (Python was reached by a hand-off) and `src`.
`VV_NO_METRICS=1` turns it off. Nothing leaves the machine.

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
