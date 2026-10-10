#!/usr/bin/env python3
"""Generate a small fixture vault for CI.

The gate's suites need a vault with: notes outside Sandbox/ (so the search
positive control has something to find), a well-linked hub (graph commands), a
folder of frontmattered notes (board/props/tags), duplicate basenames in
different folders (resolution rules), and a Standups/ folder (daily-append).
Deliberately tiny — the real-corpus pass scales with what it is given.
"""
import os, sys, pathlib

root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "fixture-vault")

def note(rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")

def note_bytes(rel, data):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)

note("Hub.md", """---
type: moc
status: open
tags: [hub, fixture]
---

# Hub

Central note. Links to [[Alpha]], [[Beta]], and [[Notes/Alpha]].

## Details

Every query needs a tenant check before it runs.

## Second section

More text so the section map has something to partition.
""")

note("Alpha.md", """---
type: note
status: done
tags: [fixture]
---

# Alpha

Back to [[Hub]]. A tenant check belongs on every query.
""")

note("Beta.md", "# Beta\n\nNo links out at all.\n")

# duplicate basename in a subfolder — exercises resolution rules
note("Notes/Alpha.md", "# Alpha (in Notes)\n\nLinks [[Hub|the hub]].\n")

note("Notes/Orphan.md", "# Orphan\n\nNothing links here and it links nowhere.\n")

for i, status in enumerate(["open", "in-progress", "done"], start=1):
    note(f"Work Items/Item {i}.md", f"""---
type: work-item
status: {status}
tags: [fixture]
---

# Item {i}

A work item in state {status}.
""")

note("Knowledge/Fact.md", """---
type: knowledge
status: open
---

# Fact

Durable knowledge note. See [[Hub]].
""")

for d in ("Sandbox", "Standups", "Templates"):
    (root / d).mkdir(parents=True, exist_ok=True)

note("Templates/todo.md", """---
type: todo
status: open
---

# {{title}}
""")

# Line-ending shapes the real vault contains (pasted documents). The live-vault
# round-trip and the write suites must see these in CI, not only on a laptop.
note_bytes("Shapes/mixed.md",
           b"---\r\ntype: note\r\n---\r\n# One\r\nalpha\r\nbeta\r\n# Two\nlf-a\nlf-b\n# Three\r\ngamma\r\n")
note_bytes("Shapes/mixed-section.md", b"# One\r\nalpha\r\nbeta\ngamma\r\n# Two\nx\n")
note_bytes("Shapes/all-crlf.md", b"---\r\ntype: note\r\n---\r\n# A\r\nx\r\n")
note_bytes("Shapes/no-trailing-nl-crlf.md", b"# A\r\nx\r\n## B\r\ny")
note_bytes("Shapes/bom-crlf.md", "﻿".encode("utf-8") + b"---\r\ntype: note\r\n---\r\n# A\r\nx\r\n")
note_bytes("Shapes/lone-cr.md", b"# A\nline with a lone \r in it\nx\n")
note_bytes("Shapes/fm-lf-body-crlf.md", b"---\ntype: note\n---\n# A\r\nx\r\n")

print(f"fixture vault at {root}")
for p in sorted(root.rglob("*.md")):
    print("  ", p.relative_to(root))
