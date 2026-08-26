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
python3 tests/test_vv.py
```

Section CAS, duplicate headings, fenced fake headings, stale-hash refusal,
name-resolution ambiguity, frontmatter round-trips, graph ops, template create,
search exclusions — plus hardening cases (CRLF, unicode headings, 0-byte files,
concurrent modification).

## Roadmap (from the spec)

- v1.5: link-aware `rename`/`move` (plan-then-apply, resolver-driven), `impact`
  blast-radius preview, `lint`, `show --max-bytes` with continuation tokens,
  disposable never-authoritative SQLite index, PyO3 in-process engine.
- v2: `pack` (budgeted evidence packets), `receipt` (drift-verifiable handoffs),
  `today` (daily-note skeleton), batch `fm set --where`, `bench`.

## Vault path

Currently pinned to `~/Documents/Obsidian Vault`; will become `VV_VAULT` env /
config before any public release.
