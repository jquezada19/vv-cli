# vv v1.1 spec — rev 2, after cross-model review

Status: REVIEWED DRAFT. Rev 1 went to a four-seat adversarial review
(three independent model families + one source-verifying seat) on 2026-08-27;
every seat returned "accept with changes" or "rework". This revision is the
synthesis. Where rev 1's claims about the code were found false, they are
corrected here; review findings that were themselves false (two seats proposed
features vv already ships) were verified against source and dropped.

Governing invariants, unchanged: (a) **output bytes are the cost function**;
(b) **two implementations, one semantics**. Review consensus: rev 1 *invoked*
invariant (a) but contained zero byte-reduction features — this revision makes
byte budgets a phase of their own (P2) and re-sequences distribution to last.

## P0 — bug: sizes are characters labeled as bytes (shipped in v1.0)

`outline` and `patch` report `len(t)` / `chars().count()` suffixed `B` in BOTH
engines — parity held on the same wrong answer, and no expected-vector covered
non-ASCII sizes. `café 🚀 naïve` reports 23B; it is 28 UTF-8 bytes. Fix both
engines to UTF-8 byte counts, add Unicode expected-vectors, and audit every
other `B`-labeled figure (`show` is already true bytes by contract). The sha8
anchors hash the same string in both engines, so CAS is unaffected; only the
labels lie. This lands before any feature: a tool whose thesis is byte
accounting must not misreport bytes.

## P1 — breakage-level polish

- **`--version`**: prints `vv <semver>`, identical output from both entries
  (byte parity — engine/SHA details belong in `doctor`, which both entries can
  reach). Version source: `VERSION` file at repo root; rust `include_str!`s it
  at build and `vrust/Cargo.toml` is asserted equal by a gate check (two files,
  one gate-pinned value — rev 1's "single source" claim was false and is
  replaced by an enforced-equality claim).
- **No-args**: both engines emit the SAME terse usage line + `next: vv --help`
  (rev 1 execed python's full help; review: an accidental bare `vv` in an agent
  loop is common, and the full catalog is the expensive resolution).
- Drop "PROTOTYPE" from help; pin `-h == --help` with a test.
- **Command typo suggestion**: max ONE suggestion, appended to the existing
  grep-stable error (`unknown command: outlien — next: vv outline --help`).
  Mechanism: same substring-then-difflib ranking the note resolver uses
  (rev 1 said commands would get "the same" treatment notes get; the note
  mechanism is substring-first difflib-second, and commands reuse it).

## P2 — byte budgets everywhere (the invariant, operationalized)

Uniform on every enumerator (`search`, `backlinks`, `links`, `orphans`,
`deadends`, `board`, `tags`, `props`, and future `tasks`/`unresolved`):

- `--limit N` — deterministic order, and a stable one-line truncation trailer
  naming the continuation (`show` already has this contract; generalize it).
- `search --files` — matching paths only, rg's `-l`. For an agent deciding
  what to read next this is the cheapest possible answer; likely the single
  highest-value byte-saver in the release.
- `search --max-count N` per file.

## P3 — structured output, named honestly

- `--jsonl` (NOT `--json` — every seat flagged the name: the output is JSON
  Lines, and agents `json.loads`-ing the stream must not be surprised). One
  object per line; schema versioned by a `v` field on the first record;
  ordering, null, and truncation semantics specified in the README; schema
  stability joins the CLI-surface SemVer contract.
- **Structured errors ride along**: under `--jsonl`, stderr carries
  `{kind, message, next, exit}` — agents parse errors more often than results.
- Implementation note from source review: python's `search` normally shells to
  the rust binary, so "route `--jsonl` to python" alone would loop; `--jsonl`
  forces the pure-python search path until native support lands.
- `lint --check` (nonzero exit on findings) + `--jsonl` diagnostics — the CI
  story; today lint reports findings and exits 0.

## P4 — invocation amortization (agent workflows)

- **`vv batch`**: read ops as JSONL from stdin, execute against one loaded
  index, emit one result object per op. Per-invocation startup + stat-walk is
  the dominant cost of real agent sessions; nothing else in the roadmap
  addresses it.
- **`vv changed [--since TS]`**: paths changed since a timestamp, served from
  the index's existing per-file `(mtime,size,ino)` data. Removes whole rescans
  from agent loops — the largest possible win under invariant (a).

## P5 — surface parity, fully specified this time

- **`trash NOTE`** (replaces rev 1's "no delete" non-goal, which every seat
  attacked from the same angle: agents denied a delete verb use `rm`, which
  bypasses containment, CAS, and the journal). Journaled move to `.trash/`,
  dry-run + plan digest like rename/move. Strictly safer than the alternative
  agents actually take.
- **`unresolved`**: real link-resolution rules (the lint path's basename test
  is NOT reusable as-is — rev 1's "data already exists" claim was false);
  output `from  line  target`, `--limit` honored.
- **`prepend NOTE TEXT`**: after frontmatter; BOM, missing-frontmatter, and
  unclosed-delimiter cases specified by expected-vectors before code.
- **`templates`**: list template files with ambiguity markers; `new`'s current
  prefix-match-first-lexicographic behavior gets documented and a refusal on
  ambiguous prefix (source review found it silently takes the first hit).
- **`tasks`**: DEFERRED to v1.2. Every seat found the rev-1 sketch
  underspecified (custom statuses, nested lists, blockquotes, block IDs,
  read-without-write half-verb). It needs its own spec, not a roadmap bullet.

## P6 — distribution, LAST (rev 1 had it second; 4/4 seats rejected that)

Rev 1's rust-only archives are withdrawn — reproduced during review: the
standalone binary execs `<repo>/src/vv.py` by relative path, so most of the
surface (and most error text) dies outside a checkout.

- Archives bundle BOTH engines: the binary + `src/vv.py` + `src/vv_impl.py`
  (stdlib-only, so the bundle is 3 files + docs). Binary resolves its python
  entry relative to the archive layout, `VV_PYTHON` overrides, and a missing
  python3 produces `engine: python engine unavailable — next: install python3`
  with a documented exit code — a contract, not a README apology.
- **Engine-skew handshake**: the binary refuses (or warns once on stderr) when
  the bundled python's VERSION differs from its own.
- `--generate man|complete-*`: requires promoting the command table to a
  declarative schema first (name, args, flags, help per command) — source
  review confirmed today's table is name→function and cannot generate docs.
  That schema is the real work; man/completions fall out of it. Homebrew tap
  after one release cycle of archives.
- Release workflow gains a **packaged-install smoke test**: unpack the archive
  in a bare temp dir, run version/help/read/write/fallback paths.

## Non-goals (revised)
- Colors (one README line). Config file (`--vault` flag + `VV_VAULT` cover it —
  one review seat proposed adding `--vault`; it already exists).
- Interactive TUI, watch mode, sync.
- ~~delete~~ → now `trash` (P5).

## Sequencing
P0 → P1 → P2 → P3 → P4 → P5 → P6. Each phase its own PR(s); P4's two items
independent. `tasks` returns as its own v1.2 spec.
