#!/usr/bin/env python3
"""vv — PROTOTYPE full vault CLI (Python orchestrator; shells to vrust for hot scans).

Read:    outline NOTE · read NOTE SEC · head NOTE · resolve NAME · search TERMS [--k N] [--w C]
Write:   patch NOTE SEC SHA8 <stdin · appendsec NOTE SEC TEXT · append NOTE TEXT
         set NOTE KEY VALUE · unset NOTE KEY · new PATH [--template T] [--k v ...]
Graph:   backlinks NOTE · links NOTE · orphans [FOLDER]
Query:   board FOLDER [k=v ...] · tags [--counts] · props KEY [FOLDER]
Daily:   daily-append TEXT   (today's standup note; creates from convention if missing)

NOTE = vault-relative path OR bare name (wikilink-style resolution).
Every op logs {op, ms, out_bytes} to ~/.claude/metrics/vv.jsonl.
Exit: 0 ok · 1 not-found/usage · 3 stale hash.
"""
import sys, os, re, json, time, hashlib, glob, subprocess, datetime

VAULT = os.environ.get("VV_VAULT") or os.path.expanduser("~/Documents/Obsidian Vault")
VRUST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vrust/target/release/vrust")
METRICS = os.path.expanduser("~/.claude/metrics/vv.jsonl")
SKIP_DIRS = {".git", ".obsidian", ".claude", ".trash", "graphify-out"}

_t0 = time.perf_counter()
_op = sys.argv[1] if len(sys.argv) > 1 else "?"

_cf_bytes = 0  # counterfactual: what a whole-file read of the touched notes would cost

def _log(out_bytes, exit_code=0, kind=None):
    # Test suites (VV_JOURNAL_ROOT) and explicit opt-out don't pollute the
    # day-to-day usage log the shadow pilot reads.
    if os.environ.get("VV_JOURNAL_ROOT") or os.environ.get("VV_NO_METRICS"):
        return
    try:
        rec = {"ts": datetime.datetime.now().isoformat(timespec="seconds"),
               "op": _op, "ms": round((time.perf_counter() - _t0) * 1000),
               "out_bytes": out_bytes, "exit": exit_code}
        if kind:
            rec["kind"] = kind
        if _cf_bytes:
            rec["cf_bytes"] = _cf_bytes
        with open(METRICS, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass

_out_total = 0
def out(s=""):
    # Encoded bytes, not characters: cf_bytes comes from os.path.getsize, so
    # counting len(s) here would divide characters by bytes in the savings math.
    global _out_total
    _out_total += len(s.encode("utf-8")) + 1
    print(s)

def die(msg, code=1):
    sys.stderr.write(msg + "\n")
    first = msg.split("\n", 1)[0].split(" ", 1)[0]
    # Error text enters the caller's context too — bill it, don't log a zero.
    _log(_out_total + len(msg.encode("utf-8")) + 1, code,
         first[:-1] if first.endswith(":") else None)
    sys.exit(code)


def use_rust():
    """Engine selection: VV_ENGINE=rust|python forces a path (tests run the suite
    under BOTH so the fallback is never the untested one — sqlx's per-backend test
    matrix, adapted); unset = rust when built. Unknown values refuse loudly."""
    eng = os.environ.get("VV_ENGINE", "")
    if eng not in ("", "rust", "python"):
        die(f"engine: unknown VV_ENGINE '{eng}' — next: use rust|python or unset")
    if eng == "python":
        return False
    if eng == "rust" and not os.path.exists(VRUST):
        die("engine: VV_ENGINE=rust but the engine is not built — next: cd vrust && cargo build --release")
    return os.path.exists(VRUST)

def md_files():
    for dirpath, dirs, names in os.walk(VAULT):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS]
        for n in names:
            if n.endswith(".md"):
                yield os.path.join(dirpath, n)

_VAULT_REAL = os.path.realpath(VAULT)

def contain(path):
    """Resolve `path` and REFUSE anything outside the vault (abs paths, .. escape, symlink escape)."""
    full = path if os.path.isabs(path) else os.path.join(VAULT, path)
    real = os.path.realpath(full)
    if real != _VAULT_REAL and not real.startswith(_VAULT_REAL + os.sep):
        die(f"escape: path leaves the vault: {path}")
    return full

_cf_seen = set()

def _cf(fp):
    """Tally the counterfactual cost (whole-file bytes) of a resolved note.

    Once per note per invocation: a command that resolves the same path twice
    (move resolves in the wrapper and again in _do_relocate) would otherwise
    double-bill the baseline and flatter vv's savings. Chains across separate
    invocations still bill each one — the report treats this as a MODELLED
    workload figure, not an observed counterfactual.
    """
    global _cf_bytes
    try:
        real = os.path.realpath(fp)
        if real in _cf_seen:
            return fp
        _cf_seen.add(real)
        _cf_bytes += os.path.getsize(fp)
    except OSError:
        pass
    return fp

def resolve(ref):
    """Vault-relative path if it exists, else wikilink-style bare-name resolution.
    All paths are contained to the vault (no abs/.. escape)."""
    fp = contain(ref)
    if os.path.isfile(fp):
        return _cf(fp)
    # allow folder/Name (no .md) exact path resolution
    fp_md = contain(ref + ".md") if not ref.endswith(".md") else fp
    if os.path.isfile(fp_md):
        return _cf(fp_md)
    want = (ref[:-3] if ref.endswith(".md") else ref).lower()
    all_notes = list(md_files())
    hits = [p for p in all_notes if os.path.basename(p)[:-3].lower() == want]
    if len(hits) == 1:
        return _cf(hits[0])
    if not hits:
        sugg = suggest_names(want, all_notes)
        extra = ("\ndid you mean: " + " | ".join(sugg)) if sugg else ""
        die(f"not-found: no note matches '{ref}'{extra}")
    die("ambiguous: " + " | ".join(os.path.relpath(h, VAULT) for h in sorted(hits)[:5]))

def suggest_names(want, paths, n=3):
    """Suggestions for a failed name lookup, tiered like rustdoc search:
    substring match outranks edit-distance similarity. A path-qualified miss
    (Folder/Notte) is compared by its basename. Ties break lexicographically so
    filesystem iteration order never changes the output. Suggestion only —
    resolve never auto-picks a fuzzy match, because resolve feeds the write path."""
    import difflib
    want = want.rsplit("/", 1)[-1]
    by_name = {}
    for p in paths:
        by_name.setdefault(os.path.basename(p)[:-3], []).append(p)
    subs = sorted((nm for nm in by_name if want in nm.lower()), key=lambda s: (len(s), s))[:n]
    if len(subs) < n:
        lower = {nm.lower(): nm for nm in sorted(by_name)}
        close = difflib.get_close_matches(want, lower.keys(), n=n, cutoff=0.6)
        subs += [lower[c] for c in close if lower[c] not in subs]
    return subs[:n]

def rel(fp):
    return os.path.relpath(fp, VAULT)

# ---------- md structure (shared with vnote2, fence-aware) ----------
BOM = "﻿"

def fm_bounds(lines):
    """Index one past the closing '---' of frontmatter, or 0. Tolerates a leading BOM."""
    if not lines:
        return 0
    first = lines[0].lstrip(BOM).rstrip("\r")
    if first != "---":
        return 0
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r") == "---":
            return i + 1
    return 0  # unterminated: treat the whole file as body, never as frontmatter

def fence_mask(lines, start=0):
    """Line indices inside fenced blocks, per CommonMark:
    a fence closes only on its OWN marker character AND a run at least as long as the
    opener. So ```` ```` ```` is not closed by ``` — which is how nested code samples
    are written — and ``` is never closed by ~~~."""
    masked = set()
    marker = None      # (char, length)
    for i in range(start, len(lines)):
        l = lines[i].rstrip("\r")
        # indent is ASCII spaces only (CommonMark; also keeps the Rust engine's
        # byte-counting equivalent — NBSP/tab indent is not fence indent)
        m = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", l)
        if marker is None:
            # CommonMark: a backtick fence's info string may not contain backticks —
            # ```code``` on one line is an inline span, not a fence (caught by the
            # expected-vector corpus + probed against Obsidian 2026-08-26)
            if m and (m.group(1)[0] == "~" or "`" not in m.group(2)):
                marker = (m.group(1)[0], len(m.group(1)))
                masked.add(i)
        else:
            masked.add(i)
            if m and m.group(1)[0] == marker[0] and len(m.group(1)) >= marker[1] \
                    and not m.group(2).strip():
                marker = None
    return masked

def parse(text):
    lines = text.split("\n")
    fm_end = fm_bounds(lines)
    fenced = set(range(fm_end))
    fenced |= fence_mask(lines, fm_end)
    heads = [(i, len(m.group(1)), m.group(2).strip())
             for i, l in enumerate(lines)
             if i not in fenced and (m := re.match(r"^(#{1,6})\s+(.*)$", l))]
    first = heads[0][0] if heads else len(lines)
    secs = [{"id": "H0", "level": 0, "title": "(preamble)", "start": 0, "end": first}]
    for j, (i, lvl, title) in enumerate(heads):
        end = heads[j + 1][0] if j + 1 < len(heads) else len(lines)
        secs.append({"id": f"H{j+1}", "level": lvl, "title": title, "start": i, "end": end})
    return lines, secs

def sec_text(lines, s):
    return "\n".join(lines[s["start"]:s["end"]])

def sha8(t):
    return hashlib.sha256(t.encode()).hexdigest()[:8]

def find_sec(lines, secs, sid):
    """Resolve a section by id, and forgive the four ways agents actually ask.

    A replay of 50 real sessions (2026-08-26) found section addressing was
    guessed wrong four distinct ways -- `--section H9`, `Note#Heading`, the
    heading TITLE instead of the id, and the outline's display label
    `(preamble)`. Every one was correctly refused, which is the tool being right
    and unhelpful at the same time: four different wrong guesses is an
    affordance problem, not four careless callers.

    Ids stay canonical and win outright; a title match is accepted only when it
    is UNAMBIGUOUS, because duplicate headings are common in these notes and
    silently picking the first would be worse than refusing.
    """
    for s in secs:
        if s["id"] == sid:
            return s
    want = (sid or "").strip()
    if want.startswith("#"):
        want = want.lstrip("#").strip()          # `#Heading` / `##Heading`
    if want.lower() in ("(preamble)", "preamble"):
        for s in secs:
            if s["title"] == "(preamble)" or s["id"] == "H0":
                return s
    matches = [s for s in secs if s["title"].strip().lower() == want.lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        ids = ", ".join(m["id"] for m in matches)
        die(f"ambiguous: {len(matches)} sections are titled {want!r} ({ids}) — "
            f"next: pass the id from vv outline NOTE")
    die(f"not-found: no section {sid} — next: vv outline NOTE")

def split_fm(text):
    fm, body, _tail, _bom = split_fm_full(text)
    return fm, body

def split_fm_full(text):
    """(fm, body, tail, bom). tail = the newline after the closing '---', preserved verbatim.
    bom = a leading byte-order mark, preserved so writers can restore it.
    Unterminated frontmatter yields fm=None: the file is body-only, never half-parsed."""
    bom = BOM if text.startswith(BOM) else ""
    t = text[len(bom):]
    m = re.match(r"^---\r?\n(.*?)\r?\n---(\r?\n)?", t, re.S)
    if not m:
        return None, text, "", bom
    return m.group(1), t[m.end():], m.group(2) or "", bom

def fm_props(fm):
    props = {}
    if fm:
        for line in fm.splitlines():
            if (m := re.match(r"^(\w[\w-]*):\s*(.*)$", line)):
                props[m.group(1)] = m.group(2).strip('"')
    return props

def file_sig(fp):
    """Cheap identity of a file's current bytes, for lost-update detection.

    Obsidian is a SECOND WRITER: it is normally running with the vault open and
    saves buffers on its own schedule. Only `patch` was compare-and-swapped;
    set/unset/append/appendsec/daily-append were read-modify-write, so a save
    landing between our read and our write was silently overwritten (found
    2026-08-26 while surveying AFFiNE's CRDT model -- the "single-writer, CAS is
    enough" premise was simply wrong).

    mtime_ns + size, not a hash: the point is to detect a concurrent write in the
    millisecond window we actually opened, and re-hashing every file on every
    frontmatter flip would cost more than it protects.
    """
    try:
        st = os.stat(fp)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def read_raw(fp):
    try:
        with open(fp, newline="", encoding="utf-8") as f:
            return f.read()
    except UnicodeDecodeError as e:
        die(f"utf8: {rel(fp)} is not valid UTF-8 ({e.reason} at byte {e.start}) — vv only edits UTF-8 notes", 5)

def eol_of(text):
    return "\r\n" if "\r\n" in text else "\n"

def atomic_write(fp, content, expect_sig=None):
    # follow a symlink to its real target so we replace the file, not the link
    target = os.path.realpath(fp) if os.path.islink(fp) else fp
    if expect_sig is not None and file_sig(target) != expect_sig:
        # Same vocabulary as patch's stale-hash refusal: exit 3, re-read, retry.
        die(f"stale: {rel(target)} changed on disk since it was read "
            f"(Obsidian or another writer) — re-run the command", 3)
    d = os.path.dirname(target) or "."
    import tempfile as _tf
    fd, tmp = _tf.mkstemp(dir=d, prefix=".vv-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="") as f:
            f.write(content)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

def block_scalar_key(fm_lines, key):
    """True if `key` at column 0 has a block/flow value that spans continuation lines
    (a `>`/`|` scalar, or a following more-indented line). set/unset would orphan it."""
    for i, l in enumerate(fm_lines):
        if re.match(rf"^{re.escape(key)}:", l):
            val = l.split(":", 1)[1].strip()
            if val in (">", "|", ">-", "|-", ">+", "|+", ""):
                nxt = fm_lines[i + 1] if i + 1 < len(fm_lines) else ""
                if nxt[:1] in (" ", "\t") or (val and val[0] in "|>"):
                    return True
            return False
    return False

def splice(lines, start, end, new_lines):
    """Replace lines[start:end] with `new_lines` (bare content, no line terminators)
    and return the file text.

    Terminators are never invented or dropped: the file's EOL style is applied to the
    inserted lines, and the file's EOF-newline property is preserved exactly as it was.
    `lines` is a split on "\\n", so on a CRLF file every element except the last carries
    a trailing "\\r" and a file ending in a newline has "" as its last element."""
    crlf = eol_of("\n".join(lines)) == "\r\n"
    ended_with_newline = bool(lines) and lines[-1] == ""
    body = [b.rstrip("\r") for b in new_lines]
    if end >= len(lines):
        # the replaced span reaches EOF: restore the file's original EOF-newline property
        # only the terminator is normalized — interior blank lines are content and are kept
        if ended_with_newline:
            if not body or body[-1] != "":
                body.append("")
        elif body and body[-1] == "":
            body.pop()
    merged = list(lines[:start]) + body + list(lines[end:])
    if crlf:
        # every element except the final one is followed by a newline -> gets the \r
        merged = [(m if m.endswith("\r") else m + "\r") if i < len(merged) - 1 else m.rstrip("\r")
                  for i, m in enumerate(merged)]
    return "\n".join(merged)

# ---------- commands ----------
def cmd_outline(ref):
    lines, secs = parse(read_raw(resolve(ref)))
    for s in secs:
        if s["start"] == s["end"]:
            continue  # empty preamble span — nothing addressable, and [] vs [""] must stay distinguishable
        t = sec_text(lines, s)
        out(f"{s['id']}\t{'#'*s['level'] or '-'}\t{s['title']}\t{len(t)}B\t{sha8(t)}")

def cmd_read(ref, sid):
    lines, secs = parse(read_raw(resolve(ref)))
    s = find_sec(lines, secs, sid)
    out(sec_text(lines, s))
    out(f"--sha8:{sha8(sec_text(lines, s))}")

def cmd_head(ref):
    fm, _ = split_fm(read_raw(resolve(ref)))
    out(fm if fm is not None else "(no frontmatter)")

def cmd_resolve(ref):
    out(rel(resolve(ref)))

def cmd_patch(ref, sid, expect):
    _dirty_gate()
    fp = resolve(ref)
    lines, secs = parse(read_raw(fp))
    s = find_sec(lines, secs, sid)
    if sid == "H0" and s["end"] > 0 and lines and lines[0].rstrip("\r") == "---":
        die("refused: H0 contains frontmatter — next: vv set/unset (patch would rewrite YAML as body)")
    cur = sec_text(lines, s)
    if sha8(cur) != expect:
        sys.stderr.write(f"stale: {sid} is {sha8(cur)}, expected {expect} — re-outline\n")
        _log(0, 3, "stale"); sys.exit(3)
    body = sys.stdin.read().replace("\r\n", "\n")
    if body.endswith("\n"):
        body = body[:-1]   # strip the one newline the caller's shell/`read` framing adds
    body_lines = [] if (body == "" and s["end"] == s["start"]) else body.split("\n")
    atomic_write(fp, splice(lines, s["start"], s["end"], body_lines))
    out(f"patched {sid} in {rel(fp)} ({len(cur)}B -> {len(body)}B)")

def cmd_appendsec(ref, sid, text):
    _dirty_gate()
    fp = resolve(ref)
    _sig = file_sig(fp)
    lines, secs = parse(read_raw(fp))
    s = find_sec(lines, secs, sid)
    ins = s["end"]
    while ins > s["start"] and lines[ins - 1].strip() == "":
        ins -= 1
    atomic_write(fp, splice(lines, ins, ins, [text]), expect_sig=_sig)
    out(f"appended to {sid} in {rel(fp)}")

def cmd_append(ref, text):
    _dirty_gate()
    fp = resolve(ref)
    _sig = file_sig(fp)
    cur = read_raw(fp)
    eol = eol_of(cur)
    atomic_write(fp, cur + ("" if cur.endswith("\n") or not cur else eol) + text + eol, expect_sig=_sig)
    out(f"appended to {rel(fp)}")

# Characters that are ALWAYS YAML indicators in first position.
# `-`, `?` and `:` are deliberately NOT here: they only indicate when followed by
# a space (or end of value). Treating them as unconditional indicators quoted
# `-1` into the STRING "-1" -- a silent type change, and a regression this
# function introduced rather than prevented (Codex review 2026-08-26).
_YAML_LEAD = set("[]{}#&*!|>'\"%@`,")


def _is_wellformed_quoted(v):
    """True only if v is a properly closed quoted scalar with no stray quote.

    The first version accepted anything that merely started and ended with the
    same quote, so `"a" junk"` was passed through as 'already quoted' and broke
    the whole block -- the exact failure this function exists to prevent.
    """
    if len(v) < 2 or v[0] != v[-1] or v[0] not in "\"'":
        return False
    q, inner = v[0], v[1:-1]
    if q == "'":
        return inner.count("'") % 2 == 0          # '' is the escape
    i = 0
    while i < len(inner):
        if inner[i] == "\\":
            i += 2
            continue
        if inner[i] == '"':
            return False                           # unescaped closing quote
        i += 1
    return True


def _is_balanced_flow(v):
    """True if v is a syntactically balanced flow collection ([...] / {...})."""
    if not v or v[0] not in "[{":
        return False
    pairs = {"]": "[", "}": "{"}
    stack = []
    for ch in v:
        if ch in "[{":
            stack.append(ch)
        elif ch in "]}":
            if not stack or stack.pop() != pairs[ch]:
                return False
    return not stack


def yaml_scalar(v):
    """Quote a frontmatter value when leaving it bare would produce invalid YAML.

    Found 2026-08-26, day one of the shadow pilot: `set description "vv pilot:
    live from ..."` wrote the colon-space bare, which makes the WHOLE
    frontmatter block unparseable -- Obsidian's metadataCache returned nothing
    for the note, silently dropping it out of every Bases view. Frontmatter is
    the vault's source of truth, so a malformed write is data loss that looks
    like success (exit 0, plausible output).

    Deliberately conservative in both directions: it must not emit invalid YAML,
    and it must not quote a value that was already fine -- over-quoting changes
    types (`-1` -> "-1") and churns notes on every write.
    """
    if not isinstance(v, str):
        return v
    if v == "":
        return '""'
    if _is_wellformed_quoted(v) or _is_balanced_flow(v):
        return v                       # already valid; leave the author's form
    needs = (": " in v or v.endswith(":") or " #" in v
             or v[0] in _YAML_LEAD or v != v.strip()
             or re.match(r"^[-?:](\s|$)", v)      # indicator only before a space
             or "\n" in v or "\r" in v or "\t" in v
             or any(ord(c) < 0x20 for c in v))
    if not needs:
        return v
    # Escape for a double-quoted scalar. \n and \r must become escapes, not raw
    # bytes: YAML folds a literal newline inside a quoted scalar to a space,
    # which would silently lose the line break.
    esc = (v.replace("\\", "\\\\").replace('"', '\\"')
            .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t"))
    return '"' + esc + '"'


def cmd_set(ref, key, value):
    _dirty_gate()
    value = yaml_scalar(value)
    fp = resolve(ref)
    _sig = file_sig(fp)
    text = read_raw(fp)
    fm, body, tail, bom = split_fm_full(text)
    eol = eol_of(text)
    if fm is None:
        atomic_write(fp, bom + f"---{eol}{key}: {value}{eol}---{eol}" + text[len(bom):], expect_sig=_sig)
    else:
        fm_lines = fm.replace("\r\n", "\n").split("\n")
        if block_scalar_key(fm_lines, key):
            die(f"refused: '{key}' has a multi-line/block value — next: edit the note directly (set would orphan continuation lines)")
        pat = re.compile(rf"^{re.escape(key)}:")
        hit = [i for i, l in enumerate(fm_lines) if pat.match(l)]
        if hit:
            fm_lines[hit[0]] = f"{key}: {value}"
        else:
            fm_lines.append(f"{key}: {value}")
        atomic_write(fp, bom + "---" + eol + eol.join(fm_lines) + eol + "---" + tail + body, expect_sig=_sig)
    out(f"set {key}={value} in {rel(fp)}")

def cmd_unset(ref, key):
    _dirty_gate()
    fp = resolve(ref)
    _sig = file_sig(fp)
    text = read_raw(fp)
    fm, body, tail, bom = split_fm_full(text)
    if fm is None:
        die(f"not-found: no frontmatter in {rel(fp)}")
    eol = eol_of(text)
    fm_lines = fm.replace("\r\n", "\n").split("\n")
    if block_scalar_key(fm_lines, key):
        die(f"refused: '{key}' has a multi-line/block value — next: edit the note directly (unset would orphan continuation lines)")
    kept = [l for l in fm_lines if not re.match(rf"^{re.escape(key)}:", l)]
    if kept == fm_lines:
        die(f"not-found: no key {key} in {rel(fp)}")
    atomic_write(fp, bom + "---" + eol + eol.join(kept) + eol + "---" + tail + body, expect_sig=_sig)
    out(f"unset {key} in {rel(fp)}")

def cmd_new(*args):
    _dirty_gate()
    path, template, kv = None, None, {}
    it = iter(args)
    for a in it:
        if a == "--template":
            template = next(it)
        elif a.startswith("--"):
            kv[a[2:]] = next(it)
        elif path is None:
            path = a
    if not path:
        die("usage: new PATH [--template NAME] [--key value ...]")
    fp = contain(path if path.endswith(".md") else path + ".md")
    if os.path.exists(fp):
        die(f"exists: {rel(fp)} — next: pick another name or edit it")
    d = os.path.dirname(fp)
    if d:
        os.makedirs(d, exist_ok=True)
    content = ""
    if template:
        hits = sorted(glob.glob(os.path.join(VAULT, "Templates", "**", template + "*.md"), recursive=True))
        if not hits:
            die(f"not-found: no template matching '{template}' under Templates/")
        content = read_raw(hits[0])
    missing = []
    for k, v in kv.items():
        pat = re.compile(rf"^{re.escape(k)}:[^\r\n]*", re.M)
        m = pat.search(content)
        if m:
            content = content[:m.start()] + f"{k}: {v}" + content[m.end():]  # literal, no re.sub template
        else:
            missing.append((k, v))
    if missing and content.lstrip(BOM).startswith("---"):
        # template HAS frontmatter: insert the new keys into it rather than dropping them
        fm, body, tail, bom = split_fm_full(content)
        if fm is not None:
            eolc = eol_of(content)
            fm_lines = fm.replace("\r\n", "\n").split("\n") + [f"{k}: {v}" for k, v in missing]
            content = bom + "---" + eolc + eolc.join(fm_lines) + eolc + "---" + tail + body
            missing = []
    kv = dict(missing) if missing else ({} if content.lstrip(BOM).startswith("---") else kv)
    if kv and not content.lstrip(BOM).startswith("---"):
        fmb = "\n".join(f"{k}: {v}" for k, v in kv.items())
        content = f"---\n{fmb}\n---\n" + content
    atomic_write(fp, content)
    out(f"created {rel(fp)}")

def bare_resolves(from_fp, tgt_fp, idx):
    """Does a bare [[basename]] written in from_fp resolve to tgt_fp? Obsidian's
    duplicate-basename rule, probed via metadataCache.getFirstLinkpathDest on the
    live vault (2026-08-26): (1) a candidate in the SAME FOLDER as the linking note
    wins; (2) otherwise the shortest vault-relative path wins; (3) an exact length
    tie is cache-insertion order in the app — unreproducible outside it, so vv uses
    lexicographic as its deterministic stand-in. Unique basenames trivially win."""
    base = os.path.basename(tgt_fp)[:-3].lower()
    cands = idx.get(base, [])
    if len(cands) <= 1:
        return True
    from_dir = os.path.dirname(os.path.abspath(from_fp))
    same_dir = [c for c in cands if os.path.dirname(os.path.abspath(c)) == from_dir]
    pool = same_dir or cands
    return min(pool, key=lambda p: (len(rel(p)), rel(p))) == tgt_fp

def link_matches(from_fp, kind, target, tgt_fp, tgt_base, tgt_rel_noext, idx):
    """THE definition of 'this link resolves to that note'. Used by backlinks,
    orphans, impact and rename so they can never disagree. idx = basename_index()."""
    t = target.strip().lower()
    if kind == "wiki":
        t_noext = t[:-3] if t.endswith(".md") else t
        # a token equal to the basename is a BARE link (winner rules apply) even
        # for a root-level note where basename == rel path — never a path form
        if t_noext == tgt_base:
            return bare_resolves(from_fp, tgt_fp, idx)
        return t_noext == tgt_rel_noext
    import urllib.parse
    dec = urllib.parse.unquote(target.strip())
    cand = os.path.normpath(os.path.join(os.path.dirname(from_fp), dec))
    return os.path.abspath(cand) == os.path.abspath(tgt_fp) or \
        os.path.normpath(os.path.join(VAULT, dec)) == tgt_fp

def cmd_backlinks(ref):
    fp = resolve(ref)
    tgt_base = os.path.basename(fp)[:-3].lower()
    tgt_rel_noext = rel(fp)[:-3].lower()
    idx = basename_index()
    hits = []
    for p, _i, kind, t in scan_links(needle=tgt_base):
        if p == fp or p in hits:
            continue
        if link_matches(p, kind, t, fp, tgt_base, tgt_rel_noext, idx):
            hits.append(p)
    for p in sorted(hits):
        out(rel(p))
    out(f"({len(hits)} backlinks)")

def cmd_links(ref):
    fp = resolve(ref)
    seen = []
    for _, kind, t in link_targets_in(read_raw(fp)):
        if kind == "wiki" and t not in seen:
            seen.append(t)
    for l in seen:
        out(l)
    out(f"({len(seen)} links)")

def cmd_orphans(folder=""):
    root = contain(folder) if folder else VAULT
    files = list(md_files())
    idx = basename_index()
    # a note is linked if ANY note resolves a link to it — using the SAME winner
    # rules as backlinks: a bare [[Dup]] rescues only the note it resolves to,
    # never every duplicate (they disagreed before 2026-08-26).
    import urllib.parse
    path_targets = set()
    bare_by_name = {}
    for p, _i, kind, t in scan_links():
        tl = t.strip().lower()
        if kind == "md":
            dec = urllib.parse.unquote(tl)
            dec = dec[:-3] if dec.endswith(".md") else dec
            path_targets.add(os.path.normpath(os.path.join(os.path.dirname(rel(p)), dec)).lower())
            path_targets.add(os.path.normpath(dec).lower())
            continue
        tl = tl[:-3] if tl.endswith(".md") else tl
        if "/" in tl:
            path_targets.add(tl)
        else:
            bare_by_name.setdefault(tl, []).append(p)
    n = 0
    for p in sorted(files):
        if not (p == root or p.startswith(root + os.sep) or not folder):
            continue
        base = os.path.basename(p)[:-3].lower()
        rel_noext = rel(p)[:-3].lower()
        linked = rel_noext in path_targets or \
            any(src != p and bare_resolves(src, p, idx) for src in bare_by_name.get(base, ()))
        if not linked:
            out(rel(p)); n += 1
    out(f"({n} orphans)")

def cmd_board(folder, *filters):
    want = dict(f.split("=", 1) for f in filters)
    root = os.path.join(VAULT, folder)
    if not os.path.isdir(root):
        die(f"not-found: no such folder: {folder}")
    rows = []
    for dirpath, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for n in sorted(names):
            if not n.endswith(".md"):
                continue
            fm, _ = split_fm(open(os.path.join(dirpath, n), errors="replace").read())
            props = fm_props(fm)
            if all(props.get(k) == v for k, v in want.items()):
                rows.append((n[:-3], props.get("status", "-"), props.get("type", "-")))
    for name, status, typ in rows:
        out(f"{status}\t{typ}\t{name}")
    out(f"({len(rows)} notes)")

def cmd_tags(*args):
    from collections import Counter
    c = Counter()
    for p in md_files():
        fm, _ = split_fm(open(p, errors="replace").read())
        props = fm_props(fm)
        t = props.get("tags", "")
        for tag in re.findall(r"[\w/-]+", t):
            c[tag] += 1
    for tag, n in c.most_common(40 if "--counts" in args else 9999):
        out(f"{n}\t{tag}" if "--counts" in args else tag)
    out(f"({len(c)} tags)")

def cmd_props(key, folder=""):
    root = contain(folder) if folder else VAULT
    from collections import Counter
    c = Counter()
    for p in md_files():
        if folder and not (p == root or p.startswith(root + os.sep)):
            continue
        fm, _ = split_fm(open(p, errors="replace").read())
        v = fm_props(fm).get(key)
        if v:
            c[v] += 1
    for v, n in c.most_common():
        out(f"{n}\t{v}")
    out(f"({sum(c.values())} notes with {key})")

def cmd_search(*args):
    if use_rust():
        r = subprocess.run([VRUST, "search", *args], capture_output=True, text=True)
        sys.stdout.write(r.stdout); sys.stderr.write(r.stderr)
        global _out_total
        _out_total += len(r.stdout.encode("utf-8"))
        _log(_out_total, r.returncode); sys.exit(r.returncode)
    k, w, terms = 5, 500, []
    it = iter(args)
    for a in it:
        if a == "--k": k = int(next(it))
        elif a == "--w": w = int(next(it))
        else: terms.append(a.lower())
    if not terms:
        die("usage: search needs a query — next: vv search <terms> [--k N] [--w CHARS]")
    # ranking (adapted from rustdoc search; IDENTICAL in the rust engine):
    #   term with "/"  -> path filter: must be a substring of the vault-relative path
    #   other terms    -> match in the note NAME (+500) and/or content (+1 per hit);
    #                     every term must match somewhere
    # so a note NAMED after the query outranks a long note that merely mentions it.
    # ties: shorter path first, then lexicographic — deterministic across engines.
    path_terms = [t for t in terms if "/" in t]
    body_terms = [t for t in terms if "/" not in t]
    hits = []
    for p in md_files():
        r_ = rel(p)
        if r_.startswith("Sandbox/") or (os.sep + "Sandbox" + os.sep) in p:
            continue  # parity with the rust engine: Sandbox excluded at ANY depth
        rl = r_.lower()
        if not all(t in rl for t in path_terms):
            continue
        try:
            text = open(p, errors="replace").read()
        except OSError:
            continue
        low = text.lower()
        base = os.path.basename(r_)[:-3].lower()
        score, pos, ok = 0, -1, True
        for t in body_terms:
            in_name = t in base
            cnt = low.count(t)
            if not in_name and cnt == 0:
                ok = False
                break
            score += (500 if in_name else 0) + cnt
            if cnt:
                fp_ = low.find(t)
                pos = fp_ if pos < 0 else min(pos, fp_)
        if not ok:
            continue
        start = max(0, pos - w // 4) if pos >= 0 else 0   # name-only match: head of note
        hits.append((score, r_, text[start:start + w].replace("\n", " ¶ ")))
    hits.sort(key=lambda h: (-h[0], len(h[1]), h[1]))
    for score, r_, snip in hits[:k]:
        out(f"== {r_} (score {score})\n{snip}\n")
    out(f"({min(len(hits), k)} of {len(hits)} matches)")

def cmd_daily_append(text):
    _dirty_gate()
    today = datetime.date.today().isoformat()
    hits = glob.glob(os.path.join(VAULT, "Standups", f"*{today}*.md"))
    if not hits:
        die(f"not-found: no standup note for {today} under Standups/ — next: create it, then re-run")
    # Three defects lived in this one line (Codex review 2026-08-26), and this is
    # the most-used writer in the tool:
    #   1. no CAS -- the "every writer is guarded" claim skipped daily-append;
    #   2. plain open() translates newlines, so appending to a CRLF standup
    #      rewrote the WHOLE note as LF while reporting a one-line append;
    #   3. a non-UTF-8 note raised a traceback instead of the documented exit 5.
    # read_raw + file_sig fixes all three by using the same path as every other
    # writer, which is the actual lesson.
    _sig = file_sig(hits[0])
    cur = read_raw(hits[0])
    eol = eol_of(cur)
    sep = "" if cur.endswith(("\n", "\r\n")) else eol
    atomic_write(hits[0], cur + sep + text + eol, expect_sig=_sig)
    out(f"appended to {rel(hits[0])}")

# ================= v1.5: show / deadends / impact / rename / move / lint / doctor =================

# VV_JOURNAL_ROOT: test suites point this at a tempdir so they can never touch
# (or worse, delete) real pending recovery journals
JOURNAL_ROOT = os.environ.get("VV_JOURNAL_ROOT") or os.path.expanduser("~/.cache/vv/journals")

def masked_lines(text):
    """ONE state walk over the note for link scanning: returns (lines, fenced_line_set,
    comment_spans_per_line). Precedence probed against Obsidian 2026-08-26:
    an OPEN html comment owns its lines — a fence marker inside it is literal text
    and the comment still closes at the next --> — while a fence keeps <!-- literal.
    Obsidian's own %% comments are NOT masked (their links index). Frontmatter is
    never fenced (`related:` links are real) but may hold comment markers.
    The closer/opener search runs on inline-code-stripped text (a backticked -->
    does not close — observable-equivalent to the app). parse()/sections keep the
    independent fence_mask: section addressing must not shift under comment rules."""
    lines = text.split("\n")
    fm_end = fm_bounds(lines)
    fenced, cspans = set(), {}
    marker = None        # (char, length) of an open fence
    in_comment = False
    for i, l in enumerate(lines):
        ls = l.rstrip("\r")
        cur, pos = [], 0
        if in_comment:
            scan = strip_inline_code(ls)
            end = scan.find("-->")
            if end == -1:
                cspans[i] = [(0, len(ls))]
                continue
            cur.append((0, end + 3))
            pos = end + 3
            in_comment = False
            # a line where a comment just closed can never open a fence (a fence
            # needs the line start; the --> prefix disqualifies it)
        else:
            m = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", ls) if i >= fm_end else None
            if marker is None:
                # backtick fence info strings may not contain backticks (CommonMark)
                if m and (m.group(1)[0] == "~" or "`" not in m.group(2)):
                    marker = (m.group(1)[0], len(m.group(1)))
                    fenced.add(i)
                    continue
            else:
                fenced.add(i)
                if m and m.group(1)[0] == marker[0] and len(m.group(1)) >= marker[1] \
                        and not m.group(2).strip():
                    marker = None
                continue
            scan = strip_inline_code(ls)
        while True:
            s = scan.find("<!--", pos)
            if s == -1:
                break
            e = scan.find("-->", s + 4)
            if e == -1:
                cur.append((s, len(ls)))
                in_comment = True
                break
            cur.append((s, e + 3))
            pos = e + 3
        if cur:
            cspans[i] = cur
    return lines, fenced, cspans

def scan_links(needle=None):
    """Yield (abs_path, line_idx0, kind, target) for every active link in the vault.

    Uses the Rust engine when present (it does the I/O and lexing; ~2x faster), else the
    Python scanner. Both are held to byte-identical output by tests/test_engine_parity.py —
    the SEMANTICS (ambiguity, .md equivalence, relative resolution) stay here in Python so
    there is only ever one definition of what a link MEANS.

    `needle` prefilters wiki targets by substring (case-insensitive); markdown links are
    always yielded because percent-encoding hides names from a substring filter.
    """
    if use_rust():
        cmd = [VRUST, "linkscan"] + (["--grep", needle] if needle else [])
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            for line in r.stdout.split("\n"):
                if not line:
                    continue
                rel_, ln, kind, tgt = line.split("\t", 3)
                yield os.path.join(VAULT, rel_), int(ln) - 1, ("wiki" if kind == "w" else "md"), tgt
            return
    n = needle.lower() if needle else None
    for p in md_files():
        try:
            text = read_raw(p)
        except SystemExit:
            continue
        for i, kind, tgt in link_targets_in(text):
            if n and kind == "wiki" and n not in tgt.lower():
                continue
            yield p, i, kind, tgt

def code_spans(line):
    """(start, end) of inline code spans, CommonMark-style: a run of N backticks is
    closed only by a run of EXACTLY N. A longer run is skipped WHOLE — its tail is
    never a closer (the old find()-based search accepted one; caught by review
    2026-08-26). Mirrors the Rust engine's loop shape exactly."""
    spans = []
    i, L = 0, len(line)
    while i < L:
        if line[i] == "`":
            n = 0
            while i + n < L and line[i + n] == "`":
                n += 1
            j = i + n
            close = None
            while j < L:
                if line[j] == "`":
                    m = 0
                    while j + m < L and line[j + m] == "`":
                        m += 1
                    if m == n:
                        close = j
                        break
                    j += m
                else:
                    j += 1
            if close is not None:
                spans.append((i, close + n))
                i = close + n
            else:
                i += n
            continue
        i += 1
    return spans

def strip_inline_code(line):
    masked = list(line)
    for a, b in code_spans(line):
        for k in range(a, b):
            masked[k] = "\0"
    return "".join(masked)

# alias may contain single ] chars (only ]] terminates — probed against Obsidian's
# metadataCache 2026-08-26: [[Note|a]b]] links Note, display "a]b").
# md-link: a ] that is itself preceded by ] is a wikilink closer, never [text]( —
# the lookbehind mirrors the Rust engine's skip-past-]] behavior.
LINK_RE = re.compile(r"(!?\[\[)([^\]|#]+)((?:#[^\]|]*)?(?:\|(?:\](?!\])|[^\]])*)?)(\]\])")
MDLINK_RE = re.compile(r"((?<!\])\]\()([^)\s]+\.md)(\))")

def wiki_target(m):
    """Target of a LINK_RE match. Backslash semantics probed against Obsidian's
    metadataCache 2026-08-26: exactly ONE trailing backslash is consumed at a
    boundary — [[Note\\|alias]] escapes the alias pipe, and a stray [[Note\\]]
    resolves to Note. A SECOND backslash stays in the target ([[N\\\\|a]] targets
    'N\\'), which resolves to nothing in the app since backslash is illegal in note
    names — so it naturally matches no note here either. Returns (target,
    escaped_pipe) so rewriters can re-emit the pipe escape."""
    t = m.group(2).strip()
    esc = t.endswith("\\") and m.group(3).startswith("|")
    if t.endswith("\\"):
        t = t[:-1].rstrip()
    return t, esc

def link_targets_in(text):
    """Yield (line_idx, kind, target) for active links; fenced lines, inline code
    and HTML comments excluded. A link that OVERLAPS a comment span at any point
    (not just its start) is inert — [[A <!-- x --> B]] is not a link. Empty targets
    ([[ ]], [[\\]]) are skipped, mirroring the Rust engine."""
    lines, fenced, cmask = masked_lines(text)
    for i, l in enumerate(lines):
        if i in fenced:
            continue
        scan = strip_inline_code(l)
        spans = cmask.get(i, [])
        def target_clear(m):
            # only the TARGET decides: a comment overlapping the alias leaves the
            # link real ([[N|a <!-- x --> b]] links N, probed 2026-08-26); one
            # overlapping the target does not. Same rule as the Rust engine's
            # NUL-in-target check.
            a2, b2 = m.start(2), m.end(2)
            return not any(a < b2 and a2 < b for a, b in spans)
        for m in LINK_RE.finditer(scan):
            t = wiki_target(m)[0]
            # "\0" in target = it overlaps an inline-code span — same drop in Rust
            if t and "\0" not in t and target_clear(m):
                yield i, "wiki", t
        for m in MDLINK_RE.finditer(scan):
            if "\0" not in m.group(2) and target_clear(m):
                yield i, "md", m.group(2)

def basename_index():
    """lowercased basename -> [paths]"""
    idx = {}
    for p in md_files():
        idx.setdefault(os.path.basename(p)[:-3].lower(), []).append(p)
    return idx

def occurrences(source_fp, include_bare=True):
    """Files with rewritable link occurrences of source. Returns (hits, ambiguous_note).
    hits = {path: n_occurrences}. Bare-name links count only if source basename is unique."""
    src_base = os.path.basename(source_fp)[:-3].lower()
    src_rel_noext = rel(source_fp)[:-3].lower()
    idx = basename_index()
    ambiguous = len(idx.get(src_base, [])) > 1
    hits = {}
    for p, _i, kind, tgt in scan_links(needle=src_base):
        t = tgt.strip().lower()
        if kind == "wiki":
            t_noext = t[:-3] if t.endswith(".md") else t  # [[Note.md]] is the same target as [[Note]]
            if t_noext == src_base:   # bare form — winner rules, even for root notes
                if include_bare and bare_resolves(p, source_fp, idx):
                    hits[p] = hits.get(p, 0) + 1
            elif t_noext == src_rel_noext:
                hits[p] = hits.get(p, 0) + 1
        elif link_matches(p, kind, tgt, source_fp, src_base, src_rel_noext, idx):
            hits[p] = hits.get(p, 0) + 1
    return hits, ambiguous

def cmd_show(ref, *args):
    max_bytes, start = 4000, "H0"
    it = iter(args)
    for a in it:
        if a == "--max-bytes": max_bytes = int(next(it))
        elif a == "--from": start = next(it)
    lines, secs = parse(read_raw(resolve(ref)))
    started = False
    used = 0
    for s in secs:
        if s["id"] == start:
            started = True
        if not started:
            continue
        t = sec_text(lines, s)
        # BYTES, not characters: `show` exists to bound context cost, and len()
        # on a non-ASCII section under-counts by up to 4x. Measured 2026-08-26:
        # --max-bytes 1000 emitted 20,009 bytes. Same chars-vs-bytes class as the
        # metrics fix earlier that day -- the metric was corrected, the ENFORCER
        # was not.
        tb = len(t.encode("utf-8"))
        if tb == 0:
            continue          # an empty preamble section still cost a newline
        # `used` counts EMITTED bytes, newline included: out() adds one per call,
        # and leaving it out put the total over the cap by exactly that much.
        if used + tb + 1 > max_bytes:
            if used > 0:
                more = (f"[more: {s['id']} '{s['title']}' {tb}B "
                        f"— continue: vv show {ref} --from {s['id']}]")
                if used + len(more.encode("utf-8")) + 1 > max_bytes:
                    more = "[more]"
                # If even "[more]" does not fit, it is still emitted: a budget
                # under ~32B cannot carry a continuation marker, and overshooting
                # by a few bytes there is strictly better than truncating
                # SILENTLY, which would look like the note simply ended.
                out(more)
                break
            # A single oversized section used to bypass the cap entirely (the
            # guard required used > 0), so the advertised ceiling was not a
            # ceiling. Truncate on a UTF-8 boundary instead: the cap holds AND
            # the caller still makes progress, which returning nothing would not.
            marker = (f"[truncated: {s['id']} '{s['title']}' is {tb}B of a {max_bytes}B "
                      f"budget — read it whole with: vv read {ref} {s['id']}]")
            # The marker and the newlines out() adds count against the budget
            # too, or the ceiling is still exceeded -- by less, which is the
            # same bug. Two out() calls => two newlines.
            room = max_bytes - used - len(marker.encode("utf-8")) - 2
            if room <= 0:
                marker = "[truncated]"
                room = max_bytes - used - len(marker.encode("utf-8")) - 2
            if room > 0:
                # sec_text already ends with a newline and out() adds one, so the
                # slice is rstripped -- otherwise the total lands 1 byte over.
                out(t.encode("utf-8")[:room].decode("utf-8", errors="ignore").rstrip("\n"))
            out(marker)
            used = max_bytes
            break
        out(t)
        used += tb + 1
    if not started:
        die(f"not-found: no section {start} — next: vv outline NOTE")

def cmd_deadends():
    n = 0
    for p in md_files():
        if not any(True for _ in link_targets_in(open(p, errors="replace").read())):
            out(rel(p)); n += 1
    out(f"({n} deadends)")

def _git(args_):
    return subprocess.run(["git", "-C", VAULT] + args_, capture_output=True, text=True).stdout.strip()

def cmd_impact(ref, *args):
    fp = resolve(ref)
    hits, ambiguous = occurrences(fp)
    out(f"note: {rel(fp)}")
    if ambiguous:
        out("AMBIGUOUS: basename shared with another note — bare-name links NOT counted or rewritable")
    out(f"incoming-link files: {len(hits)} ({sum(hits.values())} occurrences)")
    for p, n in sorted(hits.items(), key=lambda kv: -kv[1])[:15]:
        out(f"  {n}\t{rel(p)}")
    dirty = _git(["status", "--porcelain", "--", rel(fp)])
    out(f"git: {'DIRTY — uncommitted changes' if dirty else 'clean'}")
    fm, _ = split_fm(read_raw(fp))
    props = fm_props(fm)
    out(f"frontmatter: type={props.get('type','-')} status={props.get('status','-')}")

def _vault_journal_root():
    """Journals are scoped per vault (sqlx keeps its migration bookkeeping inside
    the database it describes — same idea): a leftover journal for another vault
    must neither block this one nor ever be rolled back into it."""
    vid = hashlib.sha256(_VAULT_REAL.encode()).hexdigest()[:12]
    return os.path.join(JOURNAL_ROOT, vid)

def _pending_journals():
    root = _vault_journal_root()
    return sorted(glob.glob(os.path.join(root, "*"))) if os.path.isdir(root) else []

def _dirty_gate():
    """A pending journal means an earlier apply did not finish — no write command
    runs until it is resolved (sqlx's Dirty-version gate, adapted). Exit 4 matches
    doctor's unresolved-journal code."""
    js = _pending_journals()
    if js:
        die(f"dirty: pending journal {os.path.basename(js[0])} — resolve with vv doctor "
            f"(rollback or discard) before writing", 4)

def _journal_write_manifest(jdir, manifest):
    """Replace the manifest atomically: a torn manifest is an unrecoverable
    journal, which is strictly worse than the crash it exists to survive."""
    tmp = os.path.join(jdir, "manifest.json.tmp")
    with open(tmp, "w") as f:
        json.dump(manifest, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, os.path.join(jdir, "manifest.json"))


def _journal_phase(jdir, phase, **fields):
    """Persist transaction PHASE (and any endpoints) before the step it describes.

    Everything but the file backups used to live in process memory, so a hard
    crash left recovery blind: it could not know a rename had happened, nor what
    THIS process had written. See _journal_rollback for what that cost.
    """
    mpath = os.path.join(jdir, "manifest.json")
    try:
        man = json.load(open(mpath))
    except (OSError, ValueError):
        return
    man["phase"] = phase
    man.update(fields)
    _journal_write_manifest(jdir, man)


def _journal_written(jdir, rel_path, sha):
    """Append what we just wrote, so recovery after a crash can CLASSIFY.

    Append-only and flushed per line: a partial last line is discarded on read,
    which is the correct failure mode -- an unrecorded write is treated as
    someone else's bytes and left alone rather than clobbered.
    """
    try:
        with open(os.path.join(jdir, "written.jsonl"), "a") as f:
            f.write(json.dumps({"rel": rel_path, "sha256": sha}) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        pass


def _journal_written_map(jdir):
    out_ = {}
    try:
        for line in open(os.path.join(jdir, "written.jsonl")):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue          # torn final line from a crash mid-append
            out_[rec["rel"]] = rec["sha256"]
    except OSError:
        return None               # no record at all -> caller decides
    return out_


def _journal_start(name, files, src=None, dest=None):
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    jdir = os.path.join(_vault_journal_root(), f"{ts}-{name}")
    os.makedirs(jdir)
    manifest = {"op": name, "ts": ts, "vault": _VAULT_REAL, "files": {}, "sha256": {},
                "phase": "prepare", "src": src, "dest": dest}
    import shutil as _sh
    for idx, fp in enumerate(files):  # index key — collision-proof (rel-path %2F encoding was not)
        key = f"f{idx}.bak"
        _sh.copy2(fp, os.path.join(jdir, key))
        manifest["files"][rel(fp)] = key
        with open(fp, "rb") as f:
            manifest["sha256"][rel(fp)] = hashlib.sha256(f.read()).hexdigest()
    _journal_write_manifest(jdir, manifest)
    return jdir

def _journal_rollback(jdir, written=None):
    """Restore journaled files — but CLASSIFY each first (adapted from sqlx's
    checksum checks): bytes that are neither the journaled original nor our own
    write belong to another writer and are left alone, never clobbered.
    `written` maps rel-path -> sha256 of what THIS process wrote (None = restore
    unconditionally, the pre-classification behavior). Returns rel-paths left."""
    mpath = os.path.join(jdir, "manifest.json")
    if not os.path.exists(mpath):
        # Killed during _journal_start, before the first manifest existed. There
        # is nothing to restore and nothing was written yet; refuse loudly rather
        # than raising a traceback out of doctor.
        die(f"conflict: journal {os.path.basename(jdir)} has no manifest (killed during "
            f"preparation) — next: vv doctor --discard", 1)
    man = json.load(open(mpath))
    import shutil as _sh
    left = []

    # A hard crash between os.rename() and _journal_done left the note at BOTH
    # ends: recovery restored the backup at the source path but knew nothing of
    # the destination, then deleted the journal and exited successfully. The
    # endpoints are now durable, so reverse the move FIRST -- before restoring
    # any backup -- exactly as the in-process handler does.
    src_r, dest_r = man.get("src"), man.get("dest")
    if src_r and dest_r:
        src_p, dest_p = os.path.join(VAULT, src_r), os.path.join(VAULT, dest_r)
        if os.path.exists(dest_p) and not os.path.exists(src_p):
            try:
                os.rename(dest_p, src_p)
            except OSError:
                left.append(dest_r)

    # After a crash the caller has no in-memory `written` map, and passing None
    # meant "restore unconditionally" -- which would clobber an edit Obsidian
    # made AFTER the crash. The per-write record on disk restores classification.
    if written is None:
        written = _journal_written_map(jdir)
    for r_, key in man["files"].items():
        live_p = os.path.join(VAULT, r_)
        try:
            with open(live_p, "rb") as f:
                live_h = hashlib.sha256(f.read()).hexdigest()
        except OSError:
            live_h = None
        if live_h is not None and live_h == man.get("sha256", {}).get(r_):
            continue   # untouched since journaling — nothing to undo
        if written is not None:
            if r_ not in written:
                # We never wrote it; the change is someone else's — leave it.
                #
                # A review proposed reporting this as a conflict, on the grounds
                # that a changed-but-unrecorded file might be OUR partial write
                # whose log entry never landed. Write-ahead logging closes that
                # window directly -- the intended hash is now recorded BEFORE the
                # file is touched -- so this branch can only mean a third party,
                # and a journaled-but-never-written file being edited externally
                # is normal, not a conflict. Pinned by V12c/V12d.
                continue
            if live_h is not None and live_h != written[r_]:
                left.append(r_)   # our write was overwritten since — do not clobber
                continue
        _sh.copy2(os.path.join(jdir, key), live_p)
    return left

def _journal_done(jdir):
    import shutil as _sh
    _sh.rmtree(jdir)

def _rewrite_links(text, source_fp, new_rel_noext, rename_base, linking_fp=None):
    """Rewrite active links to source. new_rel_noext = new vault-relative path w/o .md;
    rename_base = new bare name (None if unchanged). linking_fp = the file being rewritten
    (so relative md-links can be rewritten relatively, matching how occurrences() counts them)."""
    src_base = os.path.basename(source_fp)[:-3]
    src_rel_noext = rel(source_fp)[:-3]
    new_fp_abs = os.path.join(VAULT, new_rel_noext + ".md")
    lines, fenced, cmask = masked_lines(text)
    changed = 0
    for i, l in enumerate(lines):
        if i in fenced:
            continue
        # a link the scanner doesn't count must not be rewritten either:
        # inline code spans + HTML-comment spans (same masking as link_targets_in),
        # judged by link start AND target span — an overlap that only touches the
        # alias leaves the link real, same as the scanner
        spans = code_spans(l) + cmask.get(i, [])
        def in_span(pos):
            return any(a <= pos < b for a, b in spans)
        def blocked(m):
            a2, b2 = m.start(2), m.end(2)
            return in_span(m.start()) or any(a < b2 and a2 < b for a, b in spans)
        def wiki_repl(m):
            tgt, esc = wiki_target(m)
            pipe_esc = "\\" if esc else ""   # keep [[Note\|alias]] escaped after rewrite
            t = tgt.lower()
            ext = ".md" if t.endswith(".md") else ""   # preserve the author's [[Note.md]] style
            t_noext = t[:-3] if ext else t
            if t_noext == src_base.lower():   # bare form — never treated as a path form
                if rename_base:
                    return m.group(1) + rename_base + ext + pipe_esc + m.group(3) + m.group(4)
                return None   # move keeps bare links: they still resolve by name
            if t_noext == src_rel_noext.lower():
                return m.group(1) + new_rel_noext + ext + pipe_esc + m.group(3) + m.group(4)
            return None
        def md_repl(m):
            import urllib.parse
            dec = urllib.parse.unquote(m.group(2))
            is_relative = not os.path.isabs(dec) and (dec.startswith("./") or dec.startswith("../")
                          or (linking_fp and os.path.normpath(os.path.join(os.path.dirname(linking_fp), dec)) == source_fp
                              and os.path.normpath(os.path.join(VAULT, dec)) != source_fp))
            matches_root = os.path.normpath(os.path.join(VAULT, dec)) == source_fp
            matches_rel = linking_fp and os.path.normpath(os.path.join(os.path.dirname(linking_fp), dec)) == source_fp
            if matches_rel and is_relative and linking_fp:
                newrel = os.path.relpath(new_fp_abs, os.path.dirname(linking_fp))
                return m.group(1) + urllib.parse.quote(newrel) + m.group(3)
            if matches_root:
                return m.group(1) + urllib.parse.quote(new_rel_noext + ".md") + m.group(3)
            return None
        # ONE position-stable pass: all matches and spans are located on the ORIGINAL
        # line, then replacements apply right-to-left so earlier offsets never go
        # stale (sequential re.sub passes consulted dead coordinates — review
        # 2026-08-26, mixed active/inert lines made valid renames abort).
        repls = []
        for regex, repl in ((LINK_RE, wiki_repl), (MDLINK_RE, md_repl)):
            for m in regex.finditer(l):
                if blocked(m):
                    continue
                r = repl(m)
                if r is not None:
                    repls.append((m.start(), m.end(), r))
        for a, b, r in sorted(repls, reverse=True):
            l = l[:a] + r + l[b:]
        changed += len(repls)
        lines[i] = l
    return "\n".join(lines), changed

def _plan_token(args):
    """The 8-hex token following --apply, if any: `--apply <sha8>` binds the apply
    to the exact previewed plan (sqlx checksums an applied migration for the same
    reason). Plain --apply keeps the one-shot behavior."""
    a = list(args)
    if "--apply" in a:
        i = a.index("--apply")
        if i + 1 < len(a) and re.fullmatch(r"[0-9a-f]{8}", a[i + 1]):
            return a[i + 1]
    return None

def _do_relocate(ref, dest_rel_noext, apply_, opname, expect_plan=None):
    fp = resolve(ref)
    src_rel = rel(fp)
    # the destination is a WRITE target and gets the same containment as every
    # other write path — an absolute dest or ../ escape must never leave the vault
    new_fp = contain(dest_rel_noext + ".md")
    dest_rel_noext = rel(new_fp)[:-3]
    if os.path.exists(new_fp):
        die(f"exists: target {dest_rel_noext}.md")
    new_base = os.path.basename(dest_rel_noext)
    rename_base = new_base if new_base.lower() != os.path.basename(fp)[:-3].lower() else None
    idx = basename_index()
    if rename_base and rename_base.lower() in idx:
        die(f"refused: another note already has basename '{new_base}' — bare links would be ambiguous")
    hits, ambiguous = occurrences(fp, include_bare=bool(rename_base))
    if ambiguous:
        # rename: bare links can't be rewritten safely. move: bare links aren't
        # rewritten at all, but relocating one duplicate CHANGES which note the
        # same-folder/shortest-path tiers resolve them to — silent repointing.
        die(f"refused: source basename is ambiguous in vault — next: resolve duplicate notes first")
    # plan digest: operation + destination + every affected file's byte hash.
    # `--apply <digest>` then executes exactly the previewed blast radius or
    # exits stale — an edit or new link between preview and apply changes it.
    canon = [opname, src_rel, dest_rel_noext]
    for p in sorted(hits, key=rel):
        with open(p, "rb") as f:
            canon.append(f"{rel(p)}:{hits[p]}:{hashlib.sha256(f.read()).hexdigest()}")
    plan_id = sha8("\n".join(canon))
    out(f"plan {plan_id}: {opname} {src_rel} -> {dest_rel_noext}.md")
    out(f"files to rewrite: {len(hits)} ({sum(hits.values())} link occurrences)")
    for p, n in sorted(hits.items()):
        out(f"  {n}\t{rel(p)}")
    if not apply_:
        out(f"(dry-run — apply with: --apply, or --apply {plan_id} to bind to THIS plan)")
        return
    if expect_plan and expect_plan != plan_id:
        die(f"stale: plan is now {plan_id}, you reviewed {expect_plan} — next: re-run the dry-run", 3)
    # journal every file that will be written, plus the moved file itself (once)
    _dirty_gate()
    journal_targets = list(hits.keys()) + ([fp] if fp not in hits else [])
    # src/dest are recorded IN the journal so a hard crash mid-rename is
    # recoverable: without them, recovery cannot know the note moved.
    jdir = _journal_start(opname, journal_targets,
                          src=src_rel, dest=rel(new_fp))
    renamed = False
    written = {}   # rel-path -> sha256 of what WE wrote (rollback classifies with it)
    try:
        results = {}
        for p in hits:
            text = read_raw(p)
            new_text, changed = _rewrite_links(text, fp, dest_rel_noext, rename_base, linking_fp=p)
            if changed != hits[p]:
                raise RuntimeError(f"span mismatch in {rel(p)}: planned {hits[p]}, rewrote {changed}")
            results[p] = new_text
        fault_after = int(os.environ.get("VV_FAULT_AFTER", "-1"))
        # VV_FAULT_KIND=exit injects SystemExit instead — pins the BaseException
        # rollback path (a real read_raw exit-5 mid-apply takes it too)
        fault_exc = SystemExit if os.environ.get("VV_FAULT_KIND") == "exit" else RuntimeError
        for wi, (p, new_text) in enumerate(results.items()):
            if 0 <= fault_after <= wi:
                raise fault_exc(f"INJECTED FAULT after {wi} writes")
            # WRITE-AHEAD: record the INTENDED hash before touching the file.
            # The first version logged after the write, so a kill between
            # os.replace() and the log left a changed-but-unrecorded file --
            # which rollback then treated as another writer's bytes and skipped,
            # deleting the journal and reporting "originals restored" while vv's
            # own rewrite stayed on disk (Codex review 2026-08-26). Logging first
            # can only over-record, and an over-recorded file is one we compare
            # and correctly leave alone.
            _sha = hashlib.sha256(new_text.encode()).hexdigest()
            _journal_written(jdir, rel(p), _sha)
            atomic_write(p, new_text)
            written[rel(p)] = _sha
        if fault_after == len(results):
            raise RuntimeError(f"INJECTED FAULT after {len(results)} writes (pre-rename)")
        d = os.path.dirname(new_fp)
        if d:
            os.makedirs(d, exist_ok=True)
        # Phase is persisted BEFORE the rename, so a kill during it is still
        # recoverable: recovery reverses dest->src on the durable endpoints.
        _journal_phase(jdir, "renaming")
        os.rename(fp, new_fp)
        renamed = True
        _journal_phase(jdir, "renamed")
        if os.environ.get("VV_FAULT_KILL_AFTER_RENAME"):
            # Hard kill: os._exit bypasses `except BaseException`, finally blocks
            # and atexit, which is the whole point -- the existing injectors raise
            # catchable exceptions, so the in-process handler always tidied up and
            # the crash-recovery path was never exercised (review seat 2026-08-26).
            os._exit(137)
        # verification: read each file at its CURRENT location (source is now new_fp)
        old_base = os.path.basename(src_rel)[:-3].lower()
        old_rel_noext = src_rel[:-3].lower()
        stale = 0
        for p in results:
            cur_path = new_fp if p == fp else p
            for _, kind, tgt in link_targets_in(read_raw(cur_path)):
                t = tgt.strip().lower()
                t = t[:-3] if t.endswith(".md") else t
                if kind == "wiki" and (t == old_rel_noext or (rename_base and t == old_base)):
                    stale += 1
        if stale:
            raise RuntimeError(f"verification failed: {stale} stale links remain")
        _journal_done(jdir)
        out(f"applied: {len(results)} files rewritten, note {opname}d, verification clean")
    except BaseException as e:
        # BaseException, not Exception: read_raw exits via SystemExit on a non-UTF-8
        # file and Ctrl-C raises KeyboardInterrupt — both must roll back too.
        # Reverse the rename FIRST (if it happened) so journal-restore never leaves a duplicate.
        if renamed and os.path.exists(new_fp) and not os.path.exists(fp):
            os.rename(new_fp, fp)
        left = _journal_rollback(jdir, written)
        if left:
            die(f"conflict: ROLLED BACK ({e}); NOT restored (changed by another writer, journal kept "
                f"at {jdir}): {', '.join(left)}")
        _journal_done(jdir)   # clean rollback = nothing pending; don't trip the dirty gate
        die(f"rolled-back: ({e}); originals restored")

def cmd_rename(ref, new_name, *args):
    fp = resolve(ref)
    dest = os.path.join(os.path.dirname(rel(fp)), new_name[:-3] if new_name.endswith(".md") else new_name)
    _do_relocate(ref, dest, "--apply" in args, "rename", _plan_token(args))

def cmd_move(ref, dest_folder, *args):
    fp = resolve(ref)
    dest = os.path.join(dest_folder.rstrip("/"), os.path.basename(rel(fp))[:-3])
    _do_relocate(ref, dest, "--apply" in args, "move", _plan_token(args))

def cmd_lint(*args):
    canonical = os.path.join(VAULT, ".claude/skills/vault-lint/vault_lint.py")
    if "--quick" not in args and os.path.exists(canonical):
        r = subprocess.run([sys.executable, canonical] + [a for a in args], cwd=VAULT)
        _log(_out_total, r.returncode); sys.exit(r.returncode)
    # --quick: native broken-wikilink scan (fence/inline-code aware, path-style by last segment)
    limit = 50
    if "--limit" in args:
        limit = int(args[list(args).index("--limit") + 1])
    idx = basename_index()
    stems = set(idx.keys())
    for p in sorted(glob.glob(os.path.join(VAULT, "Templates/**/*.md"), recursive=True)):
        stems.add(os.path.basename(p)[:-3].lower())
    findings = []
    for p in md_files():
        try:
            text = read_raw(p)
        except SystemExit:
            continue
        for i, kind, tgt in link_targets_in(text):
            if kind != "wiki":
                continue
            t = tgt.strip().lower()
            if t.startswith(("reference-", "feedback-", "project-", "user-")):
                findings.append(("memory-slug", f"{rel(p)}:{i+1}", tgt))
                continue
            last = t.split("/")[-1]
            last = last[:-3] if last.endswith(".md") else last
            if last not in stems:
                findings.append(("broken-link", f"{rel(p)}:{i+1}", tgt))
        # an UNESCAPED alias pipe inside a wikilink on a table row: the table splits
        # the cell at the pipe, so Obsidian renders NO link at all (oracle 2026-08-26).
        # The fix is \| — flag it, since the note renders broken in the app.
        lines, fenced, cmask = masked_lines(text)
        # a line belongs to a table if a delimiter row (|---|---| etc.) is adjacent
        # within its contiguous block — tables need no leading pipes (review 2026-08-26).
        # A real delimiter row always contains a pipe (single-column: |---|); a bare
        # --- is a frontmatter fence or hr, never a table — and frontmatter is
        # excluded outright so its quoted alias pipes can't read as cells (2026-08-26)
        fm_end = fm_bounds(lines)
        delim = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
        delim_rows = {i for i, l in enumerate(lines)
                      if i >= fm_end and i not in fenced and "|" in l and delim.match(l.rstrip("\r"))}
        table_rows = set()
        for d in delim_rows:
            j = d - 1                      # header row above
            if j >= 0 and "|" in lines[j]:
                table_rows.add(j)
            j = d + 1                      # body rows below, until a line with no pipe
            while j < len(lines) and "|" in lines[j] and j not in delim_rows:
                table_rows.add(j); j += 1
        for i, l in enumerate(lines):
            if i in fenced or i not in table_rows:
                continue
            for m in LINK_RE.finditer(strip_inline_code(l)):
                a2, b2 = m.start(2), m.end(2)
                if any(a < b2 and a2 < b for a, b in cmask.get(i, [])):
                    continue
                # unescaped pipe anywhere in the link ([[N|a]] AND [[N#S|a]]) — an
                # escaped \| is fine, and \\| is an escaped backslash + escaped pipe
                if re.search(r"(?<!\\)\|", m.group(2) + m.group(3)):
                    findings.append(("table-pipe", f"{rel(p)}:{i+1}", m.group(2).strip()))
    # output is context: report every finding's COUNT, but print at most `limit` lines
    from collections import Counter
    by_kind = Counter(f[0] for f in findings)
    for kind, loc, tgt in findings[:limit]:
        out(f"{kind}\t{loc}\t[[{tgt}]]")
    summary = " ".join(f"{k}={v}" for k, v in sorted(by_kind.items()))
    shown = min(len(findings), limit)
    more = f" — {len(findings) - shown} more, raise with --limit N" if len(findings) > shown else ""
    out(f"({len(findings)} findings: {summary or 'none'}; showing {shown}{more})")

def cmd_doctor(*args):
    js = _pending_journals()
    if "--rollback" in args or "--discard" in args:
        if not js:
            die("not-found: no pending journal for this vault")
        jdir = js[0]
        if "--discard" in args:
            _journal_done(jdir)
            out(f"discarded journal {os.path.basename(jdir)} (no files restored)")
        else:
            left = _journal_rollback(jdir)   # no `written` info after a crash: restore
            if left:
                die(f"conflict: rollback incomplete — left alone (bytes match neither original nor "
                    f"journal): {', '.join(left)}; journal kept at {jdir}")
            _journal_done(jdir)
            out(f"rolled back journal {os.path.basename(jdir)}; originals restored")
        _log(_out_total); return
    out(f"vault: {VAULT} ({'ok' if os.path.isdir(VAULT) else 'MISSING'})")
    out(f"engine: {'vrust ok' if os.path.exists(VRUST) else 'vrust MISSING (python fallback)'}")
    dirty = _git(["status", "--porcelain"])
    out(f"git: {'clean' if not dirty else f'{len(dirty.splitlines())} dirty paths'}")
    out(f"journals: {'none pending' if not js else 'UNRESOLVED (writes blocked): '
        + ', '.join(os.path.basename(j) for j in js) + ' — vv doctor --rollback | --discard'}")
    try:
        with open(METRICS, "a"):
            pass
        out("metrics: writable")
    except OSError:
        out("metrics: NOT writable")
    if js:
        _log(_out_total, 4, "dirty"); sys.exit(4)

CMDS = {
    "outline": cmd_outline, "read": cmd_read, "head": cmd_head, "resolve": cmd_resolve,
    "patch": cmd_patch, "appendsec": cmd_appendsec, "append": cmd_append,
    "set": cmd_set, "unset": cmd_unset, "new": cmd_new,
    "backlinks": cmd_backlinks, "links": cmd_links, "orphans": cmd_orphans,
    "board": cmd_board, "tags": cmd_tags, "props": cmd_props,
    "search": cmd_search, "daily-append": cmd_daily_append,
    "show": cmd_show, "deadends": cmd_deadends, "impact": cmd_impact,
    "rename": cmd_rename, "move": cmd_move, "lint": cmd_lint, "doctor": cmd_doctor,
}

def _check_arity(cmd, fn, args):
    """Positional-arg validation at the boundary, so an INTERNAL TypeError is a
    defect (traceback), never mislabeled as user error (review 2026-08-26)."""
    import inspect
    ps = [p for p in inspect.signature(fn).parameters.values()]
    var = any(p.kind == p.VAR_POSITIONAL for p in ps)
    pos = [p for p in ps if p.kind == p.POSITIONAL_OR_KEYWORD]
    req = len([p for p in pos if p.default is p.empty])
    hi = None if var else len(pos)
    if len(args) < req or (hi is not None and len(args) > hi):
        want = f"{req}+" if hi is None else (str(req) if req == hi else f"{req}-{hi}")
        die(f"usage: {cmd} takes {want} positional args, got {len(args)} — "
            f"next: run vv with no args for the command list")

if __name__ == "__main__":
    a = sys.argv[1:]
    if "--vault" in a:
        i = a.index("--vault")
        if i + 1 >= len(a):
            die("usage: --vault requires a path")
        VAULT = os.path.abspath(os.path.expanduser(a[i + 1]))
        if not os.path.isdir(VAULT):
            die(f"not-found: vault directory {VAULT}")
        os.environ["VV_VAULT"] = VAULT   # rust engine subprocesses inherit it
        _VAULT_REAL = os.path.realpath(VAULT)
        a = a[:i] + a[i + 2:]
    if not a:
        sys.exit(__doc__)
    _op = a[0]   # real command, even when --vault preceded it
    fn = CMDS.get(a[0])
    if not fn:
        die(f"usage: unknown command {a[0]} — next: run vv with no args for help")
    _check_arity(a[0], fn, a[1:])
    fn(*a[1:])
    _log(_out_total)
