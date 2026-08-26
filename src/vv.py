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

def _log(out_bytes):
    try:
        with open(METRICS, "a") as f:
            f.write(json.dumps({"ts": datetime.datetime.now().isoformat(timespec="seconds"),
                                "op": _op, "ms": round((time.perf_counter() - _t0) * 1000),
                                "out_bytes": out_bytes}) + "\n")
    except OSError:
        pass

_out_total = 0
def out(s=""):
    global _out_total
    _out_total += len(s) + 1
    print(s)

def die(msg, code=1):
    sys.stderr.write(msg + "\n"); _log(0); sys.exit(code)

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
        die(f"error: path escapes vault: {path}")
    return full

def resolve(ref):
    """Vault-relative path if it exists, else wikilink-style bare-name resolution.
    All paths are contained to the vault (no abs/.. escape)."""
    fp = contain(ref)
    if os.path.isfile(fp):
        return fp
    # allow folder/Name (no .md) exact path resolution
    fp_md = contain(ref + ".md") if not ref.endswith(".md") else fp
    if os.path.isfile(fp_md):
        return fp_md
    want = (ref[:-3] if ref.endswith(".md") else ref).lower()
    hits = [p for p in md_files() if os.path.basename(p)[:-3].lower() == want]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        die(f"error: no note matches '{ref}'")
    die("ambiguous: " + " | ".join(os.path.relpath(h, VAULT) for h in sorted(hits)[:5]))

def rel(fp):
    return os.path.relpath(fp, VAULT)

# ---------- md structure (shared with vnote2, fence-aware) ----------
def parse(text):
    lines = text.split("\n")
    fenced = set(); open_ = False
    fm_end = 0
    if lines and lines[0].rstrip("\r") == "---":
        for i in range(1, len(lines)):
            if lines[i].rstrip("\r") == "---":
                fm_end = i + 1
                break
    fenced.update(range(fm_end))
    for i, l in enumerate(lines):
        if i < fm_end:
            continue
        if re.match(r"^(```|~~~)", l):
            open_ = not open_; fenced.add(i)
        elif open_:
            fenced.add(i)
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
    for s in secs:
        if s["id"] == sid:
            return s
    die(f"error: no section {sid} (run outline)")

def split_fm(text):
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n?", text, re.S)
    return (m.group(1), text[m.end():]) if m else (None, text)

def split_fm_full(text):
    """(fm, body, tail) — tail is the newline (if any) after the closing ---, preserved verbatim."""
    m = re.match(r"^---\r?\n(.*?)\r?\n---(\r?\n)?", text, re.S)
    if not m:
        return None, text, ""
    return m.group(1), text[m.end():], m.group(2) or ""

def fm_props(fm):
    props = {}
    if fm:
        for line in fm.splitlines():
            if (m := re.match(r"^(\w[\w-]*):\s*(.*)$", line)):
                props[m.group(1)] = m.group(2).strip('"')
    return props

def read_raw(fp):
    with open(fp, newline="") as f:
        return f.read()

def eol_of(text):
    return "\r\n" if "\r\n" in text else "\n"

def atomic_write(fp, content):
    # follow a symlink to its real target so we replace the file, not the link
    target = os.path.realpath(fp) if os.path.islink(fp) else fp
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

WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")

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
    fp = resolve(ref)
    lines, secs = parse(read_raw(fp))
    s = find_sec(lines, secs, sid)
    if sid == "H0" and s["end"] > 0 and lines and lines[0].rstrip("\r") == "---":
        die("error: H0 contains frontmatter — edit it with `set`/`unset`, not `patch` (patch would rewrite YAML as body)")
    cur = sec_text(lines, s)
    if sha8(cur) != expect:
        sys.stderr.write(f"stale: {sid} is {sha8(cur)}, expected {expect} — re-outline\n")
        _log(0); sys.exit(3)
    body = sys.stdin.read().replace("\r\n", "\n")
    if body.endswith("\n"):
        body = body[:-1]
    body_lines = [] if (body == "" and s["end"] == s["start"]) else body.split("\n")
    if eol_of("\n".join(lines)) == "\r\n":
        body_lines = [b + "\r" for b in body_lines]
        if s["end"] == len(lines) and lines and not lines[-1].endswith("\r") and lines[-1] != "":
            body_lines[-1] = body_lines[-1].rstrip("\r")
    atomic_write(fp, "\n".join(lines[:s["start"]] + body_lines + lines[s["end"]:]))
    out(f"patched {sid} in {rel(fp)} ({len(cur)}B -> {len(body)}B)")

def cmd_appendsec(ref, sid, text):
    fp = resolve(ref)
    lines, secs = parse(read_raw(fp))
    s = find_sec(lines, secs, sid)
    ins = s["end"]
    while ins > s["start"] and lines[ins - 1].strip() == "":
        ins -= 1
    if eol_of("\n".join(lines)) == "\r\n":
        text = text + "\r"
    atomic_write(fp, "\n".join(lines[:ins] + [text] + lines[ins:]))
    out(f"appended to {sid} in {rel(fp)}")

def cmd_append(ref, text):
    fp = resolve(ref)
    cur = read_raw(fp)
    eol = eol_of(cur)
    atomic_write(fp, cur + ("" if cur.endswith("\n") or not cur else eol) + text + eol)
    out(f"appended to {rel(fp)}")

def cmd_set(ref, key, value):
    fp = resolve(ref)
    text = read_raw(fp)
    fm, body, tail = split_fm_full(text)
    eol = eol_of(text)
    if fm is None:
        atomic_write(fp, f"---{eol}{key}: {value}{eol}---{eol}{text}")
    else:
        fm_lines = fm.replace("\r\n", "\n").split("\n")
        if block_scalar_key(fm_lines, key):
            die(f"error: '{key}' has a multi-line/block value — edit it directly, not via `set` (would orphan continuation lines)")
        pat = re.compile(rf"^{re.escape(key)}:")
        hit = [i for i, l in enumerate(fm_lines) if pat.match(l)]
        if hit:
            fm_lines[hit[0]] = f"{key}: {value}"
        else:
            fm_lines.append(f"{key}: {value}")
        atomic_write(fp, "---" + eol + eol.join(fm_lines) + eol + "---" + tail + body)
    out(f"set {key}={value} in {rel(fp)}")

def cmd_unset(ref, key):
    fp = resolve(ref)
    text = read_raw(fp)
    fm, body, tail = split_fm_full(text)
    if fm is None:
        die(f"error: no frontmatter in {rel(fp)}")
    eol = eol_of(text)
    fm_lines = fm.replace("\r\n", "\n").split("\n")
    if block_scalar_key(fm_lines, key):
        die(f"error: '{key}' has a multi-line/block value — remove it directly, not via `unset` (would orphan continuation lines)")
    kept = [l for l in fm_lines if not re.match(rf"^{re.escape(key)}:", l)]
    if kept == fm_lines:
        die(f"error: no key {key} in {rel(fp)}")
    atomic_write(fp, "---" + eol + eol.join(kept) + eol + "---" + tail + body)
    out(f"unset {key} in {rel(fp)}")

def cmd_new(*args):
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
        die(f"error: exists: {rel(fp)}")
    d = os.path.dirname(fp)
    if d:
        os.makedirs(d, exist_ok=True)
    content = ""
    if template:
        hits = sorted(glob.glob(os.path.join(VAULT, "Templates", "**", template + "*.md"), recursive=True))
        if not hits:
            die(f"error: no template matching '{template}' under Templates/")
        content = read_raw(hits[0])
    for k, v in kv.items():
        pat = re.compile(rf"^{re.escape(k)}:.*$", re.M)
        if pat.search(content):
            i = pat.search(content).start()
            content = content[:i] + f"{k}: {v}" + content[pat.search(content).end():]  # literal, no re.sub template
    if kv and not content.startswith("---"):
        fmb = "\n".join(f"{k}: {v}" for k, v in kv.items())
        content = f"---\n{fmb}\n---\n" + content
    atomic_write(fp, content)
    out(f"created {rel(fp)}")

def _links_to(target_fp):
    """True-if-p-links-to-target, using the SAME occurrence logic as impact/rename.
    Matches bare basename (when unambiguous), path-form, and md-links."""
    tgt_base = os.path.basename(target_fp)[:-3].lower()
    tgt_rel_noext = rel(target_fp)[:-3].lower()
    ambiguous = len(basename_index().get(tgt_base, [])) > 1
    def links_from(p):
        for _, kind, t in link_targets_in(read_raw(p)):
            tl = t.lower()
            if kind == "wiki":
                tl_noext = tl[:-3] if tl.endswith(".md") else tl
                if (tl_noext == tgt_base and not ambiguous) or tl_noext == tgt_rel_noext:
                    return True
            else:
                import urllib.parse
                dec = urllib.parse.unquote(t)
                cand = os.path.normpath(os.path.join(os.path.dirname(p), dec))
                if os.path.abspath(cand) == os.path.abspath(target_fp) or \
                   os.path.normpath(os.path.join(VAULT, dec)) == target_fp:
                    return True
        return False
    return links_from

def cmd_backlinks(ref):
    fp = resolve(ref)
    pred = _links_to(fp)
    n = 0
    for p in md_files():
        if p != fp and pred(p):
            out(rel(p)); n += 1
    out(f"({n} backlinks)")

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
    # a note is linked if ANY note resolves a link to it (path-aware)
    all_targets = set()
    for p in files:
        for _, kind, t in link_targets_in(read_raw(p)):
            tl = t.lower()
            all_targets.add(tl[:-3] if tl.endswith(".md") else tl)
    n = 0
    for p in sorted(files):
        if not (p == root or p.startswith(root + os.sep) or not folder):
            continue
        base = os.path.basename(p)[:-3].lower()
        rel_noext = rel(p)[:-3].lower()
        if base not in all_targets and rel_noext not in all_targets:
            out(rel(p)); n += 1
    out(f"({n} orphans)")

def cmd_board(folder, *filters):
    want = dict(f.split("=", 1) for f in filters)
    root = os.path.join(VAULT, folder)
    if not os.path.isdir(root):
        die(f"error: no such folder: {folder}")
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
    if os.path.exists(VRUST):
        r = subprocess.run([VRUST, "search", *args], capture_output=True, text=True)
        sys.stdout.write(r.stdout); sys.stderr.write(r.stderr)
        global _out_total
        _out_total += len(r.stdout)
        _log(_out_total); sys.exit(r.returncode)
    k, w, terms = 5, 500, []
    it = iter(args)
    for a in it:
        if a == "--k": k = int(next(it))
        elif a == "--w": w = int(next(it))
        else: terms.append(a.lower())
    if not terms:
        die("error: no query")
    hits = []
    for p in md_files():
        if rel(p).startswith("Sandbox/"):
            continue  # parity with vrust engine's exclusion set
        try:
            text = open(p, errors="replace").read()
        except OSError:
            continue
        low = text.lower()
        if not all(t in low for t in terms):
            continue
        score = sum(low.count(t) for t in terms)
        pos = min(low.find(t) for t in terms)
        start = max(0, pos - w // 4)
        hits.append((score, rel(p), text[start:start + w].replace("\n", " ¶ ")))
    hits.sort(key=lambda h: -h[0])
    for score, r_, snip in hits[:k]:
        out(f"== {r_} (score {score})\n{snip}\n")
    out(f"({min(len(hits), k)} of {len(hits)} matches)")

def cmd_daily_append(text):
    today = datetime.date.today().isoformat()
    hits = glob.glob(os.path.join(VAULT, "Standups", f"*{today}*.md"))
    if not hits:
        die(f"error: no standup note for {today} under Standups/")
    cur = open(hits[0]).read()
    atomic_write(hits[0], cur + ("" if cur.endswith("\n") else "\n") + text + "\n")
    out(f"appended to {rel(hits[0])}")

# ================= v1.5: show / deadends / impact / rename / move / lint / doctor =================

JOURNAL_ROOT = os.path.expanduser("~/.cache/vv/journals")

def masked_lines(text):
    """Line indices where wikilinks are inert (fences, frontmatter) — reuse parse()'s mask."""
    lines = text.split("\n")
    fenced = set(); open_ = False
    fm_end = 0
    if lines and lines[0].rstrip("\r") == "---":
        for i in range(1, len(lines)):
            if lines[i].rstrip("\r") == "---":
                fm_end = i + 1
                break
    for i, l in enumerate(lines):
        if i < fm_end:
            continue  # frontmatter links (related:) ARE real links — do not mask
        if re.match(r"^(```|~~~)", l):
            open_ = not open_; fenced.add(i)
        elif open_:
            fenced.add(i)
    return lines, fenced

def strip_inline_code(line):
    return re.sub(r"`[^`]*`", lambda m: "\0" * len(m.group(0)), line)

LINK_RE = re.compile(r"(!?\[\[)([^\]|#]+)((?:#[^\]|]*)?(?:\|[^\]]*)?)(\]\])")
MDLINK_RE = re.compile(r"(\]\()([^)\s]+\.md)(\))")

def link_targets_in(text):
    """Yield (line_idx, kind, target) for active links; fenced lines excluded."""
    lines, fenced = masked_lines(text)
    for i, l in enumerate(lines):
        if i in fenced:
            continue
        scan = strip_inline_code(l)
        for m in LINK_RE.finditer(scan):
            yield i, "wiki", m.group(2).strip()
        for m in MDLINK_RE.finditer(scan):
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
    for p in md_files():
        try:
            text = open(p, errors="replace").read()
        except OSError:
            continue
        n = 0
        for _, kind, tgt in link_targets_in(text):
            t = tgt.lower()
            if kind == "wiki":
                t_noext = t[:-3] if t.endswith(".md") else t  # [[Note.md]] is the same target as [[Note]]
                if t_noext == src_base and not ambiguous and include_bare:
                    n += 1
                elif t_noext == src_rel_noext:
                    n += 1
            else:
                import urllib.parse
                dec = urllib.parse.unquote(tgt)
                cand = os.path.normpath(os.path.join(os.path.dirname(p), dec))
                if os.path.abspath(cand) == os.path.abspath(source_fp) or \
                   os.path.normpath(os.path.join(VAULT, dec)) == source_fp:
                    n += 1
        if n:
            hits[p] = n
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
        if used + len(t) > max_bytes and used > 0:
            out(f"[more: {s['id']} '{s['title']}' {len(t)}B — continue: vv show {ref} --from {s['id']}]")
            break
        out(t)
        used += len(t)
    if not started:
        die(f"error: no section {start}")

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

def _journal_start(name, files):
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    jdir = os.path.join(JOURNAL_ROOT, f"{ts}-{name}")
    os.makedirs(jdir)
    manifest = {"op": name, "ts": ts, "files": {}}
    import shutil as _sh
    for idx, fp in enumerate(files):  # index key — collision-proof (rel-path %2F encoding was not)
        key = f"f{idx}.bak"
        _sh.copy2(fp, os.path.join(jdir, key))
        manifest["files"][rel(fp)] = key
    with open(os.path.join(jdir, "manifest.json"), "w") as f:
        json.dump(manifest, f)
    return jdir

def _journal_rollback(jdir):
    man = json.load(open(os.path.join(jdir, "manifest.json")))
    import shutil as _sh
    for r_, key in man["files"].items():
        _sh.copy2(os.path.join(jdir, key), os.path.join(VAULT, r_))

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
    lines, fenced = masked_lines(text)
    changed = 0
    for i, l in enumerate(lines):
        if i in fenced:
            continue
        spans = [(m.start(), m.end()) for m in re.finditer(r"`[^`]*`", l)]
        def in_span(pos):
            return any(a <= pos < b for a, b in spans)
        def wiki_sub(m):
            nonlocal changed
            if in_span(m.start()):
                return m.group(0)
            tgt = m.group(2).strip()
            t = tgt.lower()
            ext = ".md" if t.endswith(".md") else ""   # preserve the author's [[Note.md]] style
            t_noext = t[:-3] if ext else t
            if t_noext == src_base.lower() and rename_base:
                changed += 1
                return m.group(1) + rename_base + ext + m.group(3) + m.group(4)
            if t_noext == src_rel_noext.lower():
                changed += 1
                return m.group(1) + new_rel_noext + ext + m.group(3) + m.group(4)
            return m.group(0)
        def md_sub(m):
            nonlocal changed
            if in_span(m.start()):
                return m.group(0)
            import urllib.parse
            dec = urllib.parse.unquote(m.group(2))
            is_relative = not os.path.isabs(dec) and (dec.startswith("./") or dec.startswith("../")
                          or (linking_fp and os.path.normpath(os.path.join(os.path.dirname(linking_fp), dec)) == source_fp
                              and os.path.normpath(os.path.join(VAULT, dec)) != source_fp))
            matches_root = os.path.normpath(os.path.join(VAULT, dec)) == source_fp
            matches_rel = linking_fp and os.path.normpath(os.path.join(os.path.dirname(linking_fp), dec)) == source_fp
            if matches_rel and is_relative and linking_fp:
                changed += 1
                newrel = os.path.relpath(new_fp_abs, os.path.dirname(linking_fp))
                return m.group(1) + urllib.parse.quote(newrel) + m.group(3)
            if matches_root:
                changed += 1
                return m.group(1) + urllib.parse.quote(new_rel_noext + ".md") + m.group(3)
            return m.group(0)
        nl = LINK_RE.sub(wiki_sub, l)
        nl = MDLINK_RE.sub(md_sub, nl)
        lines[i] = nl
    return "\n".join(lines), changed

def _do_relocate(ref, dest_rel_noext, apply_, opname):
    fp = resolve(ref)
    src_rel = rel(fp)
    new_fp = os.path.join(VAULT, dest_rel_noext + ".md")
    if os.path.exists(new_fp):
        die(f"error: target exists: {dest_rel_noext}.md")
    new_base = os.path.basename(dest_rel_noext)
    rename_base = new_base if new_base.lower() != os.path.basename(fp)[:-3].lower() else None
    idx = basename_index()
    if rename_base and rename_base.lower() in idx:
        die(f"error: another note already has basename '{new_base}' — bare links would be ambiguous")
    hits, ambiguous = occurrences(fp, include_bare=bool(rename_base))
    if ambiguous and rename_base:
        die(f"error: source basename is ambiguous in vault — resolve duplicate notes first")
    out(f"plan: {opname} {src_rel} -> {dest_rel_noext}.md")
    out(f"files to rewrite: {len(hits)} ({sum(hits.values())} link occurrences)")
    for p, n in sorted(hits.items()):
        out(f"  {n}\t{rel(p)}")
    if not apply_:
        out("(dry-run — pass --apply to execute)")
        return
    # journal every file that will be written, plus the moved file itself (once)
    journal_targets = list(hits.keys()) + ([fp] if fp not in hits else [])
    jdir = _journal_start(opname, journal_targets)
    renamed = False
    try:
        results = {}
        for p in hits:
            text = read_raw(p)
            new_text, changed = _rewrite_links(text, fp, dest_rel_noext, rename_base, linking_fp=p)
            if changed != hits[p]:
                raise RuntimeError(f"span mismatch in {rel(p)}: planned {hits[p]}, rewrote {changed}")
            results[p] = new_text
        fault_after = int(os.environ.get("VV_FAULT_AFTER", "-1"))
        for wi, (p, new_text) in enumerate(results.items()):
            if fault_after >= 0 and wi >= fault_after:
                raise RuntimeError(f"INJECTED FAULT after {wi} writes")
            atomic_write(p, new_text)
        d = os.path.dirname(new_fp)
        if d:
            os.makedirs(d, exist_ok=True)
        os.rename(fp, new_fp)
        renamed = True
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
    except Exception as e:
        # reverse the rename FIRST (if it happened) so journal-restore never leaves a duplicate
        if renamed and os.path.exists(new_fp) and not os.path.exists(fp):
            os.rename(new_fp, fp)
        _journal_rollback(jdir)
        die(f"ROLLED BACK ({e}); originals restored from journal {jdir}")

def cmd_rename(ref, new_name, *args):
    fp = resolve(ref)
    dest = os.path.join(os.path.dirname(rel(fp)), new_name[:-3] if new_name.endswith(".md") else new_name)
    _do_relocate(ref, dest, "--apply" in args, "rename")

def cmd_move(ref, dest_folder, *args):
    fp = resolve(ref)
    dest = os.path.join(dest_folder.rstrip("/"), os.path.basename(rel(fp))[:-3])
    _do_relocate(ref, dest, "--apply" in args, "move")

def cmd_lint(*args):
    canonical = os.path.join(VAULT, ".claude/skills/vault-lint/vault_lint.py")
    if "--quick" not in args and os.path.exists(canonical):
        r = subprocess.run([sys.executable, canonical] + [a for a in args], cwd=VAULT)
        _log(_out_total); sys.exit(r.returncode)
    # --quick: native broken-wikilink scan (fence/inline-code aware, path-style by last segment)
    idx = basename_index()
    stems = set(idx.keys())
    for p in glob.glob(os.path.join(VAULT, "Templates/**/*.md"), recursive=True):
        stems.add(os.path.basename(p)[:-3].lower())
    n = 0
    for p in md_files():
        for i, kind, tgt in link_targets_in(open(p, errors="replace").read()):
            if kind != "wiki":
                continue
            t = tgt.strip().lower()
            if t.startswith(("reference-", "feedback-", "project-", "user-")):
                out(f"memory-slug\t{rel(p)}:{i+1}\t[[{tgt}]]"); n += 1
                continue
            last = t.split("/")[-1]
            if last not in stems:
                out(f"broken-link\t{rel(p)}:{i+1}\t[[{tgt}]]"); n += 1
    out(f"({n} findings)")

def cmd_doctor():
    out(f"vault: {VAULT} ({'ok' if os.path.isdir(VAULT) else 'MISSING'})")
    out(f"engine: {'vrust ok' if os.path.exists(VRUST) else 'vrust MISSING (python fallback)'}")
    dirty = _git(["status", "--porcelain"])
    out(f"git: {'clean' if not dirty else f'{len(dirty.splitlines())} dirty paths'}")
    js = sorted(glob.glob(os.path.join(JOURNAL_ROOT, "*"))) if os.path.isdir(JOURNAL_ROOT) else []
    out(f"journals: {'none pending' if not js else 'UNRESOLVED: ' + ', '.join(os.path.basename(j) for j in js)}")
    try:
        with open(METRICS, "a"):
            pass
        out("metrics: writable")
    except OSError:
        out("metrics: NOT writable")
    if js:
        _log(_out_total); sys.exit(4)

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

if __name__ == "__main__":
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)
    fn = CMDS.get(a[0])
    if not fn:
        die(f"error: unknown command {a[0]}")
    try:
        fn(*a[1:])
    except TypeError as e:
        die(f"usage error: {e}")
    _log(_out_total)
