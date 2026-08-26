#!/usr/bin/env python3
"""Obsidian-as-oracle: vv's link graph vs the app's own metadataCache.

vv reimplements Obsidian's link semantics (the same standing drift risk as the
Rust/Python engine pair, one level up). The strongest check is not more of our
own tests agreeing with each other — it is the authoritative engine. Pattern
stolen from sqlx, which validates queries against a live dev database at build
time instead of reimplementing the SQL parser: when the real engine is
available, ask it.

Opt-in (needs the Obsidian app open on the vault), NOT part of run_tests.sh:
    python3 tests/oracle_obsidian.py [--sample N] [--seed S]
Exits 0 with SKIP when the app/CLI/vault is unavailable. The obsidian CLI
exits 0 even on errors, so output is parsed, never the exit code.
"""
import argparse, os, random, shutil, subprocess, sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "src"))
import vv  # noqa: E402

VAULT_NAME = os.path.basename(os.path.normpath(vv.VAULT))

def obs(*args):
    r = subprocess.run(["obsidian", f"vault={VAULT_NAME}", *args],
                       capture_output=True, text=True, timeout=30)
    return r.stdout.strip()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=15)
    ap.add_argument("--seed", type=int, default=999)
    a = ap.parse_args()

    if not shutil.which("obsidian"):
        print("SKIP: obsidian CLI not installed"); return 0
    info = obs("vault")
    path_line = next((l for l in info.split("\n") if l.startswith("path\t")), "")
    if not path_line.endswith(os.path.realpath(vv.VAULT)) and not path_line.endswith(vv.VAULT.rstrip("/")):
        print(f"SKIP: obsidian is not open on this vault ({path_line or 'no response — app closed?'})")
        return 0

    notes = sorted(vv.rel(p) for p in vv.md_files())
    random.seed(a.seed)
    sample = random.sample(notes, min(a.sample, len(notes)))

    mismatches = 0
    for rel_ in sample:
        raw = obs("backlinks", f"path={rel_}")
        if raw.startswith("Error"):
            print(f"SKIP {rel_}: {raw.splitlines()[0]}"); continue
        # the CLI prints a human sentence, not empty output, for zero backlinks
        theirs = set() if raw == "No backlinks found." else set(l for l in raw.split("\n") if l)
        r = subprocess.run([sys.executable, os.path.join(REPO, "src", "vv.py"), "backlinks", rel_],
                           capture_output=True, text=True)
        ours = set(l for l in r.stdout.split("\n") if l and not l.startswith("("))
        # Known intentional divergences, classified benign rather than failed:
        #  - self-links: Obsidian lists a note linking to itself as its own backlink;
        #    vv excludes self (rename still rewrites self-links via occurrences).
        #  - frontmatter-embedded links (source: "Distilled from [[X]]"): vv counts
        #    them so rename never breaks a provenance string; Obsidian's cache only
        #    counts whole-value property links.
        #  - table rows with an UNESCAPED alias pipe: Obsidian renders no link at all
        #    (the table splits the cell); vv matches charitably and lint flags it
        #    as table-pipe for the author to fix.
        theirs.discard(rel_)
        base = os.path.basename(rel_)[:-3]
        benign = set()
        for x in sorted(ours - theirs):
            text = vv.read_raw(os.path.join(vv.VAULT, x))
            lines = text.split("\n")
            fm_end = vv.fm_bounds(lines)
            link_lines = [i for i, l in enumerate(lines) if base in l and "[[" in l]
            if link_lines and all(i < fm_end for i in link_lines):
                benign.add(x)
            elif link_lines and all(
                    lines[i].lstrip().startswith("|") and f"\\|" not in lines[i]
                    for i in link_lines):
                benign.add(x)   # unescaped table pipe — lint's table-pipe rule owns it
        if ours - benign == theirs:
            note = f" (+{len(benign)} frontmatter-embedded, benign)" if benign else ""
            print(f"PASS {rel_} ({len(theirs)} backlinks{note})")
        else:
            mismatches += 1
            print(f"DIFF {rel_}:")
            for x in sorted(ours - theirs - benign):
                print(f"  only-vv:       {x}")
            for x in sorted(theirs - ours):
                print(f"  only-obsidian: {x}")

    print(f"\n{'ORACLE PASS' if not mismatches else f'{mismatches} mismatched notes'} "
          f"({len(sample)} sampled, seed {a.seed})")
    return 1 if mismatches else 0

if __name__ == "__main__":
    sys.exit(main())
