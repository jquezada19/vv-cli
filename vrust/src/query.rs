// query.rs — native `board` / `tags` / `props` / `show` (agent C, full-rewrite plan).
//
// PARITY STRATEGY: ported line-for-line from the LIVE (non-indexed) code paths
// of cmd_board / cmd_tags / cmd_props / cmd_show / fm_props / split_fm in
// src/vv_impl.py. Per the task brief, python's indexed (SQLite) path and live
// path are parity-tested to produce identical user-visible output, so porting
// the live path's semantics — sorted order, filtering, formatting — matches
// both. Happy path ONLY: anything doubtful (bad folder, non-UTF-8, unknown
// flag shape, io error, non-ASCII tag value, bad arg count) returns
// Outcome::Fallback and main() execs python3 src/vv.py, which stays the sole
// author of error text and exit codes.
use crate::readpath::{self, Outcome};
use std::collections::HashMap;
use std::fs::{self, File};
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::time::Instant;

pub fn run(cmd: &str, args: &[String], vault: &Path) -> Outcome {
    let t0 = Instant::now();
    match cmd {
        "board" => cmd_board(args, vault, t0),
        "tags" => cmd_tags(args, vault, t0),
        "props" => cmd_props(args, vault, t0),
        "show" => cmd_show(args, vault, t0),
        _ => Outcome::Fallback,
    }
}

// ---------- frontmatter-only scan (early-stop at the closing '---') ----------

/// Parse one `^(\w[\w-]*):\s*(.*)$` frontmatter line (fm_props in vv_impl.py).
/// \w is treated as unicode-alphanumeric-or-underscore for the KEY only — real
/// frontmatter keys are ASCII identifiers in this vault, so this is a safe
/// approximation, never the parity-risky half of the port (that is the value,
/// handled by the non-ASCII Fallback in cmd_tags below).
fn parse_fm_line(line: &str) -> Option<(String, String)> {
    let chars: Vec<char> = line.chars().collect();
    if chars.is_empty() {
        return None;
    }
    if !(chars[0].is_alphanumeric() || chars[0] == '_') {
        return None;
    }
    let mut i = 1;
    while i < chars.len() && (chars[i].is_alphanumeric() || chars[i] == '_' || chars[i] == '-') {
        i += 1;
    }
    if i >= chars.len() || chars[i] != ':' {
        return None;
    }
    let key: String = chars[..i].iter().collect();
    let rest: String = chars[i + 1..].iter().collect();
    let value = rest.trim_start().trim_matches('"').to_string();
    Some((key, value))
}

/// Read only the frontmatter block of `path` (BOM tolerant), stopping at the
/// closing '---' line — never reads the body. Mirrors split_fm(text) + fm_props
/// on the LIVE path's `open(path, errors="replace").read()` semantics, except
/// this port refuses (Err) on invalid UTF-8 rather than substituting U+FFFD:
/// python's errors="replace" never errors here, so this is a deliberately
/// wider Fallback net than python's own live path (documented risk — see the
/// final report's risk list).
fn read_fm_props(path: &Path) -> Result<HashMap<String, String>, ()> {
    let f = File::open(path).map_err(|_| ())?;
    let mut reader = BufReader::new(f);
    let mut first = String::new();
    let n = reader.read_line(&mut first).map_err(|_| ())?;
    if n == 0 {
        return Ok(HashMap::new()); // empty file: no frontmatter
    }
    let first_trim = first.trim_end_matches(['\n', '\r']);
    let first_trim = first_trim.strip_prefix('\u{feff}').unwrap_or(first_trim);
    if first_trim != "---" {
        return Ok(HashMap::new()); // no frontmatter opener
    }
    let mut props = HashMap::new();
    let mut terminated = false;
    loop {
        let mut line = String::new();
        let n = reader.read_line(&mut line).map_err(|_| ())?;
        if n == 0 {
            break; // EOF without a closing '---': unterminated -> fm=None
        }
        let lt = line.trim_end_matches(['\n', '\r']);
        if lt == "---" {
            terminated = true;
            break;
        }
        if let Some((k, v)) = parse_fm_line(lt) {
            props.insert(k, v);
        }
    }
    if terminated {
        Ok(props)
    } else {
        Ok(HashMap::new()) // unterminated frontmatter -> whole file is body, no props
    }
}

/// Parallel frontmatter scan preserving `files`' input order. Returns Err on
/// any file that couldn't be read as valid UTF-8/opened — the whole command
/// then Falls back, matching the "never wrong" contract.
fn scan_fm_parallel(files: &[PathBuf]) -> Result<Vec<HashMap<String, String>>, ()> {
    if files.is_empty() {
        return Ok(Vec::new());
    }
    let nthreads = std::thread::available_parallelism()
        .map(|n| n.get())
        .unwrap_or(4)
        .min(8);
    let chunk = files.len().div_ceil(nthreads).max(1);
    let out: Vec<Result<HashMap<String, String>, ()>> = std::thread::scope(|s| {
        let mut handles = Vec::new();
        for part in files.chunks(chunk) {
            handles.push(s.spawn(move || {
                part.iter().map(|fp| read_fm_props(fp)).collect::<Vec<_>>()
            }));
        }
        handles
            .into_iter()
            .flat_map(|h| h.join().unwrap_or_default())
            .collect()
    });
    let mut result = Vec::with_capacity(out.len());
    for r in out {
        match r {
            Ok(p) => result.push(p),
            Err(_) => return Err(()),
        }
    }
    Ok(result)
}

// ---------- board: dot-dirs-only exclusion, exactly cmd_board's live os.walk ----------
fn walk_board(dir: &Path, out: &mut Vec<PathBuf>) {
    if let Ok(rd) = fs::read_dir(dir) {
        for e in rd.flatten() {
            let name = e.file_name().to_string_lossy().to_string();
            let is_dir = e.file_type().map(|t| t.is_dir()).unwrap_or(false);
            if is_dir {
                if name.starts_with('.') {
                    continue;
                }
                walk_board(&e.path(), out);
            } else if name.ends_with(".md") {
                out.push(e.path());
            }
        }
    }
}

fn cmd_board(args: &[String], vault: &Path, t0: Instant) -> Outcome {
    if args.is_empty() {
        return Outcome::Fallback; // arity: folder is required
    }
    let folder = &args[0];
    let mut filters: Vec<(String, String)> = Vec::new();
    for f in &args[1..] {
        match f.find('=') {
            Some(i) => filters.push((f[..i].to_string(), f[i + 1..].to_string())),
            None => return Outcome::Fallback, // python: dict(...) unpack crashes -> let it
        }
    }
    let root = vault.join(folder);
    if !root.is_dir() {
        return Outcome::Fallback; // python dies not-found: canonical text is python's
    }
    let mut files = Vec::new();
    walk_board(&root, &mut files);
    let props_list = match scan_fm_parallel(&files) {
        Ok(v) => v,
        Err(_) => return Outcome::Fallback,
    };
    // sort key is the BASENAME (minus .md) only — matches python's
    // rows.append((n[:-3], ...)) / rows.sort(), not full relative path.
    let mut rows: Vec<(String, String, String)> = Vec::new();
    for (fp, props) in files.iter().zip(props_list.iter()) {
        if !filters
            .iter()
            .all(|(k, v)| props.get(k).map(|pv| pv == v).unwrap_or(false))
        {
            continue;
        }
        let name = fp.file_name().and_then(|n| n.to_str()).unwrap_or("");
        let name = name.strip_suffix(".md").unwrap_or(name).to_string();
        let status = props.get("status").cloned().unwrap_or_else(|| "-".into());
        let typ = props.get("type").cloned().unwrap_or_else(|| "-".into());
        rows.push((name, status, typ));
    }
    rows.sort();
    let mut buf = String::new();
    for (name, status, typ) in &rows {
        buf.push_str(&format!("{}\t{}\t{}\n", status, typ, name));
    }
    buf.push_str(&format!("({} notes)\n", rows.len()));
    let n = readpath::emit(&buf);
    readpath::log_metrics("board", t0, n, 0);
    Outcome::Done(0)
}

// ---------- ordered Counter (count desc, ties = insertion order — matches
// collections.Counter.most_common, which decorate-sort-undecorates stably) ----------
struct OrderedCounter {
    keys: Vec<String>,
    idx: HashMap<String, usize>,
    counts: Vec<i64>,
}

impl OrderedCounter {
    fn new() -> Self {
        OrderedCounter { keys: Vec::new(), idx: HashMap::new(), counts: Vec::new() }
    }
    fn add(&mut self, k: &str) {
        if let Some(&i) = self.idx.get(k) {
            self.counts[i] += 1;
        } else {
            self.idx.insert(k.to_string(), self.keys.len());
            self.keys.push(k.to_string());
            self.counts.push(1);
        }
    }
    fn len(&self) -> usize {
        self.keys.len()
    }
    fn total(&self) -> i64 {
        self.counts.iter().sum()
    }
    /// most_common(limit): stable sort by count desc; ties keep insertion order
    /// (Vec::sort_by is a stable timsort, matching CPython's sorted()/nlargest).
    fn most_common(&self, limit: Option<usize>) -> Vec<(String, i64)> {
        let mut items: Vec<(String, i64)> =
            self.keys.iter().cloned().zip(self.counts.iter().cloned()).collect();
        items.sort_by(|a, b| b.1.cmp(&a.1));
        match limit {
            Some(n) => items.into_iter().take(n).collect(),
            None => items,
        }
    }
}

fn tag_tokens(s: &str) -> Vec<String> {
    // [\w/-]+ over an ASCII-only value (non-ASCII already Fell back upstream)
    let mut out = Vec::new();
    let mut cur = String::new();
    for c in s.chars() {
        if c.is_ascii_alphanumeric() || c == '_' || c == '/' || c == '-' {
            cur.push(c);
        } else if !cur.is_empty() {
            out.push(std::mem::take(&mut cur));
        }
    }
    if !cur.is_empty() {
        out.push(cur);
    }
    out
}

fn cmd_tags(args: &[String], vault: &Path, t0: Instant) -> Outcome {
    let counts_flag = args.iter().any(|a| a == "--counts");
    let mut files = Vec::new();
    crate::walk_ex(vault, &mut files, false); // matches md_files(): dot-dirs + graphify-out only
    files.sort_by_key(|p| p.to_string_lossy().into_owned());
    let props_list = match scan_fm_parallel(&files) {
        Ok(v) => v,
        Err(_) => return Outcome::Fallback,
    };
    let mut c = OrderedCounter::new();
    for props in &props_list {
        let t = props.get("tags").cloned().unwrap_or_default();
        if !t.is_ascii() {
            // python's \w is unicode-aware; rather than re-derive its exact
            // Unicode word-char table we Fallback on any non-ASCII tags value.
            return Outcome::Fallback;
        }
        for tag in tag_tokens(&t) {
            c.add(&tag);
        }
    }
    let limit = if counts_flag { Some(40) } else { None };
    let mut buf = String::new();
    for (tag, n) in c.most_common(limit) {
        if counts_flag {
            buf.push_str(&format!("{}\t{}\n", n, tag));
        } else {
            buf.push_str(&format!("{}\n", tag));
        }
    }
    buf.push_str(&format!("({} tags)\n", c.len()));
    let n = readpath::emit(&buf);
    readpath::log_metrics("tags", t0, n, 0);
    Outcome::Done(0)
}

fn cmd_props(args: &[String], vault: &Path, t0: Instant) -> Outcome {
    if args.is_empty() || args.len() > 2 {
        return Outcome::Fallback; // arity: key required, folder optional
    }
    let key = &args[0];
    let folder = args.get(1).cloned().unwrap_or_default();
    let root: PathBuf = if !folder.is_empty() {
        let full = vault.join(&folder);
        // contain(): realpath-resolve and require it stays under the vault.
        // Unlike python's realpath (which needs no existence), fs::canonicalize
        // requires the path to exist — a nonexistent folder Falls back here
        // where python would silently print "(0 notes with KEY)"; documented
        // as a deliberately conservative (never-wrong) divergence.
        let real = match fs::canonicalize(&full) {
            Ok(r) => r,
            Err(_) => return Outcome::Fallback,
        };
        let vreal = match fs::canonicalize(vault) {
            Ok(r) => r,
            Err(_) => return Outcome::Fallback,
        };
        if !(real == vreal || real.starts_with(&vreal)) {
            return Outcome::Fallback;
        }
        full
    } else {
        vault.to_path_buf()
    };
    let mut files = Vec::new();
    crate::walk_ex(&root, &mut files, false);
    files.sort_by_key(|p| p.to_string_lossy().into_owned());
    let props_list = match scan_fm_parallel(&files) {
        Ok(v) => v,
        Err(_) => return Outcome::Fallback,
    };
    let mut c = OrderedCounter::new();
    for props in &props_list {
        if let Some(v) = props.get(key.as_str()) {
            if !v.is_empty() {
                // python: `if v:` — empty string is falsy, skipped too
                c.add(v);
            }
        }
    }
    let mut buf = String::new();
    for (v, n) in c.most_common(None) {
        buf.push_str(&format!("{}\t{}\n", n, v));
    }
    buf.push_str(&format!("({} notes with {})\n", c.total(), key));
    let n = readpath::emit(&buf);
    readpath::log_metrics("props", t0, n, 0);
    Outcome::Done(0)
}

// ---------- show: budgeted UTF-8-boundary-safe read, ported from cmd_show ----------
fn cmd_show(args: &[String], vault: &Path, t0: Instant) -> Outcome {
    if args.is_empty() {
        return Outcome::Fallback; // arity: ref required
    }
    let ref_ = &args[0];
    let mut max_bytes: i64 = 4000;
    let mut start = "H0".to_string();
    let mut it = args[1..].iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--max-bytes" => match it.next() {
                Some(v) => match v.parse::<i64>() {
                    Ok(n) => max_bytes = n,
                    Err(_) => return Outcome::Fallback,
                },
                None => return Outcome::Fallback, // python: next(it) StopIteration -> crash
            },
            "--from" => match it.next() {
                Some(v) => start = v.clone(),
                None => return Outcome::Fallback,
            },
            _ => {} // python's for/if-elif silently ignores any other token
        }
    }
    let fp = match readpath::resolve(vault, ref_) {
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
    let (lines, secs) = readpath::parse(&text);

    let mut out_buf = String::new();
    let mut started = false;
    let mut used: i64 = 0;
    for s in &secs {
        if s.id == start {
            started = true;
        }
        if !started {
            continue;
        }
        let t = readpath::sec_text(&lines, s);
        let tb = t.as_bytes().len() as i64;
        if tb == 0 {
            continue; // empty preamble section still cost a newline (skip)
        }
        if used + tb + 1 > max_bytes {
            if used > 0 {
                let mut more = format!(
                    "[more: {} '{}' {}B — continue: vv show {} --from {}]",
                    s.id, s.title, tb, ref_, s.id
                );
                if used + more.as_bytes().len() as i64 + 1 > max_bytes {
                    more = "[more]".to_string();
                }
                out_buf.push_str(&more);
                out_buf.push('\n');
                break;
            }
            let mut marker = format!(
                "[truncated: {} '{}' is {}B of a {}B budget — read it whole with: vv read {} {}]",
                s.id, s.title, tb, max_bytes, ref_, s.id
            );
            let mut room = max_bytes - used - marker.as_bytes().len() as i64 - 2;
            if room <= 0 {
                marker = "[truncated]".to_string();
                room = max_bytes - used - marker.as_bytes().len() as i64 - 2;
            }
            if room > 0 {
                let tbytes = t.as_bytes();
                let mut cut = (room as usize).min(tbytes.len());
                while cut > 0 && !t.is_char_boundary(cut) {
                    cut -= 1;
                }
                let piece = t[..cut].trim_end_matches('\n');
                out_buf.push_str(piece);
                out_buf.push('\n');
            }
            out_buf.push_str(&marker);
            out_buf.push('\n');
            used = max_bytes;
            break;
        }
        out_buf.push_str(&t);
        out_buf.push('\n');
        used += tb + 1;
    }
    if !started {
        return Outcome::Fallback; // python: die not-found
    }
    let n = readpath::emit(&out_buf);
    readpath::log_metrics("show", t0, n, cf);
    Outcome::Done(0)
}
