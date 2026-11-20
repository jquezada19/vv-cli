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

// ---------- arity (python's `_check_arity` and its next-step builders) ----------

/// python's `str.isspace` / regex `\s`: Unicode whitespace plus U+001C..U+001F.
pub fn py_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// python's `_is_flag`: a dash-led token of two or more characters with no
/// whitespace in it (a markdown bullet `- text` is text, not a flag).
fn is_flag(tok: &str) -> bool {
    tok.starts_with('-') && tok.chars().count() > 1 && !tok.chars().any(py_space)
}

/// python's `_fm_key_ok`: a bare frontmatter key.
fn fm_key_ok(k: &str) -> bool {
    let bad_anywhere = |c: char| c == '=' || c == ':' || py_space(c) || is_ctl(c);
    !k.is_empty()
        && !k.chars().any(bad_anywhere)
        && !k.starts_with(|c: char| "-?,[]{}#&*!|>'\"%@`".contains(c))
}

/// How many operands a command takes, and the REQUIRED operand placeholders
/// from its COMMAND_TABLE `args` (tokens up to the first optional group).
/// `hi == None` means the tail is open. Commands absent here are not checked.
fn spec(cmd: &str) -> Option<(usize, Option<usize>, &'static [&'static str])> {
    Some(match cmd {
        "outline" | "head" | "links" | "backlinks" => (1, Some(1), &["NOTE"]),
        "resolve" => (1, Some(1), &["NAME"]),
        "read" => (1, Some(3), &["NOTE"]),
        "show" => (1, None, &["NOTE"]),
        "patch" => (3, Some(3), &["NOTE", "SEC", "SHA8"]),
        "append" => (2, Some(3), &["NOTE", "TEXT"]),
        "appendsec" => (3, Some(3), &["NOTE", "SEC", "TEXT"]),
        "set" => (3, Some(3), &["NOTE", "KEY", "VALUE"]),
        "unset" => (2, Some(2), &["NOTE", "KEY"]),
        "orphans" => (0, Some(1), &[]),
        "deadends" => (0, Some(0), &[]),
        "board" => (1, None, &["FOLDER"]),
        "props" => (1, Some(2), &["KEY"]),
        _ => return None,
    })
}

/// python's `_surplus_slot`: more operands than slots, no flag among them,
/// and a free-text last slot that the surplus should be joined into.
fn surplus_slot(ops: &[&str], args: &[String]) -> bool {
    !ops.is_empty()
        && args.len() > ops.len()
        && !args.iter().any(|a| is_flag(a))
        && matches!(ops[ops.len() - 1], "TEXT" | "VALUE")
}

/// python's `_next_from_table`: the caller's own operands, quoted, in the
/// slots they filled; placeholders for the rest.
fn next_from_table(cmd: &str, ops: &[&str], args: &[String]) -> String {
    if ops.is_empty() {
        return format!("vv {}", cmd);
    }
    let mut parts = vec!["vv".to_string(), cmd.to_string()];
    if surplus_slot(ops, args) {
        let last = ops.len() - 1;
        for (i, a) in args[..last].iter().enumerate() {
            parts.push(q(a, ops[i]));
        }
        parts.push(q(&args[last..].join(" "), ops[last]));
    } else {
        for (i, ph) in ops.iter().enumerate() {
            match args.get(i) {
                Some(a) if !is_flag(a) => parts.push(q(a, ph)),
                _ => parts.push(ph.to_string()),
            }
        }
    }
    parts.join(" ")
}

/// The usage refusal for a wrong operand count, or `None` when the count is
/// acceptable (or the command is not one this table covers).
pub fn arity(cmd: &str, args: &[String]) -> Option<Outcome> {
    let (req, hi, ops) = spec(cmd)?;
    let n = args.len();
    if n >= req && hi.is_none_or(|h| n <= h) {
        return None;
    }
    let wants_section = args
        .iter()
        .any(|a| a == "--section" || a.starts_with("--section="));
    // the count python reports: `read` and `append` are worded by hand there
    let want = match (cmd, hi) {
        ("read", _) => "1-3".to_string(),
        ("append", _) => "2".to_string(),
        (_, None) => format!("{}+", req),
        (_, Some(h)) if h == req => req.to_string(),
        (_, Some(h)) => format!("{}-{}", req, h),
    };
    let set_bad_key = cmd == "set" && n >= 2 && !fm_key_ok(&args[1]);
    let hint = if cmd == "append" && wants_section {
        " (append has no --section; append inside a section is appendsec)".to_string()
    } else if set_bad_key {
        " (KEY and VALUE are separate arguments; one key per call)".to_string()
    } else if cmd != "read" && surplus_slot(ops, args) {
        format!(" ({} is one argument; quote it)", ops[ops.len() - 1])
    } else {
        String::new()
    };
    let note = |args: &[String]| match args.first() {
        Some(a) => q(a, "NOTE"),
        None => "NOTE".to_string(),
    };
    let next = match cmd {
        // SEC (and patch's SHA8) come from the outline of the note already named
        "read" | "patch" => format!("vv outline {}", note(args)),
        "append" if wants_section => {
            let who = match args.first() {
                Some(a) if !is_flag(a) => q(a, "NOTE"),
                _ => "NOTE".to_string(),
            };
            format!("vv appendsec {} SEC TEXT", who)
        }
        "set" if set_bad_key => "vv set --help".to_string(),
        _ => next_from_table(cmd, ops, args),
    };
    Some(die(
        cmd,
        &format!(
            "usage: {} takes {} positional args, got {}{}",
            cmd, want, n, hint
        ),
        Some(&next),
        1,
    ))
}
