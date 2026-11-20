# vv — a fast, terse, agent-friendly CLI for Obsidian vaults

[![CI](https://github.com/jquezada19/vv-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/jquezada19/vv-cli/actions/workflows/ci.yml)
[![License: MIT OR Apache-2.0](https://img.shields.io/badge/license-MIT%20OR%20Apache--2.0-blue.svg)](#license)

Use `vv` (**Velocity Vault**) to find a Markdown note, read a single section of
it, and patch that section against the hash you read. It works directly on the
vault's files: no running Obsidian app, no plugins. Renames, moves and trashes
show a plan before they change anything.

It is built for AI agents, where **every byte of output is context the model
pays for**. On a 1,634-note vault, reading one section returned 837 bytes
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

## Benchmarks

Median wall time and stdout bytes, for the four things an agent does to a
vault.

| task | `cat` / `grep` | ripgrep | Obsidian CLI | vv |
|---|---|---|---|---|
| read one section of a 16 KB note | 4 ms · 16,281 B (whole note) | — | 6 ms · 16,281 B (whole note) | 11 ms · **837 B** (`outline` + `read`) |
| search a common word | 944 ms · 1,299,895 B (every matching line) | 89 ms · 1,299,895 B | 327 ms · 2,575,356 B | 107 ms · **3,140 B** (top five, ranked) |
| set one frontmatter field | 32 ms · 16,282 B (a script that reads the note and writes it back) | — | — | **13 ms** · **35 B** |
| what links to this note | 449 ms · 12,195 B (files containing the text) | 82 ms · 12,195 B | **7 ms** · 267 B | 267 ms · 230 B (resolved links) |

Measured 2026-11-20 on vv 3.3.0: 1,634 notes (33 MB of Markdown), Apple M4
Pro, macOS 27, ripgrep 15.2, Obsidian 1.15, warm caches, median of 9 runs on a
laptop in normal use, not an idle one. The Obsidian CLI needs the app running
and answers for the vault the app has open, which here was a superset of the
benchmarked notes (about twice as many), so its search time and bytes are not
directly comparable with the other columns. To measure your own vault, from a
built checkout: `VV_VAULT=/path/to/vault python3 bench/bench.py --note "Some
Note.md" --term word --runs 9`.

How to read it:

- **The saving is in bytes, and the latency is small.** Calls finish in 11 to
  267 ms, which is noise next to a model turn, while a section read returns
  about 19 times less than the note and a search about 400 times less than the
  matching lines. A search returns five ranked snippets where `grep` and
  ripgrep return every hit; they answer different questions, and an agent
  usually wants the first.
- **Single-purpose tools still win on raw speed.** `cat` dumps a file faster,
  ripgrep scans text faster, and the Obsidian CLI wins backlinks outright
  because the running app holds the link graph in memory. vv trades those
  milliseconds for section-scoped, ranked output with no app running.
- **Backlinks check the whole vault.** vv has no background watcher: it lists
  the vault and opens each note to confirm it can read the whole link graph
  before answering, so its time grows with the number of files under the
  vault, notes or not.
- The `grep` and ripgrep backlink rows list files containing the text
  `[[Name`. That is not link resolution: it misses path-qualified links and
  counts text inside code fences and comments.

## Install

Requirements: macOS or Linux, and **Python 3.12 or newer** on `PATH` (standard
library only, no packages). `doctor` and `impact` also call `git`. Building
from source needs Rust (no crates).

Download the archive for your platform from the
[releases page](https://github.com/jquezada19/vv-cli/releases): Apple Silicon
macOS, or GNU/Linux on x86_64 or arm64. Keep the unpacked directory together;
the `vv` binary finds its Python half (`src/`) and `VERSION` next to itself.

```console
tar -xzf vv-3.3.0-aarch64-apple-darwin.tar.gz
cd vv-3.3.0-aarch64-apple-darwin
./vv --version
mkdir -p ~/.local/bin && ln -s "$PWD/vv" ~/.local/bin/vv    # symlink it; do not copy it out
```

On Intel macOS, build from source:

```console
git clone https://github.com/jquezada19/vv-cli && cd vv-cli
(cd vrust && cargo build --release)
mkdir -p ~/.local/bin && ln -s "$PWD/vrust/target/release/vrust" ~/.local/bin/vv
```

`python3 src/vv.py …` is the same CLI without the Rust build, with Python doing
the scanning.

### Quick start

These leave your notes unchanged (vv's cache, its metrics file and git's own
index can still be written). Name the vault with `VV_VAULT` or `--vault`;
without either, vv looks in `~/Documents/Obsidian Vault`.

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

> Use `vv` for the notes vault (it is set in `VV_VAULT`) instead of reading or
> editing the files directly. Find notes with `vv search <terms> --files`;
> skim one with `vv show NOTE`. Before editing a section, run
> `vv outline NOTE` and `vv read NOTE Hn`. To replace it, pipe the complete
> new section, heading included, to `vv patch NOTE Hn SHA8`, where SHA8 is the
> eight hex digits `read` printed on its last line. Do not include that
> `--sha8:` line in what you pipe: it would be written into the note. Use
> `vv set NOTE KEY VALUE` for a frontmatter field and `vv append NOTE TEXT` to
> add to the end. On exit 3 the note changed: run `vv outline` and `vv read`
> again and reconcile before retrying. On exit 4 a refactor journal is
> pending: stop writing and report the output of `vv doctor`. For `rename`,
> `move` or `trash`, run it without `--apply`, check the plan, then run it
> again with `--apply <plan id>`.

What a caller can rely on:

- **Exit codes.** `0` the command completed. `1` a refusal or failure: usage,
  a lookup that failed, an existing destination, or findings from
  `lint --quick --check`. `3` a check failed: the section hash, the file's
  size and time, or a refactor plan id. `4` a refactor journal is pending.
  `5` a note is not UTF-8. Nothing returns `2`; treat any other code as an
  unexpected failure.
- **Refusals go to stderr and start with a kind.** Most carry a runnable next
  step, and a failed lookup may add suggestions:

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
- **`--limit N`** caps the list commands, and a truncated list says so in its
  last line. For `search` it sets the default for `--k`; an explicit `--k`
  wins.
- **`vv batch < ops.jsonl`** runs many read operations through one start-up
  and prints one JSON result per operation, each with its own exit code
  (`vv batch --help`). **`vv changed --since <epoch|ISO>`** lists notes
  modified since a time; it cannot see a deletion or a rename.

## Commands

`NOTE` is a vault-relative path or an unambiguous note name. After exact
matches, a bare id such as `24995` (quote the `#` form: `'#24995'`) selects the
one note whose filename starts `24995 - `. Wikilinks do not use the id rule.
`vv <command> --help` prints usage for any command, and the release archive
ships a man page (`vv.1`) with every command's synopsis.

### Read

| command | what it does |
|---|---|
| `outline NOTE` | section map: id · level · title · size · sha8 |
| `read NOTE SEC` | one section, by outline id, heading title, a title prefix of three or more characters, or `--hash SHA8`; an ambiguous selector is refused and names the ids |
| `show NOTE [--max-bytes N] [--from Hn]` | a budgeted read of the whole note (default 4,000 bytes); when it stops early it names the section to resume from if there is room. `read NOTE` with no section is the same |
| `head NOTE` · `resolve NAME` | frontmatter only · name → path |
| `search TERMS [--k N] [--w C] [--files]` | ranked full text. Arguments are AND-ed; a quoted argument is one phrase; a match in the note's name is weighted heavily; a term containing `/` filters by path; `--files` prints one path per hit |

### Write

| command | what it does |
|---|---|
| `patch NOTE SEC SHA8 <stdin` | replace one section if its hash still matches; exit 3 if not |
| `append NOTE TEXT` | append at the end of the note |
| `appendsec NOTE SEC TEXT` | append inside a section (`append NOTE SEC TEXT` does the same) |
| `prepend NOTE TEXT` | insert after the frontmatter |
| `set NOTE KEY VALUE` · `unset NOTE KEY` | one frontmatter field; the body is untouched |
| `new PATH [--template T] [--KEY VALUE ...]` | create a note, optionally from a vault template |
| `daily-append TEXT` | append to the one note under `Standups/` whose filename has today's date, in its `Today` section if it has one (a convention of the author's vault) |

### Refactor (link-aware, journaled)

| command | what it does |
|---|---|
| `rename NOTE NEWNAME` | dry run: the files whose links will be rewritten, and a plan id |
| `move NOTE FOLDER` | the same for moving the note into a folder |
| `trash NOTE` | the same for removal to `.trash/`, listing the files whose links will break |
| `… --apply [PLAN]` | execute; with the plan id, exit 3 if the plan is no longer the one that was reviewed |
| `impact NOTE` | incoming links to this note, before a refactor |

One note per call. Links in forms vv does not parse (Canvas files, plugin
syntax, `[x](<file.md>)`) are left as they are, without a warning.

### Graph and query

| command | what it does |
|---|---|
| `backlinks NOTE` · `links NOTE` | notes that link here · wikilink targets in this note |
| `orphans [FOLDER]` · `deadends` · `unresolved` | nothing links in · nothing links out · wikilinks whose target name matches no note |
| `board FOLDER [KEY=VALUE ...]` | frontmatter table with filters |
| `tags [--counts]` · `props KEY [FOLDER]` | frontmatter tags · notes counted by a field's value |
| `templates` · `changed --since <epoch\|ISO>` | template names · paths changed since a time |

### Health

| command | what it does |
|---|---|
| `lint --quick [--check]` | wikilinks whose target name matches no note, and links whose `\|` breaks a table row; `--check` exits 1 on findings |
| `index [--rebuild]` | sync and report on the SQLite index some commands use · rebuild it |
| `doctor` | vault, journal, metrics and git status |
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

- **Stale writes are checked for.** `patch` compares the section's current
  hash with the one you pass. `set`, `unset`, `append`, `appendsec` and
  `prepend` also compare the file's size and modification time between
  reading and replacing it. When that check fails the edit is refused with
  exit 3, or re-read and applied to the current text. Obsidian is a second
  writer whenever the app is open, so this happens in practice. Writers are
  not serialized, and an edit that lands after the last check can still be
  overwritten.
- **Refactors are previewed, bound and journaled.** `rename`, `move` and
  `trash` print a plan first. `--apply <plan id>` recomputes the plan and
  refuses if the note or a file to be rewritten changed since the preview.
  Files are backed up before they are rewritten. A run that is killed leaves a
  journal that blocks further note writes (exit 4) until
  `vv doctor --rollback` restores what it can, keeping the journal if it finds
  an edit it would overwrite, or `--discard` drops the journal without
  restoring anything.
- **Named paths stay in the vault.** A note reference, or a rename or move
  destination, that resolves outside the vault, including through a symlink,
  is refused with `escape:`. Files vv finds by walking (search, link scans,
  templates) and the `.trash/` destination are not all checked this way: use
  vv on a vault whose contents you trust.
- **Bytes outside an edit are kept.** Line endings and everything outside the
  span an edit replaces are preserved. Reading or rewriting a note that is not
  UTF-8 is refused; searches and link scans decode such a note loosely.

## How it works

vv is a Rust binary with no dependencies, paired with a Python implementation
that uses only the standard library. The binary answers the common commands
itself (`outline`, section `read`, `show`, `head`, `resolve`, `search`,
`links`, `backlinks`, `orphans`, `deadends`, `board`, `tags`, `props`, `set`,
`unset`, `append`, `appendsec`, `patch`) and hands every other call, and any
input it is unsure about, to Python with the original arguments. Both
implement one CLI. Tests run the same cases through both and compare output,
exit codes and file changes; the few known differences are
[written down](https://github.com/jquezada19/vv-cli/blob/main/docs/verification.md).

Link rules (duplicate names, aliases, escaped pipes in tables, links inside
comments and code fences) were probed against Obsidian's own metadata cache,
with a short list of documented differences.

Derived caches live under `~/.cache/vv/index/` by default, outside the vault.
A command that uses one checks it against file metadata and rebuilds it when
in doubt; there is no daemon and no file watcher.

More: [engines and caches](https://github.com/jquezada19/vv-cli/blob/main/docs/architecture.md) ·
[cache format](https://github.com/jquezada19/vv-cli/blob/main/docs/cache-format.md) · [how it is tested](https://github.com/jquezada19/vv-cli/blob/main/docs/verification.md).

## Known limits

- Sections are ATX headings (`#` to `######`). Setext (underlined) headings are
  not sections.
- `head` prints frontmatter as it is. Queries (`board`, `props`, `tags`) read
  simple one-line `key: value` fields: inline lists come back as text, and
  block scalars, nested maps and multi-line lists are not interpreted. `set`
  quotes a value YAML would misread, such as `a: b`.
- `unresolved` and `lint --quick` check wikilinks by target name, not Markdown
  links, so a path-qualified link to a name that exists elsewhere passes.
- Keeping a Rust and a Python implementation risks drift. A behaviour change
  must land in both and pass the cross-engine tests.
- Commands that walk the vault slow down with the number of files under it,
  notes or not. A section read by exact path does not walk.

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
