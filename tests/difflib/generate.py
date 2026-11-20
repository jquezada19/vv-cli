#!/usr/bin/env python3
"""Vectors for the native port of difflib.get_close_matches.

Builds a deterministic corpus of invented note-title-like strings and query
words, asks the real difflib for get_close_matches(word, possibilities, n=3,
cutoff=0.6) — the one call shape vv uses — and writes one JSON object per line
to vectors.jsonl: {"word", "possibilities", "expected"}. The Rust unit test in
vrust/src/difflib.rs replays every line.

    python3 tests/difflib/generate.py           # rewrite vectors.jsonl
    python3 tests/difflib/generate.py --check   # regenerate in memory, diff

The corpus is synthetic: every title is assembled from the word lists below.
After generating, the script checks that each edge it is meant to cover
(autojunk changing the answer, score ties, a score of exactly 0.6, ...) occurs
a minimum number of times, so a change to the generator cannot quietly drop one.
"""
import difflib
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "vectors.jsonl")
SEED = 20261120
N, CUTOFF = 3, 0.6

WORDS = (
    "alpha beta gamma delta project plan weekly review notes meeting draft "
    "design spec roadmap backlog sprint retro index daily journal reading list "
    "ideas inbox archive kitchen garden recipe budget travel itinerary music "
    "theory practice lecture chapter summary outline glossary reference api "
    "cache index parser lexer render pipeline deploy release hotfix migration "
    "schema query report metrics dashboard onboarding handbook checklist"
).split()
NON_ASCII = [
    "café", "crème brûlée", "naïve", "résumé", "über", "smörgåsbord", "piñata",
    "東京", "会議メモ", "读书笔记", "회의록", "🚀 launch", "notes 📝", "🎉🎉",
    "e\u0301te\u0301", "cafe\u0301", "n\u0303o", "Ωmega", "straße", "ﬁle",
    "zażółć", "αβγ", "день", "עברית", "中文 title",
]
ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789 -_"
EXOTIC = "éèüñçø東京📝🚀\u0301\u0303ßΩж"


def title(rng):
    k = rng.choice((1, 2, 2, 3, 3, 4))
    parts = [rng.choice(WORDS) for _ in range(k)]
    if rng.random() < 0.15:
        parts.insert(rng.randrange(len(parts) + 1), rng.choice(NON_ASCII))
    if rng.random() < 0.15:
        parts.append(str(rng.randrange(1, 2030)))
    sep = rng.choice((" ", " ", "-", "_", " - "))
    return sep.join(parts).lower()


def edit(rng, s):
    """One random edit: substitute, insert, delete, or transpose."""
    pool = ALPHABET + EXOTIC if rng.random() < 0.2 else ALPHABET
    op = rng.choice("sidt")
    if not s:
        return rng.choice(pool)
    i = rng.randrange(len(s))
    if op == "s":
        return s[:i] + rng.choice(pool) + s[i + 1:]
    if op == "i":
        return s[:i] + rng.choice(pool) + s[i:]
    if op == "d":
        return s[:i] + s[i + 1:]
    if len(s) < 2:
        return s + s
    i = min(i, len(s) - 2)
    return s[:i] + s[i + 1] + s[i] + s[i + 2:]


def substring(rng, s):
    if len(s) < 2:
        return s
    i = rng.randrange(len(s))
    j = rng.randrange(i + 1, len(s) + 1)
    return s[i:j]


def long_text(rng, lo=200, hi=300):
    """A long title-like string: few distinct characters dominate (spaces,
    vowels), so autojunk's popular-element rule has something to drop."""
    out = []
    while sum(len(w) + 1 for w in out) < rng.randrange(lo, hi):
        out.append(rng.choice(WORDS))
    if rng.random() < 0.3:
        out.insert(rng.randrange(len(out)), rng.choice(NON_ASCII))
    return " ".join(out)


def mutate(rng, s, times):
    for _ in range(times):
        s = edit(rng, s)
    return s


def case_near(rng, pool):
    """A query near one title, against a slice of the pool plus its variants."""
    target = rng.choice(pool)
    word = mutate(rng, target, rng.choice((1, 1, 1, 2, 3)))
    poss = rng.sample(pool, rng.randrange(0, 15))
    poss += [target] + [mutate(rng, target, rng.randrange(1, 4)) for _ in range(rng.randrange(0, 5))]
    rng.shuffle(poss)
    return word, poss


def case_substring(rng, pool):
    target = rng.choice(pool)
    word = substring(rng, target)
    poss = rng.sample(pool, rng.randrange(0, 12)) + [target, target + " " + rng.choice(WORDS)]
    rng.shuffle(poss)
    return word, poss


def case_transpose(rng, pool):
    target = rng.choice(pool)
    word = target
    for _ in range(rng.choice((1, 2))):
        if len(word) >= 2:
            i = rng.randrange(len(word) - 1)
            word = word[:i] + word[i + 1] + word[i] + word[i + 2:]
    poss = rng.sample(pool, rng.randrange(0, 12)) + [target]
    rng.shuffle(poss)
    return word, poss


def case_non_ascii(rng, pool):
    base = rng.choice(NON_ASCII)
    word = mutate(rng, base, rng.choice((0, 1, 1, 2)))
    poss = rng.sample(NON_ASCII, rng.randrange(1, len(NON_ASCII)))
    poss += [mutate(rng, base, 1) for _ in range(3)] + rng.sample(pool, rng.randrange(0, 10))
    rng.shuffle(poss)
    return word, poss


def case_duplicates(rng, pool):
    word, poss = case_near(rng, pool)
    poss = poss + rng.sample(poss, min(len(poss), rng.randrange(1, 6))) if poss else [word, word]
    rng.shuffle(poss)
    return word, poss


def case_ties(rng, pool):
    """Several candidates one substitution away at the same position: equal
    scores, so only the string order decides the result."""
    base = rng.choice(pool)
    if len(base) < 3:
        base = base + "abc"
    i = rng.randrange(len(base))
    letters = rng.sample(ALPHABET.strip() + "éü東", rng.randrange(3, 8))
    poss = [base[:i] + c + base[i + 1:] for c in letters]
    poss += rng.sample(pool, rng.randrange(0, 8))
    rng.shuffle(poss)
    return base, poss


def case_boundary(rng, pool):
    """Scores at, just above and just below 0.6: a word of length L against
    candidates of total length T with m matching characters, 2m/(L+T) == 0.6
    when 10m == 3(L+T)."""
    L = rng.choice((5, 10, 15, 20, 25))
    word = "".join(rng.choice("abcdefghij") for _ in range(L))
    poss = []
    for T in (L - 5, L, L + 5, L + 10):
        if T <= 0:
            continue
        m = 3 * (L + T) // 10
        if 10 * m != 3 * (L + T) or m > min(L, T):
            continue
        for delta in (-1, 0, 0, 0, 1):
            mm = max(0, min(L, T, m + delta))
            keep = word[:mm]
            filler = "".join(rng.choice("klmnopqrstuvwxyz") for _ in range(T - mm))
            poss.append(keep + filler if rng.random() < 0.5 else filler + keep)
    poss += rng.sample(pool, rng.randrange(0, 6))
    rng.shuffle(poss)
    return word, poss


def case_long(rng, pool):
    """A query of 200+ characters (autojunk applies to the word, which is
    difflib's second sequence) against long variants of itself."""
    word = long_text(rng)
    poss = [mutate(rng, word, rng.randrange(1, 40)) for _ in range(rng.randrange(1, 6))]
    poss += [long_text(rng) for _ in range(rng.randrange(0, 4))]
    poss += [substring(rng, word) for _ in range(rng.randrange(0, 3))]
    poss += rng.sample(pool, rng.randrange(0, 5))
    rng.shuffle(poss)
    return word, poss


def case_long_candidate(rng, pool):
    """A short query against long candidates: autojunk does not apply (the
    candidates are difflib's first sequence)."""
    t = long_text(rng)
    word = mutate(rng, t, rng.randrange(0, 20))
    poss = [t, long_text(rng), rng.choice(pool)]
    return word, poss


def case_empty(rng, pool):
    kind = rng.randrange(4)
    if kind == 0:
        return "", rng.sample(pool, rng.randrange(0, 10)) + [""]
    if kind == 1:
        return "", []
    if kind == 2:
        return rng.choice(pool), []
    return rng.choice(pool), ["", "", rng.choice(pool)]


KINDS = [
    (case_near, 700), (case_substring, 250), (case_transpose, 250),
    (case_non_ascii, 250), (case_duplicates, 150), (case_ties, 250),
    (case_boundary, 250), (case_long, 160), (case_long_candidate, 60),
    (case_empty, 40),
]


def build():
    rng = random.Random(SEED)
    pool = sorted({title(rng) for _ in range(600)})
    cases = []
    for fn, count in KINDS:
        for _ in range(count):
            word, poss = fn(rng, pool)
            cases.append((word, poss))
    # hand-picked: the docstring examples and a few fixed edges
    cases += [
        ("appel", ["ape", "apple", "peach", "puppy"]),
        ("wheel", ["while", "whale", "wheeled", "wholly"]),
        ("abcde", ["abcxy", "abxyz", "abcdx", "xabcd"]),
        ("ab", ["ba", "ab", "ab", "a", "b"]),
        ("a", ["a", "", "aa", "b"]),
        ("e\u0301", ["é", "e", "\u0301"]),
        ("📝", ["📝📝", "📝 notes", "📝"]),
        ("tab\there", ["tab here", "tab\there", "line\nbreak"]),
    ]
    return cases


def coverage(cases):
    """Counts of the edges the corpus must keep covering."""
    c = dict(autojunk_word=0, autojunk_changes=0, ties=0, exact_cutoff=0,
             empty_word=0, empty_candidates=0, duplicates=0, non_ascii=0,
             combining=0, astral=0, full_results=0, empty_results=0)
    for word, poss in cases:
        res = difflib.get_close_matches(word, poss, N, CUTOFF)
        c["full_results"] += len(res) == N
        c["empty_results"] += not res
        c["empty_word"] += word == ""
        c["empty_candidates"] += not poss
        c["duplicates"] += len(set(poss)) < len(poss)
        s = word + "".join(poss)
        c["non_ascii"] += any(ord(ch) > 127 for ch in s)
        c["combining"] += any(0x300 <= ord(ch) < 0x370 for ch in s)
        c["astral"] += any(ord(ch) > 0xFFFF for ch in s)
        if len(word) >= 200:
            c["autojunk_word"] += 1
            raw = []
            for x in poss:
                sm = difflib.SequenceMatcher(None, x, word, autojunk=False)
                if sm.ratio() >= CUTOFF:
                    raw.append((sm.ratio(), x))
            c["autojunk_changes"] += [x for _, x in sorted(raw, reverse=True)[:N]] != res
        scores = []
        for x in poss:
            sm = difflib.SequenceMatcher(None, x, word)
            r = sm.ratio()
            c["exact_cutoff"] += r == CUTOFF
            if r >= CUTOFF:
                scores.append((r, x))
        top = sorted(scores, reverse=True)
        c["ties"] += any(top[i][0] == top[i + 1][0] and top[i][1] != top[i + 1][1]
                         for i in range(min(len(top), N + 1) - 1))
    return c


MINIMUMS = dict(autojunk_word=150, autojunk_changes=10, ties=200, exact_cutoff=100,
                empty_word=5, empty_candidates=5, duplicates=100, non_ascii=200,
                combining=20, astral=20, full_results=300, empty_results=50)


def render(cases):
    lines = []
    for word, poss in cases:
        exp = difflib.get_close_matches(word, poss, N, CUTOFF)
        lines.append(json.dumps({"word": word, "possibilities": poss, "expected": exp},
                                ensure_ascii=False))
    return "\n".join(lines) + "\n"


def main():
    cases = build()
    cov = coverage(cases)
    short = {k: (cov[k], v) for k, v in MINIMUMS.items() if cov[k] < v}
    if short:
        print(f"FAIL coverage below minimum (got, want): {short}")
        return 1
    text = render(cases)
    if "--check" in sys.argv[1:]:
        try:
            with open(OUT, encoding="utf-8") as f:
                cur = f.read()
        except FileNotFoundError:
            cur = None
        if cur != text:
            print("FAIL tests/difflib/vectors.jsonl is stale — run python3 tests/difflib/generate.py")
            return 1
        print(f"{len(cases)} vectors fresh")
        return 0
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"wrote {len(cases)} vectors; coverage {cov}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
