#![forbid(unsafe_code)]
// Style lints allowed deliberately, not from neglect (each -D-enforced otherwise):
// - needless_range_loop: the lexers walk parallel byte/mask arrays by index on
//   purpose; rewriting verified loops into iterator chains to please a style
//   lint risks parity drift in a differential-tested engine for zero payoff.
// - type_complexity / too_many_args: the cache and link-resolution signatures
//   mirror the Python implementation's shapes; a type alias would rename the
//   complexity, not remove it.
#![allow(
    clippy::needless_range_loop,
    clippy::type_complexity,
    clippy::too_many_arguments
)]
// vrust — PROTOTYPE: Rust vault search/outline, std-only (no crates).
// Mirrors vnote2.py semantics for an apples-to-apples benchmark:
//   vrust search <terms...> [--k N] [--w CHARS]
//   vrust outline <rel-path>
use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::exit;
mod cache;
mod difflib;
mod err;
mod graph;
mod query;
mod readpath;
mod write;

/// python's os.path.normpath, LEXICALLY: drops trailing separators and "."
/// segments and collapses ".." against the previous segment without touching
/// the filesystem. components().collect() alone kept ".." — so a VV_VAULT of
/// "/a/link/../vault" resolved through the symlink natively but lexically in
/// python, and the two engines could address different vaults.
fn normpath(p: &Path) -> PathBuf {
    use std::path::Component;
    let mut out: Vec<Component> = Vec::new();
    for c in p.components() {
        match c {
            Component::CurDir => {}
            Component::ParentDir => match out.last() {
                Some(Component::Normal(_)) => {
                    out.pop();
                }
                Some(Component::RootDir) => {} // "/.." is "/"
                _ => out.push(c),              // relative: keep the ".."
            },
            other => out.push(other),
        }
    }
    if out.is_empty() {
        // python: normpath(".") == normpath("Sub/..") == "." — never "".
        // yagni: parity, not defence; drop only if no raw walk consumes vault().
        return PathBuf::from(".");
    }
    out.iter().collect()
}

fn vault() -> PathBuf {
    // Normalised at the source the way python does: a "…/vault/" VV_VAULT made
    // native `orphans .` build a root prefix no walked path shares and answer
    // a silent zero.
    if let Ok(v) = env::var("VV_VAULT") {
        if !v.is_empty() {
            // python: `or` — empty means default (Codex parity audit)
            return normpath(Path::new(&v));
        }
    }
    let home = env::var("HOME").expect("HOME unset");
    Path::new(&home).join("Documents/Obsidian Vault")
}

/// python's `SKIP_DIRS` (src/vv_impl.py), mirrored WHOLE so a future non-dot
/// member cannot drift the engines apart (query.rs::walk_board reads the same
/// list). Every member but graphify-out is a dot-dir today.
pub const SKIP_DIRS: [&str; 5] = [".git", ".obsidian", ".claude", ".trash", "graphify-out"];

/// `exclude_sandbox` is a SEARCH-relevance choice, never a graph-correctness one:
/// link/graph scans must see every note the Python side sees.
pub fn walk_ex(dir: &Path, out: &mut Vec<PathBuf>, exclude_sandbox: bool) {
    walk_checked(dir, out, exclude_sandbox, false);
}

/// Like `walk_ex`, but reports whether EVERY directory could be listed. A
/// resolver that claims a bare name or id is unique needs a complete walk —
/// an unreadable directory may hold a second note — so `readpath::resolve`
/// hands an incomplete walk to Python, which refuses (parity with
/// `_walk_errors` in vv_impl.py).
/// A corpus scan's read: non-UTF-8 decoded lossily (the link grammar is
/// ASCII, python's `_read_lossy` does the same), `None` only on an I/O error.
pub fn read_lossy(fp: &Path) -> Option<String> {
    fs::read(fp)
        .ok()
        .map(|b| String::from_utf8_lossy(&b).into_owned())
}

/// `check_read`: also treat a note we cannot open as incomplete evidence.
/// Only the graph reads ask for it — they read every note anyway; a resolver
/// needs names, not contents, and must not pay an open per note (an 18×
/// regression on `resolve` was measured when the check sat in the walker).
pub fn walk_checked(
    dir: &Path,
    out: &mut Vec<PathBuf>,
    exclude_sandbox: bool,
    check_read: bool,
) -> bool {
    let mut complete = true;
    match fs::read_dir(dir) {
        Ok(rd) => {
            for e in rd {
                // an entry we could not evaluate marks the walk incomplete: a
                // failed iterator step is skipped, a failed file_type() is
                // kept by name (it may be a note). readpath/graph discard an
                // incomplete walk; the other walk_ex callers never needed
                // completeness and keep the partial list
                let e = match e {
                    Ok(e) => e,
                    Err(_) => {
                        complete = false;
                        continue;
                    }
                };
                let p = e.path();
                let name = e.file_name().to_string_lossy().to_string();
                // file_type() does NOT follow symlinks — parity with os.walk(followlinks=False):
                // a symlinked directory is never descended (parity with python)
                let is_dir = match e.file_type() {
                    Ok(t) => t.is_dir(),
                    Err(_) => {
                        complete = false;
                        false
                    }
                };
                if is_dir {
                    if name.starts_with('.')
                        || SKIP_DIRS.contains(&name.as_str())
                        || (exclude_sandbox && name == "Sandbox")
                    {
                        continue;
                    }
                    if !walk_checked(&p, out, exclude_sandbox, check_read) {
                        complete = false;
                    }
                } else if name.ends_with(".md") {
                    // a note we cannot open is incomplete evidence too: the
                    // Python fallback records it and warns or refuses
                    if check_read && fs::File::open(&p).is_err() {
                        complete = false;
                    }
                    out.push(p);
                }
            }
        }
        Err(_) => complete = false,
    }
    complete
}

fn score_one(
    fp: &Path,
    root: &std::path::PathBuf,
    path_terms: &[&String],
    body_terms: &[&String],
    w: usize,
) -> Option<(usize, String, String)> {
    let rel = fp
        .strip_prefix(root)
        .unwrap_or(fp)
        .to_string_lossy()
        .to_string();
    let rl = rel.to_lowercase();
    if !path_terms.iter().all(|t| rl.contains(t.as_str())) {
        return None;
    }
    let text = read_lossy(fp)?; // python parity: search reads lossily
    let low = text.to_lowercase();
    let base = rl
        .rsplit('/')
        .next()
        .unwrap_or(&rl)
        .trim_end_matches(".md")
        .to_string();
    let mut score = 0usize;
    let mut first_pos: Option<usize> = None;
    for t in body_terms {
        let in_name = base.contains(t.as_str());
        let c = low.matches(t.as_str()).count();
        if !in_name && c == 0 {
            return None;
        }
        score += if in_name { 500 } else { 0 } + c;
        if c > 0 {
            if let Some(p) = low.find(t.as_str()) {
                first_pos = Some(first_pos.map_or(p, |q: usize| q.min(p)));
            }
        }
    }
    // `w` is a width in CHARACTERS, not bytes — python slices `text[start:start+w]`
    // on a char-indexed str. Byte slicing here made every snippet containing
    // multi-byte UTF-8 (em dashes, arrows, curly quotes — ubiquitous in the
    // vault) short by one char per extra byte: 16 of 18 real query terms
    // diverged from python, and the engine-parity suite never saw it because it
    // compares only the `==` path+score headers, never the snippet body.
    // `low.find` returns a BYTE offset, so convert it to a char index first.
    let start = first_pos.map_or(0, |bp| low[..bp].chars().count().saturating_sub(w / 4));
    let snip: String = text
        .chars()
        .skip(start)
        .take(w)
        .collect::<String>()
        .replace('\n', " ¶ ");
    Some((score, rel, snip))
}

fn cmd_search(args: &[String], orig: &[String]) -> ! {
    let t0 = std::time::Instant::now();
    // global --limit acts as --k unless --k is explicit (parity with python)
    let mut k = readpath::LIMIT.get().copied().unwrap_or(5);
    let mut w = 500usize;
    let mut files_only = false;
    let mut terms: Vec<String> = Vec::new();
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            // One spelling both engines parse alike: an optional `+`, then
            // ASCII digits whose value has at most 18 significant digits. Anything else is a usage error and python
            // authors that text; python applies the SAME predicate before it
            // delegates back here, so the handoff cannot loop.
            "--k" | "--w" => {
                let n = it.next().and_then(|v| {
                    let d = v.strip_prefix('+').unwrap_or(v);
                    let digits = !d.is_empty() && d.bytes().all(|b| b.is_ascii_digit());
                    let sig = d.trim_start_matches('0');
                    (digits && sig.len() <= 18).then(|| sig.parse::<usize>().unwrap_or(0))
                });
                match (a.as_str(), n) {
                    ("--k", Some(n)) => k = n,
                    (_, Some(n)) => w = n,
                    (_, None) => {
                        // python delegated this search (VV_FROM_PY): handing
                        // back would bounce between the engines without bound
                        if readpath::from_py() {
                            eprintln!(
                                "engine: search {} value is not one the native scanner parses",
                                a
                            );
                            exit(1);
                        }
                        exec_python(orig)
                    }
                }
            }
            "--files" => files_only = true,
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
    // Parallel scan: per-file open+read dominates warm-cache time (measured
    // 2026-08-27: ~72% of wall). Chunk the file list across threads; scoring is
    // pure per-file, and the final sort restores the deterministic order, so
    // output is byte-identical to the sequential form (engine-parity-tested).
    let nthreads = std::thread::available_parallelism()
        .map(|n| n.get())
        .unwrap_or(4)
        .min(8);
    let chunk = files.len().div_ceil(nthreads).max(1);
    let mut hits: Vec<(usize, String, String)> = std::thread::scope(|s| {
        let mut handles = Vec::new();
        for part in files.chunks(chunk) {
            let root = &root;
            let path_terms = &path_terms;
            let body_terms = &body_terms;
            handles.push(s.spawn(move || {
                let mut local: Vec<(usize, String, String)> = Vec::new();
                for fp in part {
                    if let Some(h) = score_one(fp, root, path_terms, body_terms, w) {
                        local.push(h);
                    }
                }
                local
            }));
        }
        handles
            .into_iter()
            .flat_map(|h| h.join().unwrap_or_default())
            .collect()
    });
    #[allow(unreachable_code)]
    hits.sort_by(|a, b| {
        b.0.cmp(&a.0)
            .then(a.1.len().cmp(&b.1.len()))
            .then(a.1.cmp(&b.1))
    });
    // Zero hits is python's to answer, not ours. python emits a phrase hint
    // ("matched as ONE phrase ... retry unquoted") that this engine does not
    // implement; printing a bare "(0 of 0 matches)" here silently un-ships that
    // hint on the default entry and reinstates the quoted-phrase SILENCE the
    // hint was added to fix. Nothing has been printed yet, so handing off is
    // clean. Caught 2026-08-27 by replaying real sessions through both engines.
    // Zero hits: only hand off when THIS binary is the top-level entry. When
    // python invoked us (VV_FROM_PY) it adds the hint itself, and handing off
    // would print it twice.
    //
    // Recursion is bounded by that same flag, not by forcing python's slow
    // in-process scanner: python re-invokes this binary WITH VV_FROM_PY set, so
    // the inner call returns normally instead of handing off again. Forcing
    // VV_ENGINE=python here also terminated, but made a zero-hit search 360 ms
    // (vs 36 ms for a hit) by rescanning with the pure python scanner.
    // The hint can only arise for a QUOTED multi-word argument, so hand off
    // only then — and tell python the scan already came back empty
    // (VV_NATIVE_ZERO), so it goes straight to the hint instead of scanning
    // the vault again to rediscover the zero.
    // "Multi-word" is python's `len(t.split()) > 1`: str.split() also breaks
    // on U+001C..U+001F, which char::is_whitespace does not count.
    let py_space = |c: char| c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c);
    if hits.is_empty()
        && !readpath::from_py()
        && terms
            .iter()
            .any(|t| t.split(py_space).filter(|w| !w.is_empty()).count() > 1)
    {
        // our pid: python believes the flag only from its own parent and
        // only on a hand-off, so a value merely inherited from an outer
        // environment does not become a false zero
        std::env::set_var("VV_NATIVE_ZERO", std::process::id().to_string());
        exec_python(orig);
    }
    let shown = hits.len().min(k);
    // Build the full output once and emit it in one shot: out_bytes must be
    // the byte length of what was actually written (headings, snippets,
    // trailer), not the hit count -- readpath::run's arms all emit(&buf)
    // for the same reason.
    let mut buf = String::new();
    if files_only {
        // --files: matching paths only, same ranking, same trailer (parity-pinned).
        for (_score, rel, _snip) in hits.iter().take(k) {
            buf.push_str(rel);
            buf.push('\n');
        }
    } else {
        for (score, rel, snip) in hits.iter().take(k) {
            buf.push_str(&format!("== {} (score {})\n{}\n\n", rel, score, snip));
        }
    }
    buf.push_str(&format!("({} of {} matches)\n", shown, hits.len()));
    let n = readpath::emit(&buf);
    // Python delegates search to this binary with VV_FROM_PY set and logs its
    // own row at exit (cmd_search in vv_impl.py) -- logging here too would
    // double the row. _phrase_hint also runs an internal search this way
    // (VV_FROM_PY set, k=0) that must not count as user activity either.
    if !readpath::from_py() {
        readpath::log_metrics("search", t0, n, 0, None);
    }
    exit(0);
}

/// find `pat` in `chars` at or after `start` (char positions, not bytes)
fn find_seq(chars: &[char], start: usize, pat: &[char]) -> Option<usize> {
    if chars.len() < pat.len() {
        return None;
    }
    (start..=chars.len() - pat.len()).find(|&i| chars[i..i + pat.len()] == *pat)
}

/// mask every <!-- --> span from `pos` onward; sets `in_comment` on an unclosed opener
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
        // non-UTF-8 is decoded lossily and scanned (the link grammar is ASCII;
        // python refuses to REWRITE such a note at plan time); only an I/O
        // failure is a `u` row — incomplete evidence, never a vanished note
        let text = match read_lossy(fp) {
            Some(t) => t,
            None => {
                println!(
                    "{}\t0\tu\t",
                    fp.strip_prefix(&root).unwrap_or(fp).to_string_lossy()
                );
                continue;
            }
        };
        let rel = fp
            .strip_prefix(&root)
            .unwrap_or(fp)
            .to_string_lossy()
            .to_string();
        let lines: Vec<&str> = text.split('\n').collect();

        // frontmatter bounds (BOM tolerant) — used only to start fence scanning after it
        let mut fm_end = 0usize;
        if !lines.is_empty()
            && lines[0]
                .trim_start_matches('\u{feff}')
                .trim_end_matches('\r')
                == "---"
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
            let raw = raw.trim_end_matches('\r'); // CRLF must not defeat fence detection
                                                  // an OPEN comment owns the line — no fence transitions until --> (probed
                                                  // against Obsidian 2026-08-26: a ``` inside a comment is literal text)
            if !in_comment {
                // fence indent = ASCII spaces only (parity with vv.py's `^ {0,3}`;
                // NBSP/tab is not fence indent, and byte-vs-char counting can't drift)
                let indent = raw.chars().take_while(|&c| c == ' ').count();
                let trimmed = &raw[indent..];
                // CommonMark: a fence closes only on its own char AND a run >= the opener's
                let fence: Option<(char, usize, &str)> =
                    if indent <= 3 && (trimmed.starts_with("```") || trimmed.starts_with("~~~")) {
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
            let masked: String = masked.into_iter().collect();
            // [[wikilink]] / ![[embed]]
            let b: Vec<char> = masked.chars().collect();
            let mut j = 0usize;
            while j + 1 < b.len() {
                if b[j] == '[' && b[j + 1] == '[' {
                    if let Some(end) =
                        (j + 2..b.len().saturating_sub(1)).find(|&k| b[k] == ']' && b[k + 1] == ']')
                    {
                        let inner: String = b[j + 2..end].iter().collect();
                        let seg = inner.split(['|', '#']).next().unwrap_or("").trim();
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
                        if !target.is_empty()
                            && !target.contains('\u{0}')
                            && needle
                                .as_ref()
                                .is_none_or(|n| target.to_lowercase().contains(n.as_str()))
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

/// Where the python entry and its interpreter are, for BOTH hand-off
/// launchers (this file's `exec_python` and write.rs's post-stdin `patch`
/// launcher). Prints the `engine:` refusal and the skew warning itself;
/// `None` means the caller exits 1.
///
/// Resolution order: VV_PY_ENTRY, then src/vv.py BESIDE the executable (the
/// archive layout: vv + src/ + VERSION in one directory), then the repo
/// layout — first that exists. v1.0 knew only the repo layout, and a
/// standalone binary lost most of its surface (roadmap review, 4/4 seats).
pub fn python_launch() -> Option<(String, String)> {
    let exe = env::current_exe()
        .ok()
        .and_then(|p| std::fs::canonicalize(p).ok());
    let vv = std::env::var("VV_PY_ENTRY").ok().or_else(|| {
        let candidates = [
            exe.as_ref()
                .and_then(|p| p.parent())
                .map(|d| d.join("src/vv.py")), // archive: sibling src/
            exe.as_ref()
                .and_then(|p| p.ancestors().nth(4).map(|a| a.to_path_buf()))
                .map(|r| r.join("src/vv.py")), // repo: exe -> release -> target -> vrust -> REPO
        ];
        candidates
            .into_iter()
            .flatten()
            .find(|c| c.exists())
            .map(|c| c.to_string_lossy().into_owned())
    });
    let vv = match vv {
        Some(v) => v,
        None => {
            eprintln!("engine: python entry (src/vv.py) not found beside the binary or in a checkout — next: set VV_PY_ENTRY, or reinstall the archive intact");
            return None;
        }
    };
    // Engine-skew handshake: the VERSION beside the resolved entry must match
    // the binary's baked version. One warning, not a wall — the python side is
    // the semantic authority either way, but silent skew is how two-engine
    // deployments rot.
    if let Some(vdir) = Path::new(&vv).parent().and_then(|s| s.parent()) {
        if let Ok(v) = std::fs::read_to_string(vdir.join("VERSION")) {
            if v.trim() != VERSION.trim() {
                eprintln!(
                    "warning: engine skew — binary {} but python entry is {} (from {})",
                    VERSION.trim(),
                    v.trim(),
                    vdir.join("VERSION").display()
                );
            }
        }
    }
    let python = std::env::var("VV_PYTHON").unwrap_or_else(|_| "python3".into());
    Some((python, vv))
}

/// The line both launchers print instead of handing a call back to python.
pub fn refuse_hand_back() {
    eprintln!(
        "engine: the native entry cannot answer this call for the python engine that started it"
    );
}

/// The line both launchers print when the interpreter cannot be started.
pub fn python_unavailable(python: &str) {
    eprintln!(
        "engine: python engine unavailable ({} not runnable) — next: install python3, or set VV_PYTHON",
        python
    );
}

/// Fall through to the python implementation — the semantic authority for
/// every command the native path doesn't (or declines to) handle. Generic
/// over the argument type so an argv that is not valid UTF-8 is handed over
/// byte for byte.
///
/// Never back to a python that called us: when python started this process
/// (`readpath::from_py`) it is the orchestrator and is waiting on us, so
/// handing off would start a second python that calls a second binary,
/// without end. Whatever the reason for declining, the answer is a refusal.
fn exec_python<S: AsRef<std::ffi::OsStr>>(argv: &[S]) -> ! {
    if readpath::from_py() {
        refuse_hand_back();
        exit(1);
    }
    let Some((python, vv)) = python_launch() else {
        exit(1);
    };
    // when this process started: python times its row from here, so a
    // fallback's `ms` is the whole invocation, not python's share of it
    match std::process::Command::new(&python)
        .arg(&vv)
        .args(argv)
        .env("VV_NATIVE_T0_NS", readpath::start_ns())
        .status()
    {
        Ok(s) => exit(s.code().unwrap_or(1)),
        Err(_) => {
            python_unavailable(&python);
            exit(1);
        }
    }
}

// Baked at build; the gate pins VERSION == Cargo.toml so the two cannot skew.
const VERSION: &str = include_str!("../../VERSION");

fn main() {
    let _ = readpath::START_NS.set(
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0),
    );
    let _ = readpath::START.set(std::time::Instant::now());
    // An argument that is not valid UTF-8 cannot be a `String`; python reads
    // it with surrogate escapes, so it is python's to answer, byte for byte.
    let raw: Vec<std::ffi::OsString> = env::args_os().skip(1).collect();
    let mut args: Vec<String> = Vec::with_capacity(raw.len());
    for a in &raw {
        match a.to_str() {
            Some(s) => args.push(s.to_string()),
            None => exec_python(&raw),
        }
    }
    if matches!(
        args.first().map(String::as_str),
        Some("--version") | Some("version")
    ) {
        println!("vv {}", VERSION.trim());
        exit(0);
    }
    let orig: Vec<String> = args.clone();
    // --jsonl is python's surface entirely (one schema author); hand off
    // before any native handler can answer in TSV.
    if args.iter().any(|a| a == "--jsonl") {
        exec_python(&orig);
    }
    // --limit N before dispatch, mirroring vv.py (orig keeps it: a python
    // fallback re-parses the full argv itself)
    if let Some(i) = args.iter().position(|a| a == "--limit") {
        let ok = args
            .get(i + 1)
            .and_then(|v| v.parse::<usize>().ok())
            .filter(|n| *n >= 1);
        match ok {
            Some(n) => {
                let _ = readpath::LIMIT.set(n);
                let _ = readpath::GLOBAL_FLAG.set(());
                args.drain(i..=i + 1);
            }
            None => {
                eprintln!("usage: --limit requires a positive integer");
                exit(1);
            }
        }
    }
    // --vault PATH before dispatch, mirroring vv.py
    if let Some(i) = args.iter().position(|a| a == "--vault") {
        let _ = readpath::GLOBAL_FLAG.set(());
        if i + 1 < args.len() {
            let mut v = args[i + 1].clone();
            if let Some(rest) = v.strip_prefix("~/") {
                // expanduser, like python
                if let Ok(h) = env::var("HOME") {
                    v = format!("{}/{}", h, rest);
                }
            }
            std::env::set_var("VV_VAULT", &v);
            args.drain(i..=i + 1);
        }
    }
    // `<cmd> --help` is python's surface for every command (it prints that
    // command's COMMAND_TABLE line), and the arity rule alone does not reach
    // it: a native arm that answers at THIS arity answers the help request
    // instead. `search --help` scored the literal string `--help` against the
    // corpus and printed hits; `tags --help` printed the tag table; `props
    // --help` counted notes carrying a property named `--help`. Hand off
    // before a handler is chosen, so the rule holds for every command the
    // native entry grows later.
    if args.get(1).map(String::as_str) == Some("--help")
        || args.get(1).map(String::as_str) == Some("-h")
    {
        exec_python(&orig);
    }
    if let Some(cmd) = args.first().map(String::as_str) {
        let handler: Option<fn(&str, &[String], &std::path::Path) -> readpath::Outcome> = match cmd
        {
            "outline" | "read" | "head" | "resolve" => Some(readpath::run),
            // links reads ONE note (7.9 ms native); backlinks/orphans/deadends scan the
            // corpus natively (~148 ms) while python answers from its SQLite index
            // (48-84 ms) — those route to python until graph.rs grows the vvidx cache
            // (measured 2026-08-27, E5). impact was never ported.
            "links" | "backlinks" | "orphans" | "deadends" => Some(graph::run), // vvidx cache landed (phase 2)
            "set" | "unset" | "append" | "appendsec" | "new" | "daily-append" | "patch" => {
                Some(write::run)
            }
            "board" | "tags" | "props" | "show" => Some(query::run),
            _ => None,
        };
        if let Some(h) = handler {
            match h(cmd, &args[1..], &vault()) {
                readpath::Outcome::Done(c) => exit(c),
                readpath::Outcome::Fallback => exec_python(&orig),
            }
        }
    }
    match args.first().map(String::as_str) {
        Some("search") => cmd_search(&args[1..], &orig),
        Some("linkscan") => cmd_linkscan(&args[1..]),
        Some(_) => exec_python(&orig), // every other vv command is python's
        None => {
            // Byte-identical to python's bare-invocation line (pinned by test).
            eprintln!("usage: vv COMMAND [ARGS] — next: vv --help for the command list");
            exit(1);
        }
    }
}
