// vrust — PROTOTYPE: Rust vault search/outline, std-only (no crates).
// Mirrors vnote2.py semantics for an apples-to-apples benchmark:
//   vrust search <terms...> [--k N] [--w CHARS]
//   vrust outline <rel-path>
use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::exit;

fn vault() -> PathBuf {
    let home = env::var("HOME").expect("HOME unset");
    Path::new(&home).join("Documents/Obsidian Vault")
}

fn walk(dir: &Path, out: &mut Vec<PathBuf>) {
    if let Ok(rd) = fs::read_dir(dir) {
        for e in rd.flatten() {
            let p = e.path();
            let name = e.file_name().to_string_lossy().to_string();
            if p.is_dir() {
                if name.starts_with('.') || name == "graphify-out" || name == "Sandbox" {
                    continue;
                }
                walk(&p, out);
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
    walk(&root, &mut files);
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

fn main() {
    let args: Vec<String> = env::args().skip(1).collect();
    match args.first().map(String::as_str) {
        Some("outline") if args.len() >= 2 => cmd_outline(&args[1]),
        Some("search") => cmd_search(&args[1..]),
        _ => {
            eprintln!("usage: vrust search <terms...> [--k N] [--w CHARS] | outline <rel-path>");
            exit(1);
        }
    }
}
