// graph.rs — native backlinks/links/orphans/deadends/impact (agent A,
// docs/rust-rewrite-plan.md). Ported LINE-FOR-LINE from src/vv_impl.py:
// cmd_backlinks, cmd_links, cmd_orphans, cmd_deadends, bare_resolves,
// link_matches, basename_index, scan_links/link_targets_in/masked_lines.
//
// Contract: happy path only. On ANY doubt (name miss/ambiguity, non-UTF-8,
// io error, unsupported flag) return Outcome::Fallback; main() execs Python,
// which stays the sole author of error text and exit codes.
//
// `impact` shells to `git status --porcelain` in Python and mixes frontmatter
// + occurrence-count formatting; the win from porting it natively is small and
// the parity surface is not, so it is Fallback here — see report.
#![allow(dead_code)]
use crate::readpath::{self, Outcome};
use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};
use std::time::Instant;

pub fn run(cmd: &str, args: &[String], vault: &Path) -> Outcome {
    let t0 = Instant::now();
    match cmd {
        "backlinks" if args.len() == 1 => cmd_backlinks(&args[0], vault, t0),
        "links" if args.len() == 1 => cmd_links(&args[0], vault, t0),
        "orphans" if args.len() <= 1 => {
            cmd_orphans(args.first().map(|s| s.as_str()).unwrap_or(""), vault, t0)
        }
        "deadends" if args.is_empty() => cmd_deadends(vault, t0),
        // impact: git subprocess + frontmatter formatting — deliberately Fallback.
        _ => Outcome::Fallback,
    }
}

// ---------------------------------------------------------------------------
// link lexer — mirrors cmd_linkscan's char-scan engine in main.rs (the tested
// parity anchor for masked_lines/link_targets_in/strip_inline_code), minus
// the TSV/line-number bookkeeping the graph commands don't need. Duplicated
// here because main.rs's version is a private fn.
// ---------------------------------------------------------------------------

fn find_seq(chars: &[char], start: usize, pat: &[char]) -> Option<usize> {
    if chars.len() < pat.len() {
        return None;
    }
    (start..=chars.len() - pat.len()).find(|&i| chars[i..i + pat.len()] == *pat)
}

fn mask_comments(masked: &mut [char], pos: &mut usize, in_comment: &mut bool) {
    let opener = ['<', '!', '-', '-'];
    let closer = ['-', '-', '>'];
    while let Some(s) = find_seq(masked, *pos, &opener) {
        match find_seq(masked, s + 4, &closer) {
            Some(e) => {
                for k in s..e + 3 {
                    masked[k] = '\u{0}';
                }
                *pos = e + 3;
            }
            None => {
                let n = masked.len();
                for k in s..n {
                    masked[k] = '\u{0}';
                }
                *in_comment = true;
                break;
            }
        }
    }
}

/// Active [[wiki]] / ](path.md) link targets in `text`: kind 'w' or 'm'.
/// Fenced blocks (own-marker close), inline code spans and HTML comments are
/// excluded, matching link_targets_in/masked_lines in vv_impl.py.
pub fn active_links(text: &str) -> Vec<(char, String)> {
    let mut result = Vec::new();
    let lines: Vec<&str> = text.split('\n').collect();
    let mut fm_end = 0usize;
    if !lines.is_empty()
        && lines[0].trim_start_matches('\u{feff}').trim_end_matches('\r') == "---"
    {
        for i in 1..lines.len() {
            if lines[i].trim_end_matches('\r') == "---" {
                fm_end = i + 1;
                break;
            }
        }
    }
    let mut marker: Option<(char, usize)> = None;
    let mut in_comment = false;
    for (i, raw) in lines.iter().enumerate() {
        let raw = raw.trim_end_matches('\r');
        if !in_comment {
            let indent = raw.chars().take_while(|&c| c == ' ').count();
            let trimmed = &raw[indent..];
            let fence: Option<(char, usize, &str)> = if indent <= 3
                && (trimmed.starts_with("```") || trimmed.starts_with("~~~"))
            {
                let c = trimmed.chars().next().unwrap();
                let n = trimmed.chars().take_while(|&x| x == c).count();
                Some((c, n, &trimmed[n..]))
            } else {
                None
            };
            let mut line_fenced = false;
            if i >= fm_end {
                match (marker, fence) {
                    (None, Some((c, n, rest))) if c == '~' || !rest.contains('`') => {
                        marker = Some((c, n));
                        line_fenced = true;
                    }
                    (None, Some(_)) => {}
                    (Some((mc, mn)), Some((c, n, rest)))
                        if c == mc && n >= mn && rest.trim().is_empty() =>
                    {
                        marker = None;
                        line_fenced = true;
                    }
                    (Some(_), _) => line_fenced = true,
                    _ => {}
                }
            }
            if line_fenced {
                continue;
            }
        }
        let chars: Vec<char> = raw.chars().collect();
        let mut masked: Vec<char> = chars.clone();
        let mut ci = 0usize;
        while ci < chars.len() {
            if chars[ci] == '`' {
                let mut n = 0usize;
                while ci + n < chars.len() && chars[ci + n] == '`' {
                    n += 1;
                }
                let mut j = ci + n;
                let mut close: Option<usize> = None;
                while j < chars.len() {
                    if chars[j] == '`' {
                        let mut m = 0usize;
                        while j + m < chars.len() && chars[j + m] == '`' {
                            m += 1;
                        }
                        if m == n {
                            close = Some(j);
                            break;
                        }
                        j += m;
                    } else {
                        j += 1;
                    }
                }
                if let Some(c) = close {
                    for k in ci..c + n {
                        masked[k] = '\u{0}';
                    }
                    ci = c + n;
                    continue;
                }
                ci += n;
                continue;
            }
            ci += 1;
        }
        if in_comment {
            match find_seq(&masked, 0, &['-', '-', '>']) {
                None => {
                    continue;
                }
                Some(e) => {
                    for k in 0..e + 3 {
                        masked[k] = '\u{0}';
                    }
                    in_comment = false;
                    let mut pos = e + 3;
                    mask_comments(&mut masked, &mut pos, &mut in_comment);
                }
            }
        } else {
            let mut pos = 0usize;
            mask_comments(&mut masked, &mut pos, &mut in_comment);
        }
        let b: Vec<char> = masked;
        let mut j = 0usize;
        while j + 1 < b.len() {
            if b[j] == '[' && b[j + 1] == '[' {
                if let Some(end) = (j + 2..b.len().saturating_sub(1))
                    .find(|&k| b[k] == ']' && b[k + 1] == ']')
                {
                    let inner: String = b[j + 2..end].iter().collect();
                    let seg = inner
                        .split(|c| c == '|' || c == '#')
                        .next()
                        .unwrap_or("")
                        .trim();
                    let target = seg
                        .strip_suffix('\\')
                        .map(str::trim_end)
                        .unwrap_or(seg)
                        .to_string();
                    if !target.is_empty() && !target.contains('\u{0}') {
                        result.push(('w', target));
                    }
                    j = end + 2;
                    continue;
                }
            }
            if b[j] == ']' && b[j + 1] == '(' && (j == 0 || b[j - 1] != ']') {
                if let Some(end) = (j + 2..b.len()).find(|&k| b[k] == ')') {
                    let inner: String = b[j + 2..end].iter().collect();
                    if inner.ends_with(".md")
                        && !inner.contains(char::is_whitespace)
                        && !inner.contains('\u{0}')
                    {
                        result.push(('m', inner));
                    }
                    j = end + 1;
                    continue;
                }
            }
            j += 1;
        }
    }
    result
}

// ---------------------------------------------------------------------------
// path helpers — posix-lexical, no filesystem access (paths here are already
// absolute and constructed by walk_ex the same way vv_impl.py builds them).
// ---------------------------------------------------------------------------

fn rel_string(p: &Path, vault: &Path) -> String {
    p.strip_prefix(vault)
        .map(|r| r.to_string_lossy().to_string())
        .unwrap_or_else(|_| p.to_string_lossy().to_string())
}

fn basename_noext_lower(p: &Path) -> Option<String> {
    let name = p.file_name()?.to_str()?;
    Some(name.strip_suffix(".md").unwrap_or(name).to_lowercase())
}

fn posix_dirname(p: &str) -> String {
    match p.rfind('/') {
        Some(i) => p[..i].to_string(),
        None => String::new(),
    }
}

fn posix_join(a: &str, b: &str) -> String {
    if b.starts_with('/') {
        return b.to_string();
    }
    if a.is_empty() {
        return b.to_string();
    }
    if a.ends_with('/') {
        format!("{}{}", a, b)
    } else {
        format!("{}/{}", a, b)
    }
}

/// os.path.normpath, POSIX case, lexical only (no symlink resolution).
fn posix_normpath(p: &str) -> String {
    let is_abs = p.starts_with('/');
    let mut parts: Vec<&str> = Vec::new();
    for comp in p.split('/') {
        match comp {
            "" | "." => continue,
            ".." => {
                if let Some(&last) = parts.last() {
                    if last != ".." {
                        parts.pop();
                    } else {
                        parts.push("..");
                    }
                } else if !is_abs {
                    parts.push("..");
                }
            }
            c => parts.push(c),
        }
    }
    let joined = parts.join("/");
    if is_abs {
        format!("/{}", joined)
    } else if joined.is_empty() {
        ".".to_string()
    } else {
        joined
    }
}

/// urllib.parse.unquote (utf-8, errors='replace' approximated with lossy).
fn unquote(s: &str) -> String {
    fn hexval(b: u8) -> Option<u8> {
        match b {
            b'0'..=b'9' => Some(b - b'0'),
            b'a'..=b'f' => Some(b - b'a' + 10),
            b'A'..=b'F' => Some(b - b'A' + 10),
            _ => None,
        }
    }
    let bytes = s.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' && i + 2 < bytes.len() {
            if let (Some(h), Some(l)) = (hexval(bytes[i + 1]), hexval(bytes[i + 2])) {
                out.push((h << 4) | l);
                i += 3;
                continue;
            }
        }
        out.push(bytes[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

// ---------------------------------------------------------------------------
// winner rules — vv_impl.py:610-650, ported exactly.
// ---------------------------------------------------------------------------

fn basename_index(files: &[PathBuf]) -> HashMap<String, Vec<PathBuf>> {
    let mut idx: HashMap<String, Vec<PathBuf>> = HashMap::new();
    for p in files {
        if let Some(b) = basename_noext_lower(p) {
            idx.entry(b).or_default().push(p.clone());
        }
    }
    idx
}

fn bare_resolves(
    from_fp: &Path,
    tgt_fp: &Path,
    idx: &HashMap<String, Vec<PathBuf>>,
    vault: &Path,
) -> bool {
    let base = match basename_noext_lower(tgt_fp) {
        Some(b) => b,
        None => return true,
    };
    let cands = match idx.get(&base) {
        Some(c) => c,
        None => return true,
    };
    if cands.len() <= 1 {
        return true;
    }
    let from_dir = from_fp.parent();
    let same_dir: Vec<&PathBuf> = cands.iter().filter(|c| c.parent() == from_dir).collect();
    let pool: Vec<&PathBuf> = if !same_dir.is_empty() {
        same_dir
    } else {
        cands.iter().collect()
    };
    let winner = pool
        .iter()
        .min_by_key(|p| {
            let r = rel_string(p, vault);
            (r.chars().count(), r)
        })
        .unwrap();
    **winner == *tgt_fp
}

fn link_matches(
    from_fp: &Path,
    kind: char,
    target: &str,
    tgt_fp: &Path,
    tgt_base: &str,
    tgt_rel_noext: &str,
    idx: &HashMap<String, Vec<PathBuf>>,
    vault: &Path,
) -> bool {
    if kind == 'w' {
        let t = target.trim().to_lowercase();
        let t_noext = t.strip_suffix(".md").unwrap_or(&t);
        if t_noext == tgt_base {
            return bare_resolves(from_fp, tgt_fp, idx, vault);
        }
        return t_noext == tgt_rel_noext;
    }
    let dec = unquote(target.trim());
    let from_dir = posix_dirname(&from_fp.to_string_lossy());
    let cand = posix_normpath(&posix_join(&from_dir, &dec));
    let tgt_str = tgt_fp.to_string_lossy().to_string();
    if cand == tgt_str {
        return true;
    }
    let cand2 = posix_normpath(&posix_join(&vault.to_string_lossy(), &dec));
    cand2 == tgt_str
}

// ---------------------------------------------------------------------------
// commands
// ---------------------------------------------------------------------------

fn cmd_backlinks(ref_: &str, vault: &Path, t0: Instant) -> Outcome {
    let fp = match readpath::resolve(vault, ref_) {
        Some(f) => f,
        None => return Outcome::Fallback,
    };
    let cf = fs::metadata(&fp).map(|m| m.len()).unwrap_or(0);
    let tgt_base = match basename_noext_lower(&fp) {
        Some(b) => b,
        None => return Outcome::Fallback,
    };
    let tgt_rel = rel_string(&fp, vault);
    let tgt_rel_noext = tgt_rel.strip_suffix(".md").unwrap_or(&tgt_rel).to_lowercase();

    let mut files = Vec::new();
    crate::walk_ex(vault, &mut files, false);
    let idx = basename_index(&files);

    let mut hits: HashSet<String> = HashSet::new();
    if let Some(cachemap) = crate::cache::links_map(vault) {
        for (rp, fl) in &cachemap {
            let p = vault.join(rp);
            if p == fp || !fl.utf8_ok {
                continue; // python's scanner skips non-UTF-8 files entirely
            }
            for (kind, target) in &fl.links {
                if *kind == 'w' && !target.to_lowercase().contains(tgt_base.as_str()) {
                    continue;
                }
                if link_matches(&p, *kind, target, &fp, &tgt_base, &tgt_rel_noext, &idx, vault) {
                    hits.insert(rp.clone());
                    break;
                }
            }
        }
    } else {
        // cache doubtful: the parity-proven live scan, never a wrong answer
        for p in &files {
            if *p == fp {
                continue;
            }
            let rp = rel_string(p, vault);
            if hits.contains(&rp) {
                continue;
            }
            let text = match fs::read_to_string(p) {
                Ok(t) => t,
                Err(_) => continue,
            };
            for (kind, target) in active_links(&text) {
                if kind == 'w' && !target.to_lowercase().contains(tgt_base.as_str()) {
                    continue; // needle filter: scan_links(needle=tgt_base)
                }
                if link_matches(p, kind, &target, &fp, &tgt_base, &tgt_rel_noext, &idx, vault) {
                    hits.insert(rp.clone());
                    break;
                }
            }
        }
    }
    let mut sorted: Vec<String> = hits.into_iter().collect();
    sorted.sort();
    let mut buf = String::new();
    for h in &sorted {
        buf.push_str(h);
        buf.push('\n');
    }
    buf.push_str(&format!("({} backlinks)\n", sorted.len()));
    let n = readpath::emit(&buf);
    readpath::log_metrics("backlinks", t0, n, cf);
    Outcome::Done(0)
}

fn cmd_links(ref_: &str, vault: &Path, t0: Instant) -> Outcome {
    let fp = match readpath::resolve(vault, ref_) {
        Some(f) => f,
        None => return Outcome::Fallback,
    };
    let cf = fs::metadata(&fp).map(|m| m.len()).unwrap_or(0);
    // cmd_links calls read_raw(fp) directly (no try/except) — non-UTF-8
    // there is a real python error (exit 5), so Fallback rather than skip.
    let text = match fs::read_to_string(&fp) {
        Ok(t) => t,
        Err(_) => return Outcome::Fallback,
    };
    let mut seen: Vec<String> = Vec::new();
    for (kind, target) in active_links(&text) {
        if kind == 'w' && !seen.contains(&target) {
            seen.push(target);
        }
    }
    let mut buf = String::new();
    for l in &seen {
        buf.push_str(l);
        buf.push('\n');
    }
    buf.push_str(&format!("({} links)\n", seen.len()));
    let n = readpath::emit(&buf);
    readpath::log_metrics("links", t0, n, cf);
    Outcome::Done(0)
}

fn cmd_orphans(folder: &str, vault: &Path, t0: Instant) -> Outcome {
    let root: PathBuf = if folder.is_empty() {
        vault.to_path_buf()
    } else {
        match readpath::contain(vault, folder) {
            Some(p) => p,
            None => return Outcome::Fallback,
        }
    };
    let mut files = Vec::new();
    crate::walk_ex(vault, &mut files, false);
    let idx = basename_index(&files);

    let mut path_targets: HashSet<String> = HashSet::new();
    let mut bare_by_name: HashMap<String, Vec<PathBuf>> = HashMap::new();
    // cache-first: same rows the live loop would lex; non-UTF-8 files are
    // skipped (utf8_ok=0), mirroring the python scanner's skip
    let cachemap = crate::cache::links_map(vault);
    let mut cached_rows: Vec<(PathBuf, Vec<(char, String)>)> = Vec::new();
    if let Some(cm) = &cachemap {
        for (rp, fl) in cm {
            if fl.utf8_ok {
                cached_rows.push((vault.join(rp), fl.links.clone()));
            }
        }
    }
    let live_iter: Vec<(PathBuf, Vec<(char, String)>)> = if cachemap.is_some() {
        cached_rows
    } else {
        files.iter().filter_map(|p| {
            fs::read_to_string(p).ok().map(|text| (p.clone(), active_links(&text)))
        }).collect()
    };
    for (p, file_links) in &live_iter {
        for (kind, target) in file_links {
            let kind = *kind;
            let target = target.clone();
            let tl = target.trim().to_lowercase();
            if kind == 'm' {
                let dec = unquote(&tl);
                let dec = dec.strip_suffix(".md").unwrap_or(&dec).to_string();
                let rp = rel_string(p, vault);
                let dirn = posix_dirname(&rp);
                path_targets.insert(posix_normpath(&posix_join(&dirn, &dec)).to_lowercase());
                path_targets.insert(posix_normpath(&dec).to_lowercase());
                continue;
            }
            let tl2 = tl.strip_suffix(".md").unwrap_or(&tl).to_string();
            if tl2.contains('/') {
                path_targets.insert(tl2);
            } else {
                bare_by_name.entry(tl2).or_default().push(p.clone());
            }
        }
    }

    let mut sorted_files: Vec<(String, PathBuf)> = files
        .iter()
        .map(|p| (p.to_string_lossy().to_string(), p.clone()))
        .collect();
    sorted_files.sort_by(|a, b| a.0.cmp(&b.0));
    let root_str = root.to_string_lossy().to_string();
    let root_prefix = format!("{}/", root_str);

    let mut buf = String::new();
    let mut n = 0usize;
    for (pstr, p) in &sorted_files {
        let included = pstr == &root_str || pstr.starts_with(&root_prefix) || folder.is_empty();
        if !included {
            continue;
        }
        let base = match basename_noext_lower(p) {
            Some(b) => b,
            None => continue,
        };
        let rp = rel_string(p, vault);
        let rel_noext = rp.strip_suffix(".md").unwrap_or(&rp).to_lowercase();
        let linked = path_targets.contains(&rel_noext)
            || bare_by_name.get(&base).is_some_and(|srcs| {
                srcs.iter()
                    .any(|src| src != p && bare_resolves(src, p, &idx, vault))
            });
        if !linked {
            buf.push_str(&rp);
            buf.push('\n');
            n += 1;
        }
    }
    buf.push_str(&format!("({} orphans)\n", n));
    let bytes = readpath::emit(&buf);
    readpath::log_metrics("orphans", t0, bytes, 0);
    Outcome::Done(0)
}

fn cmd_deadends(vault: &Path, t0: Instant) -> Outcome {
    let mut files = Vec::new();
    crate::walk_ex(vault, &mut files, false);
    let mut rels: Vec<String> = files.iter().map(|p| rel_string(p, vault)).collect();
    rels.sort();

    let cachemap = crate::cache::links_map(vault);
    let mut buf = String::new();
    let mut n = 0usize;
    for rp in &rels {
        let empty = if let Some(cm) = &cachemap {
            match cm.get(rp) {
                Some(fl) => fl.links.is_empty(),   // deadends counts lossy-lexed links
                None => return Outcome::Fallback,  // cache/walk disagree: refuse to guess
            }
        } else {
            let bytes = match fs::read(vault.join(rp)) {
                Ok(b) => b,
                Err(_) => return Outcome::Fallback,
            };
            active_links(&String::from_utf8_lossy(&bytes)).is_empty()
        };
        if empty {
            buf.push_str(rp);
            buf.push('\n');
            n += 1;
        }
    }
    buf.push_str(&format!("({} deadends)\n", n));
    let bytes = readpath::emit(&buf);
    readpath::log_metrics("deadends", t0, bytes, 0);
    Outcome::Done(0)
}
