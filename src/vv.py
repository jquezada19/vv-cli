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

VAULT = os.path.expanduser("~/Documents/Obsidian Vault")
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

def resolve(ref):
    """Vault-relative path if it exists, else wikilink-style bare-name resolution."""
    fp = os.path.join(VAULT, ref)
    if os.path.isfile(fp):
        return fp
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
    m = re.match(r"^---\n(.*?)\n---\n?", text, re.S)
    return (m.group(1), text[m.end():]) if m else (None, text)

def fm_props(fm):
    props = {}
    if fm:
        for line in fm.splitlines():
            if (m := re.match(r"^(\w[\w-]*):\s*(.*)$", line)):
                props[m.group(1)] = m.group(2).strip('"')
    return props

def atomic_write(fp, content):
    tmp = fp + ".tmp"
    with open(tmp, "w") as f:
        f.write(content)
    os.replace(tmp, fp)

WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")

# ---------- commands ----------
def cmd_outline(ref):
    lines, secs = parse(open(resolve(ref)).read())
    for s in secs:
        t = sec_text(lines, s)
        out(f"{s['id']}\t{'#'*s['level'] or '-'}\t{s['title']}\t{len(t)}B\t{sha8(t)}")

def cmd_read(ref, sid):
    lines, secs = parse(open(resolve(ref)).read())
    s = find_sec(lines, secs, sid)
    out(sec_text(lines, s))
    out(f"--sha8:{sha8(sec_text(lines, s))}")

def cmd_head(ref):
    fm, _ = split_fm(open(resolve(ref)).read())
    out(fm if fm is not None else "(no frontmatter)")

def cmd_resolve(ref):
    out(rel(resolve(ref)))

def cmd_patch(ref, sid, expect):
    fp = resolve(ref)
    lines, secs = parse(open(fp).read())
    s = find_sec(lines, secs, sid)
    cur = sec_text(lines, s)
    if sha8(cur) != expect:
        sys.stderr.write(f"stale: {sid} is {sha8(cur)}, expected {expect} — re-outline\n")
        _log(0); sys.exit(3)
    body = sys.stdin.read()
    if body.endswith("\n"):
        body = body[:-1]
    atomic_write(fp, "\n".join(lines[:s["start"]] + body.split("\n") + lines[s["end"]:]))
    out(f"patched {sid} in {rel(fp)} ({len(cur)}B -> {len(body)}B)")

def cmd_appendsec(ref, sid, text):
    fp = resolve(ref)
    lines, secs = parse(open(fp).read())
    s = find_sec(lines, secs, sid)
    ins = s["end"]
    while ins > s["start"] and lines[ins - 1].strip() == "":
        ins -= 1
    atomic_write(fp, "\n".join(lines[:ins] + [text] + lines[ins:]))
    out(f"appended to {sid} in {rel(fp)}")

def cmd_append(ref, text):
    fp = resolve(ref)
    cur = open(fp).read()
    atomic_write(fp, cur + ("" if cur.endswith("\n") or not cur else "\n") + text + "\n")
    out(f"appended to {rel(fp)}")

def cmd_set(ref, key, value):
    fp = resolve(ref)
    text = open(fp).read()
    fm, body = split_fm(text)
    if fm is None:
        atomic_write(fp, f"---\n{key}: {value}\n---\n{text}")
    else:
        pat = re.compile(rf"^{re.escape(key)}:.*$", re.M)
        new_fm = pat.sub(f"{key}: {value}", fm) if pat.search(fm) else fm + f"\n{key}: {value}"
        atomic_write(fp, f"---\n{new_fm}\n---\n{body}")
    out(f"set {key}={value} in {rel(fp)}")

def cmd_unset(ref, key):
    fp = resolve(ref)
    fm, body = split_fm(open(fp).read())
    if fm is None:
        die(f"error: no frontmatter in {rel(fp)}")
    new_fm = "\n".join(l for l in fm.splitlines() if not re.match(rf"^{re.escape(key)}:", l))
    if new_fm == fm:
        die(f"error: no key {key} in {rel(fp)}")
    atomic_write(fp, f"---\n{new_fm}\n---\n{body}")
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
    fp = os.path.join(VAULT, path if path.endswith(".md") else path + ".md")
    if os.path.exists(fp):
        die(f"error: exists: {rel(fp)}")
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    content = ""
    if template:
        hits = glob.glob(os.path.join(VAULT, "Templates", "**", template + "*.md"), recursive=True)
        if not hits:
            die(f"error: no template matching '{template}' under Templates/")
        content = open(hits[0]).read()
    for k, v in kv.items():
        pat = re.compile(rf"^{re.escape(k)}:.*$", re.M)
        if pat.search(content):
            content = pat.sub(f"{k}: {v}", content, count=1)
    if kv and not content.startswith("---"):
        fmb = "\n".join(f"{k}: {v}" for k, v in kv.items())
        content = f"---\n{fmb}\n---\n" + content
    atomic_write(fp, content)
    out(f"created {rel(fp)}")

def cmd_backlinks(ref):
    target = os.path.basename(resolve(ref))[:-3].lower()
    n = 0
    for p in md_files():
        try:
            text = open(p, errors="replace").read()
        except OSError:
            continue
        if any(l.strip().lower() == target for l in WIKILINK.findall(text)):
            out(rel(p)); n += 1
    out(f"({n} backlinks)")

def cmd_links(ref):
    fp = resolve(ref)
    seen = []
    for l in WIKILINK.findall(open(fp).read()):
        l = l.strip()
        if l not in seen:
            seen.append(l)
    for l in seen:
        out(l)
    out(f"({len(seen)} links)")

def cmd_orphans(folder=""):
    root = os.path.join(VAULT, folder) if folder else VAULT
    linked = set()
    names = {}
    for p in md_files():
        names[os.path.basename(p)[:-3].lower()] = p
        for l in WIKILINK.findall(open(p, errors="replace").read()):
            linked.add(l.strip().lower())
    n = 0
    for name, p in sorted(names.items()):
        if name not in linked and p.startswith(root):
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
    root = os.path.join(VAULT, folder) if folder else VAULT
    from collections import Counter
    c = Counter()
    for p in md_files():
        if not p.startswith(root):
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

CMDS = {
    "outline": cmd_outline, "read": cmd_read, "head": cmd_head, "resolve": cmd_resolve,
    "patch": cmd_patch, "appendsec": cmd_appendsec, "append": cmd_append,
    "set": cmd_set, "unset": cmd_unset, "new": cmd_new,
    "backlinks": cmd_backlinks, "links": cmd_links, "orphans": cmd_orphans,
    "board": cmd_board, "tags": cmd_tags, "props": cmd_props,
    "search": cmd_search, "daily-append": cmd_daily_append,
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
