#![forbid(unsafe_code)]
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
    files.sort();
    // ranking — IDENTICAL to cmd_search in vv.py (adapted from rustdoc search):
    // a "/" term is a path filter; other terms score +500 for a NAME match plus
    // +1 per content hit, and every term must match somewhere. Ties: shorter
    // path, then lexicographic — deterministic across engines.
    let path_terms: Vec<&String> = terms.iter().filter(|t| t.contains('/')).collect();
    let body_terms: Vec<&String> = terms.iter().filter(|t| !t.contains('/')).collect();
    let mut hits: Vec<(usize, String, String)> = Vec::new();
    for fp in &files {
        let rel = fp.strip_prefix(&root).unwrap_or(fp).to_string_lossy().to_string();
        let rl = rel.to_lowercase();
        if !path_terms.iter().all(|t| rl.contains(t.as_str())) {
            continue;
        }
        let text = match fs::read_to_string(fp) {
            Ok(t) => t,
            Err(_) => continue,
        };
        let low = text.to_lowercase();
        let base = rl.rsplit('/').next().unwrap_or(&rl).trim_end_matches(".md").to_string();
        let mut score = 0usize;
        let mut ok = true;
        let mut first_pos: Option<usize> = None;
        for t in &body_terms {
            let in_name = base.contains(t.as_str());
            let c = low.matches(t.as_str()).count();
            if !in_name && c == 0 {
                ok = false;
                break;
            }
            score += if in_name { 500 } else { 0 } + c;
            if c > 0 {
                if let Some(p) = low.find(t.as_str()) {
                    first_pos = Some(first_pos.map_or(p, |q: usize| q.min(p)));
                }
            }
        }
        if !ok {
            continue;
        }
        let start = first_pos.map_or(0, |p| p.saturating_sub(w / 4));
        let start = (0..=start).rev().find(|i| text.is_char_boundary(*i)).unwrap_or(0);
        let end = (start + w).min(text.len());
        let end = (end..text.len().max(end)).find(|i| text.is_char_boundary(*i)).unwrap_or(text.len());
        let snip = text[start..end].replace('\n', " ¶ ");
        hits.push((score, rel, snip));
    }
    hits.sort_by(|a, b| b.0.cmp(&a.0).then(a.1.len().cmp(&b.1.len())).then(a.1.cmp(&b.1)));
    let shown = hits.len().min(k);
    for (score, rel, snip) in hits.iter().take(k) {
        println!("== {} (score {})\n{}\n", rel, score, snip);
    }
    println!("({} of {} matches)", shown, hits.len());
}


/// find `pat` in `chars` at or after `start` (char positions, not bytes)
fn find_seq(chars: &[char], start: usize, pat: &[char]) -> Option<usize> {
    if chars.len() < pat.len() { return None; }
    (start..=chars.len() - pat.len()).find(|&i| chars[i..i + pat.len()] == *pat)
}

/// mask every <!-- --> span from `pos` onward; sets `in_comment` on an unclosed opener
fn mask_comments(masked: &mut [char], pos: &mut usize, in_comment: &mut bool) {
    let opener = ['<', '!', '-', '-'];
    let closer = ['-', '-', '>'];
    while let Some(s) = find_seq(masked, *pos, &opener) {
        match find_seq(masked, s + 4, &closer) {
            Some(e) => {
                for k in s..e + 3 { masked[k] = '\u{0}'; }
                *pos = e + 3;
            }
            None => {
                let n = masked.len();
                for k in s..n { masked[k] = '\u{0}'; }
                *in_comment = true;
                break;
            }
        }
    }
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
    files.sort();
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
        let mut in_comment = false;
        for (i, raw) in lines.iter().enumerate() {
            let raw = raw.trim_end_matches('\r'); // CRLF must not defeat fence detection
            // an OPEN comment owns the line — no fence transitions until --> (probed
            // against Obsidian 2026-08-26: a ``` inside a comment is literal text)
            if !in_comment {
                // fence indent = ASCII spaces only (parity with vv.py's `^ {0,3}`;
                // NBSP/tab is not fence indent, and byte-vs-char counting can't drift)
                let indent = raw.chars().take_while(|&c| c == ' ').count();
                let trimmed = &raw[indent..];
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
                let mut line_fenced = false;
                if i >= fm_end {
                    match (marker, fence) {
                        // CommonMark: a backtick fence's info string may not
                        // contain backticks — ```code``` is an inline span
                        (None, Some((c, n, rest)))
                            if c == '~' || !rest.contains('`') =>
                        {
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
            // mask inline code spans (CommonMark: a run of N backticks closes on a run
            // of exactly N)
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
            // HTML comments: Obsidian does not index links inside <!-- --> (it DOES
            // inside its own %% comments) — mirrors html_comment_spans() in vv.py.
            if in_comment {
                match find_seq(&masked, 0, &['-', '-', '>']) {
                    None => {
                        continue; // whole line inside a comment
                    }
                    Some(e) => {
                        for k in 0..e + 3 { masked[k] = '\u{0}'; }
                        in_comment = false;
                        let mut pos = e + 3;
                        mask_comments(&mut masked, &mut pos, &mut in_comment);
                    }
                }
            } else {
                let mut pos = 0usize;
                mask_comments(&mut masked, &mut pos, &mut in_comment);
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
                        let seg = inner
                            .split(|c| c == '|' || c == '#')
                            .next()
                            .unwrap_or("")
                            .trim();
                        // exactly ONE trailing backslash is consumed at a boundary:
                        // [[Note\|alias]] escapes the alias pipe, [[Note\]] resolves to
                        // Note; a second backslash stays in the target and resolves to
                        // nothing (probed against Obsidian 2026-08-26). Mirrors
                        // wiki_target() in vv.py.
                        let target = seg
                            .strip_suffix('\\')
                            .map(str::trim_end)
                            .unwrap_or(seg)
                            .to_string();
                        // a target overlapping a masked region (inline code / HTML
                        // comment) is not a link — the NUL bytes are the evidence
                        if !target.is_empty() && !target.contains('\u{0}')
                            && needle.as_ref().map_or(true, |n| target.to_lowercase().contains(n.as_str()))
                        {
                            buf.push_str(&format!("{}\t{}\tw\t{}\n", rel, i + 1, target));
                        }
                        j = end + 2;
                        continue;
                    }
                }
                // ](path.md)
                // a ] preceded by ] is a wikilink closer, never [text]( —
                // mirrors MDLINK_RE's (?<!\]) lookbehind in vv.py
                if b[j] == ']' && b[j + 1] == '(' && (j == 0 || b[j - 1] != ']') {
                    if let Some(end) = (j + 2..b.len()).find(|&k| b[k] == ')') {
                        let inner: String = b[j + 2..end].iter().collect();
                        if inner.ends_with(".md")
                            && !inner.contains(char::is_whitespace)
                            && !inner.contains('\u{0}')
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
        Some("search") => cmd_search(&args[1..]),
        Some("linkscan") => cmd_linkscan(&args[1..]),
        _ => {
            eprintln!("usage: vrust search <terms...> [--k N] [--w CHARS] | linkscan [--grep NEEDLE]");
            exit(1);
        }
    }
}
