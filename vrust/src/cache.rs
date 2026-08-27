// cache.rs — native vvidx link cache (rewrite phase 2, 2026-08-27).
//
// Own file, own format, disposable: `~/.cache/vv/index/<sha16>.vvidx`, TSV,
// version-headed, delete-don't-repair. NEVER shares python's SQLite DB (two
// writers on one cache is how caches lie). Freshness = per-invocation stat
// walk diffed by (mtime_ns, size, ino) EQUALITY + git's racily-clean re-hash
// (sha256, stored per file) for anything whose mtime ties the commit stamp.
// Any doubt at all -> None, and the caller uses its parity-proven live scan.
use crate::readpath::sha256_hex;
use std::collections::HashMap;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

const VERSION: &str = "3";
const SLOP_NS: u128 = 2_000_000_000;

pub struct FileLinks {
    pub utf8_ok: bool,
    pub links: Vec<(char, String)>, // (kind w/m, raw target) — lossy-lexed
    sha: String,                    // content identity, computed at lex time
}

impl FileLinks {
    /// Experiment arm C builds these from SQL rows (sha unused on that path).
    pub fn new(utf8_ok: bool, links: Vec<(char, String)>) -> Self {
        FileLinks {
            utf8_ok,
            links,
            sha: String::new(),
        }
    }
}

struct Row {
    mtime: u128,
    size: u64,
    ino: u64,
    sha: String,
}

fn cache_path(vault: &Path) -> Option<PathBuf> {
    let real = fs::canonicalize(vault).ok()?;
    let key = &sha256_hex(real.to_string_lossy().as_bytes())[..16];
    let home = std::env::var_os("HOME")?;
    Some(
        PathBuf::from(home)
            .join(".cache/vv/index")
            .join(format!("{}.vvidx", key)),
    )
}

/// Integrity checksum over the cache body. Guards against a TORN WRITE, not an
/// adversary, so it is not cryptographic — but it IS on every read, so it runs
/// 8 bytes at a time. Byte-at-a-time FNV-1a measured 1.75 ms over this vault's
/// 1.2 MB body; that cost is charged to every invocation, not just writes.
fn fnv1a64(b: &[u8]) -> u64 {
    let mut h: u64 = 0x9e3779b97f4a7c15 ^ (b.len() as u64);
    let mut it = b.chunks_exact(8);
    for w in &mut it {
        let v = u64::from_le_bytes(w.try_into().unwrap());
        h ^= v.wrapping_mul(0xff51afd7ed558ccd);
        h = h.rotate_left(31).wrapping_mul(0xc4ceb9fe1a85ec53);
    }
    for &x in it.remainder() {
        h = (h ^ x as u64).wrapping_mul(0x100000001b3);
    }
    h
}

fn esc(s: &str) -> String {
    s.replace('\\', "\\\\")
        .replace('\t', "\\t")
        .replace('\n', "\\n")
        .replace('\r', "\\r")
}
fn unesc(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut it = s.chars();
    while let Some(c) = it.next() {
        if c == '\\' {
            match it.next() {
                Some('t') => out.push('\t'),
                Some('n') => out.push('\n'),
                Some('r') => out.push('\r'),
                Some('\\') => out.push('\\'),
                Some(x) => {
                    out.push('\\');
                    out.push(x);
                }
                None => out.push('\\'),
            }
        } else {
            out.push(c);
        }
    }
    out
}

/// Split off and check the integrity footer; returns the body, or None if the
/// file is truncated/torn (caller rebuilds).
fn verify(t: &str) -> Option<usize> {
    let end = t.rfind("\nvvidx-end\t")?;
    let body = &t[..end + 1];
    let f: Vec<&str> = t[end + 1..].trim_end_matches('\n').split('\t').collect();
    if f.len() != 3 || f[0] != "vvidx-end" {
        return None;
    }
    if f[1].parse::<usize>().ok()? != body.len() {
        return None;
    }
    if u64::from_str_radix(f[2], 16).ok()? != fnv1a64(body.as_bytes()) {
        return None;
    }
    Some(end + 1) // body is t[..this]; no copy
}

fn stat_walk(vault: &Path) -> HashMap<String, (u128, u64, u64)> {
    let mut files = Vec::new();
    crate::walk_ex(vault, &mut files, false);
    let mut m = HashMap::new();
    for p in files {
        if let Ok(md) = fs::metadata(&p) {
            use std::os::unix::fs::MetadataExt;
            let mt = (md.mtime() as u128) * 1_000_000_000 + (md.mtime_nsec() as u128);
            let rp = p
                .strip_prefix(vault)
                .unwrap_or(&p)
                .to_string_lossy()
                .into_owned();
            m.insert(rp, (mt, md.len(), md.ino()));
        }
    }
    m
}

fn lex_file(vault: &Path, rp: &str) -> Option<FileLinks> {
    let bytes = fs::read(vault.join(rp)).ok()?;
    let sha = sha256_hex(&bytes);
    let utf8_ok = std::str::from_utf8(&bytes).is_ok();
    let text = String::from_utf8_lossy(&bytes).into_owned();
    Some(FileLinks {
        utf8_ok,
        links: crate::graph::active_links(&text),
        sha,
    })
}

/// ARM D: targeted TSV read — same needle filter sqlq pushes into SQL, done
/// during the line scan so non-candidate L rows are never unescaped/allocated.
/// Returns (candidates, utf8_ok map) or None (caller live-scans).
pub fn backlink_candidates(
    vault: &Path,
    needle: &str,
) -> Option<(Vec<(String, char, String)>, HashMap<String, bool>)> {
    let full = links_map(vault)?; // freshness + sync, identical rules
    let m = std::time::Instant::now();
    let mut u8ok = HashMap::new();
    let mut out = Vec::new();
    for (rp, fl) in &full {
        u8ok.insert(rp.clone(), fl.utf8_ok);
        for (k, t) in &fl.links {
            if *k == 'w' && !t.to_lowercase().contains(needle) {
                continue;
            }
            out.push((rp.clone(), *k, t.clone()));
        }
    }
    if std::env::var_os("VV_PROF").is_some() {
        eprintln!("PROF\ttsv:candidates\t{}", out.len());
    }
    prof("tsv:targeted", m);
    Some((out, u8ok))
}

/// Fresh link map for the vault, or None (caller live-scans).
pub fn links_map(vault: &Path) -> Option<HashMap<String, FileLinks>> {
    let mut __m = std::time::Instant::now();
    let cp = cache_path(vault)?;
    let disk = stat_walk(vault);
    prof("stat_walk", __m);
    __m = std::time::Instant::now();
    let mut rows: HashMap<String, Row> = HashMap::new();
    let mut links: HashMap<String, FileLinks> = HashMap::new();
    let mut stamp: u128 = 0;
    if let Some((t, blen)) = fs::read_to_string(&cp).ok().and_then(|t| {
        let b = verify(&t)?;
        Some((t, b))
    }) {
        prof("cache_read", __m);
        __m = std::time::Instant::now();
        let mut ok = false;
        for (i, line) in t[..blen].lines().enumerate() {
            let f: Vec<&str> = line.split('\t').collect();
            if i == 0 {
                if f.len() == 3 && f[0] == "vvidx" && f[1] == VERSION {
                    stamp = f[2].parse().ok()?;
                    ok = true;
                    continue;
                }
                break; // wrong version/corrupt header: rebuild below
            }
            match f.first() {
                Some(&"F") if f.len() == 7 => {
                    rows.insert(
                        unesc(f[1]),
                        Row {
                            mtime: f[2].parse().ok()?,
                            size: f[3].parse().ok()?,
                            ino: f[4].parse().ok()?,
                            sha: f[5].to_string(),
                        },
                    );
                    links.entry(unesc(f[1])).or_insert(FileLinks {
                        utf8_ok: f[6] == "1",
                        links: Vec::new(),
                        sha: f[5].to_string(),
                    });
                }
                Some(&"L") if f.len() == 4 => {
                    let kind = if f[2] == "w" { 'w' } else { 'm' };
                    if let Some(fl) = links.get_mut(&unesc(f[1])) {
                        fl.links.push((kind, unesc(f[3])));
                    } // an L row before its F row: corrupt — but harmless to drop
                }
                _ => return rebuild(vault, &cp, &disk), // corrupt row: full rebuild
            }
        }
        if !ok {
            return rebuild(vault, &cp, &disk);
        }
    } else {
        return rebuild(vault, &cp, &disk);
    }
    prof("tsv_parse", __m);
    __m = std::time::Instant::now();
    // diff: equality on the triple; racily-clean re-hash near the stamp
    let mut changed: Vec<String> = Vec::new();
    for (rp, st) in &disk {
        match rows.get(rp) {
            None => changed.push(rp.clone()),
            Some(r) => {
                if (r.mtime, r.size, r.ino) != (st.0, st.1, st.2) {
                    changed.push(rp.clone());
                } else if st.0 + SLOP_NS >= stamp {
                    let sha = sha256_hex(&fs::read(vault.join(rp)).ok()?);
                    if sha != r.sha {
                        changed.push(rp.clone());
                    }
                }
            }
        }
    }
    let gone: Vec<String> = rows
        .keys()
        .filter(|k| !disk.contains_key(*k))
        .cloned()
        .collect();
    prof("diff", __m);
    __m = std::time::Instant::now();
    if changed.is_empty() && gone.is_empty() {
        prof("nchanged=0", __m);
        return Some(links);
    }
    for rp in &gone {
        links.remove(rp);
        rows.remove(rp);
    }
    for rp in &changed {
        let fl = lex_file(vault, rp)?;
        links.insert(rp.clone(), fl);
    }
    if std::env::var_os("VV_PROF").is_some() {
        eprintln!("PROF\tnchanged\t{}", changed.len());
    }
    prof("lex_changed", __m);
    __m = std::time::Instant::now();
    write_cache(&cp, &disk, &links, vault)?;
    prof("cache_write", __m);
    Some(links)
}

fn rebuild(
    vault: &Path,
    cp: &Path,
    disk: &HashMap<String, (u128, u64, u64)>,
) -> Option<HashMap<String, FileLinks>> {
    let mut links = HashMap::new();
    for rp in disk.keys() {
        links.insert(rp.clone(), lex_file(vault, rp)?);
    }
    write_cache(cp, disk, &links, vault)?;
    Some(links)
}

fn write_cache(
    cp: &Path,
    disk: &HashMap<String, (u128, u64, u64)>,
    links: &HashMap<String, FileLinks>,
    _vault: &Path,
) -> Option<()> {
    let __w = std::time::Instant::now();
    fs::create_dir_all(cp.parent()?).ok()?;
    let now: u128 = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .ok()?
        .as_nanos();
    use std::fmt::Write as _;
    let mut buf = String::with_capacity(1 << 21);
    let _ = write!(buf, "vvidx\t{}\t{}\n", VERSION, now);
    let mut rps: Vec<&String> = links.keys().collect();
    rps.sort();
    for rp in rps {
        let fl = &links[rp];
        let (mt, sz, ino) = *disk.get(rp)?;
        let e_rp = esc(rp);
        let _ = write!(
            buf,
            "F\t{}\t{}\t{}\t{}\t{}\t{}\n",
            e_rp,
            mt,
            sz,
            ino,
            fl.sha,
            if fl.utf8_ok { "1" } else { "0" }
        );
        for (k, t) in &fl.links {
            let _ = write!(buf, "L\t{}\t{}\t{}\n", e_rp, k, esc(t));
        }
    }
    prof("  w:format", __w);
    let __w2 = std::time::Instant::now();
    // Integrity footer. Without it a crash can leave a RECORD-ALIGNED PREFIX:
    // valid header, every surviving row well-formed, and an F row whose own
    // trailing L rows were lost. That file passes the per-file (mtime,size,ino)
    // check — so it is never re-lexed — and silently serves missing links.
    // Demonstrated 2026-08-27 (a dropped backlink python still found), which is
    // why the fsync below could only be removed once this footer existed.
    let body_len = buf.len();
    let h = fnv1a64(buf.as_bytes());
    let _ = write!(buf, "vvidx-end\t{}\t{:016x}\n", body_len, h);

    let tmp = cp.with_extension("vvidx.tmp");
    let mut f = fs::File::create(&tmp).ok()?;
    f.write_all(buf.as_bytes()).ok()?;
    prof("  w:write", __w2);
    let __w3 = std::time::Instant::now();
    // no fsync: disposable, delete-don't-repair cache; rename() is atomic
    if std::env::var_os("VV_FSYNC").is_some() {
        f.sync_all().ok();
    }
    prof("  w:fsync", __w3);
    let __w4 = std::time::Instant::now();
    fs::rename(&tmp, cp).ok()?;
    prof("  w:rename", __w4);
    Some(())
}

// --- experiment-only instrumentation (VV_PROF=1 -> stderr phase timings) ---
pub fn prof(label: &str, t: std::time::Instant) {
    if std::env::var_os("VV_PROF").is_some() {
        eprintln!("PROF\t{}\t{:.3}", label, t.elapsed().as_secs_f64() * 1000.0);
    }
}
