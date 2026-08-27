#!/usr/bin/env python3
"""Master full-Rust differential gate.

Every public command assigned to the native rewrite is run against two fresh,
byte-identical vaults: once through vrust and once through the Python semantic
authority.  Output bytes, error bytes, exit status, and (for writers) the
resulting vault bytes must agree.

Set VV_PARITY_EXPECT_NATIVE=read|graph|write|query|search|all to turn on the
positive control.  In that mode each happy-path case in the selected module is
also run with VV_PY_ENTRY aimed at a sentinel; exit 42/SENTINEL proves that the
native handler fell back and therefore fails the gate even if ordinary parity
is perfect.
"""

import datetime
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
VRUST = REPO / "vrust" / "target" / "release" / "vrust"
VV = REPO / "src" / "vv.py"
FALLBACK_SENTINEL = b"FALLBACK_SENTINEL\n"
WRITERS = {"set", "unset", "append", "appendsec", "patch", "new", "daily-append"}
MODULE_COMMANDS = {
    "read": {"outline", "read", "head", "resolve"},
    "graph": {"backlinks", "links", "orphans", "deadends", "impact"},
    "write": WRITERS,
    "query": {"board", "tags", "props", "show"},
    "search": {"search"},
}


@dataclass(frozen=True)
class Case:
    name: str
    module: str
    argv: tuple[str, ...]
    stdin: bytes = b""
    native_expected: bool = True


def put(root: Path, rel: str, data: bytes | str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)


def build_fixture(root: Path) -> None:
    """Build specification pins, including byte-shape and lexer edge cases."""
    today = datetime.date.today().isoformat()

    put(root, "Core.md", """---
type: note
status: open
tags: [alpha, café, 日本語, emoji-🧭, slash/タグ]
plain: ordinary
empty: ""
quoted: "already quoted"
single: 'already ''quoted'''
flow-list: [a, b]
flow-map: {a: b}
colon-space: "alpha: beta"
trailing-colon: "alpha:"
hash-space: "alpha # beta"
lead-bracket: "[broken"
lead-brace: "{broken"
lead-hash: "#hash"
lead-amp: "&anchor"
lead-star: "*alias"
lead-bang: "!tag"
lead-pipe: "|pipe"
lead-fold: ">fold"
lead-single: "'quote"
lead-double: '\"quote'
lead-percent: "%percent"
lead-at: "@at"
lead-backtick: "`tick"
lead-comma: ",comma"
lead-dash: "- value"
lead-question: "? value"
lead-colon: ": value"
padded: " padded "
escaped-controls: "line\\ncarriage\\rtab\\tcontrol\\u0001"
numeric-negative: -1
---
Preamble [[Target]] and [space](Folder/Space%20Note.md).

## Edit
old body

## Tail
tail body
""")
    put(root, "Block Scalar.md", """---
type: note
description: >
  first folded line
  second folded line
literal: |-
  first literal line
  second literal line
---
# Block body
body
""")
    put(root, "Target.md", "---\ntype: target\nstatus: done\ntags: [alpha]\n---\n# Target\nlinked\n")
    put(root, "Folder/Space Note.md", "---\ntype: note\nstatus: done\n---\n# Space Note\nspace\n")

    # Duplicate basenames plus both bare-link winner tiers.  A/Source resolves
    # [[Dup]] to A/Dup (same folder); Root Source resolves it by shortest path,
    # then lexicographically, to A/Dup.
    put(root, "A/Dup.md", "# A duplicate\n")
    put(root, "B/Dup.md", "# B duplicate\n")
    put(root, "A/Source.md", "[[Dup]]\n")
    put(root, "Root Source.md", "[[Dup]]\n")

    put(root, "Links.md", """# Active links
[[Target]] [[Target|alias]] [[Target#Target]]
[encoded space](Folder/Space%20Note.md)
`[[Inline Hidden]]` and ``[[Also Inline Hidden]]``
```
[[Fence Hidden]]
[hidden](Folder/Space%20Note.md)
```
~~~
[[Tilde Hidden]]
~~~
<!-- [[Comment Hidden]] -->
""")
    put(root, "Inline Hidden.md", "# should remain orphaned\n")
    put(root, "Fence Hidden.md", "# should remain orphaned\n")
    put(root, "Tilde Hidden.md", "# should remain orphaned\n")
    put(root, "Comment Hidden.md", "# should remain orphaned\n")
    put(root, "Orphan.md", "---\ntype: note\nstatus: parked\n---\n# Orphan\nnone\n")
    put(root, "No Links.md", "---\ntype: note\n---\n# Dead end\nplain text\n")

    # Exact byte-shape fixtures.
    put(root, "Bytes/BOM CRLF.md", b"\xef\xbb\xbf---\r\ntype: note\r\nstatus: open\r\n---\r\n# CRLF\r\nbody\r\n")
    put(root, "Bytes/No Trailing Newline.md", b"# No final newline\nbody")
    put(root, "Bytes/Empty.md", b"")

    # The 130-byte show budget cuts between the two bytes of an e-acute.  A
    # second section also exercises the continuation-marker path.
    put(root, "Show Unicode.md", "## Multi\n" + ("é" * 180) + "\n\n## Later\nlater\n")
    put(root, "Board/Open.md", "---\ntype: task\nstatus: open\ntags: [café, sprint/一]\n---\n# Open\n")
    put(root, "Board/Done.md", "---\ntype: task\nstatus: done\ntags: [done]\n---\n# Done\n")
    put(root, "Templates/Task Template.md", "---\ntype: task\nstatus: template\n---\n# New task\n")
    put(root, f"Standups/Standup {today}.md", b"---\r\ntype: standup\r\n---\r\n\r\n# Today\r\n")


def edit_sha() -> str:
    return hashlib.sha256(b"## Edit\nold body\n").hexdigest()[:8]


def cases() -> list[Case]:
    result = [
        Case("outline/basic", "read", ("outline", "Core.md")),
        Case("outline/crlf-bom", "read", ("outline", "Bytes/BOM CRLF.md")),
        Case("read/section", "read", ("read", "Core.md", "H1")),
        Case("read/preamble", "read", ("read", "Core.md", "(preamble)")),
        Case("head/frontmatter", "read", ("head", "Core.md")),
        Case("head/no-frontmatter-no-eof-nl", "read", ("head", "Bytes/No Trailing Newline.md")),
        Case("resolve/path", "read", ("resolve", "Folder/Space Note")),
        Case("resolve/ambiguous-error", "read", ("resolve", "Dup"), native_expected=False),
        Case("backlinks/bare-winner", "graph", ("backlinks", "A/Dup.md")),
        Case("backlinks/markdown-percent20", "graph", ("backlinks", "Folder/Space Note.md")),
        Case("links/masking", "graph", ("links", "Links.md")),
        Case("orphans/scoped", "graph", ("orphans", "A")),
        Case("deadends/all", "graph", ("deadends",)),
        Case("impact/target", "graph", ("impact", "Target.md")),
        Case("board/filter", "query", ("board", "Board", "status=open")),
        Case("tags/all-unicode", "query", ("tags",)),
        Case("tags/counts", "query", ("tags", "--counts")),
        # Unsupported options are a deliberate native fallback surface.  Python
        # currently ignores unknown tags arguments; parity pins that behavior.
        Case("tags/unsupported-option", "query", ("tags", "--bogus"), native_expected=False),
        Case("props/scoped", "query", ("props", "status", "Board")),
        Case("show/multibyte-boundary", "query", ("show", "Show Unicode.md", "--max-bytes", "130")),
        Case("show/from", "query", ("show", "Core.md", "--from", "H2", "--max-bytes", "300")),
        Case("search/hit", "search", ("search", "old", "body", "--k", "3", "--w", "80")),
        Case("search/zero-hit", "search", ("search", "definitely-no-such-term-zzqx")),
        Case("unset/basic", "write", ("unset", "Core.md", "status")),
        Case("unset/block-scalar-error", "write", ("unset", "Block Scalar.md", "description"), native_expected=False),
        Case("append/no-trailing-newline", "write", ("append", "Bytes/No Trailing Newline.md", "appended")),
        Case("appendsec/crlf-bom", "write", ("appendsec", "Bytes/BOM CRLF.md", "H1", "inside crlf")),
        Case("patch/success", "write", ("patch", "Core.md", "H1", edit_sha()), "## Edit\nreplacement é\n".encode()),
        Case("patch/stale-error", "write", ("patch", "Core.md", "H1", "deadbeef"), b"replacement\n", False),
        Case("new/plain", "write", ("new", "Created/Plain", "--type", "task", "--status", "open")),
        Case("new/template", "write", ("new", "Created/From Template", "--template", "Task", "--status", "open")),
        Case("daily-append/crlf", "write", ("daily-append", "- deterministic entry")),
    ]

    # One fresh-vault mutation per yaml_scalar decision-table value.  Keeping
    # these as separate cases catches both emitted YAML and complete file bytes.
    scalar_values = [
        ("empty", ""),
        ("wellformed-double", '"already quoted"'),
        ("wellformed-single", "'already ''quoted'''"),
        ("balanced-list", "[a, b]"),
        ("balanced-map", "{a: b}"),
        ("colon-space", "a: b"),
        ("trailing-colon", "a:"),
        ("space-hash", "a # b"),
        ("lead-bracket", "[broken"), ("lead-brace", "{broken"),
        ("lead-hash", "#hash"), ("lead-amp", "&anchor"),
        ("lead-star", "*alias"), ("lead-bang", "!tag"),
        ("lead-pipe", "|pipe"), ("lead-fold", ">fold"),
        ("lead-single", "'broken"), ("lead-double", '"broken'),
        ("lead-percent", "%value"), ("lead-at", "@value"),
        ("lead-backtick", "`value"), ("lead-comma", ",value"),
        ("dash-indicator", "- value"), ("question-indicator", "? value"),
        ("colon-indicator", ": value"), ("dash-alone", "-"),
        ("padding", " padded "), ("newline", "line1\nline2"),
        ("carriage", "left\rright"), ("tab", "left\tright"),
        ("control", "left\x01right"), ("escape", 'slash\\quote"'),
        ("malformed-quoted", '"a" junk"'), ("unbalanced-flow", "[a, b]]"),
        ("negative-number-bare", "-1"), ("dash-word-bare", "-word"),
        ("question-word-bare", "?word"), ("colon-word-bare", ":word"),
        ("ordinary-bare", "plain"),
    ]
    result.extend(
        Case(f"set/yaml/{name}", "write", ("set", "Core.md", "scalar-test", value))
        for name, value in scalar_values
    )
    result.append(Case("set/block-scalar-error", "write", ("set", "Block Scalar.md", "literal", "x"), native_expected=False))
    return result


def env_for(vault: Path, aux: Path, sentinel: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "VV_VAULT": str(vault),
        "VV_ENGINE": "python",  # make src/vv.py the authority, not a Rust round-trip
        "VV_NO_METRICS": "1",
        "VV_INDEX_ROOT": str(aux / "index"),
        "VV_JOURNAL_ROOT": str(aux / "journals"),
    })
    if sentinel is not None:
        env["VV_PY_ENTRY"] = str(sentinel)
    else:
        env.pop("VV_PY_ENTRY", None)
    return env


def run(cmd: list[str], case: Case, env: dict[str, str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        cmd + list(case.argv), input=case.stdin, capture_output=True, env=env,
        timeout=10, check=False,
    )


def vault_bytes(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def short(data: bytes) -> str:
    return repr(data[:240]) + ("..." if len(data) > 240 else "")


def selected_modules() -> set[str]:
    raw = os.environ.get("VV_PARITY_EXPECT_NATIVE", "").strip().lower()
    if not raw:
        return set()
    selected = {part.strip() for part in raw.split(",") if part.strip()}
    if "all" in selected:
        return set(MODULE_COMMANDS)
    unknown = selected - set(MODULE_COMMANDS)
    if unknown:
        raise ValueError("unknown VV_PARITY_EXPECT_NATIVE module(s): " + ", ".join(sorted(unknown)))
    return selected


def main() -> int:
    failures: list[str] = []
    total = 0
    try:
        expected_native = selected_modules()
    except ValueError as exc:
        print(f"FAIL configuration: {exc}")
        print("1 FAILURES (full-parity: 0)")
        return 1

    if not VRUST.is_file():
        print(f"FAIL missing native binary: {VRUST}")
        print("1 FAILURES (full-parity: 0)")
        return 1

    with tempfile.TemporaryDirectory(prefix="vv-full-parity-") as td:
        work = Path(td)
        fixture = work / "fixture"
        fixture.mkdir()
        build_fixture(fixture)
        sentinel = work / "fallback_sentinel.py"
        sentinel.write_text("import sys\nprint('FALLBACK_SENTINEL')\nsys.exit(42)\n", encoding="utf-8")

        for number, case in enumerate(cases()):
            native_vault = work / f"n-{number}"
            python_vault = work / f"p-{number}"
            shutil.copytree(fixture, native_vault)
            shutil.copytree(fixture, python_vault)
            native_aux = work / f"na-{number}"
            python_aux = work / f"pa-{number}"

            try:
                native = run([str(VRUST)], case, env_for(native_vault, native_aux))
                python = run(["python3", str(VV)], case, env_for(python_vault, python_aux))
            except (subprocess.TimeoutExpired, OSError) as exc:
                failures.append(f"{case.name}: invocation failed: {exc}")
                total += 1
                continue
            total += 1

            if native.returncode != python.returncode:
                failures.append(
                    f"{case.name}: exit native={native.returncode} python={python.returncode}"
                )
            if native.stdout != python.stdout:
                failures.append(
                    f"{case.name}: stdout native={short(native.stdout)} python={short(python.stdout)}"
                )
            if native.stderr != python.stderr:
                failures.append(
                    f"{case.name}: stderr native={short(native.stderr)} python={short(python.stderr)}"
                )
            if case.argv[0] in WRITERS:
                nb, pb = vault_bytes(native_vault), vault_bytes(python_vault)
                if nb != pb:
                    changed = sorted(set(nb) | set(pb))
                    changed = [p for p in changed if nb.get(p) != pb.get(p)]
                    failures.append(f"{case.name}: vault bytes differ: {changed[:8]}")

            # Positive control is intentionally a separate invocation and
            # disposable vault.  Native success may mutate it; fallback cannot
            # escape the sentinel, so neither result affects parity above.
            if case.module in expected_native and case.native_expected:
                control_vault = work / f"c-{number}"
                shutil.copytree(fixture, control_vault)
                try:
                    control = run(
                        [str(VRUST)], case,
                        env_for(control_vault, work / f"ca-{number}", sentinel),
                    )
                    if control.returncode == 42 and control.stdout == FALLBACK_SENTINEL:
                        failures.append(f"{case.name}: native positive control fell back")
                except (subprocess.TimeoutExpired, OSError) as exc:
                    failures.append(f"{case.name}: positive control invocation failed: {exc}")

    if failures:
        for failure in failures:
            print("FAIL", failure)
        print(f"{len(failures)} FAILURES (full-parity: {total})")
        return 1
    print(f"ALL PASS (full-parity: {total})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
