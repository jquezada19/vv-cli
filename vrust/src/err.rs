// Refusals the native entry words itself.
//
// The grammar is python's `die` (src/vv_impl.py): one line on stderr,
// `kind: message — next: <command>`, with every control character in the
// message and the next step escaped, and any literal ` — next: ` inside them
// rewritten so the separator this function appends is the only one. The
// metrics row carries the exit code and the kind.
use crate::readpath::{log_error, Outcome};

const SEP: &str = " — next: ";

/// python's `_CTL`: C0 and C1 controls, DEL, and the two Unicode line
/// separators. (Its lone-surrogate range cannot occur in a Rust `str`.)
fn is_ctl(c: char) -> bool {
    matches!(c, '\u{0}'..='\u{1f}' | '\u{7f}'..='\u{9f}' | '\u{2028}' | '\u{2029}')
}

/// python's `_esc`.
fn esc(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        match c {
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if is_ctl(c) && (c as u32) < 256 => out.push_str(&format!("\\x{:02x}", c as u32)),
            c if is_ctl(c) => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out
}

/// python's `_neutralise`: to convergence, because the separator overlaps
/// itself and one replacement can complete another.
fn neutralise(mut s: String) -> String {
    while s.contains(SEP) {
        s = s.replace(SEP, " —next: ");
    }
    s
}

/// python's `_q`: a token for a RUNNABLE next step. Shell-quoted as
/// `shlex.quote` does it, or the placeholder when the token carries a control
/// character or the separator, since such a next step cannot be copied.
pub fn q(s: &str, placeholder: &str) -> String {
    if s.chars().any(is_ctl) || s.contains(SEP) {
        return placeholder.to_string();
    }
    let safe = |b: u8| b.is_ascii_alphanumeric() || b"_@%+=:,./-".contains(&b);
    if !s.is_empty() && s.bytes().all(safe) {
        return s.to_string();
    }
    format!("'{}'", s.replace('\'', "'\"'\"'"))
}

/// Print the refusal, log its row, and return the exit code to leave with.
pub fn die(op: &str, msg: &str, next: Option<&str>, code: i32) -> Outcome {
    let msg = neutralise(esc(msg));
    let wire = match next {
        Some(n) => format!("{}{}{}\n", msg, SEP, neutralise(esc(n))),
        None => format!("{}\n", msg),
    };
    eprint!("{}", wire);
    // the kind is the message's first word when it ends in a colon
    let first = msg
        .split('\n')
        .next()
        .unwrap_or("")
        .split(' ')
        .next()
        .unwrap_or("");
    log_error(op, first.strip_suffix(':'), code, wire.len());
    Outcome::Done(code)
}
