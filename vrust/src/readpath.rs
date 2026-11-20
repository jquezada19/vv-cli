// Native read path: outline / read SEC / head / resolve — E2 experiment 2026-08-27.
//
// PARITY STRATEGY: this module implements ONLY the happy path, ported
// line-for-line from vv_impl.py (parse/fence_mask/fm_bounds/find_sec/sec_text/
// sha8/read_raw/resolve). On ANYTHING unusual — name miss, ambiguity, section
// miss, non-UTF-8, path escape, io error — it returns Fallback and main()
// execs the Python implementation, which stays the authority for the error
// grammar, did-you-mean suggestions (difflib is not exactly portable), and
// exit codes. The exceptions are the refusals this entry words itself through
// err.rs, each byte-identical to python's and pinned in tests/golden/.
#![allow(dead_code)]
use std::fs;
use std::path::{Path, PathBuf};

pub enum Outcome {
    Done(i32),
    Fallback,
}

// ---------- sha256 (std-only; FIPS 180-4) ----------
const K: [u32; 64] = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
];

pub fn sha256_hex(data: &[u8]) -> String {
    let mut h: [u32; 8] = [
        0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab,
        0x5be0cd19,
    ];
    let bitlen = (data.len() as u64) * 8;
    let mut msg = data.to_vec();
    msg.push(0x80);
    while msg.len() % 64 != 56 {
        msg.push(0);
    }
    msg.extend_from_slice(&bitlen.to_be_bytes());
    for chunk in msg.chunks(64) {
        let mut w = [0u32; 64];
        for i in 0..16 {
            w[i] = u32::from_be_bytes([
                chunk[4 * i],
                chunk[4 * i + 1],
                chunk[4 * i + 2],
                chunk[4 * i + 3],
            ]);
        }
        for i in 16..64 {
            let s0 = w[i - 15].rotate_right(7) ^ w[i - 15].rotate_right(18) ^ (w[i - 15] >> 3);
            let s1 = w[i - 2].rotate_right(17) ^ w[i - 2].rotate_right(19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16]
                .wrapping_add(s0)
                .wrapping_add(w[i - 7])
                .wrapping_add(s1);
        }
        let (mut a, mut b, mut c, mut d, mut e, mut f, mut g, mut hh) =
            (h[0], h[1], h[2], h[3], h[4], h[5], h[6], h[7]);
        for i in 0..64 {
            let s1 = e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25);
            let ch = (e & f) ^ ((!e) & g);
            let t1 = hh
                .wrapping_add(s1)
                .wrapping_add(ch)
                .wrapping_add(K[i])
                .wrapping_add(w[i]);
            let s0 = a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22);
            let maj = (a & b) ^ (a & c) ^ (b & c);
            let t2 = s0.wrapping_add(maj);
            hh = g;
            g = f;
            f = e;
            e = d.wrapping_add(t1);
            d = c;
            c = b;
            b = a;
            a = t1.wrapping_add(t2);
        }
        h[0] = h[0].wrapping_add(a);
        h[1] = h[1].wrapping_add(b);
        h[2] = h[2].wrapping_add(c);
        h[3] = h[3].wrapping_add(d);
        h[4] = h[4].wrapping_add(e);
        h[5] = h[5].wrapping_add(f);
        h[6] = h[6].wrapping_add(g);
        h[7] = h[7].wrapping_add(hh);
    }
    h.iter().map(|x| format!("{:08x}", x)).collect()
}

pub fn sha8(t: &str) -> String {
    sha256_hex(t.as_bytes())[..8].to_string()
}

// ---------- md structure, ported line-for-line from vv_impl.py ----------
const BOM: char = '\u{feff}';

pub fn fm_bounds(lines: &[&str]) -> usize {
    if lines.is_empty() {
        return 0;
    }
    let first = lines[0].trim_start_matches(BOM).trim_end_matches('\r');
    if first != "---" {
        return 0;
    }
    for i in 1..lines.len() {
        if lines[i].trim_end_matches('\r') == "---" {
            return i + 1;
        }
    }
    0
}

fn fence_line(l: &str) -> Option<(char, usize, &str)> {
    // ^ {0,3}(`{3,}|~{3,})(.*)$  — ASCII spaces only
    let mut idx = 0;
    let b = l.as_bytes();
    while idx < 3 && idx < b.len() && b[idx] == b' ' {
        idx += 1;
    }
    let rest = &l[idx..];
    let ch = rest.chars().next()?;
    if ch != '`' && ch != '~' {
        return None;
    }
    let run = rest.chars().take_while(|&c| c == ch).count();
    if run < 3 {
        return None;
    }
    Some((ch, run, &rest[run..]))
}

pub fn fence_mask(lines: &[&str], start: usize) -> Vec<bool> {
    let mut masked = vec![false; lines.len()];
    let mut marker: Option<(char, usize)> = None;
    for i in start..lines.len() {
        let l = lines[i].trim_end_matches('\r');
        let m = fence_line(l);
        match marker {
            None => {
                if let Some((ch, n, info)) = m {
                    // backtick fence's info string may not contain backticks
                    if ch == '~' || !info.contains('`') {
                        marker = Some((ch, n));
                        masked[i] = true;
                    }
                }
            }
            Some((mc, mn)) => {
                masked[i] = true;
                if let Some((ch, n, info)) = m {
                    if ch == mc && n >= mn && info.trim().is_empty() {
                        marker = None;
                    }
                }
            }
        }
    }
    masked
}

pub struct Sec {
    pub id: String,
    pub level: usize,
    pub title: String,
    pub start: usize,
    pub end: usize,
}

pub fn heading(l: &str) -> Option<(usize, &str)> {
    // ^(#{1,6})\s+(.*)$ — python \s matches unicode ws but headings use ASCII;
    // match python: any char with is_whitespace() after the hashes.
    let b = l.as_bytes();
    let n = b.iter().take_while(|&&c| c == b'#').count();
    if n == 0 || n > 6 {
        return None;
    }
    let rest = &l[n..];
    let mut ch = rest.chars();
    match ch.next() {
        Some(c) if c.is_whitespace() && c != '\n' => Some((n, ch.as_str())),
        _ => None,
    }
}

pub fn parse(text: &str) -> (Vec<&str>, Vec<Sec>) {
    let lines: Vec<&str> = text.split('\n').collect();
    let fm_end = fm_bounds(&lines);
    let mut fenced = fence_mask(&lines, fm_end);
    for i in 0..fm_end {
        fenced[i] = true;
    }
    let mut heads: Vec<(usize, usize, String)> = Vec::new();
    for (i, l) in lines.iter().enumerate() {
        if fenced[i] {
            continue;
        }
        if let Some((lvl, t)) = heading(l) {
            heads.push((i, lvl, t.trim().to_string()));
        }
    }
    let first = heads.first().map_or(lines.len(), |h| h.0);
    let mut secs = vec![Sec {
        id: "H0".into(),
        level: 0,
        title: "(preamble)".into(),
        start: 0,
        end: first,
    }];
    for (j, (i, lvl, title)) in heads.iter().enumerate() {
        let end = heads.get(j + 1).map_or(lines.len(), |h| h.0);
        secs.push(Sec {
            id: format!("H{}", j + 1),
            level: *lvl,
            title: title.clone(),
            start: *i,
            end,
        });
    }
    (lines, secs)
}

pub fn sec_text(lines: &[&str], s: &Sec) -> String {
    lines[s.start..s.end].join("\n")
}

// find_sec: one total order, mirroring vv_impl.py's _match_sec —
// id -> (preamble) -> exact title -> content sha8 -> unique title prefix.
// Ok carries the SECTION and the TIER that answered (the metrics row's
// selector). Any miss — nothing matched, or a tier matched more than once —
// returns Err and the caller falls back so python emits the one canonical
// refusal, which is the only place the two reasons are told apart.
pub struct Miss;

// Below this a prefix selects almost anything, so it is never tried.
const PREFIX_MIN: usize = 3;

pub fn find_sec<'a>(
    lines: &[&str],
    secs: &'a [Sec],
    sid: &str,
) -> Result<(&'a Sec, &'static str), Miss> {
    find_sec_opts(lines, secs, sid, false)
}

// `hash_only` collapses the order to the sha8 tier alone, for `read --hash X`:
// the caller is saying the token is CONTENT, so a section whose TITLE is 8 hex
// characters must not intercept a hash taken from the outline's 5th column.
pub fn find_sec_opts<'a>(
    lines: &[&str],
    secs: &'a [Sec],
    sid: &str,
    hash_only: bool,
) -> Result<(&'a Sec, &'static str), Miss> {
    if !hash_only {
        for s in secs {
            if s.id == sid {
                return Ok((s, "id"));
            }
        }
    }
    let mut want = sid.trim().to_string();
    if want.starts_with('#') {
        want = want.trim_start_matches('#').trim().to_string();
    }
    let wl = want.to_lowercase();
    if !hash_only {
        if wl == "(preamble)" || wl == "preamble" {
            return secs
                .iter()
                .find(|s| s.title == "(preamble)" || s.id == "H0")
                .map(|s| (s, "preamble"))
                .ok_or(Miss);
        }
        let matches: Vec<&Sec> = secs
            .iter()
            .filter(|s| s.title.trim().to_lowercase() == wl)
            .collect();
        if !matches.is_empty() {
            return only(&matches, "title");
        }
    }
    // sha8 sits BELOW exact title: a heading that happens to be 8 hex
    // characters is what someone typing those 8 characters means.
    if want.len() == 8 && want.chars().all(|c| c.is_ascii_hexdigit()) {
        let hits: Vec<&Sec> = secs
            .iter()
            .filter(|s| sha8(&sec_text(lines, s)) == wl)
            .collect();
        if !hits.is_empty() {
            return only(&hits, "sha8");
        }
    }
    let n = want.chars().count();
    if !hash_only && n >= PREFIX_MIN {
        let hits: Vec<&Sec> = secs
            .iter()
            .filter(|s| s.title.trim().to_lowercase().starts_with(&wl))
            .collect();
        if !hits.is_empty() {
            // a token that ends at a word break outranks one stopping
            // mid-word, so `Today` reaches `Today (Tuesday)` past `Today's
            // plan`; when the break singles out nobody, every prefix hit
            // counts, so the refusal names what the caller is choosing between
            let whole: Vec<&Sec> = hits
                .iter()
                .copied()
                .filter(|s| ends_at_break(s.title.trim(), &want))
                .collect();
            if whole.len() == 1 {
                return Ok((whole[0], "prefix"));
            }
            return only(&hits, "prefix");
        }
    }
    Err(Miss)
}

// read's flag-spelled SEC operand as (selector, hash_only), mirroring
// _peel_read_flags in vv_impl.py -- but only its ACCEPTING half: every
// refusal (an unknown flag, a missing or non-hex value, two selectors) is
// None here, so python emits the one canonical usage text.
fn peel_read(args: &[String]) -> Option<(String, bool)> {
    let (name, val) = match args.len() {
        2 => {
            let (n, v) = args[1].split_once('=')?;
            (n.to_string(), v.to_string())
        }
        3 => (args[1].clone(), args[2].clone()),
        _ => return None,
    };
    if val.is_empty() {
        return None;
    }
    let hash_only = match name.as_str() {
        "--section" => false,
        "--hash" => true,
        _ => return None,
    };
    if hash_only && !(val.len() == 8 && val.chars().all(|c| c.is_ascii_hexdigit())) {
        return None;
    }
    Some((val, hash_only))
}

fn only<'a>(hits: &[&'a Sec], kind: &'static str) -> Result<(&'a Sec, &'static str), Miss> {
    if hits.len() == 1 {
        Ok((hits[0], kind))
    } else {
        Err(Miss)
    }
}

// True when `want` stops at the end of `title` or at a word break in it. `(` is
// a break because the parenthetical is what a caller leaves off.
//
// Measured on the FOLDED text, using the folded selector's char count: the tier
// above matched case-insensitively, and a case fold can change a string's
// length (a dotted capital I folds to two characters). An offset taken from the
// original title at the raw selector's length then points at the wrong
// character, and two headings that fold to the same text stop being ambiguous —
// one of them wins by an accident of encoding, on the resolver that feeds the
// write path.
fn ends_at_break(title: &str, want: &str) -> bool {
    let n = want.to_lowercase().chars().count();
    match title.to_lowercase().chars().nth(n) {
        None => true,
        Some(c) => c == ' ' || c == '(',
    }
}

// ---------- resolve ----------
pub fn contain(vault: &Path, rel: &str) -> Option<PathBuf> {
    let full = vault.join(rel);
    let real = fs::canonicalize(&full).ok()?;
    let vreal = fs::canonicalize(vault).ok()?;
    if real == vreal || real.starts_with(&vreal) {
        Some(full)
    } else {
        None
    }
}

/// python's `os.path.realpath` (non-strict), on bytes: every symlink that can
/// be read is followed, a component that does not exist is kept as written,
/// and a symlink loop is left unresolved.
fn realpath(filename: &[u8]) -> Vec<u8> {
    use std::ffi::OsStr;
    use std::os::unix::ffi::OsStrExt;
    let split = |b: &[u8]| -> Vec<Option<Vec<u8>>> {
        b.split(|c| *c == b'/')
            .rev()
            .map(|p| Some(p.to_vec()))
            .collect()
    };
    // unresolved parts, last first; a `None` marks "the link below me is now resolved"
    let mut rest = split(filename);
    let mut part_count = rest.len();
    let mut path: Vec<u8> = if filename.starts_with(b"/") {
        b"/".to_vec()
    } else {
        std::env::current_dir()
            .map(|d| d.as_os_str().as_bytes().to_vec())
            .unwrap_or_else(|_| b"/".to_vec())
    };
    let mut seen: std::collections::HashMap<Vec<u8>, Option<Vec<u8>>> =
        std::collections::HashMap::new();
    while part_count > 0 {
        let Some(name) = rest.pop().flatten() else {
            if let Some(Some(link)) = rest.pop() {
                seen.insert(link, Some(path.clone()));
            }
            continue;
        };
        part_count -= 1;
        if name.is_empty() || name == b"." {
            continue;
        }
        if name == b".." {
            let cut = path.iter().rposition(|c| *c == b'/').unwrap_or(0);
            path.truncate(cut.max(1));
            continue;
        }
        let mut newpath = path.clone();
        if path != b"/" {
            newpath.push(b'/');
        }
        newpath.extend_from_slice(&name);
        let is_link = fs::symlink_metadata(OsStr::from_bytes(&newpath))
            .map(|m| m.file_type().is_symlink())
            .unwrap_or(false);
        if !is_link {
            path = newpath;
            continue;
        }
        if let Some(known) = seen.get(&newpath) {
            // resolved before: reuse it; still open: a loop, kept as written
            path = known.clone().unwrap_or(newpath);
            continue;
        }
        match fs::read_link(OsStr::from_bytes(&newpath)) {
            Ok(target) => {
                let t = target.as_os_str().as_bytes();
                if t.starts_with(b"/") {
                    path = b"/".to_vec();
                }
                seen.insert(newpath.clone(), None);
                rest.push(Some(newpath));
                rest.push(None);
                let parts = split(t);
                part_count += parts.len();
                rest.extend(parts);
            }
            Err(_) => path = newpath,
        }
    }
    path
}

/// python's `real != _VAULT_REAL and not real.startswith(_VAULT_REAL + os.sep)`,
/// negated.
fn within(vault: &Path, full: &[u8]) -> bool {
    use std::os::unix::ffi::OsStrExt;
    let real = realpath(full);
    let mut vreal = realpath(vault.as_os_str().as_bytes());
    if real == vreal {
        return true;
    }
    vreal.push(b'/');
    real.starts_with(&vreal)
}

/// python's `contain` for a TYPED reference: the joined path, or `None` when
/// it leaves the vault. Unlike `contain` above, a path that does not exist is
/// judged by where it would be.
fn typed(vault: &Path, rel: &str) -> Option<PathBuf> {
    use std::os::unix::ffi::OsStrExt;
    let full = if rel.starts_with('/') {
        PathBuf::from(rel)
    } else {
        let mut b = vault.as_os_str().as_bytes().to_vec();
        if !b.ends_with(b"/") {
            b.push(b'/');
        }
        b.extend_from_slice(rel.as_bytes());
        PathBuf::from(std::ffi::OsStr::from_bytes(&b))
    };
    within(vault, full.as_os_str().as_bytes()).then_some(full)
}

/// python's `_WRITE_OPS`: a write through a name the walk could not prove
/// unique is refused, where a read answers with a warning.
const WRITE_OPS: [&str; 10] = [
    "set",
    "unset",
    "append",
    "appendsec",
    "prepend",
    "patch",
    "rename",
    "move",
    "trash",
    "batch",
];

/// The notes under the vault in `os.walk` order, the directories that could
/// not be listed (vault-relative, in the order python records them), and
/// whether anything was met that this engine reads differently from python
/// (a name that is not UTF-8, an entry whose type could not be read).
struct Notes {
    files: Vec<PathBuf>,
    unreadable: Vec<String>,
    odd: bool,
}

fn walk_notes(vault: &Path, dir: &Path, w: &mut Notes) {
    let rd = match fs::read_dir(dir) {
        Ok(rd) => rd,
        Err(_) => {
            match dir.strip_prefix(vault).ok().and_then(|r| r.to_str()) {
                Some("") => w.unreadable.push(".".to_string()),
                Some(r) => w.unreadable.push(r.to_string()),
                None => w.odd = true,
            }
            return;
        }
    };
    // python lists a directory whole before descending into any of it
    let mut subdirs = Vec::new();
    for e in rd {
        let Ok(e) = e else {
            w.odd = true;
            continue;
        };
        let Some(name) = e.file_name().to_str().map(str::to_string) else {
            w.odd = true;
            continue;
        };
        match e.file_type() {
            // a symlink is never descended, as in os.walk(followlinks=False)
            Ok(t) if t.is_dir() => {
                if !name.starts_with('.') && !crate::SKIP_DIRS.contains(&name.as_str()) {
                    subdirs.push(e.path());
                }
            }
            Ok(_) if name.ends_with(".md") => w.files.push(e.path()),
            Ok(_) => {}
            Err(_) => w.odd = true,
        }
    }
    for d in subdirs {
        walk_notes(vault, &d, w);
    }
}

fn stem_lower(p: &Path) -> Option<String> {
    let n = p.file_name()?.to_str()?;
    Some(n.strip_suffix(".md").unwrap_or(n).to_lowercase())
}

fn rel_str(vault: &Path, p: &Path) -> String {
    p.strip_prefix(vault)
        .unwrap_or(p)
        .to_string_lossy()
        .to_string()
}

/// python's `rel` for a note a caller may have TYPED: `os.path.relpath`,
/// which is lexical. `Area/../Hub.md` is shown as `Hub.md`, and a note
/// reached through a spelling outside the vault as `../Alias/N.md`.
pub fn rel_shown(vault: &Path, fp: &Path) -> String {
    use std::path::Component;
    let parts = |p: &Path| -> Vec<std::ffi::OsString> {
        let abs = if p.is_absolute() {
            p.to_path_buf()
        } else {
            std::env::current_dir().unwrap_or_default().join(p)
        };
        crate::normpath(&abs)
            .components()
            .filter_map(|c| match c {
                Component::Normal(n) => Some(n.to_os_string()),
                _ => None,
            })
            .collect()
    };
    let (start, path) = (parts(vault), parts(fp));
    let common = start.iter().zip(&path).take_while(|(a, b)| a == b).count();
    let mut out: Vec<String> = vec!["..".to_string(); start.len() - common];
    out.extend(
        path[common..]
            .iter()
            .map(|n| n.to_string_lossy().to_string()),
    );
    if out.is_empty() {
        ".".to_string()
    } else {
        out.join("/")
    }
}

/// python's `os.path.realpath` of a path.
pub fn realpath_of(p: &Path) -> PathBuf {
    use std::os::unix::ffi::OsStrExt;
    PathBuf::from(std::ffi::OsStr::from_bytes(&realpath(
        p.as_os_str().as_bytes(),
    )))
}

/// python's `resolve`: the note a reference names, or why not. `Err` carries
/// the outcome to return: a refusal this engine worded (`escape:`,
/// `ambiguous:`, `refused:`, and `not-found:` when its suggestions need no
/// similarity ranking), or `Fallback` for what python still answers.
pub fn resolve(vault: &Path, ref_: &str, op: &str) -> Result<PathBuf, Outcome> {
    use crate::err::{die, die_suggest, q};
    let escape = |shown: &str| {
        die(
            op,
            &format!("escape: path leaves the vault: {}", shown),
            None,
            1,
        )
    };
    // A typed form, in python's order: contained and a file is the answer;
    // one that leaves the vault is refused, never passed over for the next.
    let typed_file = |r: &str| -> Result<Option<PathBuf>, Outcome> {
        match typed(vault, r) {
            None => Err(escape(r)),
            Some(fp) => Ok(fp.is_file().then_some(fp)),
        }
    };
    let as_path = |fp: PathBuf| {
        let _ = REF_KIND.set("path");
        Ok(fp)
    };
    if let Some(fp) = typed_file(ref_)? {
        return as_path(fp);
    }
    if !ref_.ends_with(".md") {
        if let Some(fp) = typed_file(&format!("{}.md", ref_))? {
            return as_path(fp);
        }
    }
    let mut want = ref_.strip_suffix(".md").unwrap_or(ref_).to_lowercase();
    let mut w = Notes {
        files: Vec::new(),
        unreadable: Vec::new(),
        odd: false,
    };
    walk_notes(vault, vault, &mut w);
    let writing = WRITE_OPS.contains(&op);
    if w.odd || (!w.unreadable.is_empty() && !writing) {
        // a read over an incomplete walk answers with a warning: python's
        return Err(Outcome::Fallback);
    }
    let partial = !w.unreadable.is_empty();
    let cannot_prove = |claim: String| {
        die(
            op,
            &format!(
                "refused: cannot prove {} — unreadable: {}",
                claim,
                w.unreadable
                    .iter()
                    .take(3)
                    .cloned()
                    .collect::<Vec<_>>()
                    .join(", ")
            ),
            Some("vv doctor"),
            1,
        )
    };
    // a dangling symlink is walk-visible but not a note: never a hit
    let name_hits = |want: &str| -> Result<Vec<&PathBuf>, Outcome> {
        let mut hits: Vec<&PathBuf> = w
            .files
            .iter()
            .filter(|p| stem_lower(p).as_deref() == Some(want) && p.is_file())
            .collect();
        if !hits.is_empty() && partial {
            return Err(cannot_prove(format!("'{}' is unique", want)));
        }
        // python sorts the path STRINGS: `A-/x.md` before `A/x.md`
        hits.sort_by(|a, b| a.as_os_str().cmp(b.as_os_str()));
        Ok(hits)
    };
    // a note chosen from the walk gets the containment a typed path gets
    let contained = |p: &PathBuf| -> Result<PathBuf, Outcome> {
        use std::os::unix::ffi::OsStrExt;
        if within(vault, p.as_os_str().as_bytes()) {
            Ok(p.clone())
        } else {
            Err(escape(&rel_str(vault, p)))
        }
    };
    let is_id = |s: &str| !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit());
    let mut hits = name_hits(&want)?;
    if hits.is_empty() && want.starts_with('#') && is_id(&want[1..]) {
        // `#NNNNN` once a literal `#NNNNN` note has missed: exact path, exact
        // basename, then the id rule, all against this one walk
        let bare = &ref_[1..];
        if let Some(fp) = typed_file(bare)? {
            return as_path(fp);
        }
        if let Some(fp) = typed_file(&format!("{}.md", bare))? {
            return as_path(fp);
        }
        want.remove(0);
        hits = name_hits(&want)?;
    }
    if hits.len() == 1 {
        let fp = contained(hits[0])?;
        let _ = REF_KIND.set("name");
        return Ok(fp);
    }
    if hits.len() > 1 {
        // Two names are one name here when they lowercase alike, and the two
        // engines' Unicode tables are not the same edition: outside ASCII
        // python judges whether these really collide.
        if !want.is_ascii()
            || hits
                .iter()
                .any(|h| !stem_lower(h).is_some_and(|s| s.is_ascii()))
        {
            return Err(Outcome::Fallback);
        }
        let rels: Vec<String> = hits.iter().map(|h| rel_str(vault, h)).collect();
        return Err(die(
            op,
            &format!("ambiguous: {}", rels[..rels.len().min(5)].join(" | ")),
            Some(&format!("vv resolve {}", q(&rels[0], "NOTE"))),
            1,
        ));
    }
    if is_id(&want) {
        // the id rule: the one note whose basename starts `<digits> - `
        let prefix = format!("{} - ", want);
        let mut cands: Vec<&PathBuf> = w
            .files
            .iter()
            .filter(|p| {
                p.file_name()
                    .and_then(|n| n.to_str())
                    .is_some_and(|n| n.starts_with(&prefix))
                    && p.is_file()
            })
            .collect();
        cands.sort_by(|a, b| a.as_os_str().cmp(b.as_os_str()));
        if partial {
            // zero visible candidates is not "none"
            return Err(cannot_prove(format!("id {} is unique", want)));
        }
        if cands.len() > 1 {
            let rels: Vec<String> = cands.iter().map(|c| rel_str(vault, c)).collect();
            return Err(die(
                op,
                &format!(
                    "ambiguous: {} matches {} notes: {}",
                    want,
                    cands.len(),
                    rels[..rels.len().min(5)].join(" | ")
                ),
                Some(&format!("vv resolve {}", q(&rels[0], "NOTE"))),
                1,
            ));
        }
        if let Some(only) = cands.first() {
            let fp = contained(only)?;
            let _ = REF_KIND.set("id");
            return Ok(fp);
        }
    }
    if partial {
        return Err(cannot_prove(format!("'{}' is absent", ref_)));
    }
    // Suggestions: names containing the missed one, shortest first. When
    // fewer than three contain it python fills the list by similarity, which
    // this engine does not rank yet.
    let base = want.rsplit('/').next().unwrap_or("");
    let mut names: Vec<(usize, String, &PathBuf)> = w
        .files
        .iter()
        .filter_map(|p| {
            let n = p.file_name()?.to_str()?;
            let stem = &n[..n.len() - 3];
            stem.to_lowercase()
                .contains(base)
                .then(|| (stem.chars().count(), stem.to_string(), p))
        })
        .collect();
    names.sort();
    let mut sugg: Vec<String> = Vec::new();
    for (_, stem, p) in &names {
        if !sugg.contains(stem) && p.is_file() {
            sugg.push(stem.clone());
            if sugg.len() == 3 {
                return Err(die_suggest(
                    op,
                    &format!("not-found: no note matches '{}'", ref_),
                    &sugg.join(" | "),
                    1,
                ));
            }
        }
    }
    Err(Outcome::Fallback)
}

// ---------- metrics (mirror _log in vv_impl.py) ----------
/// The row for a refusal the native entry worded itself (mirrors python's
/// `die` → `_log`): the exit code and the kind; no `cf_bytes`, `ref` or `sel`,
/// which python records on success only. Timed from process start.
pub fn log_error(op: &str, kind: Option<&str>, code: i32, out_bytes: usize) {
    if std::env::var_os("VV_JOURNAL_ROOT").is_some() || std::env::var_os("VV_NO_METRICS").is_some()
    {
        return;
    }
    let Some(home) = std::env::var_os("HOME") else {
        return;
    };
    let ts = std::process::Command::new("date")
        .arg("+%Y-%m-%dT%H:%M:%S")
        .output()
        .ok()
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .unwrap_or_default();
    let ms = START.get().map(|t| t.elapsed().as_millis()).unwrap_or(0);
    let src: String = std::env::var("VV_METRICS_SRC")
        .unwrap_or_default()
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || *c == '-' || *c == '_')
        .take(24)
        .collect();
    let mut tail = String::new();
    if !src.is_empty() {
        tail.push_str(&format!(", \"src\": \"{}\"", src));
    }
    // a kind is a bare word from our own message text; anything else is dropped
    if let Some(k) =
        kind.filter(|k| !k.is_empty() && k.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-'))
    {
        tail.push_str(&format!(", \"kind\": \"{}\"", k));
    }
    let rec = format!(
        "{{\"ts\": \"{}\", \"op\": \"{}\", \"ms\": {}, \"out_bytes\": {}, \"exit\": {}, \"engine\": \"native\", \"ver\": \"{}\"{}}}\n",
        ts, op, ms, out_bytes, code, env!("CARGO_PKG_VERSION"), tail);
    use std::io::Write;
    let path = PathBuf::from(home).join(".claude/metrics/vv.jsonl");
    if let Ok(mut f) = fs::OpenOptions::new().create(true).append(true).open(path) {
        let _ = f.write_all(rec.as_bytes());
    }
}

pub fn log_metrics(op: &str, t0: std::time::Instant, out_bytes: usize, cf: u64, sel: Option<&str>) {
    if std::env::var_os("VV_JOURNAL_ROOT").is_some() || std::env::var_os("VV_NO_METRICS").is_some()
    {
        return;
    }
    let path = match std::env::var_os("HOME") {
        Some(h) => PathBuf::from(h).join(".claude/metrics/vv.jsonl"),
        None => return,
    };
    // local-time ISO seconds, matching datetime.now().isoformat(timespec="seconds")
    let now = std::process::Command::new("date")
        .arg("+%Y-%m-%dT%H:%M:%S")
        .output();
    let ts = now
        .ok()
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .unwrap_or_default();
    let ms = t0.elapsed().as_millis();
    // Provenance label (VV_METRICS_SRC) so the pilot report can separate
    // benchmark traffic from usage instead of guessing from the arrival rate.
    // Sanitised to [A-Za-z0-9_-]{0,24}: this record is built by string
    // formatting, so an unescaped quote here would corrupt the whole log.
    let src: String = std::env::var("VV_METRICS_SRC")
        .unwrap_or_default()
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || *c == '-' || *c == '_')
        .take(24)
        .collect();
    let src_field = if src.is_empty() {
        String::new()
    } else {
        format!(", \"src\": \"{}\"", src)
    };
    let mut sel_field = match sel {
        Some(s) => format!(", \"sel\": \"{}\"", s),
        None => String::new(),
    };
    if let Some(k) = REF_KIND.get() {
        sel_field.push_str(&format!(", \"ref\": \"{}\"", k));
    }
    let rec = format!(
        "{{\"ts\": \"{}\", \"op\": \"{}\", \"ms\": {}, \"out_bytes\": {}, \"exit\": 0, \"cf_bytes\": {}, \"engine\": \"native\", \"ver\": \"{}\"{}{}}}\n",
        ts, op, ms, out_bytes, cf, env!("CARGO_PKG_VERSION"), src_field, sel_field);
    use std::io::Write;
    if let Ok(mut f) = fs::OpenOptions::new().create(true).append(true).open(path) {
        let _ = f.write_all(rec.as_bytes());
    }
}

/// Global --limit N, stripped from argv in main(). Enumerators honor it via
/// push_limited; absent = unlimited.
pub static LIMIT: std::sync::OnceLock<usize> = std::sync::OnceLock::new();

/// Wall-clock nanoseconds at process start, set first thing in `main` and
/// handed to python on a fallback (`VV_NATIVE_T0_NS`) so the row it logs
/// covers the whole invocation: the native share and interpreter start-up.
pub static START_NS: std::sync::OnceLock<u128> = std::sync::OnceLock::new();

pub fn start_ns() -> String {
    START_NS.get().copied().unwrap_or(0).to_string()
}

/// True when the python engine started THIS process to do part of its work.
/// Python sets VV_FROM_PY to its own pid; the value must name our parent, so
/// a marker merely inherited from some outer environment (or an empty one)
/// changes nothing.
pub fn from_py() -> bool {
    match std::env::var("VV_FROM_PY") {
        #[cfg(unix)]
        Ok(v) => v == std::os::unix::process::parent_id().to_string(),
        #[cfg(not(unix))]
        Ok(v) => !v.is_empty(),
        Err(_) => false,
    }
}

/// Process start on the monotonic clock: a refusal's row is timed from here.
pub static START: std::sync::OnceLock<std::time::Instant> = std::sync::OnceLock::new();

/// Set when `--vault` or `--limit` was on the command line. Python validates
/// those before anything else, so a native refusal that would otherwise
/// outrank its global-flag refusal hands off instead.
pub static GLOBAL_FLAG: std::sync::OnceLock<()> = std::sync::OnceLock::new();

/// How the NOTE operand was addressed, for the metrics row: "path" (a typed
/// vault-relative path), "name" (a unique basename) or "id" (the id rule).
pub static REF_KIND: std::sync::OnceLock<&'static str> = std::sync::OnceLock::new();

/// Entry lines + count trailer for an enumerator. The K-of-M trailer appears
/// ONLY when --limit actually truncated — `total` may exceed entries.len()
/// even without a limit (tags --counts shows the top 40 while the trailer
/// counts every distinct tag), and that case keeps the plain total (parity
/// with python's _list_out).
pub fn push_limited(buf: &mut String, entries: &[String], total: usize, noun: &str) {
    let lim = LIMIT.get().copied();
    let shown = match lim {
        Some(l) if entries.len() > l => &entries[..l],
        _ => entries,
    };
    for e in shown {
        buf.push_str(e);
        buf.push('\n');
    }
    if lim.is_some_and(|l| entries.len() > l) {
        buf.push_str(&format!("({} of {} {})\n", shown.len(), total, noun));
    } else {
        buf.push_str(&format!("({} {})\n", total, noun));
    }
}

pub fn emit(buf: &str) -> usize {
    print!("{}", buf);
    buf.len()
}

pub fn run(cmd: &str, args: &[String], vault: &Path) -> Outcome {
    let t0 = std::time::Instant::now();
    match cmd {
        "resolve" if args.len() == 1 => {
            let fp = match resolve(vault, &args[0], cmd) {
                Ok(f) => f,
                Err(o) => return o,
            };
            let cf = fs::metadata(&fp).map(|m| m.len()).unwrap_or(0);
            let n = emit(&format!("{}\n", rel_shown(vault, &fp)));
            log_metrics("resolve", t0, n, cf, None);
            Outcome::Done(0)
        }
        "head" if args.len() == 1 => {
            let fp = match resolve(vault, &args[0], cmd) {
                Ok(f) => f,
                Err(o) => return o,
            };
            let cf = fs::metadata(&fp).map(|m| m.len()).unwrap_or(0);
            let bytes = match fs::read(&fp) {
                Ok(b) => b,
                Err(_) => return Outcome::Fallback,
            };
            let text = match String::from_utf8(bytes) {
                Ok(t) => t,
                Err(_) => return Outcome::Fallback,
            };
            // split_fm: ^---\r?\n(.*?)\r?\n---(\r?\n)? after optional BOM
            let t = text.strip_prefix(BOM).unwrap_or(&text);
            let fm = split_fm(t);
            let n = match fm {
                Some(f) => emit(&format!("{}\n", f)),
                None => emit("(no frontmatter)\n"),
            };
            log_metrics("head", t0, n, cf, None);
            Outcome::Done(0)
        }
        "outline" if args.len() == 1 => {
            let fp = match resolve(vault, &args[0], cmd) {
                Ok(f) => f,
                Err(o) => return o,
            };
            let cf = fs::metadata(&fp).map(|m| m.len()).unwrap_or(0);
            let bytes = match fs::read(&fp) {
                Ok(b) => b,
                Err(_) => return Outcome::Fallback,
            };
            let text = match String::from_utf8(bytes) {
                Ok(t) => t,
                Err(_) => return Outcome::Fallback,
            };
            let (lines, secs) = parse(&text);
            let mut buf = String::new();
            for s in &secs {
                if s.start == s.end {
                    continue;
                }
                let t = sec_text(&lines, s);
                let hashes = if s.level > 0 {
                    "#".repeat(s.level)
                } else {
                    "-".into()
                };
                buf.push_str(&format!(
                    "{}\t{}\t{}\t{}B\t{}\n",
                    s.id,
                    hashes,
                    s.title,
                    t.len(), // UTF-8 bytes — chars() under-reported multibyte sections (2026-08-27)
                    sha8(&t)
                ));
            }
            let n = emit(&buf);
            log_metrics("outline", t0, n, cf, None);
            Outcome::Done(0)
        }
        // A bare `read NOTE` (len 1) is python's `show`, so it is not matched
        // here and falls through to Fallback with everything else unhandled.
        // `show` HAS a native arm, so this is a deliberate parity exception
        // rather than an unimplemented one: the budget line and the
        // continuation token are output this delegation keeps under a single
        // author, instead of two engines each wording it.
        "read" if args.len() == 2 || args.len() == 3 => {
            let (sid, hash_only, flagged) = if args.len() == 2 && !args[1].starts_with("--") {
                (args[1].clone(), false, false)
            } else {
                match peel_read(args) {
                    Some((v, h)) => (v, h, true),
                    None => return Outcome::Fallback, // python words the refusal
                }
            };
            let fp = match resolve(vault, &args[0], cmd) {
                Ok(f) => f,
                Err(o) => return o,
            };
            let cf = fs::metadata(&fp).map(|m| m.len()).unwrap_or(0);
            let bytes = match fs::read(&fp) {
                Ok(b) => b,
                Err(_) => return Outcome::Fallback,
            };
            let text = match String::from_utf8(bytes) {
                Ok(t) => t,
                Err(_) => return Outcome::Fallback,
            };
            let (lines, secs) = parse(&text);
            let (s, tier) = match find_sec_opts(&lines, &secs, &sid, hash_only) {
                Ok(v) => v,
                Err(_) => return Outcome::Fallback,
            };
            // A flag-spelled selector is its own kind in the sink (mirroring
            // _sel = "flag"): the question it answers is which SPELLING
            // callers reach for, which the tier would hide.
            let kind = if flagged { "flag" } else { tier };
            let t = sec_text(&lines, s);
            let n = emit(&format!("{}\n--sha8:{}\n", t, sha8(&t)));
            log_metrics("read", t0, n, cf, Some(kind));
            Outcome::Done(0)
        }
        _ => Outcome::Fallback,
    }
}

pub fn split_fm(t: &str) -> Option<&str> {
    // ^---\r?\n(.*?)\r?\n---(\r?\n)?  non-greedy, DOTALL
    let rest = t
        .strip_prefix("---\r\n")
        .or_else(|| t.strip_prefix("---\n"))?;
    // find earliest \r?\n---(\r?\n|$)
    let b = rest.as_bytes();
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'\n' {
            let mut fm_end = i;
            if fm_end > 0 && b[fm_end - 1] == b'\r' {
                fm_end -= 1;
            }
            let after = &rest[i + 1..];
            if after == "---" || after.starts_with("---\r\n") || after.starts_with("---\n") {
                return Some(&rest[..fm_end]);
            }
        }
        i += 1;
    }
    None
}
