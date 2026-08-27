# The vvidx cache: format, integrity, and why it is not SQLite

The native engine keeps a link cache at `~/.cache/vv/index/<sha16>.vvidx` — TSV,
version-headed, disposable. This note records the format's integrity contract
and the measured reason it is not backed by SQLite.

## Format (version 3)

```
vvidx	3	<stamp_ns>
F	<path>	<mtime_ns>	<size>	<ino>	<sha256>	<utf8_ok>
L	<path>	<w|m>	<target>
...
vvidx-end	<body_bytes>	<checksum_hex>
```

`F` rows carry file identity; `L` rows carry that file's links, immediately
after its `F` row. Tabs, newlines and backslashes inside fields are escaped.

## The two-layer freshness contract

1. **Per invocation**, every file in the corpus is `stat`ed and compared to its
   `F` row by **equality** on `(mtime_ns, size, ino)` — never `>` against a
   watermark, so a `git checkout` or a cloud-sync timestamp rewind is caught.
   Files whose mtime lands within 2 s of the stamp are re-hashed (git's "racily
   clean" guard). Any mismatch re-lexes that file; missing files are dropped.
2. **The integrity footer** guards the cache as a whole.

Layer 1 alone is not sufficient, and that is the whole reason layer 2 exists.

### The hole the footer closes

Layer 1 proves each *surviving* `F` row still describes its file. It does not
prove that row's own `L` rows survived. So a crash mid-write can leave a
**record-aligned prefix**: valid header, every surviving row well-formed, and
one `F` row whose trailing `L` rows were lost. That file passes every structural
check and passes the stat comparison — so it is never re-lexed — and silently
serves *missing links*.

This is not theoretical. Reproduced on the real vault 2026-08-27: truncating one
record's `L` rows made `backlinks` drop a link that the Python engine still
found. Raised by an adversarial review of a proposal to drop the write's
`fsync`; the proposal's safety argument ("any corruption fails to parse and
rebuilds") was wrong, and the reproduction is now `tests/test_cache_integrity.py`.

The footer records the body's **length** and a **checksum**, and both are
load-bearing — each was confirmed by disabling it and watching the suite fail:

| Failure | Caught by |
|---|---|
| truncation / lost tail | footer presence + body length |
| same-length corruption of a link row | checksum |

Because a torn write is now *detectable*, `fsync` before the `rename` is no
longer what protects correctness, and it is not performed. A crash can lose the
newest cache; it cannot produce a cache that lies. `VV_FSYNC=1` restores it for
A/B measurement.

The checksum is FNV-flavoured but consumes 8 bytes at a time: it runs on **every
read**, and a byte-at-a-time FNV-1a measured 1.75 ms over this vault's 1.2 MB
body versus ~0.3 ms for the word-at-a-time version. It is an integrity check,
not a security boundary.

A version bump rebuilds automatically — v2 caches are rejected by the header
check and re-derived on first use. Nothing needs to be migrated or cleared.

## Why not SQLite (measured 2026-08-27)

A full `rusqlite` arm was built and benchmarked against the TSV cache on
`backlinks`, interleaved, over 1,502 notes / 10,860 links / 1.2 MB of cache.
Parity was checked on a 120-note three-arm sweep plus a 30-note Python-oracle
check: 0 divergences.

Where the time actually goes, steady state: **stat_walk 7.6 ms**, tsv_parse
4.3 ms, read 0.3 ms, diff 0.13 ms. The stat walk is the floor, and *no* cache
backend removes it — SQLite included.

|  | steady | 1 file changed |
|---|---|---|
| TSV, as it shipped | 23.90 ms | 34.13 ms |
| TSV + footer, tuned write, no fsync (**current**) | 24.26 ms | 28.71 ms |
| rusqlite, targeted query | 22.11 ms | 23.07 ms |

The changed-file cost was never query speed — it was **write amplification**:
the TSV rewrites the entire 1.2 MB for a one-file edit (`format!` churn 6.7 ms +
fsync 6.2 ms). Tuning the serialization and removing the now-unnecessary fsync
recovered most of it with no dependency.

What SQLite still buys, after that tuning: **2.2 ms steady, 5.6 ms changed**.

What it costs: 25 transitive crates (`wasm-bindgen`, `syn`, `proc-macro2` among
them), binary 812 KB → 2.68 MB, clean build 6.5 s → 25.8 s, cache file 1.2 MB →
2.58 MB, and the zero-dependency property that makes this engine auditable in an
afternoon. Not worth 2.2 ms.

**Tripwire — this verdict is scale-dependent and will expire.** The TSV rewrite
is `O(corpus)` per edit; SQLite's update is `O(changed)`. The gap therefore grows
linearly with the vault. Revisit when either holds:

- the corpus passes **~5,000 notes**, or
- the changed-path write (`VV_PROF=1`, the `w:` rows) exceeds **~15 ms**.

The experiment is preserved in full on the `exp/rusqlite` branch — five arms,
both cache backends, and the targeted-query variant — so re-testing is a
checkout, not a rebuild.

## Measuring

`VV_PROF=1 vv backlinks <note>` prints phase timings to stderr: `stat_walk`,
`cache_read`, `tsv_parse`, `diff`, `lex_changed`, `cache_write`, and the write's
internal `w:format` / `w:write` / `w:fsync` / `w:rename` split.
