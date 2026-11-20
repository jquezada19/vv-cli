# How vv is tested

What stands behind the claims in the [README](../README.md).

## The gate

`./run_tests.sh` builds the native binary and runs about fifty suite
invocations, including three fuzz seeds and a read-only verification pass over the
configured vault. CI runs it on macOS and Linux against a generated fixture
vault; [CONTRIBUTING.md](../CONTRIBUTING.md) has the exact recipe. The
original suites run through the Python entry twice, once with the native
scanner and once with `VV_ENGINE=python`.

## Link semantics

Link parsing has an expected-vector corpus written independently of both
engines, and cross-engine parity tests. The rules were probed against
Obsidian's own `metadataCache` on a live vault where the documentation was
unclear: duplicate names resolve same-folder-first, then by shortest path;
`[[Note\|alias]]` inside a table; links inside `<!-- -->` are inert while
`%%` comments are not; an open comment owns fence markers; an alias may
contain `]`. One deliberate difference: among duplicate names with paths of
equal length, vv picks in lexicographic order where the app picks by cache
insertion order.

`tests/oracle_obsidian.py` is an opt-in test that compares sampled
`vv backlinks` output with a running Obsidian, allowing the documented
differences.

## The move to one engine

Three instruments measure the port from two engines to one:

- **Golden vectors** (`tests/golden/`): 214 recorded cases of what Python
  answers. Each is marked `native` (the binary must give the same bytes with
  no interpreter started), `handoff` (the binary must hand the call to Python
  untouched) or a recorded divergence. At 3.3.0: 141 native, 70 hand-off, 3
  divergent. Both directions are enforced, and the marking is generated from
  observed behaviour, so porting a case means implementing it, regenerating
  and reviewing the change.
- **A mutation runner** (`bench/mutate.py`), run separately from the gate: 54
  single-line breakages of the engines' guards, each of which must turn a
  named check red. A guard that can be removed with the suite still green is
  the finding.
- **Difflib vectors** (`tests/difflib/`): 2,571 answers from CPython's real
  `difflib`, replayed through the Rust port by `cargo test`. The port is
  tested but not yet called by any command.

The three recorded divergences: native `search` keeps the carriage returns of
a CRLF note in its snippet, and two successful native `patch` calls name the
section by outline id where Python echoes the selector that was typed.

## Faults and crashes

Property and fuzz tests assert that sections partition every file and that a
fault injected at each rewrite index rolls back byte-identically. Fault
injection covers two different kinds of failure. Catchable faults
(`VV_FAULT_AFTER`, `VV_FAULT_KIND=exit`) exercise the in-process handler.
`VV_FAULT_KILL_AFTER_RENAME` ends the process with `os._exit`, skipping
handlers, `finally` blocks and `atexit`, which is the only way to reach the
crash-recovery path.

## Replaying real usage

`bench/vault_ops_replay.py` samples the vault operations recorded in recent
agent session transcripts and replays them against the current code. Reads
run against the live vault; writes are directed at a disposable copy, so a
replayed `set` or `rename` cannot touch a real note. It skips `patch` (which
needs stdin and a live hash) and reports outcomes by exit code.
