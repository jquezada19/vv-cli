#!/usr/bin/env python3
"""Rust engine <-> Python fallback parity.

vv keeps two implementations of the hot scan: the Rust engine (fast path) and a
pure-Python fallback (always available). Two implementations of the same lexing is
a standing drift risk, so it is pinned here: both must return the SAME links, on
synthetic edge cases and on the live corpus. If this test fails, the fast path is
lying about the vault and must not be used until it agrees again.

Semantics (what a link MEANS) live only in Python — this test covers lexing only.
"""
import sys, os, subprocess, tempfile, shutil

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "src"))
import vv  # noqa: E402

VRUST = os.path.join(REPO, "vrust", "target", "release", "vrust")
fails = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{str(detail)[:200]}]" if detail and not cond else ""))
    if not cond: fails.append(name)

if not os.path.exists(VRUST):
    print("SKIP: rust engine not built (cd vrust && cargo build --release)")
    sys.exit(0)

def rust_links(vault):
    r = subprocess.run([VRUST, "linkscan"], capture_output=True, text=True,
                       env=dict(os.environ, VV_VAULT=vault))
    out = set()
    for line in r.stdout.split("\n"):
        if not line:
            continue
        rel_, ln, kind, tgt = line.split("\t", 3)
        out.add((rel_, int(ln), kind, tgt))
    return out

def py_links(vault):
    old = vv.VAULT
    vv.VAULT = vault
    try:
        out = set()
        for p in vv.md_files():
            try:
                text = vv.read_raw(p)
            except SystemExit:
                continue
            for i, kind, tgt in vv.link_targets_in(text):
                out.add((vv.rel(p), i + 1, "w" if kind == "wiki" else "m", tgt.strip()))
        return out
    finally:
        vv.VAULT = old

# ---- synthetic edge cases ----
CASES = {
    "plain.md": "A [[One]] and [[Two|alias]] and [[Three#head]] and ![[Four]].\n",
    "fenced.md": "```\n[[Hidden]]\n```\n[[Visible]]\n",
    "tilde.md": "~~~\n[[HiddenTilde]]\n~~~\n[[VisibleAfterTilde]]\n",
    "mixed_fence.md": "```\n~~~\n[[StillHidden]]\n```\n[[VisibleNow]]\n",
    "inline.md": "`[[InCode]]` but [[NotInCode]].\n",
    "double_backtick.md": "``[[InDoubleCode]]`` but [[OutsideDouble]].\n",
    "triple_inline.md": "```[[InTripleInline]]``` and [[AfterTriple]].\n",
    "unclosed_backtick.md": "`[[UnclosedSpan]] stays active.\n",
    "nested_fence.md": "````md\nouter\n```py\n[[DeepHidden]]\n```\nouter\n````\n[[AfterNested]]\n",
    "long_fence.md": "```\n[[InsideShort]]\n````\nstill inside\n```\n[[AfterShort]]\n",
    "frontmatter.md": "---\nrelated:\n  - \"[[FromYaml]]\"\n---\n[[FromBody]]\n",
    "unterminated_fm.md": "---\nk: v\nno close\n[[AfterUnterminated]]\n",
    "mdlinks.md": "[a](Some%20Note.md) and [b](sub/Other.md) and [c](http://x.com/page.md)\n",
    "unicode.md": "[[Überblick]] and [[日本語ノート]] and [[emoji🚀note]]\n",
    "indented_fence.md": "   ```\n   [[IndentedHidden]]\n   ```\n[[AfterIndented]]\n",
    "empty.md": "",
    "crlf.md": "[[CrlfOne]]\r\n```\r\n[[CrlfHidden]]\r\n```\r\n[[CrlfTwo]]\r\n",
    # trailing backslashes are never part of a name: \| escapes the alias pipe in
    # tables, and a stray [[Note\]] still resolves to Note (backslash is illegal
    # in note names — both verified against Obsidian's metadataCache 2026-08-26).
    "escaped_pipe.md": "| [[TableTarget\\|alias]] | x |\n| [[Frag#Sec\\|al]] | y |\n[[TrailBack\\]]\n",
    # Obsidian does not index links inside <!-- --> HTML comments; %% comments DO index
    "html_comment.md": "a <!-- [[InComment]] --> [[AfterComment]]\n<!--\n[[InBlockComment]]\n-->\n[[AfterBlock]]\n%% [[InPercent]] %%\n`<!--` [[NotAComment]]\n",
    "comment_in_fence.md": "```\n<!--\n```\n[[NotSwallowed]]\n",
    "comment_unclosed.md": "<!-- open forever\n[[Hidden1]]\n[[Hidden2]]\n",
    # round-2 review fixtures (2026-08-26): every case probed against Obsidian
    "runlen.md": "`a``` [[RunLenX]] ` [[RunLenY]]\n",              # 3-run tail is NOT a 1-tick closer
    "empty_targets.md": "[[ ]] and [[\\]] and [[Real Target]]\n",  # empty targets skipped
    "rsqb_alias.md": "[[RSQTarget|a]b]] end\n",                    # alias may contain single ]
    "glued_mdlink.md": "[[Glued]](x.md) and a]](y.md) and [t](z.md)\n",  # ]] never opens [text](
    "dbl_backslash.md": "[[Dbl\\\\|alias]] and [[Trail\\]]\n",     # ONE backslash consumed per boundary
    "comment_overlap.md": "[[A <!-- x --> B]] then [[CleanLink]]\n",
    "comment_in_alias.md": "[[AliasKept|a <!-- h --> b]] and [[Plain2]]\n",  # comment in ALIAS: link stays
    # a fence marker inside an OPEN comment is literal; the comment closes at -->
    # and later links are active (probed against Obsidian 2026-08-26)
    "comment_owns_fence.md": "<!--\n```\n-->\n[[AfterCmtFence]]\n",
    "nbsp_fence.md": "\u00a0\u00a0```\n[[NbspNotFenced]]\n",  # NBSP is not fence indent in either engine
    "triple_backslash.md": "[[Tri\\\\\\|alias]] and [[TrailTwo\\\\]]\n",  # one backslash consumed per boundary
}
# EXPECTED is hand-authored from the documented/probed semantics — an artifact
# independent of BOTH engines, so a shared implementation bug cannot bless
# itself through the rust==python comparison alone (review 2026-08-26).
# Tuples: (file, 1-based line, kind w|m, target).
EXPECTED = {
    ("plain.md", 1, "w", "One"), ("plain.md", 1, "w", "Two"),
    ("plain.md", 1, "w", "Three"), ("plain.md", 1, "w", "Four"),
    ("fenced.md", 4, "w", "Visible"),
    ("tilde.md", 4, "w", "VisibleAfterTilde"),
    ("mixed_fence.md", 5, "w", "VisibleNow"),
    ("inline.md", 1, "w", "NotInCode"),
    ("double_backtick.md", 1, "w", "OutsideDouble"),
    ("triple_inline.md", 1, "w", "AfterTriple"),
    ("unclosed_backtick.md", 1, "w", "UnclosedSpan"),
    ("nested_fence.md", 8, "w", "AfterNested"),
    # long_fence: ```` (4-run) CLOSES the ``` opener; the later ``` re-opens a
    # fence that swallows [[AfterShort]] — so no links at all
    ("frontmatter.md", 3, "w", "FromYaml"), ("frontmatter.md", 5, "w", "FromBody"),
    ("unterminated_fm.md", 4, "w", "AfterUnterminated"),
    ("mdlinks.md", 1, "m", "Some%20Note.md"), ("mdlinks.md", 1, "m", "sub/Other.md"),
    ("mdlinks.md", 1, "m", "http://x.com/page.md"),
    ("unicode.md", 1, "w", "Überblick"), ("unicode.md", 1, "w", "日本語ノート"),
    ("unicode.md", 1, "w", "emoji🚀note"),
    ("indented_fence.md", 4, "w", "AfterIndented"),
    ("crlf.md", 1, "w", "CrlfOne"), ("crlf.md", 5, "w", "CrlfTwo"),
    ("escaped_pipe.md", 1, "w", "TableTarget"), ("escaped_pipe.md", 2, "w", "Frag"),
    ("escaped_pipe.md", 3, "w", "TrailBack"),
    ("html_comment.md", 1, "w", "AfterComment"), ("html_comment.md", 5, "w", "AfterBlock"),
    ("html_comment.md", 6, "w", "InPercent"), ("html_comment.md", 7, "w", "NotAComment"),
    ("comment_in_fence.md", 4, "w", "NotSwallowed"),
    ("runlen.md", 1, "w", "RunLenY"),
    ("empty_targets.md", 1, "w", "Real Target"),
    ("rsqb_alias.md", 1, "w", "RSQTarget"),
    ("glued_mdlink.md", 1, "w", "Glued"), ("glued_mdlink.md", 1, "m", "z.md"),
    ("dbl_backslash.md", 1, "w", "Dbl\\"), ("dbl_backslash.md", 1, "w", "Trail"),
    ("comment_overlap.md", 1, "w", "CleanLink"),
    ("comment_in_alias.md", 1, "w", "AliasKept"), ("comment_in_alias.md", 1, "w", "Plain2"),
    ("comment_owns_fence.md", 4, "w", "AfterCmtFence"),
    ("nbsp_fence.md", 2, "w", "NbspNotFenced"),
    ("triple_backslash.md", 1, "w", "Tri\\\\"), ("triple_backslash.md", 1, "w", "TrailTwo\\"),
}
tmp = tempfile.mkdtemp(prefix="vv-parity-")
for name, body in CASES.items():
    with open(os.path.join(tmp, name), "w", newline="") as f:
        f.write(body)
r, p = rust_links(tmp), py_links(tmp)
check("synthetic: python == EXPECTED", p == EXPECTED,
      f"only-python={sorted(p - EXPECTED)[:4]} only-expected={sorted(EXPECTED - p)[:4]}")
check("synthetic: rust == EXPECTED", r == EXPECTED,
      f"only-rust={sorted(r - EXPECTED)[:4]} only-expected={sorted(EXPECTED - r)[:4]}")
check("synthetic: rust == python", r == p, f"only-rust={sorted(r - p)[:4]} only-python={sorted(p - r)[:4]}")

# ---- search parity: same query -> BYTE-IDENTICAL output in both engines ----
# This used to compare only the "==" path+score headers, waiving snippets as
# "may differ at multi-byte boundaries". Measured 2026-08-27, that waiver was
# hiding a systematic bug rather than an edge case: `w` is a width in CHARACTERS
# (python slices a char-indexed str) but the rust engine sliced BYTES, so every
# snippet containing multi-byte UTF-8 came back short by one char per extra
# byte — 16 of 18 real vault query terms diverged. The corpus below is now
# deliberately full of em dashes, arrows and accents so byte/char slicing cannot
# agree by accident, and the whole stdout is compared.
SEARCH_CORPUS = {
    "Alpha.md": "beta beta beta beta beta beta beta beta\n",   # many mentions
    "beta.md": "unrelated body\n",                              # named match
    "sub/beta notes.md": "one beta mention\n",                  # name + mention
    "sub/gamma.md": "beta once\n",
}
tmp2 = tempfile.mkdtemp(prefix="vv-parity-s-")
os.makedirs(os.path.join(tmp2, "sub"))
for name, body in SEARCH_CORPUS.items():
    with open(os.path.join(tmp2, name), "w") as f:
        f.write(body)
def search_lines(vault, engine, *q):
    env = dict(os.environ, VV_VAULT=vault, VV_ENGINE=engine)
    rr = subprocess.run([sys.executable, os.path.join(REPO, "src", "vv.py"), "search", *q],
                        capture_output=True, text=True, env=env)
    return [l.strip() for l in rr.stdout.split("\n") if l.startswith("==")]
def search_full(vault, engine, *q):
    env = dict(os.environ, VV_VAULT=vault, VV_ENGINE=engine)
    rr = subprocess.run([sys.executable, os.path.join(REPO, "src", "vv.py"), "search", *q],
                        capture_output=True, text=True, env=env)
    return rr.returncode, rr.stdout, rr.stderr
rs = search_lines(tmp2, "rust", "beta")
ps = search_lines(tmp2, "python", "beta")
check("search: rust == python (paths+scores)", rs == ps and len(rs) == 4, f"rust={rs} py={ps}")
# the snippet BODY, not just the ranking — this is what the old waiver hid.
# Its own corpus: deliberately multi-byte, so byte-slicing and char-slicing
# cannot agree by accident, and separate from the ranking fixture above.
tmp3 = tempfile.mkdtemp(prefix="vv-parity-utf8-")
os.makedirs(os.path.join(tmp3, "sub"))
UTF8_CORPUS = {
    "utf8.md": ("beta \u2014 em dashes \u2014 arrows \u2192 \u2192 caf\u00e9 na\u00efve "
                + "padding \u2014 \u00e9\u00e9\u00e9 \u2192 " * 40 + "\n"),
    "sub/utf8 beta.md": ("\u00a1Hola! beta \u2014 \u00fcber \u2192 "
                         + "\u00e9m dash \u2014 " * 60 + "\n"),
    "plain.md": "beta with pure ascii padding " * 30 + "\n",
}
for name, body in UTF8_CORPUS.items():
    with open(os.path.join(tmp3, name), "w") as f:
        f.write(body)
for _w in ("500", "80", "37", "12"):
    fr = search_full(tmp3, "rust", "beta", "--w", _w)
    fp = search_full(tmp3, "python", "beta", "--w", _w)
    check(f"search: rust == python BYTE-identical incl. snippets (--w {_w})", fr == fp,
          f"rust={fr[1][:160]!r} py={fp[1][:160]!r}")
check("search: name matches outrank mention count",
      len(rs) == 4 and "sub/beta notes.md" in rs[0] and rs[1].startswith("== beta.md")
      and "Alpha.md" in rs[2], rs)
shutil.rmtree(tmp2, ignore_errors=True)
shutil.rmtree(tmp, ignore_errors=True)

# ---- live corpus ----
r, p = rust_links(vv.VAULT), py_links(vv.VAULT)
check(f"corpus: rust == python ({len(p)} links)", r == p,
      f"only-rust={sorted(r - p)[:3]} only-python={sorted(p - r)[:3]}")

print(f"\n{len(fails)} failures: {fails}" if fails else "\nENGINE PARITY PASS")
sys.exit(1 if fails else 0)
