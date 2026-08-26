# vv — a fast, terse, agent-friendly CLI for Obsidian vaults

`vv` reads, writes, and edits an Obsidian vault headlessly, designed for AI coding
agents where **every output byte enters the model's context**. Outputs are terse by
contract, edits are hash-anchored compare-and-swap, and every op logs its latency
and output size.

Status: **v1 prototype** (Python orchestrator + optional Rust scan engine).
Design spec: converged from four independent model designs (2026-08-26).

## Why

Measured on a real 2,500-note vault: agents reading whole 9KB notes to change one
line burned ~49% of their read bytes on edit-enablement alone. Section-addressed
reads + CAS patches cut that by 60–91%.

## Commands (v1)

```
Read:    outline NOTE · read NOTE SEC · head NOTE · resolve NAME · search TERMS [--k N] [--w C]
Write:   patch NOTE SEC SHA8 <stdin · appendsec NOTE SEC TEXT · append NOTE TEXT
         set NOTE KEY VALUE · unset NOTE KEY · new PATH [--template T] [--k v ...]
Graph:   backlinks NOTE · links NOTE · orphans [FOLDER]
Query:   board FOLDER [k=v ...] · tags [--counts] · props KEY [FOLDER]
Daily:   daily-append TEXT
```

## Commands (v1.5)

```
show NOTE [--max-bytes N] [--from SEC]   # budgeted read with continuation tokens
impact NOTE                              # blast radius: incoming links, git state, frontmatter
rename NOTE NEWNAME [--apply]            # link-aware rename; dry-run plan by default
move NOTE DESTFOLDER [--apply]           # link-aware move; bare-name links stay untouched
deadends                                 # notes with no outgoing links
lint [--quick]                           # delegates to canonical vault_lint.py; --quick = native broken-link scan
doctor                                   # vault/engine/git/journal/metrics health (exit 4 on unresolved journal)
```

Rename/move safety: resolver-driven (typed wikilink/embed/alias/heading/block/YAML/md-link
occurrences; fenced and inline-code text never rewritten), plan-then-apply, journaled
multi-file transaction with rollback + post-apply verification, ambiguity and collisions
refuse before any write.

`NOTE` is a vault-relative path or a bare name (wikilink-style resolution;
ambiguity is an error, never a guess).

Contracts:
- Exit codes: `0` ok · `1` not-found/usage · `3` stale hash (re-`outline`).
- `patch` refuses to write when the section hash doesn't match — the file is
  never touched on a stale anchor.
- Fenced ```` ``` ```` blocks are never parsed as headings or links.
- Sections end at the next heading of any level: patching a parent cannot
  wipe its children.
- Telemetry: every op appends `{op, ms, out_bytes}` to `~/.claude/metrics/vv.jsonl`.

## Rust engine

`vrust/` holds the hot-loop engine (full-vault search today; walk/hash/link-scan
next). The Python CLI uses it when built, and falls back to pure Python otherwise.

```
cd vrust && cargo build --release
```

## Tests

```
./run_tests.sh
```

| Suite | Covers |
|---|---|
| `tests/test_vv.py` | v1 commands; CRLF, unicode, 0-byte, concurrent-edit hardening |
| `tests/test_vv15.py` | rename/move link corpus, impact, show budgets, lint, doctor |
| `tests/test_panel_findings.py` | one regression per defect found by review; they cannot return |
| `tests/test_engine_parity.py` | Rust engine and Python fallback must agree, on fixtures and the live corpus |
| `tests/test_stress.py` | property/fuzz: 8 invariants incl. crash-injection rollback, byte locality, section partition |
| `tests/verify_real_vault.py` | the real corpus, read-only: structure, round-trip, and end-to-end CLI byte equality |

Invariants the fuzz suite pins: sections partition a file exactly; patching a
section with its own content is a byte-identical no-op; a patch changes only its
own span; frontmatter edits never touch the body; an injected crash at any write
index restores every file byte-identically; renames leave no stale links and
never rewrite inert text.

Current corpus run: 1,490 notes / 9,190 sections — sections partition every
note, every section round-trips byte-identically, and sampled sections verify
end-to-end through real CLI subprocesses with zero differences.

## Roadmap (from the spec)

- v1.5: link-aware `rename`/`move` (plan-then-apply, resolver-driven), `impact`
  blast-radius preview, `lint`, `show --max-bytes` with continuation tokens,
  disposable never-authoritative SQLite index, PyO3 in-process engine.
- v2: `pack` (budgeted evidence packets), `receipt` (drift-verifiable handoffs),
  `today` (daily-note skeleton), batch `fm set --where`, `bench`.

## Vault path

`VV_VAULT` selects the vault; it defaults to `~/Documents/Obsidian Vault`.
All tests run against throwaway vaults via that variable.

## Safety properties

- Writes are confined to the vault: absolute paths, `..`, and symlinks pointing
  outside are refused before anything is opened.
- Section edits are compare-and-swap: a stale hash exits 3 and writes nothing.
- Multi-file operations (rename/move) run through a journal with backups; a
  failure at any point restores every file and leaves no duplicate note.
  `doctor` exits 4 while an unresolved journal exists.
- Line-ending style, byte-order marks, and end-of-file terminators are preserved
  exactly; nothing is normalized behind your back.
- Markdown lexing follows CommonMark where it matters for safety: a fence closes
  only on its own marker and length (so nested code samples are inert), and
  inline code spans of any backtick run are never rewritten.
- Non-UTF-8 notes are reported (exit 5), never partially written.
