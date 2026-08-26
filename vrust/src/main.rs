// vrust — PROTOTYPE: Rust vault search/outline, std-only (no crates).
// Mirrors vnote2.py semantics for an apples-to-apples benchmark:
//   vrust search <terms...> [--k N] [--w CHARS]
//   vrust outline <rel-path>
use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::exit;

fn vault() -> PathBuf {
    if let Ok(v) = env::var("VV_VAULT") {
        return PathBuf::from(v);
    }
    let home = env::var("HOME").expect("HOME unset");
    Path::new(&home).join("Documents/Obsidian Vault")
}

fn walk(dir: &Path, out: &mut Vec<PathBuf>) {
    walk_ex(dir, out, false)
}

/// `exclude_sandbox` is a SEARCH-relevance choice, never a graph-correctness one:
/// link/graph scans must see every note the Python side sees.
fn walk_ex(dir: &Path, out: &mut Vec<PathBuf>, exclude_sandbox: bool) {
    if let Ok(rd) = fs::read_dir(dir) {
        for e in rd.flatten() {
            let p = e.path();
            let name = e.file_name().to_string_lossy().to_string();
            if p.is_dir() {
                if name.starts_with('.') || name == "graphify-out" || (exclude_sandbox && name == "Sandbox") {
                    continue;
                }
                walk_ex(&p, out, exclude_sandbox);
            } else if name.ends_with(".md") {
                out.push(p);
            }
        }
    }
}

fn sha8(data: &str) -> String {
    // FNV-1a 64-bit, hex-8 — prototype anchor only (not cryptographic; the
    // python side uses sha256; for the benchmark only output SIZE matters).
    let mut h: u64 = 0xcbf29ce484222325;
    for b in data.as_bytes() {
        h ^= *b as u64;
        h = h.wrapping_mul(0x100000001b3);
    }
    format!("{:08x}", (h >> 32) as u32)
}

fn cmd_outline(rel: &str) {
    let fp = vault().join(rel);
    let text = match fs::read_to_string(&fp) {
        Ok(t) => t,
        Err(_) => {
            eprintln!("error: no such note: {}", rel);
            exit(1);
        }
    };
    let lines: Vec<&str> = text.split('\n').collect();
    let mut fenced = vec![false; lines.len()];
    let mut fm_end = 0usize;
    if !lines.is_empty() && lines[0].trim_end_matches('\r') == "---" {
        for i in 1..lines.len() {
            if lines[i].trim_end_matches('\r') == "---" {
                fm_end = i + 1;
                break;
            }
        }
    }
    for f in fenced.iter_mut().take(fm_end) {
        *f = true;
    }
    let mut open = false;
    for (i, l) in lines.iter().enumerate() {
        if i < fm_end {
            continue;
        }
        if l.starts_with("```") || l.starts_with("~~~") {
            open = !open;
            fenced[i] = true;
        } else if open {
            fenced[i] = true;
        }
    }
    let mut heads: Vec<(usize, usize, String)> = Vec::new();
    for (i, l) in lines.iter().enumerate() {
        if fenced[i] {
            continue;
        }
        let hashes = l.chars().take_while(|c| *c == '#').count();
        if hashes >= 1 && hashes <= 6 && l.chars().nth(hashes) == Some(' ') {
            heads.push((i, hashes, l[hashes + 1..].trim().to_string()));
        }
    }
    let first = heads.first().map(|h| h.0).unwrap_or(lines.len());
    let mut secs: Vec<(String, usize, String, usize, usize)> = Vec::new();
    secs.push(("H0".into(), 0, "(preamble)".into(), 0, first));
    for (j, (i, lvl, title)) in heads.iter().enumerate() {
        let end = heads.get(j + 1).map(|h| h.0).unwrap_or(lines.len());
        secs.push((format!("H{}", j + 1), *lvl, title.clone(), *i, end));
    }
    for (id, lvl, title, start, end) in secs {
        let body = lines[start..end].join("\n");
        let marks = if lvl == 0 { "-".into() } else { "#".repeat(lvl) };
        println!("{}\t{}\t{}\t{}B\t{}", id, marks, title, body.len(), sha8(&body));
    }
}

fn cmd_search(args: &[String]) {
    let mut k = 5usize;
    let mut w = 500usize;
    let mut terms: Vec<String> = Vec::new();
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--k" => k = it.next().and_then(|v| v.parse().ok()).unwrap_or(5),
            "--w" => w = it.next().and_then(|v| v.parse().ok()).unwrap_or(500),
            t => terms.push(t.to_lowercase()),
        }
    }
    if terms.is_empty() {
        eprintln!("error: no query");
        exit(1);
    }
    let root = vault();
    let mut files = Vec::new();
    walk_ex(&root, &mut files, true); // search excludes Sandbox
    let mut hits: Vec<(usize, String, String)> = Vec::new();
    for fp in &files {
        let text = match fs::read_to_string(fp) {
            Ok(t) => t,
            Err(_) => continue,
        };
        let low = text.to_lowercase();
        let mut score = 0usize;
        let mut all = true;
        let mut first_pos = usize::MAX;
        for t in &terms {
            let c = low.matches(t.as_str()).count();
            if c == 0 {
                all = false;
                break;
            }
            score += c;
            if let Some(p) = low.find(t.as_str()) {
                first_pos = first_pos.min(p);
            }
        }
        if !all || score == 0 {
            continue;
        }
        let start = first_pos.saturating_sub(w / 4);
        let start = (0..=start).rev().find(|i| text.is_char_boundary(*i)).unwrap_or(0);
        let end = (start + w).min(text.len());
        let end = (end..text.len().max(end)).find(|i| text.is_char_boundary(*i)).unwrap_or(text.len());
        let snip = text[start..end].replace('\n', " ¶ ");
        let rel = fp.strip_prefix(&root).unwrap_or(fp).to_string_lossy().to_string();
        hits.push((score, rel, snip));
    }
    hits.sort_by(|a, b| b.0.cmp(&a.0));
    let shown = hits.len().min(k);
    for (score, rel, snip) in hits.iter().take(k) {
        println!("== {} (score {})\n{}\n", rel, score, snip);
    }
    println!("({} of {} matches)", shown, hits.len());
}

/// Emit every active wikilink/markdown-link target in the vault as TSV:
///   <rel-path>\t<line-1-based>\t<kind: w|m>\t<target>
/// "Active" excludes fenced blocks (own-marker close) and inline code spans.
/// Frontmatter is NOT excluded: `related:` links there are real links.
fn cmd_linkscan(args: &[String]) {
    // --grep <needle>: emit only rows whose target contains <needle> (case-insensitive).
    // Rust does the I/O and parsing (the expensive part); the caller keeps the semantics
    // (ambiguity, .md equivalence, relative resolution) so there is no duplicated meaning.
    let needle = args
        .iter()
        .position(|a| a == "--grep")
        .and_then(|i| args.get(i + 1))
        .map(|s| s.to_lowercase());
    let root = vault();
    let mut files = Vec::new();
    walk_ex(&root, &mut files, false); // graph sees everything
    let mut buf = String::with_capacity(1 << 20);
    for fp in &files {
        let text = match fs::read_to_string(fp) {
            Ok(t) => t,
            Err(_) => continue,
        };
        let rel = fp.strip_prefix(&root).unwrap_or(fp).to_string_lossy().to_string();
        let lines: Vec<&str> = text.split('\n').collect();

        // frontmatter bounds (BOM tolerant) — used only to start fence scanning after it
        let mut fm_end = 0usize;
        if !lines.is_empty() && lines[0].trim_start_matches('\u{feff}').trim_end_matches('\r') == "---" {
            for i in 1..lines.len() {
                if lines[i].trim_end_matches('\r') == "---" {
                    fm_end = i + 1;
                    break;
                }
            }
        }
        let mut marker: Option<(char, usize)> = None;
        for (i, raw) in lines.iter().enumerate() {
            let raw = raw.trim_end_matches('\r'); // CRLF must not defeat fence detection
            let trimmed = raw.trim_start();
            let indent = raw.len() - trimmed.len();
            // CommonMark: a fence closes only on its own char AND a run >= the opener's
            let fence: Option<(char, usize, &str)> = if indent <= 3
                && (trimmed.starts_with("```") || trimmed.starts_with("~~~"))
            {
                let c = trimmed.chars().next().unwrap();
                let n = trimmed.chars().take_while(|&x| x == c).count();
                Some((c, n, &trimmed[n..]))
            } else {
                None
            };
            if i >= fm_end {
                match (marker, fence) {
                    (None, Some((c, n, _))) => {
                        marker = Some((c, n));
                        continue;
                    }
                    (Some((mc, mn)), Some((c, n, rest)))
                        if c == mc && n >= mn && rest.trim().is_empty() =>
                    {
                        marker = None;
                        continue;
                    }
                    (Some(_), _) => continue,
                    _ => {}
                }
            }
            // mask inline code spans (CommonMark: a run of N backticks closes on a run of exactly N)
            let chars: Vec<char> = raw.chars().collect();
            let mut masked: Vec<char> = chars.clone();
            let mut ci = 0usize;
            while ci < chars.len() {
                if chars[ci] == '`' {
                    let mut n = 0usize;
                    while ci + n < chars.len() && chars[ci + n] == '`' {
                        n += 1;
                    }
                    // find a closing run of exactly n backticks
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
            let masked: String = masked.into_iter().collect();
            // [[wikilink]] / ![[embed]]
            let b: Vec<char> = masked.chars().collect();
            let mut j = 0usize;
            while j + 1 < b.len() {
                if b[j] == '[' && b[j + 1] == '[' {
                    if let Some(end) = (j + 2..b.len().saturating_sub(1))
                        .find(|&k| b[k] == ']' && b[k + 1] == ']')
                    {
                        let inner: String = b[j + 2..end].iter().collect();
                        let target = inner
                            .split(|c| c == '|' || c == '#')
                            .next()
                            .unwrap_or("")
                            .trim()
                            .to_string();
                        if !target.is_empty()
                            && needle.as_ref().map_or(true, |n| target.to_lowercase().contains(n.as_str()))
                        {
                            buf.push_str(&format!("{}\t{}\tw\t{}\n", rel, i + 1, target));
                        }
                        j = end + 2;
                        continue;
                    }
                }
                // ](path.md)
                if b[j] == ']' && b[j + 1] == '(' {
                    if let Some(end) = (j + 2..b.len()).find(|&k| b[k] == ')') {
                        let inner: String = b[j + 2..end].iter().collect();
                        if inner.ends_with(".md")
                            && !inner.contains(char::is_whitespace)
                        {
                            buf.push_str(&format!("{}\t{}\tm\t{}\n", rel, i + 1, inner));
                        }
                        j = end + 1;
                        continue;
                    }
                }
                j += 1;
            }
        }
    }
    print!("{}", buf);
}

fn main() {
    let args: Vec<String> = env::args().skip(1).collect();
    match args.first().map(String::as_str) {
        Some("outline") if args.len() >= 2 => cmd_outline(&args[1]),
        Some("search") => cmd_search(&args[1..]),
        Some("linkscan") => cmd_linkscan(&args[1..]),
        _ => {
            eprintln!("usage: vrust search <terms...> [--k N] [--w CHARS] | outline <rel-path> | linkscan [--grep NEEDLE]");
            exit(1);
        }
    }
}
