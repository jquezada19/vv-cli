#!/usr/bin/env python3
"""Inventory of the Python standard-library behaviour the native engine must
reproduce to answer every command without the python entry.

    python3 bench/port_inventory.py            # print the markdown
    python3 bench/port_inventory.py --write    # rewrite docs/port-inventory.md
    python3 bench/port_inventory.py --check    # exit 1 if the doc is out of date

The doc is generated, so it cannot drift from src/vv_impl.py: the gate runs
--check. A function this script has not been told about is listed as
`unassigned`, and --check fails on it — a new python function has to be given a stage before it
can land, whether or not it uses one of the listed facilities.
"""
import collections, os, re, sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "src", "vv_impl.py")
DOC = os.path.join(REPO, "docs", "port-inventory.md")

# What each facility costs the port: the behaviour that differs from the
# obvious Rust std call, i.e. what a vector has to pin.
FACILITIES = collections.OrderedDict([
    ("re", (r"\bre\.(compile|match|search|sub|fullmatch|finditer|findall|split|escape)\(",
            "No regex crate: each pattern becomes a hand-written matcher. `\\w` and `\\s` are Unicode-aware in python; `(?<!…)` lookbehind and lazy `.*?` need explicit code.")),
    ("difflib", (r"\bdifflib\.",
                 "`get_close_matches`: Ratcliff/Obershelp ratio, 0.6 cutoff, autojunk for sequences of 200+, result order by score then string.")),
    ("shlex", (r"\bshlex\.", "`quote`: unquoted only for `[A-Za-z0-9_@%+=:,./-]+`, `''` for empty, `'\"'\"'` for an embedded single quote.")),
    ("json", (r"\bjson\.(dumps|loads|load|dump)\(",
              "`dumps`: `\", \"` and `\": \"` separators, insertion order, `ensure_ascii=False` leaves non-ASCII raw; controls are escaped (`\\n`, `\\t`, `\\r`, `\\b`, `\\f` short forms, the rest as `\\u00XX`). `loads`: error text and non-string coercion are observable in `batch`.")),
    ("os.path.normpath", (r"os\.path\.normpath\(", "Lexical: collapses `.`, `..`, repeated `/` (exactly two leading slashes are kept); never touches the filesystem; `\"\"` becomes `.`.")),
    ("os.path.relpath", (r"os\.path\.relpath\(", "Lexical on absolute forms of both arguments; produces `..` segments.")),
    ("os.path.realpath", (r"os\.path\.realpath\(", "Resolves symlinks and succeeds on a missing tail, where `fs::canonicalize` fails.")),
    ("os.walk / scandir / listdir", (r"os\.(walk|scandir|listdir)\(", "`os.walk` is top-down and lists a directory fully before descending; error order and symlinked directories follow from that.")),
    ("glob", (r"\bglob\.(glob|iglob|escape)\(", "`**` with `recursive=True`; names starting with `.` are not matched by `*`; metacharacters in the template name are patterns.")),
    ("urllib.parse", (r"urllib\.parse\.\w+\(", "`quote` keeps `/` and unreserved characters; `unquote` passes malformed escapes through unchanged.")),
    ("datetime / time", (r"(datetime\.\w+|\.strftime\(|\.isoformat\(|time\.(time|localtime|strftime)\()",
                         "Local wall-clock time. Rust std has no time zone database; the native engine asks `date`.")),
    ("unicodedata", (r"unicodedata\.\w+\(", "Normalisation (NFC) needs tables Rust std does not ship.")),
    ("str.lower()", (r"\.lower\(\)", "Whole-string lowercasing with the final-sigma rule, as `str::to_lowercase` on a whole string; per-character lowercasing differs. Table versions can differ between a python and a Rust release.")),
    ("str.casefold()", (r"\.casefold\(\)", "Full case folding (`ß` becomes `ss`); no std equivalent.")),
    ("str.split()", (r"\.split\(\)", "Splits on Unicode whitespace plus U+001C..U+001F and drops empty fields.")),
    ("str.splitlines()", (r"\.splitlines\(", "Also breaks on `\\r`, `\\v`, `\\f`, U+001C..U+001E, U+0085, U+2028, U+2029.")),
    ("repr()", (r"(!r\}|\brepr\()", "Quote choice depends on the content; non-printables are escaped.")),
    ("sorted / sort", (r"(\bsorted\(|\.sort\()", "Code-point order on `str`, which equals byte order on UTF-8. `PathBuf` ordering is component-wise and differs.")),
    ("subprocess", (r"subprocess\.\w+\(", "Child processes: the native scanner, `git`, the vault's lint script.")),
    ("sqlite3", (r"sqlite3\.\w+", "The python index. The native engine has its own cache and never opens this file.")),
    ("hashlib", (r"hashlib\.\w+\(", "SHA-256; the native engine has its own implementation, pinned against known digests.")),
    ("tempfile / shutil", (r"\b(_tf|_sh|tempfile|shutil)\.\w+\(", "`mkstemp` in the target directory (mode 0600); `copy2` preserves times; `rmtree` on journal cleanup.")),
    ("collections.Counter", (r"\bCounter\(", "`most_common` breaks ties by first insertion.")),
])

NATIVE = "partly native today"
STAGES = collections.OrderedDict([
    (NATIVE, "parse fence_mask masked_lines split_fm_full fm_props find_sec _match_sec _ends_at_break sha8 "
             "yaml_scalar block_scalar_key _fm_parts atomic_write cmd_set cmd_unset "
             "scan_links link_matches bare_resolves cmd_backlinks cmd_orphans cmd_deadends "
             "cmd_board cmd_props cmd_tags cmd_search _search_hits _parse_search_args "
             "md_files contain _contained resolve _resolve_id_prefix _name_hits rel _cf _log "
             "_metrics_src _elapsed_ms fm_bounds sec_text split_fm file_sig read_raw _read_strict _read_lossy "
             "_elem_cr _insert_cr _last_eol _fm_add _fm_join splice cmd_outline _peel_read_flags cmd_read cmd_head "
             "cmd_resolve cmd_patch cmd_appendsec cmd_append _is_wellformed_quoted _is_balanced_flow _fm_key_ok "
             "cmd_links code_spans strip_inline_code wiki_target link_targets_in cmd_show"),
    ("1 errors and writes", "die _q _incomplete _walk_onerror suggest_names cmd_daily_append cmd_new "
                            "out _esc _neutralise _is_flag _sec_hint _next_set _next_read _next_from_table _surplus_slot "
                            "_wants_section _next_append _arity_hint _check_arity cmd_prepend _dirty_gate"),
    ("2 output modes and reads", "main _table_operands _phrase_hint cmd_batch cmd_changed cmd_templates cmd_unresolved cmd_impact "
                                 "_jrec _list_out _generate _version"),
    ("3 relocation and journal", "_do_relocate _rewrite_links _relocate_tail occurrences basename_index cmd_trash _file_sha256 _ondisk "
                                 "_journal_start _journal_phase _journal_written _journal_written_map _journal_write_manifest "
                                 "_journal_rollback _journal_done _pending_journals _vault_journal_root cmd_rename cmd_move"),
    ("4 lint, index, doctor", "cmd_lint _lint_report _table_pipe_findings cmd_doctor _git _scope _walk_scope "
                              "_index_db_path _index_parse _index_stat_walk _index_sync index_handle cmd_index"),
    ("5 cutover", "use_rust"),
])
STAGE_OF = {fn: stage for stage, fns in STAGES.items() for fn in fns.split()}
REGEX_LITERAL = re.compile(
    r're\.(?:compile|match|search|sub|fullmatch|finditer|findall|split)\(\s*((?:rf?|fr?)?(?:"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'))')


def scan():
    lines = open(SRC, encoding="utf-8").read().split("\n")
    owner, cur = [], "<module>"
    for l in lines:
        m = re.match(r"def (\w+)", l)
        if m:
            cur = m.group(1)
        elif l and not l[0].isspace() and not l.startswith(("#", ")", "]", "}", '"', "'", "@")):
            cur = "<module>"
        owner.append(cur)
    code = [(i, l) for i, l in enumerate(lines) if not l.lstrip().startswith("#")]
    defs = [m.group(1) for l in lines if (m := re.match(r"def (\w+)", l))]
    sites = collections.OrderedDict()
    for name, (pat, _why) in FACILITIES.items():
        rx, hits = re.compile(pat), collections.OrderedDict()
        for i, l in code:
            if rx.search(l):
                hits.setdefault(owner[i], []).append(i + 1)
        sites[name] = hits
    literals = collections.OrderedDict()
    for i, l in code:
        for m in REGEX_LITERAL.finditer(l):
            literals.setdefault(m.group(1), []).append(owner[i])
    return sites, literals, defs


def render():
    sites, literals, defs = scan()
    out = ["# Port inventory: python behaviour the native engine must reproduce", "",
           "Generated by `python3 bench/port_inventory.py --write`; the gate runs `--check`. Do not edit by hand.", "",
           "Each row is a standard-library facility used by `src/vv_impl.py`, what about it differs from the obvious",
           "Rust std call, and the functions that use it grouped by the port stage that owns them. Functions under",
           f"*{NATIVE}* have a native counterpart for their success path; the refusals and unusual inputs they hand off are",
           "still python's and are ported in stage 1.", ""]
    unassigned = {d for d in defs if d not in STAGE_OF}
    gone = sorted(f for f in STAGE_OF if f not in defs)
    for name, hits in sites.items():
        total = sum(len(v) for v in hits.values())
        if not total:
            continue
        out += [f"## {name} — {total} call site{'s' if total != 1 else ''}", "", FACILITIES[name][1], ""]
        by_stage = collections.OrderedDict((s, []) for s in list(STAGES) + ["module level", "unassigned"])
        for fn, ls in hits.items():
            stage = "module level" if fn == "<module>" else STAGE_OF.get(fn, "unassigned")
            if stage == "unassigned":
                unassigned.add(fn)
            by_stage[stage].append(f"`{fn}` ×{len(ls)}" if len(ls) > 1 else f"`{fn}`")
        for stage, fns in by_stage.items():
            if fns:
                out.append(f"- **{stage}:** " + ", ".join(fns))
        out.append("")
    out += [f"## Regular expressions — {len(literals)} distinct literals", "",
            "Every pattern the python engine compiles from a literal. Patterns built from a caller's text",
            "(`re.escape(key)`) are anchored prefix tests. One per line, then the functions that use it.", "", "```text"]
    for lit, fns in literals.items():
        out.append(f"{lit}    <- " + ", ".join(dict.fromkeys(fns)))
    out.append("```")
    out += ["", "## Every function has a stage", "",
            f"{len(defs)} top-level functions in `src/vv_impl.py`, whether or not they use a facility above:", ""]
    for stage in STAGES:
        n = sum(1 for d in defs if STAGE_OF.get(d) == stage)
        out.append(f"- **{stage}:** {n}")
    out += ["", f"Unassigned functions: {len(unassigned)}" + (" — " + ", ".join(sorted(unassigned)) if unassigned else ""),
            f"Stage entries naming no function: {len(gone)}" + (" — " + ", ".join(gone) if gone else ""), ""]
    return "\n".join(out), unassigned | set(gone)


def main():
    text, unassigned = render()
    if "--write" in sys.argv:
        with open(DOC, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"wrote {os.path.relpath(DOC, REPO)}")
    elif "--check" in sys.argv:
        current = open(DOC, encoding="utf-8").read() if os.path.exists(DOC) else ""
        problems = []
        if current != text:
            problems.append("docs/port-inventory.md is out of date — next: python3 bench/port_inventory.py --write")
        if unassigned:
            problems.append("functions with no port stage, or stage entries naming no function: " + ", ".join(sorted(unassigned))
                            + " — next: fix STAGES in bench/port_inventory.py")
        for p in problems:
            print("FAIL " + p)
        print("port inventory: " + ("FAIL" if problems else "in sync, every function has a stage"))
        sys.exit(1 if problems else 0)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
