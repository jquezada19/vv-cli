# Contributing to vv

## The one rule that is not negotiable

`vv` has **two implementations of one semantics**: a Rust binary (the default
entry) and a Python implementation (the semantic authority). A semantic change
**lands twice or not at all** — and `./run_tests.sh` is what proves the twice
matched. A PR that changes behavior in one engine and not the other will fail
the differential parity suites, and that failure is the feature.

If your change adds a command the native path does not handle, that is fine:
the native entry execs Python for anything it does not answer, so Python-only
is a complete implementation. The rule bites when you change an *existing*
native command's behavior.

## Setup

```
git clone https://github.com/jquezada19/vv-cli && cd vv-cli
(cd vrust && cargo build --release)
```

No Python dependencies and no crates — both implementations are standard-library
only, and pull requests that add a dependency need to argue for it.

## Running the gate

```
./run_tests.sh              # everything: ~500 checks, both engines, 3 fuzz seeds
SEEDS="1 2 3" ./run_tests.sh   # extra fuzz seeds
```

The suites need a vault to work against. Point them anywhere with `VV_VAULT`:

```
VV_VAULT=/path/to/a/scratch/vault VV_TEST_SEARCH_TERMS="a term in it" ./run_tests.sh
```

Tests write only under that vault's `Sandbox/` and clean up after themselves;
the real-corpus pass (`tests/verify_real_vault.py`) is read-only toward the
vault and copies notes to a temp dir before attempting any write. CI runs the
gate against a generated fixture vault on Linux and macOS — see
[`.github/workflows/ci.yml`](.github/workflows/ci.yml).

Two suites are opt-in and not part of CI because they need a running Obsidian
app (`tests/oracle_obsidian.py`) or the author's own transcript history
(`bench/vault_ops_replay.py`).

## What a good change looks like

- **Correctness claims are pinned, not asserted.** New link-semantics behavior
  needs an expected-vector case (independent of both engines), not just a test
  that agrees with the current code.
- **Watch your new guard fail.** Disable it and confirm the suite goes red. A
  test that cannot fail is not evidence.
- **Output bytes are the cost function.** This tool exists because every output
  byte enters a model's context. A change that makes output more verbose needs
  to say what it buys.
- **Errors stay grep-stable**: `kind: message — next: <command>`.

## Benchmarks

`python3 bench/bench.py` reproduces the README table. The index harness
(`bench/index_bench.py`) needs `VV_BENCH_HUB` and `VV_BENCH_TERM` set to a
well-linked note and a term that actually occurs in your vault.
