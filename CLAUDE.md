# CLAUDE.md — vv-cli

Agent-facing rules for working in this repository. Human contributor guidance
lives in [CONTRIBUTING.md](CONTRIBUTING.md); this file covers only what an
agent cannot infer from the tree.

## Changes land by pull request — never a direct commit or push to `main`

- Branch from `origin/main` (`git checkout -b <branch> origin/main` — pass the
  base explicitly; a bare `checkout -b` inherits whatever HEAD is checked out),
  commit, push the branch, open a pull request. `main` moves only by merging a PR.
- Stage explicit paths (`git add <file> ...`). Never `git add -A` or `git add .`:
  more than one working session may have this clone checked out, and a blanket
  add sweeps a peer's uncommitted work into your commit.
- Run CI's exact commands before opening the PR, not an approximation of them.
  `.github/workflows/ci.yml` has two jobs. The `gate` job (ubuntu + macos)
  builds a fixture vault and runs the suite against it — a bare
  `./run_tests.sh` instead targets the default vault path hard-coded in both
  engines (`src/vv_impl.py` `VAULT`, `vrust/src/main.rs`, when `VV_VAULT` is
  unset) — a real vault, not what CI tests:

  ```
  FIX=$(mktemp -d)            # unique per run — a shared fixed path lets concurrent sessions clobber each other's fixture
  python3 .github/workflows/fixture_vault.py "$FIX"
  env -u VV_INDEX_ROOT -u VV_NO_INDEX -u SEEDS -u STRESS_ITER VV_VAULT="$FIX" VV_TEST_SEARCH_TERMS="tenant check" VV_NO_METRICS=1 ./run_tests.sh
  ```

  `VV_INDEX_ROOT` and `VV_NO_INDEX` must not be inherited from your shell: the
  native cache honors both (`vrust/src/cache.rs`), and the cache suites expect
  the `HOME`-derived location CI has. `SEEDS` and `STRESS_ITER` must not be
  inherited either — `run_tests.sh` and `tests/test_stress.py` read them, and an
  inherited value shrinks the fuzz coverage CI runs with its defaults. The `fmt-clippy` job (check name
  `rustfmt + clippy`, ubuntu) runs:

  ```
  cargo fmt --manifest-path vrust/Cargo.toml --check
  cargo clippy --manifest-path vrust/Cargo.toml --all-targets -- -D warnings
  ```

  A local run with different flags, a different vault, or inherited cache
  variables can pass while missing exactly the failures CI exposes.
- Releases: bump `VERSION`, `vrust/Cargo.toml` (and the resulting
  `vrust/Cargo.lock`) and `CHANGELOG.md` together on a PR branch — the gate's
  `version-cargo-skew` check fails when `VERSION` and the manifest disagree.
  After that PR merges, tag the resulting commit on `origin/main` as `vX.Y.Z`
  where `X.Y.Z` equals the `VERSION` in that commit; the tag push is what
  triggers `.github/workflows/release.yml`. Never tag a commit that is not on
  `origin/main`.

Why the rule is written down: while the repository was private, branch
protection was unavailable on that plan and a direct-to-`main` collision on
2026-08-27 made this a standing rule. The repository is public now and the
`protect-main` ruleset (active since 2026-08-27; GitHub configuration recorded
here as of 2026-09-06 — verify live with `gh api repos/jquezada19/vv-cli/rulesets`)
requires a pull request with one approving review (a push after approval
dismisses it, and unattributed changes need an extra approval), requires the two
`gate` status checks (`rustfmt + clippy` runs on PRs but is not a required check),
allows merge or squash but not rebase, and forbids force-pushes and deletion of
`main` — but it carries an always-on admin bypass, so for the repository admin
(and any agent acting with that identity) the rule is still discipline, not
machinery.

## The parity rule

Two implementations of one semantics: the Rust binary is the default entry, the
Python implementation is the semantic authority. Changing an *existing* native
command's behavior lands in both engines or in neither, and `./run_tests.sh` is
the proof. A Python-only addition is complete on its own — the native entry
execs Python for anything it does not handle. Full statement and what a good
change looks like: [CONTRIBUTING.md](CONTRIBUTING.md).
