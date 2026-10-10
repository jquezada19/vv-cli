// write.rs — full-rewrite module (agent B; see docs/rust-rewrite-plan.md).
// Contract: happy path only; ANY doubt returns Outcome::Fallback and main()
// execs the Python implementation. Byte parity is pinned by differential
// tests. Ported line-for-line from src/vv_impl.py: cmd_set, cmd_unset,
// cmd_append, cmd_appendsec, yaml_scalar (+ _YAML_LEAD, _is_wellformed_quoted,
// _is_balanced_flow), split_fm_full, block_scalar_key, file_sig, atomic_write,
// read_raw, splice, and the _dirty_gate journal check.
//
// new / daily-append / patch are NOT implemented here (left as Fallback) —
// per the task brief they are optional and only worth doing once set/unset/
// append/appendsec are solid; they are not.
#![allow(dead_code)]
use crate::readpath::{self, Outcome};
use std::fs;
use std::io::Write;
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};

const BOM: char = '\u{feff}';

// ---------- _dirty_gate (pending-journal CAS) ----------

fn journal_root() -> PathBuf {
    if let Some(v) = std::env::var_os("VV_JOURNAL_ROOT") {
        return PathBuf::from(v);
    }
    let home = std::env::var("HOME").unwrap_or_default();
    Path::new(&home).join(".cache/vv/journals")
}

fn vault_journal_root(vault: &Path) -> PathBuf {
    let real = fs::canonicalize(vault).unwrap_or_else(|_| vault.to_path_buf());
    let vid = &readpath::sha256_hex(real.to_string_lossy().as_bytes())[..12];
    journal_root().join(vid)
}

/// True if a pending journal exists for this vault — no write command may run
/// (sqlx's Dirty-version gate, adapted). We never emit the exit-4 message
/// ourselves; Fallback lets Python re-check and print its canonical text.
fn has_pending_journal(vault: &Path) -> bool {
    let root = vault_journal_root(vault);
    match fs::read_dir(&root) {
        Ok(mut it) => it.next().is_some(),
        Err(_) => false,
    }
}

// ---------- file_sig / atomic_write / read_raw ----------

type Sig = (i64, u64);

fn file_sig(fp: &Path) -> Option<Sig> {
    let md = fs::metadata(fp).ok()?;
    let ns = (md.mtime() as i64) * 1_000_000_000 + md.mtime_nsec() as i64;
    Some((ns, md.len()))
}

fn read_raw(fp: &Path) -> Option<String> {
    let bytes = fs::read(fp).ok()?;
    String::from_utf8(bytes).ok() // strict UTF-8; anything else -> caller Fallback (python exit 5)
}

fn elem_cr(lines: &[&str], i: usize) -> bool {
    // the final element is unterminated by construction, so it never counts
    i + 1 < lines.len() && lines[i].ends_with('\r')
}

fn insert_cr(lines: &[&str], at: usize) -> bool {
    // nearest terminated line above the insertion point, else below, else LF
    let last_term = lines.len().saturating_sub(1);
    let above = at.min(last_term);
    if above > 0 {
        return lines[above - 1].ends_with('\r');
    }
    if at < last_term {
        return lines[at].ends_with('\r');
    }
    false
}

fn last_eol(text: &str) -> &'static str {
    match text.rfind('\n') {
        Some(i) if i > 0 && text.as_bytes()[i - 1] == b'\r' => "\r\n",
        _ => "\n",
    }
}

struct FmParts {
    bom: String,
    eol1: String,
    fm_lines: Vec<String>,
    eol2: String,
    tail: String,
    body: String,
}

fn fm_parts(text: &str) -> Option<FmParts> {
    let (fm, body, tail, bom) = split_fm_full(text);
    let fm = fm?;
    let head = &text[..text.len() - body.len()];
    let after_open = &head[bom.len() + 3..];
    let eol1 = if after_open.starts_with("\r\n") {
        "\r\n"
    } else {
        "\n"
    };
    let rest = &after_open[eol1.len() + fm.len()..];
    let eol2 = &rest[..rest.len() - 3 - tail.len()];
    Some(FmParts {
        bom,
        eol1: eol1.to_string(),
        fm_lines: fm.split('\n').map(|s| s.to_string()).collect(),
        eol2: eol2.to_string(),
        tail,
        body,
    })
}

fn fm_join(p: &FmParts) -> String {
    format!(
        "{}---{}{}{}---{}{}",
        p.bom,
        p.eol1,
        p.fm_lines.join("\n"),
        p.eol2,
        p.tail,
        p.body
    )
}

/// Atomic write with optional CAS on the pre-read signature. Returns false on
/// ANY doubt (CAS mismatch, io error) — caller must Fallback, never emit text.
fn atomic_write(fp: &Path, content: &str, expect_sig: Option<Sig>) -> bool {
    let target: PathBuf = match fs::symlink_metadata(fp) {
        Ok(m) if m.file_type().is_symlink() => match fs::canonicalize(fp) {
            Ok(p) => p,
            Err(_) => return false,
        },
        Ok(_) => fp.to_path_buf(),
        Err(_) => return false,
    };
    if let Some(sig) = expect_sig {
        if file_sig(&target) != Some(sig) {
            return false;
        }
    }
    let dir = target.parent().unwrap_or_else(|| Path::new("."));
    let pid = std::process::id();
    let mut tmp;
    let mut attempt: u32 = 0;
    let mut f = loop {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.subsec_nanos())
            .unwrap_or(0);
        tmp = dir.join(format!(".vv-{}-{}-{}.tmp", pid, nanos, attempt));
        match fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&tmp)
        {
            Ok(f) => break f,
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {
                attempt += 1;
                if attempt > 1000 {
                    return false;
                }
                continue;
            }
            Err(_) => return false,
        }
    };
    if f.write_all(content.as_bytes()).is_err() {
        drop(f);
        let _ = fs::remove_file(&tmp);
        return false;
    }
    if f.flush().is_err() {
        drop(f);
        let _ = fs::remove_file(&tmp);
        return false;
    }
    drop(f);
    if fs::rename(&tmp, &target).is_err() {
        let _ = fs::remove_file(&tmp);
        return false;
    }
    true
}

// ---------- split_fm_full (fm, body, tail, bom) ----------

fn split_fm_full(text: &str) -> (Option<String>, String, String, String) {
    let bom = if text.starts_with(BOM) {
        BOM.to_string()
    } else {
        String::new()
    };
    let t = &text[bom.len()..];
    let rest = if let Some(r) = t.strip_prefix("---\r\n") {
        r
    } else if let Some(r) = t.strip_prefix("---\n") {
        r
    } else {
        return (None, text.to_string(), String::new(), bom);
    };
    let bytes = rest.as_bytes();
    let mut i = 0usize;
    while i < bytes.len() {
        if bytes[i] == b'\n' {
            let mut fm_end = i;
            if fm_end > 0 && bytes[fm_end - 1] == b'\r' {
                fm_end -= 1;
            }
            let after = &rest[i + 1..];
            if let Some(post) = after.strip_prefix("---") {
                let (tail, body) = if let Some(b) = post.strip_prefix("\r\n") {
                    ("\r\n", b)
                } else if let Some(b) = post.strip_prefix('\n') {
                    ("\n", b)
                } else {
                    ("", post)
                };
                return (
                    Some(rest[..fm_end].to_string()),
                    body.to_string(),
                    tail.to_string(),
                    bom,
                );
            }
        }
        i += 1;
    }
    (None, text.to_string(), String::new(), bom)
}

// ---------- block_scalar_key ----------

fn block_scalar_key(fm_lines: &[String], key: &str) -> bool {
    for (i, l) in fm_lines.iter().enumerate() {
        if let Some(rest) = l.strip_prefix(key) {
            if let Some(rest2) = rest.strip_prefix(':') {
                let val = rest2.trim();
                let is_block_form = matches!(val, ">" | "|" | ">-" | "|-" | ">+" | "|+" | "");
                if is_block_form {
                    let nxt = fm_lines.get(i + 1).map(|s| s.as_str()).unwrap_or("");
                    let nxt_first = nxt.chars().next();
                    let indented = matches!(nxt_first, Some(' ') | Some('\t'));
                    let block_marker =
                        !val.is_empty() && (val.starts_with('|') || val.starts_with('>'));
                    if indented || block_marker {
                        return true;
                    }
                }
                return false;
            }
        }
    }
    false
}

// ---------- yaml_scalar ----------

const YAML_LEAD: &str = "[]{}#&*!|>'\"%@`,";

fn is_wellformed_quoted(v: &str) -> bool {
    let chars: Vec<char> = v.chars().collect();
    if chars.len() < 2 {
        return false;
    }
    let first = chars[0];
    let last = chars[chars.len() - 1];
    if first != last || (first != '"' && first != '\'') {
        return false;
    }
    let inner = &chars[1..chars.len() - 1];
    if first == '\'' {
        return inner.iter().filter(|&&c| c == '\'').count() % 2 == 0;
    }
    let mut i = 0usize;
    while i < inner.len() {
        if inner[i] == '\\' {
            i += 2;
            continue;
        }
        if inner[i] == '"' {
            return false;
        }
        i += 1;
    }
    true
}

fn is_balanced_flow(v: &str) -> bool {
    let first = match v.chars().next() {
        Some(c) => c,
        None => return false,
    };
    if first != '[' && first != '{' {
        return false;
    }
    let mut stack: Vec<char> = Vec::new();
    for ch in v.chars() {
        match ch {
            '[' | '{' => stack.push(ch),
            ']' => {
                if stack.pop() != Some('[') {
                    return false;
                }
            }
            '}' if stack.pop() != Some('{') => {
                return false;
            }
            _ => {}
        }
    }
    stack.is_empty()
}

fn indicator_before_space(v: &str) -> bool {
    let mut it = v.chars();
    match it.next() {
        Some(c) if c == '-' || c == '?' || c == ':' => match it.next() {
            None => true,
            Some(c2) => c2.is_whitespace(),
        },
        _ => false,
    }
}

fn yaml_scalar(v: &str) -> String {
    if v.is_empty() {
        return "\"\"".to_string();
    }
    if is_wellformed_quoted(v) || is_balanced_flow(v) {
        return v.to_string();
    }
    let first = v.chars().next().unwrap();
    let needs = v.contains(": ")
        || v.ends_with(':')
        || v.contains(" #")
        || YAML_LEAD.contains(first)
        || v != v.trim()
        || indicator_before_space(v)
        || v.contains('\n')
        || v.contains('\r')
        || v.contains('\t')
        || v.chars().any(|c| (c as u32) < 0x20);
    if !needs {
        return v.to_string();
    }
    let mut esc = String::with_capacity(v.len() + 2);
    for c in v.chars() {
        match c {
            '\\' => esc.push_str("\\\\"),
            '"' => esc.push_str("\\\""),
            '\n' => esc.push_str("\\n"),
            '\r' => esc.push_str("\\r"),
            '\t' => esc.push_str("\\t"),
            _ => esc.push(c),
        }
    }
    format!("\"{}\"", esc)
}

// ---------- splice (used by appendsec; patch would reuse it too) ----------

fn splice(lines: &[&str], start: usize, end: usize, new_lines: &[String]) -> String {
    let ended_with_newline = !lines.is_empty() && lines.last().unwrap().is_empty();
    let end_c = end.min(lines.len());
    let old: Vec<&str> = lines[start..end_c].to_vec();
    let mut body: Vec<String> = new_lines
        .iter()
        .map(|b| b.trim_end_matches('\r').to_string())
        .collect();
    if end >= lines.len() {
        if ended_with_newline {
            if body.is_empty() || body.last().map(|s| s.as_str()) != Some("") {
                body.push(String::new());
            }
        } else if body.last().map(|s| s.as_str()) == Some("") {
            body.pop();
        }
    }
    let old_c: Vec<&str> = old.iter().map(|o| o.trim_end_matches('\r')).collect();
    let mut p = 0;
    while p < old_c.len() && p < body.len() && old_c[p] == body[p] {
        p += 1;
    }
    let mut s = 0;
    while s < old_c.len() - p
        && s < body.len() - p
        && old_c[old_c.len() - 1 - s] == body[body.len() - 1 - s]
    {
        s += 1;
    }
    let first = start + p;
    let cr = if p < old_c.len() - s && first + 1 < lines.len() {
        elem_cr(lines, first)
    } else {
        insert_cr(lines, first)
    };
    let middle: Vec<String> = body[p..body.len() - s]
        .iter()
        .map(|m| if cr { format!("{}\r", m) } else { m.clone() })
        .collect();
    let mut merged: Vec<String> = Vec::with_capacity(lines.len() + body.len());
    merged.extend(lines[..start].iter().map(|x| x.to_string()));
    merged.extend(old[..p].iter().map(|x| x.to_string()));
    let middle_empty = middle.is_empty();
    merged.extend(middle);
    let tail_empty = s == 0;
    merged.extend(old[old.len() - s..].iter().map(|x| x.to_string()));
    merged.extend(lines[end_c..].iter().map(|x| x.to_string()));
    if !middle_empty
        && first == lines.len()
        && !lines.is_empty()
        && !lines[lines.len() - 1].is_empty()
        && !merged[first - 1].ends_with('\r')
        && insert_cr(lines, lines.len() - 1)
    {
        merged[first - 1].push('\r'); // the old unterminated final line is no longer last
    }
    if cr && !middle_empty && tail_empty && end >= lines.len() {
        if let Some(last) = merged.last_mut() {
            if last.ends_with('\r') {
                last.pop(); // the final element is unterminated; never invent a \r there
            }
        }
    }
    merged.join("\n")
}

// ---------- helpers shared by the commands ----------

fn rel_of<'a>(vault: &Path, fp: &'a Path) -> std::borrow::Cow<'a, str> {
    match fp.strip_prefix(vault) {
        Ok(r) => r.to_string_lossy(),
        Err(_) => fp.to_string_lossy(),
    }
}

// ---------- commands ----------

fn cmd_set(vault: &Path, args: &[String]) -> Outcome {
    if args.len() != 3 {
        return Outcome::Fallback;
    }
    if has_pending_journal(vault) {
        return Outcome::Fallback;
    }
    let t0 = std::time::Instant::now();
    let (ref_, key, raw_value) = (&args[0], &args[1], &args[2]);
    let value = yaml_scalar(raw_value);
    let fp = match readpath::resolve(vault, ref_) {
        Some(f) => f,
        None => return Outcome::Fallback,
    };
    let cf = fs::metadata(&fp).map(|m| m.len()).unwrap_or(0);
    let sig = match file_sig(&fp) {
        Some(s) => s,
        None => return Outcome::Fallback,
    };
    let text = match read_raw(&fp) {
        Some(t) => t,
        None => return Outcome::Fallback,
    };
    let new_content = match fm_parts(&text) {
        None => {
            let (_, _, _, bom) = split_fm_full(&text);
            let rest = &text[bom.len()..];
            let first = rest.split('\n').next().unwrap_or("");
            let eol = if first.ends_with('\r') && rest.contains('\n') {
                "\r\n"
            } else {
                "\n"
            };
            format!(
                "{}---{}{}: {}{}---{}{}",
                bom, eol, key, value, eol, eol, rest
            )
        }
        Some(mut p) => {
            let stripped: Vec<String> = p
                .fm_lines
                .iter()
                .map(|l| l.trim_end_matches('\r').to_string())
                .collect();
            if block_scalar_key(&stripped, key) {
                return Outcome::Fallback; // python emits the canonical "refused" text
            }
            let prefix = format!("{}:", key);
            match p.fm_lines.iter().position(|l| l.starts_with(&prefix)) {
                Some(i) => {
                    let cr = if p.fm_lines[i].ends_with('\r') {
                        "\r"
                    } else {
                        ""
                    };
                    p.fm_lines[i] = format!("{}: {}{}", key, value, cr);
                }
                None => {
                    if p.eol2 == "\r\n" {
                        p.fm_lines.last_mut().unwrap().push('\r');
                    }
                    p.fm_lines.push(format!("{}: {}", key, value));
                }
            }
            fm_join(&p)
        }
    };
    if !atomic_write(&fp, &new_content, Some(sig)) {
        return Outcome::Fallback;
    }
    let out = format!("set {}={} in {}\n", key, value, rel_of(vault, &fp));
    let n = readpath::emit(&out);
    readpath::log_metrics("set", t0, n, cf, None);
    Outcome::Done(0)
}

fn cmd_unset(vault: &Path, args: &[String]) -> Outcome {
    if args.len() != 2 {
        return Outcome::Fallback;
    }
    if has_pending_journal(vault) {
        return Outcome::Fallback;
    }
    let t0 = std::time::Instant::now();
    let (ref_, key) = (&args[0], &args[1]);
    let fp = match readpath::resolve(vault, ref_) {
        Some(f) => f,
        None => return Outcome::Fallback,
    };
    let cf = fs::metadata(&fp).map(|m| m.len()).unwrap_or(0);
    let sig = match file_sig(&fp) {
        Some(s) => s,
        None => return Outcome::Fallback,
    };
    let text = match read_raw(&fp) {
        Some(t) => t,
        None => return Outcome::Fallback,
    };
    let mut p = match fm_parts(&text) {
        Some(p) => p,
        None => return Outcome::Fallback, // python: die "no frontmatter"
    };
    let stripped: Vec<String> = p
        .fm_lines
        .iter()
        .map(|l| l.trim_end_matches('\r').to_string())
        .collect();
    if block_scalar_key(&stripped, key) {
        return Outcome::Fallback;
    }
    let prefix = format!("{}:", key);
    let kept: Vec<String> = p
        .fm_lines
        .iter()
        .filter(|l| !l.starts_with(&prefix))
        .cloned()
        .collect();
    if kept.len() == p.fm_lines.len() {
        return Outcome::Fallback; // python: die "no key"
    }
    p.fm_lines = kept;
    if let Some(last) = p.fm_lines.last_mut() {
        if last.ends_with('\r') {
            last.pop(); // followed by eol2, not by its own terminator
        }
    }
    let new_content = fm_join(&p);
    if !atomic_write(&fp, &new_content, Some(sig)) {
        return Outcome::Fallback;
    }
    let out = format!("unset {} in {}\n", key, rel_of(vault, &fp));
    let n = readpath::emit(&out);
    readpath::log_metrics("unset", t0, n, cf, None);
    Outcome::Done(0)
}

fn cmd_append(vault: &Path, args: &[String]) -> Outcome {
    if args.len() != 2 {
        return Outcome::Fallback;
    }
    if has_pending_journal(vault) {
        return Outcome::Fallback;
    }
    let t0 = std::time::Instant::now();
    let (ref_, text_arg) = (&args[0], &args[1]);
    let fp = match readpath::resolve(vault, ref_) {
        Some(f) => f,
        None => return Outcome::Fallback,
    };
    let cf = fs::metadata(&fp).map(|m| m.len()).unwrap_or(0);
    let sig = match file_sig(&fp) {
        Some(s) => s,
        None => return Outcome::Fallback,
    };
    let cur = match read_raw(&fp) {
        Some(t) => t,
        None => return Outcome::Fallback,
    };
    let eol = last_eol(&cur);
    let sep = if cur.ends_with('\n') || cur.is_empty() {
        ""
    } else {
        eol
    };
    let new_content = format!("{}{}{}{}", cur, sep, text_arg, eol);
    if !atomic_write(&fp, &new_content, Some(sig)) {
        return Outcome::Fallback;
    }
    let out = format!("appended to {}\n", rel_of(vault, &fp));
    let n = readpath::emit(&out);
    readpath::log_metrics("append", t0, n, cf, None);
    Outcome::Done(0)
}

fn cmd_appendsec(vault: &Path, args: &[String]) -> Outcome {
    if args.len() != 3 {
        return Outcome::Fallback;
    }
    if has_pending_journal(vault) {
        return Outcome::Fallback;
    }
    let t0 = std::time::Instant::now();
    let (ref_, sid, text_arg) = (&args[0], &args[1], &args[2]);
    let fp = match readpath::resolve(vault, ref_) {
        Some(f) => f,
        None => return Outcome::Fallback,
    };
    let cf = fs::metadata(&fp).map(|m| m.len()).unwrap_or(0);
    let sig = match file_sig(&fp) {
        Some(s) => s,
        None => return Outcome::Fallback,
    };
    let text = match read_raw(&fp) {
        Some(t) => t,
        None => return Outcome::Fallback,
    };
    let (lines, secs) = readpath::parse(&text);
    let (s, kind) = match readpath::find_sec(&lines, &secs, sid) {
        Ok(v) => v,
        Err(_) => return Outcome::Fallback,
    };
    let mut ins = s.end;
    while ins > s.start && lines[ins - 1].trim().is_empty() {
        ins -= 1;
    }
    let new_content = splice(&lines, ins, ins, std::slice::from_ref(text_arg));
    if !atomic_write(&fp, &new_content, Some(sig)) {
        return Outcome::Fallback;
    }
    let out = format!("appended to {} in {}\n", sid, rel_of(vault, &fp));
    let n = readpath::emit(&out);
    readpath::log_metrics("appendsec", t0, n, cf, Some(kind));
    Outcome::Done(0)
}

// ---------- patch (phase 2) ----------
// STDIN ORDERING IS THE WHOLE TRICK (mirrors python exactly): every check that
// can Fallback runs BEFORE stdin is consumed — python re-reads stdin itself on
// fallback and nothing is lost. Only after the sha8 check passes is stdin
// read; from that point on, any failure re-feeds the captured bytes to python
// via a piped child instead of the bare exec (which would hand python an
// empty pipe and silently patch an empty body).
fn exec_python_with_stdin(vault: &Path, args: &[String], body: &[u8]) -> Outcome {
    use std::process::{Command, Stdio};
    let vv = std::env::var("VV_PY_ENTRY").unwrap_or_else(|_| {
        std::env::current_exe()
            .ok()
            .and_then(|p| std::fs::canonicalize(p).ok())
            .and_then(|p| p.ancestors().nth(4).map(|a| a.to_path_buf()))
            .map(|r| r.join("src/vv.py").to_string_lossy().into_owned())
            .unwrap_or_else(|| "vv.py".into())
    });
    let mut argv: Vec<String> = vec!["patch".into()];
    argv.extend(args.iter().cloned());
    let child = Command::new("python3")
        .arg(&vv)
        .arg("--vault")
        .arg(vault)
        .args(&argv)
        .stdin(Stdio::piped())
        .spawn();
    let mut child = match child {
        Ok(c) => c,
        Err(_) => return Outcome::Done(1),
    };
    if let Some(mut si) = child.stdin.take() {
        let _ = si.write_all(body);
    }
    match child.wait() {
        Ok(s) => Outcome::Done(s.code().unwrap_or(1)),
        Err(_) => Outcome::Done(1),
    }
}

fn cmd_patch(vault: &Path, args: &[String]) -> Outcome {
    let t0 = std::time::Instant::now();
    if args.len() != 3 {
        return Outcome::Fallback;
    }
    if has_pending_journal(vault) {
        return Outcome::Fallback; // python emits the canonical exit-4 text
    }
    let (ref_, sid, expect) = (&args[0], &args[1], &args[2]);
    let fp = match crate::readpath::resolve(vault, ref_) {
        Some(f) => f,
        None => return Outcome::Fallback,
    };
    let cf = fs::metadata(&fp).map(|m| m.len()).unwrap_or(0);
    let sig = match file_sig(&fp) {
        Some(s) => s,
        None => return Outcome::Fallback,
    };
    let bytes = match fs::read(&fp) {
        Ok(b) => b,
        Err(_) => return Outcome::Fallback,
    };
    let text = match String::from_utf8(bytes) {
        Ok(t) => t,
        Err(_) => return Outcome::Fallback,
    };
    let (lines, secs) = crate::readpath::parse(&text);
    let (s, kind) = match crate::readpath::find_sec(&lines, &secs, sid) {
        Ok(v) => v,
        Err(_) => return Outcome::Fallback,
    };
    if s.id == "H0" && s.end > 0 && !lines.is_empty() && lines[0].trim_end_matches('\r') == "---" {
        return Outcome::Fallback; // python's "refused: H0 contains frontmatter" text
    }
    let cur = crate::readpath::sec_text(&lines, s);
    if crate::readpath::sha8(&cur) != *expect {
        return Outcome::Fallback; // stale: python re-checks and emits exit 3 — stdin UNTOUCHED so far
    }
    // ---- point of no return: consume stdin ----
    let mut raw = Vec::new();
    if std::io::Read::read_to_end(&mut std::io::stdin(), &mut raw).is_err() {
        return exec_python_with_stdin(vault, args, &raw);
    }
    let body_str = match String::from_utf8(raw.clone()) {
        Ok(b) => b,
        Err(_) => return exec_python_with_stdin(vault, args, &raw),
    };
    let mut body = body_str.replace("\r\n", "\n");
    if body.ends_with('\n') {
        body.pop();
    }
    let body_lines: Vec<String> = if body.is_empty() && s.end == s.start {
        Vec::new()
    } else {
        body.split('\n').map(|x| x.to_string()).collect()
    };
    let new_text = splice(&lines, s.start, s.end, &body_lines);
    if !atomic_write(&fp, &new_text, Some(sig)) {
        return exec_python_with_stdin(vault, args, &raw);
    }
    let rel = fp
        .strip_prefix(vault)
        .unwrap_or(&fp)
        .to_string_lossy()
        .into_owned();
    let n = crate::readpath::emit(&format!(
        "patched {} in {} ({}B -> {}B)\n",
        s.id,
        rel,
        cur.len(), // UTF-8 bytes, matching python's encode() count
        body.len()
    ));
    crate::readpath::log_metrics("patch", t0, n, cf, Some(kind));
    Outcome::Done(0)
}

pub fn run(cmd: &str, args: &[String], vault: &Path) -> Outcome {
    match cmd {
        "set" => cmd_set(vault, args),
        "unset" => cmd_unset(vault, args),
        "append" => cmd_append(vault, args),
        "appendsec" => cmd_appendsec(vault, args),
        "patch" => cmd_patch(vault, args),
        // new / daily-append: not implemented — Fallback to python.
        _ => Outcome::Fallback,
    }
}
