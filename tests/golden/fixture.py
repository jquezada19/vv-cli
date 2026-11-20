#!/usr/bin/env python3
"""The frozen vault every golden case runs against.

Dedicated to the goldens: nothing else builds it, so a change to another
suite's fixture cannot invalidate a checked-in vector. A change HERE changes
recorded bytes and shows up as a diff to tests/golden/*.jsonl.
"""
import os

NOTES = {
    "Hub.md": (
        "---\ntype: moc\nstatus: open\ntags: [hub, fixture]\n---\n\n# Hub\n\n"
        "Central note. Links to [[Solo]], [[Notes/Beta]], [[Twin]] and [text](Notes/Beta.md).\n\n"
        "## Details\n\nEvery query needs a tenant check before it runs.\n\n"
        "## Today (Tuesday)\n\n- first line\n\n## Today's plan\n\nlater\n\n"
        "## Dup\n\none\n\n## Dup\n\ntwo\n"
    ),
    "Solo.md": "---\ntype: note\nstatus: draft\n---\n\n# Solo\n\nLinks back to [[Hub]].\n\n## Body\n\nalpha beta gamma\n",
    "Twin.md": "# Twin at the root\n\nalpha\n",
    "Notes/Twin.md": "# Twin in Notes\n\nSee [[Hub]].\n",
    "Notes/Beta.md": (
        "---\ntype: note\nstatus: open\ntags:\n  - fixture\n  - beta\nsummary: |\n  a block\n  value\n---\n\n"
        "# Beta\n\nalpha in the body, and a phrase: tenant check passes.\n\n"
        "```\n[[Hub]] inside a fence is not a link\n```\n\n<!-- [[Solo]] inside a comment is not a link -->\n"
    ),
    "Plain.md": "No frontmatter here.\n\n# Plain\n\ntext\n",
    "Crlf.md": "---\r\ntype: note\r\nstatus: open\r\n---\r\n\r\n# Crlf\r\n\r\n## Log\r\n\r\n- a\r\n",
    "Bom.md": "﻿---\ntype: note\n---\n\n# Bom\n\nbody\n",
    "NoNewline.md": "# NoNewline\n\nends without a newline",
    "Café — Ünï.md": "---\ntype: note\n---\n\n# Café — Ünï\n\nété — naïve → done\n",
    "Work/101 - First ticket.md": "---\ntype: ticket\nstatus: in-progress\n---\n\n# 101\n\n## Work log\n\n- started\n",
    "Work/202 - Dup one.md": "---\ntype: ticket\nstatus: done\n---\n\n# 202 one\n",
    "Other/202 - Dup two.md": "---\ntype: ticket\nstatus: open\n---\n\n# 202 two\n",
    "Work/303.md": "# exact basename 303\n",
    "Work/303 - Also an id.md": "# id form 303\n",
    "Board/a.md": "---\ntype: task\nstatus: open\nowner: ana\ntags: [x, y]\n---\n\n# a\n",
    "Board/b.md": "---\ntype: task\nstatus: done\nowner: bo\ntags: [y]\n---\n\n# b\n",
    "Board/Sub/c.md": "---\ntype: task\nstatus: open\n---\n\n# c\n",
    "Orphan.md": "# Orphan\n\nnothing links here, and it links nowhere\n",
    "Templates/Task.md": "---\ntype: task\nstatus: open\n---\n\n# Task\n",
}


def build(root):
    os.makedirs(os.path.join(root, ".obsidian"))
    for rel, text in NOTES.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(text)
