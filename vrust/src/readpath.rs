// Native read path: outline / read SEC / head / resolve — E2 experiment 2026-08-27.
//
// PARITY STRATEGY: this module implements ONLY the happy path, ported
// line-for-line from vv_impl.py (parse/fence_mask/fm_bounds/find_sec/sec_text/
// sha8/read_raw/resolve). On ANYTHING unusual — name miss, ambiguity, section
// miss, non-UTF-8, path escape, io error — it returns Fallback and main()
// execs the Python implementation, which stays the single authority for error
// grammar, did-you-mean suggestions (difflib is not exactly portable), and
// exit codes. A native fast path that can only ever be right or silent.
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

// find_sec happy path: id, #Heading, (preamble), unambiguous title. Anything
// else (miss OR ambiguity) -> None -> python fallback for the canonical error.
pub fn find_sec<'a>(secs: &'a [Sec], sid: &str) -> Option<&'a Sec> {
    for s in secs {
        if s.id == sid {
            return Some(s);
        }
    }
    let mut want = sid.trim().to_string();
    if want.starts_with('#') {
        want = want.trim_start_matches('#').trim().to_string();
    }
    let wl = want.to_lowercase();
    if wl == "(preamble)" || wl == "preamble" {
        return secs
            .iter()
            .find(|s| s.title == "(preamble)" || s.id == "H0");
    }
    let matches: Vec<&Sec> = secs
        .iter()
        .filter(|s| s.title.trim().to_lowercase() == wl)
        .collect();
    if matches.len() == 1 {
        Some(matches[0])
    } else {
        None
    }
}

// ---------- resolve happy path ----------
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

pub fn resolve(vault: &Path, ref_: &str) -> Option<PathBuf> {
    if let Some(fp) = contain(vault, ref_) {
        if fp.is_file() {
            return Some(fp);
        }
    }
    if !ref_.ends_with(".md") {
        if let Some(fp) = contain(vault, &format!("{}.md", ref_)) {
            if fp.is_file() {
                return Some(fp);
            }
        }
    }
    let want = ref_.strip_suffix(".md").unwrap_or(ref_).to_lowercase();
    let mut files = Vec::new();
    crate::walk_ex(vault, &mut files, false);
    let hits: Vec<&PathBuf> = files
        .iter()
        .filter(|p| {
            p.file_name()
                .and_then(|n| n.to_str())
                .map(|n| n.strip_suffix(".md").unwrap_or(n).to_lowercase() == want)
                .unwrap_or(false)
        })
        .collect();
    if hits.len() == 1 {
        Some(hits[0].clone())
    } else {
        None
    } // 0 or 2+: python
}

// ---------- metrics (mirror _log in vv_impl.py) ----------
pub fn log_metrics(op: &str, t0: std::time::Instant, out_bytes: usize, cf: u64) {
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
    let rec = format!(
        "{{\"ts\": \"{}\", \"op\": \"{}\", \"ms\": {}, \"out_bytes\": {}, \"exit\": 0, \"cf_bytes\": {}, \"engine\": \"native\"{}}}\n",
        ts, op, ms, out_bytes, cf, src_field);
    use std::io::Write;
    if let Ok(mut f) = fs::OpenOptions::new().create(true).append(true).open(path) {
        let _ = f.write_all(rec.as_bytes());
    }
}

/// Global --limit N, stripped from argv in main(). Enumerators honor it via
/// push_limited; absent = unlimited.
pub static LIMIT: std::sync::OnceLock<usize> = std::sync::OnceLock::new();

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
            let fp = match resolve(vault, &args[0]) {
                Some(f) => f,
                None => return Outcome::Fallback,
            };
            let cf = fs::metadata(&fp).map(|m| m.len()).unwrap_or(0);
            let rel = fp.strip_prefix(vault).unwrap_or(&fp);
            let n = emit(&format!("{}\n", rel.display()));
            log_metrics("resolve", t0, n, cf);
            Outcome::Done(0)
        }
        "head" if args.len() == 1 => {
            let fp = match resolve(vault, &args[0]) {
                Some(f) => f,
                None => return Outcome::Fallback,
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
            log_metrics("head", t0, n, cf);
            Outcome::Done(0)
        }
        "outline" if args.len() == 1 => {
            let fp = match resolve(vault, &args[0]) {
                Some(f) => f,
                None => return Outcome::Fallback,
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
            log_metrics("outline", t0, n, cf);
            Outcome::Done(0)
        }
        "read" if args.len() == 2 => {
            let fp = match resolve(vault, &args[0]) {
                Some(f) => f,
                None => return Outcome::Fallback,
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
            let s = match find_sec(&secs, &args[1]) {
                Some(s) => s,
                None => return Outcome::Fallback,
            };
            let t = sec_text(&lines, s);
            let n = emit(&format!("{}\n--sha8:{}\n", t, sha8(&t)));
            log_metrics("read", t0, n, cf);
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
