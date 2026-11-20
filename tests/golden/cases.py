#!/usr/bin/env python3
"""The golden corpus: for every command the native dispatcher serves, one case
per form in its COMMAND_TABLE `args` string and one per refusal reachable from
the pristine fixture with an argv and a stdin.

A case is (id, argv, stdin). `{sha8 NOTE SEC}` in an argv element is replaced
by that section's current sha8 on the pristine fixture, so a patch case names
real content without a hard-coded hash.
"""

H = "{sha8 Hub.md H2}"        # Hub.md "Details"

CASES = {
    "outline": [
        ("path", ["outline", "Hub.md"], None),
        ("bare-name", ["outline", "Solo"], None),
        ("id", ["outline", "101"], None),
        ("no-frontmatter", ["outline", "Plain.md"], None),
        ("bom", ["outline", "Bom.md"], None),
        ("crlf", ["outline", "Crlf.md"], None),
        ("non-ascii-name", ["outline", "Café — Ünï"], None),
        ("not-found", ["outline", "Nope"], None),
        ("not-found-near", ["outline", "Sollo"], None),
        ("ambiguous-name", ["outline", "Twin"], None),
        ("ambiguous-id", ["outline", "202"], None),
        ("escape", ["outline", "../outside.md"], None),
        ("arity-0", ["outline"], None),
        ("arity-2", ["outline", "Hub.md", "extra"], None),
    ],
    "read": [
        ("sec-id", ["read", "Hub.md", "H2"], None),
        ("sec-title", ["read", "Hub.md", "Details"], None),
        ("sec-hash-heading", ["read", "Hub.md", "#Details"], None),
        ("sec-prefix-word-break", ["read", "Hub.md", "Today"], None),
        ("sec-preamble", ["read", "Hub.md", "(preamble)"], None),
        ("flag-section", ["read", "Hub.md", "--section", "Details"], None),
        ("flag-hash", ["read", "Hub.md", "--hash", H], None),
        ("positional-sha8", ["read", "Hub.md", H], None),
        ("bare-note", ["read", "Area/Solo.md"], None),
        ("by-id-note", ["read", "101", "Work log"], None),
        ("sec-not-found", ["read", "Hub.md", "Nope"], None),
        ("sec-ambiguous-title", ["read", "Hub.md", "Dup"], None),
        ("note-not-found", ["read", "Nope", "H1"], None),
        ("arity-0", ["read"], None),
        ("arity-4", ["read", "Hub.md", "a", "b", "c"], None),
        ("two-selectors", ["read", "Hub.md", "H2", "--hash", H], None),
        ("hash-not-hex", ["read", "Hub.md", "--hash", "zzzzzzzz"], None),
        ("flag-missing-value", ["read", "Hub.md", "--section"], None),
    ],
    "show": [
        ("note", ["show", "Hub.md"], None),
        ("max-bytes", ["show", "Hub.md", "--max-bytes", "120"], None),
        ("from-sec", ["show", "Hub.md", "--from", "H3"], None),
        ("non-ascii-budget", ["show", "Café — Ünï.md", "--max-bytes", "40"], None),
        ("not-found", ["show", "Nope"], None),
        ("arity-0", ["show"], None),
    ],
    "head": [
        ("frontmatter", ["head", "Hub.md"], None),
        ("block-value", ["head", "Notes/Beta.md"], None),
        ("crlf", ["head", "Crlf.md"], None),
        ("no-frontmatter", ["head", "Plain.md"], None),
        ("not-found", ["head", "Nope"], None),
        ("arity-0", ["head"], None),
    ],
    "resolve": [
        ("path", ["resolve", "Notes/Beta.md"], None),
        ("path-no-ext", ["resolve", "Notes/Beta"], None),
        ("bare-name", ["resolve", "Solo"], None),
        ("bare-name-case", ["resolve", "solo"], None),
        ("id", ["resolve", "101"], None),
        ("hash-id", ["resolve", "#" + "101"], None),
        ("exact-basename-beats-id", ["resolve", "303"], None),
        ("not-found", ["resolve", "Nope"], None),
        ("ambiguous-name", ["resolve", "Twin"], None),
        ("ambiguous-id", ["resolve", "202"], None),
        ("id-no-candidate", ["resolve", "999"], None),
        ("arity-0", ["resolve"], None),
    ],
    "search": [
        ("one-term", ["search", "alpha"], None),
        ("two-terms", ["search", "alpha", "beta"], None),
        ("phrase", ["search", "tenant check"], None),
        ("path-term", ["search", "notes/", "alpha"], None),
        ("k", ["search", "alpha", "--k", "1"], None),
        ("w", ["search", "alpha", "--w", "20"], None),
        ("files", ["search", "alpha", "--files"], None),
        ("non-ascii", ["search", "naïve"], None),
        ("name-match", ["search", "solo"], None),
        ("zero-hit", ["search", "zzqqxx"], None),
        ("zero-hit-phrase-hint", ["search", "check tenant"], None),
        ("zero-hit-phrase-no-hint", ["search", "zzqq xxyy"], None),
        ("no-query", ["search"], None),
        ("k-not-int", ["search", "alpha", "--k", "x"], None),
        ("k-missing", ["search", "alpha", "--k"], None),
    ],
    "patch": [
        ("replace", ["patch", "Hub.md", "H2", H], "## Details\n\nRewritten.\n"),
        ("by-title", ["patch", "Hub.md", "Details", H], "## Details\n\nBy title.\n"),
        ("crlf-note", ["patch", "Crlf.md", "Log", "{sha8 Crlf.md Log}"], "## Log\n\n- b\n"),
        ("stale-hash", ["patch", "Hub.md", "H2", "00000000"], "## Details\n\nnever\n"),
        ("preamble-frontmatter", ["patch", "Hub.md", "H0", "{sha8 Hub.md H0}"], "x\n"),
        ("sec-not-found", ["patch", "Hub.md", "Nope", "00000000"], "x\n"),
        ("note-not-found", ["patch", "Nope", "H1", "00000000"], "x\n"),
        ("arity-2", ["patch", "Hub.md", "H2"], "x\n"),
    ],
    "append": [
        ("note-text", ["append", "Area/Solo.md", "- appended"], None),
        ("multi-line", ["append", "Area/Solo.md", "one\ntwo"], None),
        ("no-final-newline", ["append", "NoNewline.md", "tail"], None),
        ("crlf", ["append", "Crlf.md", "- c"], None),
        ("by-id", ["append", "101", "- by id"], None),
        ("note-sec-text", ["append", "Hub.md", "Details", "- into section"], None),
        ("third-operand-not-a-section", ["append", "Hub.md", "Nope", "- x"], None),
        ("not-found", ["append", "Nope", "x"], None),
        ("arity-1", ["append", "Area/Solo.md"], None),
        ("arity-4", ["append", "Area/Solo.md", "a", "b", "c"], None),
    ],
    "appendsec": [
        ("by-id", ["appendsec", "Hub.md", "H2", "- by id"], None),
        ("by-title", ["appendsec", "Hub.md", "Details", "- by title"], None),
        ("last-section", ["appendsec", "Area/Solo.md", "Body", "- last"], None),
        ("crlf", ["appendsec", "Crlf.md", "Log", "- b"], None),
        ("note-by-id", ["appendsec", "101", "Work log", "- more"], None),
        ("sec-not-found", ["appendsec", "Hub.md", "Nope", "x"], None),
        ("sec-ambiguous", ["appendsec", "Hub.md", "Dup", "x"], None),
        ("not-found", ["appendsec", "Nope", "H1", "x"], None),
        ("arity-2", ["appendsec", "Hub.md", "H2"], None),
    ],
    "set": [
        ("replace", ["set", "Area/Solo.md", "status", "done"], None),
        ("add", ["set", "Area/Solo.md", "owner", "me"], None),
        ("no-frontmatter", ["set", "Plain.md", "type", "note"], None),
        ("crlf", ["set", "Crlf.md", "status", "done"], None),
        ("bom", ["set", "Bom.md", "status", "open"], None),
        ("needs-quoting", ["set", "Area/Solo.md", "summary", "a: b # c"], None),
        ("numeric-looking", ["set", "Area/Solo.md", "n", "-1"], None),
        ("by-id", ["set", "101", "status", "done"], None),
        ("block-value-refused", ["set", "Notes/Beta.md", "summary", "x"], None),
        ("bad-key", ["set", "Area/Solo.md", "a=b", "c"], None),
        ("not-found", ["set", "Nope", "k", "v"], None),
        ("arity-2", ["set", "Area/Solo.md", "status"], None),
    ],
    "unset": [
        ("existing", ["unset", "Area/Solo.md", "status"], None),
        ("last-key-crlf", ["unset", "Crlf.md", "status"], None),
        ("missing-key", ["unset", "Area/Solo.md", "nope"], None),
        ("no-frontmatter", ["unset", "Plain.md", "k"], None),
        ("block-value-refused", ["unset", "Notes/Beta.md", "summary"], None),
        ("arity-1", ["unset", "Area/Solo.md"], None),
    ],
    "links": [
        ("hub", ["links", "Hub.md"], None),
        ("inert-links-ignored", ["links", "Notes/Beta.md"], None),
        ("none", ["links", "Orphan.md"], None),
        ("not-found", ["links", "Nope"], None),
    ],
    "backlinks": [
        ("hub", ["backlinks", "Hub.md"], None),
        ("by-name", ["backlinks", "Solo"], None),
        ("none", ["backlinks", "Orphan.md"], None),
        ("not-found", ["backlinks", "Nope"], None),
    ],
    "orphans": [
        ("vault", ["orphans"], None),
        ("folder", ["orphans", "Board"], None),
        ("limit", ["--limit", "2", "orphans"], None),
    ],
    "deadends": [
        ("vault", ["deadends"], None),
        ("arity-1", ["deadends", "x"], None),
    ],
    "board": [
        ("folder", ["board", "Board"], None),
        ("filter-kv", ["board", "Board", "status=open"], None),
        ("filter-flag", ["board", "Board", "--status", "open"], None),
        ("filter-flag-eq", ["board", "Board", "--status=done"], None),
        ("no-match", ["board", "Board", "status=nope"], None),
        ("missing-folder", ["board", "NoSuchFolder"], None),
        ("arity-0", ["board"], None),
    ],
    "tags": [
        ("list", ["tags"], None),
        ("counts", ["tags", "--counts"], None),
    ],
    "props": [
        ("key", ["props", "status"], None),
        ("key-folder", ["props", "status", "Board"], None),
        ("key-absent", ["props", "nokey"], None),
        ("arity-0", ["props"], None),
    ],
}

def _extend(cmd, rows):
    CASES[cmd].extend(rows)

# ---- forms and refusals the first pass missed ----
_extend("outline", [
    ("bare-name-in-folder", ["outline", "Beta"], None),
    ("surplus-flag-like", ["outline", "Hub.md", "--x"], None),
])
_extend("read", [
    ("flag-section-eq", ["read", "Hub.md", "--section=Details"], None),
    ("flag-hash-eq", ["read", "Hub.md", "--hash=" + H], None),
    ("two-positional-selectors", ["read", "Hub.md", "H1", "H2"], None),
    ("positional-and-flag", ["read", "Hub.md", "H1", "--section=H2"], None),
    ("unknown-flag", ["read", "Hub.md", "--other"], None),
    ("flag-section-empty", ["read", "Hub.md", "--section="], None),
    ("flag-hash-missing-value", ["read", "Hub.md", "--hash"], None),
    ("flag-hash-absent", ["read", "Hub.md", "--hash", "00000000"], None),
    ("sec-ambiguous-prefix", ["read", "Hub.md", "Tod"], None),
    ("note-ambiguous-id", ["read", "202", "H1"], None),
    ("note-escape", ["read", "../outside.md", "H1"], None),
])
_extend("show", [
    ("from-and-max-bytes", ["show", "Hub.md", "--from", "H2", "--max-bytes", "40"], None),
    ("from-missing-section", ["show", "Hub.md", "--from", "H99"], None),
    ("note-ambiguous-id", ["show", "202"], None),
    ("arity-surplus", ["show", "Hub.md", "x"], None),
])
_extend("head", [
    ("arity-2", ["head", "Hub.md", "x"], None),
    ("note-ambiguous-id", ["head", "202"], None),
])
_extend("resolve", [
    ("arity-2", ["resolve", "Hub", "x"], None),
    ("escape", ["resolve", "../outside.md"], None),
    ("near-miss-suggestion", ["resolve", "Sollo"], None),
])
_extend("search", [
    ("k-and-w", ["search", "alpha", "--k", "2", "--w", "30"], None),
    ("k-w-files", ["search", "alpha", "--k", "2", "--w", "30", "--files"], None),
    ("k-zero", ["search", "alpha", "--k", "0"], None),
    ("k-plus-and-zeros", ["search", "alpha", "--k", "+002"], None),
    ("global-limit-as-k", ["--limit", "1", "search", "alpha"], None),
    ("w-not-int", ["search", "alpha", "--w", "x"], None),
    ("w-missing", ["search", "alpha", "--w"], None),
    ("k-negative", ["search", "alpha", "--k", "-1"], None),
    ("k-19-digits", ["search", "alpha", "--k", "1111111111111111111"], None),
    ("crlf-snippet", ["search", "crlf"], None),
])
_extend("patch", [
    ("sec-ambiguous-title", ["patch", "Hub.md", "Dup", "00000000"], "x\n"),
    ("arity-4", ["patch", "Hub.md", "H2", "00000000", "x"], "x\n"),
    ("note-ambiguous-id", ["patch", "202", "H1", "00000000"], "x\n"),
])
_extend("append", [
    ("third-operand-ambiguous-section", ["append", "Hub.md", "Dup", "x"], None),
    ("flag-shaped-section", ["append", "Hub.md", "--section", "H2", "text"], None),
    ("note-ambiguous-id", ["append", "202", "x"], None),
])
_extend("appendsec", [
    ("sec-ambiguous-prefix", ["appendsec", "Hub.md", "Tod", "x"], None),
    ("arity-4", ["appendsec", "Hub.md", "H2", "a", "b"], None),
    ("note-ambiguous-id", ["appendsec", "202", "H1", "x"], None),
])
_extend("set", [
    ("arity-4", ["set", "Area/Solo.md", "k", "a", "b"], None),
    ("arity-4-bad-key", ["set", "Area/Solo.md", "a=b", "x", "y"], None),
    ("note-ambiguous-id", ["set", "202", "k", "v"], None),
    ("non-ascii-key", ["set", "Area/Solo.md", "caf\u00e9", "x"], None),
])
_extend("unset", [
    ("arity-3", ["unset", "Area/Solo.md", "k", "x"], None),
    ("not-found", ["unset", "Nope", "k"], None),
    ("note-ambiguous-id", ["unset", "202", "k"], None),
])
_extend("links", [
    ("arity-0", ["links"], None),
    ("arity-2", ["links", "Hub.md", "x"], None),
    ("note-ambiguous-id", ["links", "202"], None),
])
_extend("backlinks", [
    ("arity-0", ["backlinks"], None),
    ("arity-2", ["backlinks", "Hub.md", "x"], None),
    ("note-ambiguous-id", ["backlinks", "202"], None),
])
_extend("orphans", [
    ("arity-2", ["orphans", "Board", "x"], None),
    ("missing-folder", ["orphans", "NoSuchFolder"], None),
    ("file-scope", ["orphans", "Hub.md"], None),
    ("escape-scope", ["orphans", ".."], None),
    ("skip-dir-scope", ["orphans", ".obsidian"], None),
])
_extend("board", [
    ("two-filters", ["board", "Board", "status=open", "owner=ana"], None),
    ("flag-missing-value", ["board", "Board", "--status"], None),
    ("flag-empty-key", ["board", "Board", "--", "open"], None),
    ("bare-malformed-filter", ["board", "Board", "status"], None),
    ("file-scope", ["board", "Hub.md"], None),
    ("escape-scope", ["board", ".."], None),
])
_extend("props", [
    ("arity-3", ["props", "status", "Board", "x"], None),
    ("missing-folder", ["props", "status", "NoSuchFolder"], None),
    ("file-scope", ["props", "status", "Hub.md"], None),
    ("escape-scope", ["props", "status", ".."], None),
])
_extend("tags", [
    ("limit-missing-value", ["tags", "--limit"], None),
    ("limit-zero", ["tags", "--limit", "0"], None),
    ("vault-missing-value", ["tags", "--vault"], None),
])

# Inputs on which the python engine itself dies in a traceback today. They are
# defects, not answers, so they are not golden cases; the generator checks each
# still crashes and fails when one stops, which is the cue to move it into
# CASES above.
ORACLE_CRASHES = [
    ["show", "Hub.md", "--max-bytes", "x"],
]
