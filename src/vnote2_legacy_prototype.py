#!/usr/bin/env python3
"""vnote2 — PROTOTYPE: section-addressed vault ops (candidate vnote extension).

Commands (all output deliberately terse; output bytes are the cost function):
  outline <path>                      # frontmatter keys + heading tree with section ids & sizes
  read <path> <section-id> [n]       # print one section (by id from outline); n extra context sections
  patch <path> <section-id> <sha8> <<stdin   # replace section body; sha8 = expected hash from outline/read
  appendsec <path> <section-id> <text>       # append a line to a section
  search <query...> [--k N] [--w CHARS]      # snippet search: fat windows, dedup by file

Section id = H<index>, stable within one outline/read→patch cycle, verified by hash.
Exit codes: 0 ok, 1 not found/usage, 3 hash mismatch (stale — re-outline).
"""
import sys, os, re, hashlib

VAULT = os.path.expanduser("~/Documents/Obsidian Vault")

def full(p):
    fp = os.path.join(VAULT, p)
    if not os.path.isfile(fp):
        sys.stderr.write(f"error: no such note: {p}\n"); sys.exit(1)
    return fp

def parse(text):
    """Return (fm_text|None, sections). sections = list of dicts:
    {id, level, title, start, end} over line indices; section 0 = preamble
    (frontmatter + content before first heading)."""
    lines = text.split("\n")
    fm_end = 0
    if lines and lines[0] == "---":
        for i in range(1, len(lines)):
            if lines[i] == "---":
                fm_end = i + 1
                break
    heads = [(i, len(m.group(1)), m.group(2).strip())
             for i, l in enumerate(lines)
             if (m := re.match(r"^(#{1,6})\s+(.*)$", l)) and not in_fence(lines, i)]
    secs = []
    first = heads[0][0] if heads else len(lines)
    secs.append({"id": "H0", "level": 0, "title": "(preamble)", "start": 0, "end": first})
    for j, (i, lvl, title) in enumerate(heads):
        end = heads[j + 1][0] if j + 1 < len(heads) else len(lines)
        secs.append({"id": f"H{j+1}", "level": lvl, "title": title, "start": i, "end": end})
    return lines, secs

_fence_cache = {}
def in_fence(lines, idx):
    key = id(lines)
    if key not in _fence_cache:
        fenced = set(); open_ = False
        for i, l in enumerate(lines):
            if re.match(r"^(```|~~~)", l):
                open_ = not open_
                fenced.add(i)
            elif open_:
                fenced.add(i)
        _fence_cache[key] = fenced
    return idx in _fence_cache[key]

def sec_text(lines, s):
    return "\n".join(lines[s["start"]:s["end"]])

def sha8(text):
    return hashlib.sha256(text.encode()).hexdigest()[:8]

def cmd_outline(path):
    lines, secs = parse(open(full(path)).read())
    for s in secs:
        t = sec_text(lines, s)
        print(f"{s['id']}\t{'#'*s['level'] or '-'}\t{s['title']}\t{len(t)}B\t{sha8(t)}")

def find_sec(lines, secs, sid):
    for s in secs:
        if s["id"] == sid:
            return s
    sys.stderr.write(f"error: no section {sid} (run outline)\n"); sys.exit(1)

def cmd_read(path, sid, ctx="0"):
    lines, secs = parse(open(full(path)).read())
    s = find_sec(lines, secs, sid)
    print(sec_text(lines, s))
    print(f"--sha8:{sha8(sec_text(lines, s))}")

def cmd_patch(path, sid, expect):
    fp = full(path)
    text = open(fp).read()
    lines, secs = parse(text)
    s = find_sec(lines, secs, sid)
    cur = sec_text(lines, s)
    if sha8(cur) != expect:
        sys.stderr.write(f"stale: section {sid} is {sha8(cur)}, expected {expect} — re-outline\n"); sys.exit(3)
    new_body = sys.stdin.read()
    if new_body.endswith("\n"):
        new_body = new_body[:-1]
    out = lines[:s["start"]] + new_body.split("\n") + lines[s["end"]:]
    tmp = fp + ".tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(out))
    os.replace(tmp, fp)
    print(f"patched {sid} in {path} ({len(cur)}B -> {len(new_body)}B)")

def cmd_appendsec(path, sid, line):
    fp = full(path)
    lines, secs = parse(open(fp).read())
    s = find_sec(lines, secs, sid)
    insert = s["end"]
    while insert > s["start"] and lines[insert - 1].strip() == "":
        insert -= 1
    out = lines[:insert] + [line] + lines[insert:]
    tmp = fp + ".tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(out))
    os.replace(tmp, fp)
    print(f"appended to {sid} in {path}")

def cmd_search(*args):
    k, w = 5, 500
    terms = []
    it = iter(args)
    for a in it:
        if a == "--k": k = int(next(it))
        elif a == "--w": w = int(next(it))
        else: terms.append(a.lower())
    if not terms:
        sys.stderr.write("error: no query\n"); sys.exit(1)
    hits = []
    for dirpath, dirs, names in os.walk(VAULT):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("graphify-out", "Sandbox")]
        for n in names:
            if not n.endswith(".md"): continue
            fp = os.path.join(dirpath, n)
            try: text = open(fp, errors="replace").read()
            except OSError: continue
            low = text.lower()
            score = sum(low.count(t) for t in terms)
            if score == 0 or not all(t in low for t in terms): continue
            pos = min(low.find(t) for t in terms)
            start = max(0, pos - w // 4)
            snip = text[start:start + w].replace("\n", " ¶ ")
            hits.append((score, os.path.relpath(fp, VAULT), snip))
    hits.sort(key=lambda h: -h[0])
    for score, rel, snip in hits[:k]:
        print(f"== {rel} (score {score})\n{snip}\n")
    print(f"({min(len(hits),k)} of {len(hits)} matches)")

if __name__ == "__main__":
    a = sys.argv[1:]
    if not a: sys.exit(__doc__)
    cmd, rest = a[0], a[1:]
    fn = {"outline": cmd_outline, "read": cmd_read, "patch": cmd_patch,
          "appendsec": cmd_appendsec, "search": cmd_search}.get(cmd)
    if not fn: sys.stderr.write(f"error: unknown command {cmd}\n"); sys.exit(1)
    fn(*rest)
