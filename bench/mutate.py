#!/usr/bin/env python3
"""Mutation pass for the native engine's guards: break one line, rebuild, and
require the named check to turn red.

    python3 bench/mutate.py              # run every mutation
    python3 bench/mutate.py NAME ...     # run the named ones

Each mutation runs in a throwaway copy of the checkout (the working tree is
never edited), under a per-run timeout, and is followed by a count of processes
still running from that copy: a guard removed from one engine has, before now,
left the two engines handing a call back and forth without end.

A mutation that leaves its suite green is the finding: the guarded line is
dead, or nothing pins it. The script exits 1 on any of: a mutation that
applies to zero or several places, a build failure, a suite that stays green,
a named check that is not among the failures, a timeout, or leftover
processes.
"""
import os, shutil, subprocess, sys, tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIMEOUT = 900

# name, file, text to find (exactly once), replacement, suite, a check that must fail
MUTATIONS = [
    ("arity-hands-off-again", "vrust/src/main.rs",
     "if let Some(readpath::Outcome::Done(code)) = err::arity(cmd, &args[1..]) {",
     "if let Some(readpath::Outcome::Done(code)) = None::<readpath::Outcome> {",
     "tests/test_native_errors.py", "A1[no operand]"),
    ("arity-ignores-global-flag", "vrust/src/main.rs",
     "if readpath::GLOBAL_FLAG.get().is_none() {\n            if let Some(readpath::Outcome::Done(code)) = err::arity",
     "if true {\n            if let Some(readpath::Outcome::Done(code)) = err::arity",
     "tests/test_native_errors.py", "G1[--limit]"),
    ("arity-table-row-wrong", "vrust/src/err.rs",
     '"unset" => (2, Some(2), &["NOTE", "KEY"]),',
     '"unset" => (2, Some(3), &["NOTE", "KEY"]),',
     "tests/test_native_errors.py", "B1"),
    ("arity-open-tail-closed", "vrust/src/err.rs",
     '"show" => (1, None, &["NOTE"]),',
     '"show" => (1, Some(1), &["NOTE"]),',
     "tests/test_native_errors.py", "B2[show]"),
    ("arity-surplus-joins-flags", "vrust/src/err.rs",
     "        && !args.iter().any(|a| is_flag(a))\n",
     "",
     "tests/test_native_errors.py", "B1"),
    ("arity-flag-fills-a-slot", "vrust/src/err.rs",
     "Some(a) if !is_flag(a) => parts.push(q(a, ph)),",
     "Some(a) => parts.push(q(a, ph)),",
     "tests/test_native_errors.py", "B1"),
    ("arity-bullet-is-a-flag", "vrust/src/err.rs",
     "tok.starts_with('-') && tok.chars().count() > 1 && !tok.chars().any(py_space)",
     "tok.starts_with('-') && tok.chars().count() > 1",
     "tests/test_native_errors.py", "B1"),
    ("arity-set-key-hint-dropped", "vrust/src/err.rs",
     'let set_bad_key = cmd == "set" && n >= 2 && !fm_key_ok(&args[1]);',
     "let set_bad_key = false;",
     "tests/test_native_errors.py", "B1"),
    ("arity-append-section-hint-dropped", "vrust/src/err.rs",
     'let hint = if cmd == "append" && wants_section {',
     'let hint = if false {',
     "tests/test_native_errors.py", "B1"),
    ("arity-quote-hint-dropped", "vrust/src/err.rs",
     '} else if cmd != "read" && surplus_slot(ops, args) {',
     "} else if false {",
     "tests/test_native_errors.py", "B1"),
    ("search-no-query-ignores-global-flag", "vrust/src/main.rs",
     "if readpath::GLOBAL_FLAG.get().is_some() || readpath::from_py() {\n            exec_python(orig);",
     "if readpath::from_py() {\n            exec_python(orig);",
     "tests/test_native_errors.py", "B5 with a global flag that refusal is python's too"),
    ("vault-not-checked-to-be-a-directory", "vrust/src/main.rs",
     "Some(v) if !v.is_empty() && !v.starts_with('~') && normpath(Path::new(v)).is_dir() => {",
     "Some(v) if !v.is_empty() && !v.starts_with('~') => {",
     "tests/test_native_errors.py", "W1[--vault not a directory]"),
    ("empty-vault-taken-natively", "vrust/src/main.rs",
     "Some(v) if !v.is_empty() && !v.starts_with('~') && normpath(Path::new(v)).is_dir() => {",
     "Some(v) if !v.starts_with('~') && (v.is_empty() || normpath(Path::new(v)).is_dir()) => {",
     "tests/test_native_errors.py", "W1[--vault empty]"),
    ("tilde-vault-taken-natively", "vrust/src/main.rs",
     "Some(v) if !v.is_empty() && !v.starts_with('~') && normpath(Path::new(v)).is_dir() => {",
     "Some(v) if !v.is_empty() && (v.starts_with('~') || normpath(Path::new(v)).is_dir()) => {",
     "tests/test_native_errors.py", "W1[--vault tilde]"),
    ("vault-checked-before-normalising", "vrust/src/main.rs",
     "Some(v) if !v.is_empty() && !v.starts_with('~') && normpath(Path::new(v)).is_dir() => {",
     "Some(v) if !v.is_empty() && !v.starts_with('~') && Path::new(v).is_dir() => {",
     "tests/test_native_errors.py", "W1[--vault through a link and ..]"),
    ("signed-limit-read-natively", "vrust/src/main.rs",
     ".filter(|v| (1..=18).contains(&v.len()) && v.bytes().all(|b| b.is_ascii_digit()))",
     ".filter(|v| (1..=18).contains(&v.len()))",
     "tests/test_native_errors.py", "W1[--limit +1]"),
    ("long-limit-read-natively", "vrust/src/main.rs",
     ".filter(|v| (1..=18).contains(&v.len()) && v.bytes().all(|b| b.is_ascii_digit()))",
     ".filter(|v| !v.is_empty() && v.bytes().all(|b| b.is_ascii_digit()))",
     "tests/test_native_errors.py", "W1[--limit zero-padded past python's digit limit]"),
    ("python-delegates-flag-like-terms", "src/vv_impl.py",
     'if any(a in ("--limit", "--vault", "--jsonl") for a in args) or args[:1] in (["--help"], ["-h"]):',
     "if False:",
     "tests/test_native_errors.py", "W5[a term named --limit]"),
    ("python-delegates-an-empty-query", "src/vv_impl.py",
     "    if use_rust() and terms and _native_argv_ok(args):",
     "    if use_rust() and _native_argv_ok(args):",
     "tests/test_native_errors.py", "B7[bare]"),
    ("die-drops-next-step", "vrust/src/err.rs",
     'Some(n) => format!("{}{}{}\\n", msg, SEP, neutralise(esc(n))),',
     'Some(_) => format!("{}\\n", msg),',
     "tests/test_native_errors.py", "A2[no operand]"),
    ("die-skips-metrics-row", "vrust/src/err.rs",
     "log_error(op, first.strip_suffix(':'), code, wire.len());",
     "let _ = (op, first, code);",
     "tests/test_native_errors.py", "M1 exactly one row"),
    ("q-never-quotes", "vrust/src/err.rs",
     "if !s.is_empty() && s.bytes().all(safe) {",
     "if true {",
     "tests/test_native_errors.py", "A2[a quoted operand]"),
    ("q-ignores-control-characters", "vrust/src/err.rs",
     "if s.chars().any(is_ctl) || s.contains(SEP) {",
     "if s.contains(SEP) {",
     "tests/test_native_errors.py", "A2[a control character]"),
    ("non-utf8-argv-decoded-lossily", "vrust/src/main.rs",
     "None => exec_python(&raw),",
     "None => args.push(a.to_string_lossy().into_owned()),",
     "tests/test_native_errors.py", "U2 python receives each argument byte for byte"),
    ("native-hands-back-to-python", "vrust/src/main.rs",
     "if readpath::from_py() {\n        refuse_hand_back();\n        exit(1);",
     "if false {\n        refuse_hand_back();\n        exit(1);",
     "tests/test_native_errors.py", "U5 a binary that python called never hands the call back"),
    ("marker-believed-from-anyone", "vrust/src/readpath.rs",
     "Ok(v) => v == std::os::unix::process::parent_id().to_string(),",
     "Ok(_) => true,",
     "tests/test_native_errors.py", "U8[another pid]"),
    ("patch-launcher-hands-back", "vrust/src/write.rs",
     "if crate::readpath::from_py() {\n        crate::refuse_hand_back();",
     "if false {\n        crate::refuse_hand_back();",
     "tests/test_native_errors.py", "P5 the post-stdin launcher does not hand back"),
    ("python-ignores-undecodable-vault", "src/vv_impl.py",
     "for a in (*args, VAULT):",
     "for a in args:",
     "tests/test_native_errors.py", "U9b and delegates nothing when the vault path itself is undecodable"),
    ("unreadable-stdin-forwarded", "vrust/src/write.rs",
     "if std::io::Read::read_to_end(&mut std::io::stdin(), &mut raw).is_err() {",
     "if false {",
     "tests/test_native_errors.py", "P6 a body that could not be read whole"),
    ("python-delegates-undecodable-search", "src/vv_impl.py",
     "    if use_rust() and terms and _native_argv_ok(args):\n        import subprocess\n        # VV_FROM_PY (our pid",
     "    if use_rust() and terms:\n        import subprocess\n        # VV_FROM_PY (our pid",
     "tests/test_native_errors.py", "U4[one term]"),
    ("patch-handoff-drops-the-body", "vrust/src/write.rs",
     "let _ = si.write_all(body);",
     "let _ = body;",
     "tests/test_native_errors.py", "P1 the post-stdin hand-off reaches VV_PY_ENTRY"),
    ("patch-handoff-ignores-vv-python", "vrust/src/write.rs",
     "let child = Command::new(&python)",
     'let child = Command::new("python3")',
     "tests/test_native_errors.py", "P2 and honours VV_PYTHON"),
    ('typed-escape-passed-over', 'vrust/src/readpath.rs',
     '            None => Err(escape(r)),',
     '            None => Ok(None),',
     "tests/test_native_errors.py", 'R1x'),
    ('walk-hit-not-contained', 'vrust/src/readpath.rs',
     '        if within(vault, p.as_os_str().as_bytes()) {',
     '        if true {',
     "tests/test_native_errors.py", 'R2x'),
    ('dangling-link-counted-as-a-note', 'vrust/src/readpath.rs',
     '.filter(|p| stem_lower(p).as_deref() == Some(want) && p.is_file())',
     '.filter(|p| stem_lower(p).as_deref() == Some(want))',
     "tests/test_native_errors.py", 'R2x'),
    ('ambiguous-lists-every-hit', 'vrust/src/readpath.rs',
     '&format!("ambiguous: {}", rels[..rels.len().min(5)].join(" | ")),',
     '&format!("ambiguous: {}", rels.join(" | ")),',
     "tests/test_native_errors.py", 'R2x'),
    ('write-through-an-unproven-name', 'vrust/src/readpath.rs',
     '        if !hits.is_empty() && partial {',
     '        if false {',
     "tests/test_native_errors.py", 'R2x'),
    ('write-through-an-unproven-id', 'vrust/src/readpath.rs',
     '        if partial {\n            // zero visible candidates is not "none"',
     '        if false {\n            // zero visible candidates is not "none"',
     "tests/test_native_errors.py", 'R2x'),
    ('absence-claimed-over-a-partial-walk', 'vrust/src/readpath.rs',
     '    if partial {\n        return Err(cannot_prove(format!("\'{}\' is absent", ref_)));',
     '    if false {\n        return Err(cannot_prove(format!("\'{}\' is absent", ref_)));',
     "tests/test_native_errors.py", 'R1x'),
    ('read-answered-over-a-partial-walk', 'vrust/src/readpath.rs',
     '    if w.odd || (!w.unreadable.is_empty() && !writing) {',
     '    if w.odd {',
     "tests/test_native_errors.py", 'R5'),
    ('two-suggestions-taken-for-three', 'vrust/src/readpath.rs',
     '            if sugg.len() == 3 {',
     '            if sugg.len() == 2 {',
     "tests/test_native_errors.py", 'R2x'),
    ('hash-id-keeps-its-hash', 'vrust/src/readpath.rs',
     '        want.remove(0);\n',
     '',
     "tests/test_native_errors.py", 'R6'),
    ('realpath-follows-no-links', 'vrust/src/readpath.rs',
     '        if !is_link {\n            path = newpath;',
     '        if true {\n            path = newpath;',
     "tests/test_native_errors.py", 'R2x'),
    ('shown-path-not-normalised', 'vrust/src/readpath.rs',
     '        crate::normpath(&abs)\n',
     '        abs\n',
     "tests/test_native_errors.py", "R2x[resolve 'Area/../Hub']"),
    ('undecodable-vault-replaced-by-default', 'vrust/src/main.rs',
     'if env::var_os("VV_VAULT").is_some_and(|v| v.to_str().is_none()) {',
     'if false {',
     "tests/test_native_errors.py", 'U10'),
    ('journal-keyed-on-the-canonical-path', 'vrust/src/write.rs',
     '    let real = readpath::realpath_of(vault);',
     '    let real = fs::canonicalize(vault).unwrap_or_else(|_| vault.to_path_buf());',
     "tests/test_native_errors.py", 'R10'),
    ('ambiguous-sorted-by-component', 'vrust/src/readpath.rs',
     '        hits.sort_by(|a, b| a.as_os_str().cmp(b.as_os_str()));',
     '        hits.sort();',
     "tests/test_native_errors.py", 'R2x'),
    ('non-ascii-ambiguity-judged-natively', 'vrust/src/readpath.rs',
     '        if !want.is_ascii()',
     '        if false && !want.is_ascii()',
     "tests/test_native_errors.py", 'R7c'),
    ('unreadable-list-uncapped', 'vrust/src/readpath.rs',
     '.take(3)',
     '.take(99)',
     "tests/test_native_errors.py", 'R2x'),
    ('write-ops-forget-unset', 'vrust/src/readpath.rs',
     '    "unset",\n',
     '',
     "tests/test_native_errors.py", 'R1x'),
    ('suggestions-ranked-by-bytes', 'vrust/src/readpath.rs',
     '(stem.chars().count(), stem.to_string(), p)',
     '(stem.len(), stem.to_string(), p)',
     "tests/test_native_errors.py", 'R3c[zq]'),
    ('suggestions-use-the-whole-path', 'vrust/src/readpath.rs',
     'let base = want.rsplit(\'/\').next().unwrap_or("");',
     'let base = want.as_str();',
     "tests/test_native_errors.py", 'R3c[Nope/gamm]'),
    ('refusal-row-forgets-the-name', 'vrust/src/readpath.rs',
     '        let _ = REF_KIND.set("name");\n        return contained(hits[0]);',
     '        return contained(hits[0]);',
     "tests/test_native_errors.py", 'R12'),
]


def leftovers(root):
    ps = subprocess.run(["ps", "-eo", "pid=,command="], capture_output=True, text=True)
    if ps.returncode != 0 or not ps.stdout.strip():
        return ["(could not list processes)"]
    out = ps.stdout
    return [l.strip() for l in out.splitlines() if root in l and "bench/mutate.py" not in l]


def baseline(suites):
    """The unmutated suites must pass here first: a check that is already red
    on this host would otherwise be credited to whatever mutation names it."""
    for suite in sorted(suites):
        try:
            r = subprocess.run([sys.executable, suite], cwd=REPO, capture_output=True, text=True, timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            return f"{suite} did not finish within {TIMEOUT}s before any mutation"
        if r.returncode != 0:
            return f"{suite} fails before any mutation"
    return None


def run_one(name, rel, old, new, suite, must_fail):
    work = os.path.realpath(tempfile.mkdtemp(prefix="vv-mutate-"))
    try:
        for d in ("src", "tests", "vrust", "bench"):
            shutil.copytree(os.path.join(REPO, d), os.path.join(work, d), symlinks=True)
        shutil.copy(os.path.join(REPO, "VERSION"), work)
        path = os.path.join(work, rel)
        text = open(path, encoding="utf-8").read()
        if text.count(old) != 1:
            return f"the text to mutate occurs {text.count(old)} times in {rel}, not once"
        with open(path, "w", encoding="utf-8") as f:
            f.write(text.replace(old, new))
        try:
            b = subprocess.run(["cargo", "build", "--release"], cwd=os.path.join(work, "vrust"),
                               capture_output=True, text=True, timeout=TIMEOUT)
            if b.returncode != 0:
                return "the mutated source does not build: " + b.stderr.strip().splitlines()[-1][:200]
            r = subprocess.run([sys.executable, suite], cwd=work, capture_output=True, text=True, timeout=TIMEOUT)
        except subprocess.TimeoutExpired as e:
            return f"did not finish within {TIMEOUT}s: {e.cmd}"
        stray = leftovers(work)
        if stray:
            return f"{len(stray)} process(es) still running from the mutated copy: {stray[0][:120]}"
        failed = [l[5:] for l in r.stdout.splitlines() if l.startswith("FAIL ")]
        if r.returncode == 0 or not failed:
            return f"{suite} stayed green: nothing pins this line"
        if not any(f.startswith(must_fail) for f in failed):
            return f"{suite} went red, but not on `{must_fail}`: {failed[:3]}"
        return None
    finally:
        shutil.rmtree(work, True)


def main():
    want = set(sys.argv[1:])
    unknown = want - {m[0] for m in MUTATIONS}
    if unknown:
        print("FAIL no such mutation: " + ", ".join(sorted(unknown)))
        sys.exit(1)
    problem = baseline({m[4] for m in MUTATIONS if not want or m[0] in want})
    if problem:
        print("FAIL " + problem)
        sys.exit(1)
    bad = 0
    for m in MUTATIONS:
        if want and m[0] not in want:
            continue
        problem = run_one(*m)
        print(("FAIL " if problem else "ok   ") + m[0] + (f" — {problem}" if problem else f" — red on `{m[5]}`"))
        bad += bool(problem)
    print(f"\n{bad} mutation(s) not caught" if bad else "\nALL MUTATIONS CAUGHT")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
