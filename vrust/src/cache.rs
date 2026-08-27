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

const VERSION: &str = "2";
const SLOP_NS: u128 = 2_000_000_000;

pub struct FileLinks {
    pub utf8_ok: bool,
    pub links: Vec<(char, String)>,   // (kind w/m, raw target) — lossy-lexed
    sha: String,                      // content identity, computed at lex time
}

struct Row { mtime: u128, size: u64, ino: u64, sha: String }

fn cache_path(vault: &Path) -> Option<PathBuf> {
    let real = fs::canonicalize(vault).ok()?;
    let key = &sha256_hex(real.to_string_lossy().as_bytes())[..16];
    let home = std::env::var_os("HOME")?;
    Some(PathBuf::from(home).join(".cache/vv/index").join(format!("{}.vvidx", key)))
}

fn esc(s: &str) -> String {
    s.replace('\\', "\\\\").replace('\t', "\\t").replace('\n', "\\n").replace('\r', "\\r")
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
                Some(x) => { out.push('\\'); out.push(x); }
                None => out.push('\\'),
            }
        } else { out.push(c); }
    }
    out
}

fn stat_walk(vault: &Path) -> HashMap<String, (u128, u64, u64)> {
    let mut files = Vec::new();
    crate::walk_ex(vault, &mut files, false);
    let mut m = HashMap::new();
    for p in files {
        if let Ok(md) = fs::metadata(&p) {
            use std::os::unix::fs::MetadataExt;
            let mt = (md.mtime() as u128) * 1_000_000_000 + (md.mtime_nsec() as u128);
            let rp = p.strip_prefix(vault).unwrap_or(&p).to_string_lossy().into_owned();
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
    Some(FileLinks { utf8_ok, links: crate::graph::active_links(&text), sha })
}

/// Fresh link map for the vault, or None (caller live-scans).
pub fn links_map(vault: &Path) -> Option<HashMap<String, FileLinks>> {
    let cp = cache_path(vault)?;
    let disk = stat_walk(vault);
    let mut rows: HashMap<String, Row> = HashMap::new();
    let mut links: HashMap<String, FileLinks> = HashMap::new();
    let mut stamp: u128 = 0;
    if let Ok(t) = fs::read_to_string(&cp) {
        let mut ok = false;
        for (i, line) in t.lines().enumerate() {
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
                    rows.insert(unesc(f[1]), Row {
                        mtime: f[2].parse().ok()?, size: f[3].parse().ok()?,
                        ino: f[4].parse().ok()?, sha: f[5].to_string() });
                    links.entry(unesc(f[1])).or_insert(FileLinks {
                        utf8_ok: f[6] == "1", links: Vec::new(), sha: f[5].to_string() });
                }
                Some(&"L") if f.len() == 4 => {
                    let kind = if f[2] == "w" { 'w' } else { 'm' };
                    if let Some(fl) = links.get_mut(&unesc(f[1])) {
                        fl.links.push((kind, unesc(f[3])));
                    } // an L row before its F row: corrupt — but harmless to drop
                }
                _ => return rebuild(vault, &cp, &disk),   // corrupt row: full rebuild
            }
        }
        if !ok { return rebuild(vault, &cp, &disk); }
    } else {
        return rebuild(vault, &cp, &disk);
    }
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
                    if sha != r.sha { changed.push(rp.clone()); }
                }
            }
        }
    }
    let gone: Vec<String> = rows.keys().filter(|k| !disk.contains_key(*k)).cloned().collect();
    if changed.is_empty() && gone.is_empty() {
        return Some(links);
    }
    for rp in &gone { links.remove(rp); rows.remove(rp); }
    for rp in &changed {
        let fl = lex_file(vault, rp)?;
        links.insert(rp.clone(), fl);
    }
    write_cache(&cp, &disk, &links, vault)?;
    Some(links)
}

fn rebuild(vault: &Path, cp: &Path, disk: &HashMap<String, (u128, u64, u64)>)
           -> Option<HashMap<String, FileLinks>> {
    let mut links = HashMap::new();
    for rp in disk.keys() {
        links.insert(rp.clone(), lex_file(vault, rp)?);
    }
    write_cache(cp, disk, &links, vault)?;
    Some(links)
}

fn write_cache(cp: &Path, disk: &HashMap<String, (u128, u64, u64)>,
               links: &HashMap<String, FileLinks>, _vault: &Path) -> Option<()> {
    fs::create_dir_all(cp.parent()?).ok()?;
    let now: u128 = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH).ok()?.as_nanos();
    let mut buf = format!("vvidx\t{}\t{}\n", VERSION, now);
    let mut rps: Vec<&String> = links.keys().collect();
    rps.sort();
    for rp in rps {
        let fl = &links[rp];
        let (mt, sz, ino) = *disk.get(rp)?;
        buf.push_str(&format!("F\t{}\t{}\t{}\t{}\t{}\t{}\n",
            esc(rp), mt, sz, ino, fl.sha, if fl.utf8_ok { "1" } else { "0" }));
        for (k, t) in &fl.links {
            buf.push_str(&format!("L\t{}\t{}\t{}\n", esc(rp), k, esc(t)));
        }
    }
    let tmp = cp.with_extension("vvidx.tmp");
    let mut f = fs::File::create(&tmp).ok()?;
    f.write_all(buf.as_bytes()).ok()?;
    f.sync_all().ok();
    fs::rename(&tmp, cp).ok()?;
    Some(())
}
