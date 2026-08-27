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

print(f"fixture vault at {root}")
for p in sorted(root.rglob("*.md")):
    print("  ", p.relative_to(root))
