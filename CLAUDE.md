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
- Run CI's exact commands before opening the PR, not an approximation of them:

  ```
  ./run_tests.sh
  cargo fmt --manifest-path vrust/Cargo.toml --check
  cargo clippy --manifest-path vrust/Cargo.toml --all-targets -- -D warnings
  ```

  These are the three gating steps in `.github/workflows/ci.yml`. A local run
  with different flags passes locally and fails the PR.
- Release tags and version bumps go through the existing release flow
  (`VERSION`, `CHANGELOG.md`, `.github/workflows/release.yml`), never by hand
  on `main`.

Why: this rule was discipline-only while the repository was private (branch
protection was unavailable on that plan) and a direct-to-`main` collision on
2026-08-27 is what made it a standing rule. Now that the repository is public,
branch protection on `main` (require a PR, no force-push) is the mechanical
enforcement — enable it rather than relying on this paragraph.

## The parity rule

Two implementations of one semantics: the Rust binary is the default entry, the
Python implementation is the semantic authority. A semantic change lands in both
or in neither, and `./run_tests.sh` is the proof. Full statement and what a good
change looks like: [CONTRIBUTING.md](CONTRIBUTING.md).
