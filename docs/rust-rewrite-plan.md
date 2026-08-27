# vv full-Rust rewrite — phase plan (2026-08-27)

Goal: every command answered natively in single-digit ms where possible.
Measured basis (E4): OS spawn 2.1 ms, Rust tax +0.8 ms, binary size free,
Python floor 27 ms. Reads already native (E2, 0 divergences over 6,004+117).

## Non-negotiable contract (every module)

1. **Fast or silent, never wrong.** Native handles the happy path ONLY; on any
   doubt (miss, ambiguity, non-UTF-8, io error, unsupported flag) return
   Fallback -> exec python3 src/vv.py with ORIGINAL argv. Python stays the sole
   author of error text and exit codes.
2. **Byte parity** on stdout+stderr+exit against the Python implementation,
   pinned by differential tests (fixtures + full-corpus sweep). Semantics are
   ported line-for-line from src/vv_impl.py — never re-derived from taste.
3. **Zero dependencies stands.** No crates. The native index is vv's OWN
   versioned cache file (TSV, disposable, delete-don't-repair), NOT a second
   writer on Python's SQLite DB — two writers on one DB is how caches lie.
4. **Metrics parity**: every native command appends the same JSONL shape to
   ~/.claude/metrics/vv.jsonl with "engine":"native" (see readpath.rs
   log_metrics), honoring VV_NO_METRICS / VV_JOURNAL_ROOT suppression.
5. **CRLF/BOM/EOF-newline byte preservation** on every write; UTF-8 strict.

## Module split (disjoint files; dispatcher stubs pre-wired)

| module | commands | owner |
|---|---|---|
| readpath.rs | outline read head resolve | DONE (E2) |
| graph.rs | backlinks links orphans deadends impact | agent A |
| write.rs | set unset append appendsec new daily-append patch | agent B |
| query.rs | board tags props show | agent C |
| (python, deliberate) | rename move doctor lint index search-hint help | not ported: crash-safety journal + canonical error surfaces stay Python this phase |

search/linkscan already native.

## graph.rs notes (agent A)
- Port bare_resolves + link_matches winner rules EXACTLY (vv_impl.py:605-650):
  same-folder tier, shortest-rel-path tier, lexicographic tie.
- Native cache: ~/.cache/vv/index/<vault-sha16>.vvidx — versioned TSV:
  header line `vvidx 1 <commit_ns>`, then `F<TAB>path<TAB>mtime_ns<TAB>size<TAB>ino`
  and `L<TAB>path<TAB>line<TAB>kind<TAB>target` rows. Stat-diff by EQUALITY,
  racily-clean re-hash (sha256 in readpath.rs) for files with mtime >= stamp-2s,
  full rewrite of the cache file on change (atomic tmp+rename). Any parse
  error/version mismatch -> delete, rebuild from a parallel scan.
- Fallback to live scan (existing linkscan lexer) when cache doubtful.

## write.rs notes (agent B)
- file_sig (mtime_ns,size) CAS: stat at read, refuse exit 3 "stale: ..." — BUT
  contract rule 1: produce NO error text natively; on CAS mismatch return
  Fallback and let python re-run (it will re-read and refuse with canonical
  text — acceptable double-read on the rare conflict).
- atomic write: tmp in same dir + rename; preserve BOM, CRLF (write bytes,
  never translate), trailing-newline state.
- yaml_scalar port from vv_impl.py (quoting decision table) — parity-critical;
  fixtures must include every branch (colon-space, trailing colon, " #", lead
  chars []{}#&*!|>'"%@`, -/?/: before space, whitespace padding, -1 numeric).
- patch: CAS on section sha8 (reuse readpath parse/sha8); H0-frontmatter guard
  -> Fallback (error text is python's).

## query.rs notes (agent C)
- board/tags/props/show WITHOUT an index: parallel frontmatter-only read
  (bytes up to end of closing ---; full read only when show needs body).
  Board folder filter + sorted output ordering exactly as python (sorted paths).
- show: budgeted UTF-8-boundary truncation port (cmd_show) — fixture the
  boundary cases (multibyte at limit, oversized first section marker).

## Test plan (Codex)
- tests/test_full_parity.py: EVERY native command differential vs python:
  fixtures (write commands run against disposable copies of a fixture vault;
  compare RESULTING FILE BYTES as well as stdout/stderr/exit) + read commands
  swept over the real corpus. Positive control per module (sabotage flag).

## Rollout
Integrate module by module behind the existing dispatcher; a module ships only
when its differential suite is green AND the full gate passes. Default entry
remains python3 src/vv.py until the 2026-09-02 checkpoint decision.
