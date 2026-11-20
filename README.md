# vv — a fast, terse, agent-friendly CLI for Obsidian vaults

[![CI](https://github.com/jquezada19/vv-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/jquezada19/vv-cli/actions/workflows/ci.yml)
[![License: MIT OR Apache-2.0](https://img.shields.io/badge/license-MIT%20OR%20Apache--2.0-blue.svg)](#license)

Use `vv` (**Velocity Vault**) to find a Markdown note, read one heading of it,
and patch that section against the hash you read. It works directly on the
vault's files: no running Obsidian app, no plugins. Renames, moves and trashes
show a plan before they change anything.

It is built for AI agents, where **every byte of output enters the model's
context**. On a 1,634-note vault, reading one section returned 837 bytes
instead of the 16 KB note, a search returned 3 KB instead of 1.3 MB of matching
lines, and setting a frontmatter field returned 35 bytes
([benchmarks](#benchmarks)).

## Demo

An illustrative session (ids, sizes and hashes depend on the note):

```console
$ vv outline Hub.md
H0	-	(preamble)	52B	69e4e87e
H1	#	Hub	72B	40631b2c
H2	##	Details	61B	71a265ae
H3	##	Second section	76B	31890858

$ vv read Hub.md H2
## Details

Every query needs a tenant check before it runs.

--sha8:71a265ae

$ printf '## Details\n\nRewritten body.\n' | vv patch Hub.md H2 71a265ae
patched H2 in Hub.md (61B -> 27B)

$ printf 'again\n' | vv patch Hub.md H2 71a265ae     # retrying with the old hash
stale: H2 is a658c3af, expected 71a265ae — re-outline
$ echo $?
3

$ vv set Hub.md status active
set status=active in Hub.md

$ vv rename Beta.md "Beta Renamed"
plan d351fd67: rename Beta.md -> Beta Renamed.md
files to rewrite: 1 (1 link occurrences)
  1	Hub.md
(dry-run — apply with: --apply, or --apply d351fd67 to bind to THIS plan)
```

Read a section, patch it against the hash you read, and the patch is refused
if the section changed in between. Refactors list the files they will rewrite
before they touch anything.

## Benchmarks

Median wall time and stdout bytes, for the four things an agent does to a
vault. Reproduce on your own vault with `python3 bench/bench.py`.

| task | `cat` / `grep` | ripgrep | Obsidian CLI | vv |
|---|---|---|---|---|
| read one section of a 16 KB note | 4 ms · 16,281 B (whole note) | — | 6 ms · 16,281 B (whole note) | 11 ms · **837 B** (`outline` + `read`) |
| search a common word | 944 ms · 1,299,895 B (every matching line) | 89 ms · 1,299,895 B | 327 ms · 2,575,356 B | 107 ms · **3,140 B** (top five, ranked) |
| set one frontmatter field | 32 ms · 16,282 B (read the note, write it back) | — | — | **13 ms** · **35 B** |
| what links to this note | 449 ms · 12,195 B (files containing the text) | 82 ms · 12,195 B | **7 ms** · 267 B | 267 ms · 230 B (resolved links) |

Measured 2026-11-20 on vv 3.3.0: 1,634 notes (33 MB of Markdown), Apple M4
Pro, macOS 27, ripgrep 15.2, Obsidian 1.15, warm caches, median of 9 runs on a
laptop in normal use, not an idle one. The Obsidian CLI needs the app running
and answers for the vault the app has open, which here was a superset of the
benchmarked notes (about twice as many), so its search bytes are not comparable
with the other columns.

What the table does and does not show:

- **Bytes are the point.** A section read is about 19 times smaller than the
  note. A search is about 400 times smaller than the matching lines, because
  it returns five ranked snippets where `grep` and ripgrep return every hit.
  They are answering different questions; an agent usually wants the first.
- **vv is not the fastest at anything here.** `cat` wins a whole-file read,
  ripgrep finds text faster, and the Obsidian CLI wins backlinks outright,
  since the app holds the link graph in memory. vv's job is to be accurate and
  cheap in context without the app.
- **Backlinks pay for safety.** vv opens every note to prove it can read the
  whole link graph before answering, so its time grows with the number of
  files in the vault, including files that are not notes.
- The `grep` and ripgrep backlink rows list files that contain the text
  `[[Name`. They do not resolve links, so duplicate names, aliases, links
  inside code fences and comments are all counted wrongly.

## Install

Requirements: macOS or Linux, and **Python 3.12 or newer** on `PATH` (standard
library only, no packages). Building from source also needs Rust (no crates).

Download the archive for your platform from the
[releases page](https://github.com/jquezada19/vv-cli/releases): Apple Silicon
macOS, or GNU/Linux on x86_64 or arm64. Keep the unpacked directory together;
the `vv` binary finds its Python half (`src/`) and `VERSION` next to itself.

```console
tar -xzf vv-3.3.0-aarch64-apple-darwin.tar.gz
cd vv-3.3.0-aarch64-apple-darwin
./vv --version
```

Run `./vv` from there, or symlink it into a directory on your `PATH`. On Intel
macOS, build from source:

```console
git clone https://github.com/jquezada19/vv-cli && cd vv-cli
(cd vrust && cargo build --release)
ln -s "$PWD/vrust/target/release/vrust" ~/.local/bin/vv
```

Without the Rust build, `python3 src/vv.py …` is the same CLI, slower.

### Quick start

Nothing here writes to your vault:

```console
export VV_VAULT="/path/to/your/vault"     # or pass --vault PATH on each call
vv doctor                                  # vault, engine and journal state
vv search "project" --files                # paths of the best matches
vv outline "path/from/search.md"           # sections, sizes and hashes
vv read "path/from/search.md" H1           # one section, by the id outline printed
vv rename "Some Note" "New Name"           # a dry run: the plan, nothing changed
```

## Using vv with an AI agent

Paste this into the agent's instructions:

> Use `vv` for the notes vault instead of reading whole files. Find notes with
> `vv search <terms> --files`. Before editing, run `vv outline NOTE` and
> `vv read NOTE Hn`. To change a section, send the complete replacement,
> heading included, on stdin to `vv patch NOTE Hn SHA8`, using the hash `read`
> printed. For one frontmatter field use `vv set NOTE KEY VALUE`. On exit 3
> the note changed: read it again and reconcile before retrying. On exit 4 a
> refactor was interrupted: stop writing and report `vv doctor`. For a rename
> or move, run it without `--apply`, check the plan, then run it again with
> `--apply <plan id>`.

What a caller can rely on:

- **Exit codes.** `0` ok. `1` a refusal: usage, a lookup that failed, or
  findings from `lint --quick --check`. `3` a stale hash or a plan that
  drifted. `4` a pending refactor journal. `5` a note that is not UTF-8.
- **Errors go to stderr and start with a kind.** Most carry a runnable next
  step; a failed lookup may add suggestions:

  ```console
  $ vv read Hubb H1
  not-found: no note matches 'Hubb'
  did you mean: Hub
  $ vv resolve Twin
  ambiguous: Area/Twin.md | Notes/Twin.md — next: vv resolve Area/Twin.md
  $ vv set Hub.md
  usage: set takes 3 positional args, got 1 — next: vv set Hub.md KEY VALUE
  ```

- **Default output is the terse form.** `--jsonl` gives JSON Lines from the
  list commands (`backlinks`, `links`, `orphans`, `deadends`, `unresolved`,
  `templates`, `changed`, `board`, `tags`, `props`), `search` and
  `lint --quick`, and most errors as `{"kind","message","next","exit"}`. Not
  every error is wrapped (a stale `patch` stays plain text), so check the exit
  code first and parse second.
- **`--limit N`** caps a list and says so in its trailer (`N of M`).
- **`vv batch < ops.jsonl`** runs many read operations in one process, and
  **`vv changed --since <time>`** lists what moved since a timestamp.

## Commands

`NOTE` is a vault-relative path or an unambiguous note name. After exact
matches, a bare id such as `24995` (quote the `#` form: `'#24995'`) selects the
one note whose filename starts `24995 - `. Wikilinks do not use the id rule.
`vv <command> --help` prints usage for any command, and the release archive
ships a man page (`vv.1`) with the full reference.

### Read

| command | what it does |
|---|---|
| `outline NOTE` | section map: id · level · title · size · sha8 |
| `read NOTE SEC` | one section, by outline id, heading title, a title prefix of three or more characters, or `--hash SHA8`; an ambiguous selector is refused and names the ids |
| `show NOTE [--max-bytes N] [--from Hn]` | a budgeted read of the whole note (default 4,000 bytes) that says where to resume; `read NOTE` with no section is the same |
| `head NOTE` · `resolve NAME` | frontmatter only · name → path |
| `search TERMS [--k N] [--w C] [--files]` | ranked full text. Arguments are AND-ed; a quoted argument is one phrase; a note named for the query ranks above mentions; a term containing `/` filters by path; `--files` prints paths only |

### Write

| command | what it does |
|---|---|
| `patch NOTE SEC SHA8 <stdin` | replace one section if its hash still matches; exit 3 if not |
| `appendsec NOTE SEC TEXT` · `append NOTE [SEC] TEXT` | append inside a section · at the end of the note |
| `prepend NOTE TEXT` | insert after the frontmatter |
| `set NOTE KEY VALUE` · `unset NOTE KEY` | one frontmatter field; the body is untouched |
| `new PATH [--template T] [--KEY VALUE ...]` | create a note, optionally from a vault template |
| `daily-append TEXT` | append under `## Today` in the one note under `Standups/` dated today (a convention of the author's vault) |

### Refactor (link-aware, journaled)

| command | what it does |
|---|---|
| `rename NOTE NEWNAME` | dry run: the files whose links will be rewritten, and a plan id |
| `move NOTE FOLDER` | the same for a folder move |
| `trash NOTE` | the same for removal to `.trash/`, listing links that will break |
| `… --apply [PLAN]` | execute; with the plan id, exit 3 if the plan no longer matches what was reviewed |
| `impact NOTE` | what a refactor of this note would touch |

One note per call. Links in forms vv does not parse (Canvas files, plugin
syntax, `[x](<file.md>)`) are left as they are, without a warning.

### Graph and query

| command | what it does |
|---|---|
| `backlinks NOTE` · `links NOTE` | notes that link here · wikilink targets in this note |
| `orphans [FOLDER]` · `deadends` · `unresolved` | nothing links in · nothing links out · links to no note |
| `board FOLDER [KEY=VALUE ...]` | frontmatter table with filters |
| `tags [--counts]` · `props KEY [FOLDER]` | frontmatter tags · notes counted by a field's value |
| `templates` · `changed --since <epoch\|ISO>` | template names · paths changed since a time |

### Health

| command | what it does |
|---|---|
| `lint --quick [--check]` | broken links, and links whose `\|` breaks a table row; `--check` exits 1 on findings |
| `index [--rebuild]` | status of the Python engine's index · rebuild it |
| `doctor` | vault, engine, git, journal and metrics status |
| `doctor --rollback` · `--discard` | resolve a pending refactor journal |

### Flags and environment

|  |  |
|---|---|
| `--vault PATH` / `VV_VAULT` | the vault (the flag wins) |
| `--limit N` · `--jsonl` | cap a list · JSON Lines output (see above) |
| `VV_NO_INDEX=1` / `VV_INDEX_ROOT` | disable the derived caches · put them somewhere else (default `~/.cache/vv/index/`) |
| `VV_NO_METRICS=1` | do not append a timing row per call to `~/.claude/metrics/vv.jsonl` (written only when that directory exists) |
| `VV_PYTHON` / `VV_PY_ENTRY` | the interpreter and script the native entry hands off to |
| `--version` · `--help` · `--generate man\|complete-bash\|complete-zsh\|complete-fish` | |

## Safety model

- **Stale writes are refused.** `patch` checks the hash of the section you
  read. `set`, `unset`, `append`, `appendsec` and `prepend` check the file's
  size and modification time between reading and replacing it, and exit 3 if
  it changed. Obsidian is a second writer whenever the app is open, so this
  happens in practice. The check and the replace are separate steps and
  writers are not serialized: this catches most concurrent edits, not all.
  After exit 3, read again before retrying.
- **Refactors are previewed, bound and journaled.** `rename`, `move` and
  `trash` print a plan first. `--apply <plan id>` refuses if the note or any
  file to be rewritten changed since the preview. Every file is backed up
  before it is rewritten, and an interrupted run leaves a journal that blocks
  further writes (exit 4) until `vv doctor --rollback` restores it or
  `--discard` drops it. Recovery is tested by killing the process mid-run.
- **Paths stay in the vault.** A note reference or a rename or move
  destination that resolves outside the vault, including through a symlink,
  is refused with `escape:`. Files vv discovers by walking (search, link
  scans, templates) are not all checked this way: use vv on a vault whose
  contents you trust.
- **Untouched bytes stay untouched.** Edits keep line endings, a BOM and
  everything outside the span they change. A note that is not UTF-8 is refused
  by reads and edits, never rewritten.

## How it works

vv is a Rust binary with no dependencies, paired with a Python implementation
that uses only the standard library. The binary answers the common commands
itself (`outline`, section `read`, `show`, `head`, `resolve`, `search`,
`links`, `backlinks`, `orphans`, `deadends`, `board`, `tags`, `props`, `set`,
`unset`, `append`, `appendsec`, `patch`) and hands every other call, and any
input it is unsure about, to Python with the original arguments. Both implement
one CLI; differential tests and recorded golden vectors compare their output,
exit codes and file changes, and the three known differences are recorded in
the repository.

Link resolution follows Obsidian's rules (duplicate names, aliases, escaped
pipes in tables, links inside comments and code fences), checked against the
app's own metadata cache where a rule was unclear.

Derived caches live under `~/.cache/vv/index/`, outside the vault. They are
checked against file metadata on each call and rebuilt when in doubt; there is
no daemon and no file watcher.

More: [engines and caches](https://github.com/jquezada19/vv-cli/blob/main/docs/architecture.md) ·
[cache format](https://github.com/jquezada19/vv-cli/blob/main/docs/cache-format.md) · [how it is tested](https://github.com/jquezada19/vv-cli/blob/main/docs/verification.md).

## Known limits

- Sections are ATX headings (`#` to `######`). Setext (underlined) headings are
  not sections.
- Frontmatter is read as simple one-line `key: value` fields. Inline lists are
  returned as text; block scalars, nested maps and multi-line lists are not
  interpreted. `set` quotes a value that YAML would misread, such as `a: b`.
- Two engines is a standing risk of drift. A behaviour change lands in both or
  not at all, and the gate is what shows the two still match.
- Timing grows with the number of files under the vault, not only notes.

## Contributing

`vv` keeps two implementations of one semantics, so a semantic change lands
twice or not at all — see [CONTRIBUTING.md](https://github.com/jquezada19/vv-cli/blob/main/CONTRIBUTING.md) for the gate, how
to run it against your own vault, and what a good change looks like. Bugs and
proposals go through the [issue tracker](https://github.com/jquezada19/vv-cli/issues); security
issues go [privately](https://github.com/jquezada19/vv-cli/blob/main/SECURITY.md).

## License

Dual-licensed under either of

- Apache License, Version 2.0 ([LICENSE-APACHE](LICENSE-APACHE))
- MIT license ([LICENSE-MIT](LICENSE-MIT))

at your option. Unless you explicitly state otherwise, any contribution
intentionally submitted for inclusion in this work by you shall be dual-licensed
as above, without any additional terms or conditions.
