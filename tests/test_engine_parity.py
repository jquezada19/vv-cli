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
tmp = tempfile.mkdtemp(prefix="vv-parity-")
for name, body in CASES.items():
    with open(os.path.join(tmp, name), "w", newline="") as f:
        f.write(body)
r, p = rust_links(tmp), py_links(tmp)
check("synthetic: rust == python", r == p, f"only-rust={sorted(r - p)[:4]} only-python={sorted(p - r)[:4]}")
shutil.rmtree(tmp, ignore_errors=True)

# ---- live corpus ----
r, p = rust_links(vv.VAULT), py_links(vv.VAULT)
check(f"corpus: rust == python ({len(p)} links)", r == p,
      f"only-rust={sorted(r - p)[:3]} only-python={sorted(p - r)[:3]}")

print(f"\n{len(fails)} failures: {fails}" if fails else "\nENGINE PARITY PASS")
sys.exit(1 if fails else 0)
