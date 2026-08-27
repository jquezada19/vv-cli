`vv` reads, writes, and refactors an Obsidian vault headlessly, built for
AI-agent workflows where every output byte enters the model's context.

```console
$ vv read Hub.md H2
## Details

Every query needs a tenant check before it runs.

--sha8:71a265ae

$ printf '## Details\n\nRewritten.\n' | vv patch Hub.md H2 71a265ae
patched H2 in Hub.md (61B -> 27B)
```

Read a section, patch it against the hash you read. Every writer refuses if the
bytes moved underneath you — because Obsidian is a second writer whenever the
app is open.

## Why

Measured on a real 1,500-note vault, against `grep`/`cat` and the official
`obsidian` CLI:

| task | shell | obsidian CLI | vv |
|---|---|---|---|
| read ONE section | 16,125 B (whole file) | 16,125 B (whole file) | **1,442 B** |
| search a common term | 3,505,090 B | 299,519 B | **2,993 B** |
| flip one frontmatter field | 16,126 B round-trip | n/a | **35 B** |

Bytes are the column that matters: that is the context bill, paid on every
operation, every session.

## What's in it

- **Read** — `outline`, `read`, `show` (hard byte ceiling + continuation token),
  `head`, `resolve`, `search`
- **Write** — `patch` (compare-and-swap on a section's sha8), `append`,
  `appendsec`, `set`/`unset`, `new`, `daily-append`
- **Refactor** — link-aware `rename`/`move`, dry-run by default, journaled with
  hash-manifested backups; `--apply <digest>` refuses a plan that drifted
- **Graph & query** — `backlinks`, `links`, `impact`, `orphans`, `deadends`,
  `board`, `tags`, `props`
- **Health** — `lint`, `index`, `doctor`, `doctor --rollback`/`--discard`

## How it's built

Two implementations of one semantics: a std-only Rust binary (the default entry,
3–26 ms) and a stdlib-only Python implementation that remains the semantic
authority — the native path execs it for anything it doesn't handle or isn't
sure about. Zero dependencies in both: no crates, no PyPI packages.

## Why you can trust it with your notes

Link semantics were probed against Obsidian's own `metadataCache` on a live
vault rather than assumed, then pinned three ways: an expected-vector corpus
independent of both engines, differential engine-parity suites, and an opt-in
oracle test that diffs `vv backlinks` against the running app — clean across
1,400+ sampled note comparisons.

Every read and write path resolves through a realpath check that refuses to
leave the vault. Refactors are recoverable after a hard crash, and rollback
classifies each file first so another writer's bytes are never clobbered —
including an edit made *after* the crash. Line endings, BOM, and EOF-newline
are preserved byte-for-byte; non-UTF-8 files are refused, never mangled.

`./run_tests.sh` runs the full gate — over 500 checks across both engines,
including six native-vs-python differential suites, seeded property/fuzz
invariants, crash injection at every write index, cache-damage and concurrency
torture, and a read-only verification pass over a real corpus.

## Install

```
git clone https://github.com/jquezada19/vv-cli && cd vv-cli
(cd vrust && cargo build --release)
ln -s "$PWD/vrust/target/release/vrust" /opt/homebrew/bin/vv
export VV_VAULT=~/path/to/YourVault
vv outline "Some Note.md"
```

Skipping the Rust build leaves the pure-Python entry (`python3 src/vv.py`) —
same output on every command, held identical by the parity gate, just slower.

## Known limits

Setext headings are not sections; `.canvas` and plugin link formats are refused
rather than guessed; angle-bracket markdown links are not rewritten by `rename`.
The frontmatter reader is YAML-*shaped*, not YAML — no block scalars, nested
maps, or anchors.

Full detail in the [CHANGELOG](../CHANGELOG.md).
