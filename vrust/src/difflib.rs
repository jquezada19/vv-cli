// Python's `difflib.get_close_matches`, as CPython computes it.
//
// The one call shape vv needs is `get_close_matches(word, names, n=3,
// cutoff=0.6)` (python's `suggest_names`), with `isjunk=None` and
// `autojunk=True` — the only `SequenceMatcher` configuration ported here.
// Sequences are compared element by element over Unicode code points, which is
// what indexing a python `str` yields and what `char` is in Rust. Each method
// below mirrors the CPython method of the same name, step for step, including
// the tie-breaking that decides which longest match is found first; the
// vectors in `tests/difflib/vectors.jsonl` are the proof.
// The first caller arrives with the `did you mean:` suggestion slice.
#![allow(dead_code)]

use std::collections::HashMap;

/// `SequenceMatcher(None, a, b)` for `str` sequences. `b` is fixed at
/// construction (python's `set_seq2`, which builds `b2j`); `a` is swapped per
/// candidate (python's `set_seq1`), which drops only the matching-block cache.
struct SequenceMatcher {
    a: Vec<char>,
    b: Vec<char>,
    /// element of `b` -> the ascending indices where it occurs, minus the
    /// autojunk "popular" elements. With `isjunk=None`, `bjunk` is always
    /// empty, so the junk-extension passes of `find_longest_match` never fire.
    b2j: HashMap<char, Vec<usize>>,
    /// element of `b` -> how often it occurs (python's `fullbcount`).
    fullbcount: HashMap<char, usize>,
}

/// python's `_calculate_ratio`: `2.0 * matches / length`, evaluated in that
/// order so the float is the one python produces.
fn calculate_ratio(matches: usize, length: usize) -> f64 {
    if length != 0 {
        2.0 * matches as f64 / length as f64
    } else {
        1.0
    }
}

impl SequenceMatcher {
    fn new(b: &str) -> Self {
        let b: Vec<char> = b.chars().collect();
        let mut b2j: HashMap<char, Vec<usize>> = HashMap::new();
        for (i, &elt) in b.iter().enumerate() {
            b2j.entry(elt).or_default().push(i);
        }
        // autojunk: in a `b` of 200 or more items, an element occurring more
        // than 1% (+1) of the time is "popular" and dropped from b2j. It is not
        // added to bjunk, so matches may still extend across it.
        let n = b.len();
        if n >= 200 {
            let ntest = n / 100 + 1;
            b2j.retain(|_, idxs| idxs.len() <= ntest);
        }
        let mut fullbcount: HashMap<char, usize> = HashMap::new();
        for &elt in &b {
            *fullbcount.entry(elt).or_insert(0) += 1;
        }
        SequenceMatcher {
            a: Vec::new(),
            b,
            b2j,
            fullbcount,
        }
    }

    fn set_seq1(&mut self, a: &str) {
        self.a.clear();
        self.a.extend(a.chars());
    }

    /// Longest matching block in `a[alo..ahi]` / `b[blo..bhi]`: of all maximal
    /// blocks, the one starting earliest in `a`, and of those the one starting
    /// earliest in `b`. Returns `(i, j, k)`.
    fn find_longest_match(
        &self,
        alo: usize,
        ahi: usize,
        blo: usize,
        bhi: usize,
    ) -> (usize, usize, usize) {
        let (a, b) = (&self.a, &self.b);
        let (mut besti, mut bestj, mut bestsize) = (alo, blo, 0usize);
        // j2len[j] = length of the longest match ending with a[i-1] and b[j]
        let mut j2len: HashMap<usize, usize> = HashMap::new();
        let mut newj2len: HashMap<usize, usize> = HashMap::new();
        for i in alo..ahi {
            newj2len.clear();
            if let Some(js) = self.b2j.get(&a[i]) {
                for &j in js {
                    if j < blo {
                        continue;
                    }
                    if j >= bhi {
                        break;
                    }
                    let prev = if j == 0 {
                        0
                    } else {
                        j2len.get(&(j - 1)).copied().unwrap_or(0)
                    };
                    let k = prev + 1;
                    newj2len.insert(j, k);
                    if k > bestsize {
                        besti = i + 1 - k;
                        bestj = j + 1 - k;
                        bestsize = k;
                    }
                }
            }
            std::mem::swap(&mut j2len, &mut newj2len);
        }
        // Extend across elements b2j does not index (the popular ones). With
        // bjunk empty, `not isbjunk(...)` is always true, so only equality
        // decides, and CPython's two junk-only passes that follow are no-ops.
        while besti > alo && bestj > blo && a[besti - 1] == b[bestj - 1] {
            besti -= 1;
            bestj -= 1;
            bestsize += 1;
        }
        while besti + bestsize < ahi
            && bestj + bestsize < bhi
            && a[besti + bestsize] == b[bestj + bestsize]
        {
            bestsize += 1;
        }
        (besti, bestj, bestsize)
    }

    /// python's `get_matching_blocks`: the recursive split, run off an explicit
    /// stack popped from the end, then sorted and with adjacent blocks merged,
    /// ending in the `(len(a), len(b), 0)` sentinel.
    fn get_matching_blocks(&self) -> Vec<(usize, usize, usize)> {
        let (la, lb) = (self.a.len(), self.b.len());
        let mut queue = vec![(0usize, la, 0usize, lb)];
        let mut matching_blocks: Vec<(usize, usize, usize)> = Vec::new();
        while let Some((alo, ahi, blo, bhi)) = queue.pop() {
            let x = self.find_longest_match(alo, ahi, blo, bhi);
            let (i, j, k) = x;
            if k != 0 {
                matching_blocks.push(x);
                if alo < i && blo < j {
                    queue.push((alo, i, blo, j));
                }
                if i + k < ahi && j + k < bhi {
                    queue.push((i + k, ahi, j + k, bhi));
                }
            }
        }
        matching_blocks.sort_unstable();
        let (mut i1, mut j1, mut k1) = (0usize, 0usize, 0usize);
        let mut non_adjacent = Vec::with_capacity(matching_blocks.len() + 1);
        for (i2, j2, k2) in matching_blocks {
            if i1 + k1 == i2 && j1 + k1 == j2 {
                k1 += k2;
            } else {
                if k1 != 0 {
                    non_adjacent.push((i1, j1, k1));
                }
                (i1, j1, k1) = (i2, j2, k2);
            }
        }
        if k1 != 0 {
            non_adjacent.push((i1, j1, k1));
        }
        non_adjacent.push((la, lb, 0));
        non_adjacent
    }

    fn ratio(&self) -> f64 {
        let matches: usize = self.get_matching_blocks().iter().map(|t| t.2).sum();
        calculate_ratio(matches, self.a.len() + self.b.len())
    }

    /// Upper bound on `ratio`: the multiset intersection size.
    fn quick_ratio(&self) -> f64 {
        let mut avail: HashMap<char, isize> = HashMap::new();
        let mut matches = 0usize;
        for elt in &self.a {
            let numb = match avail.get(elt) {
                Some(&v) => v,
                None => self.fullbcount.get(elt).copied().unwrap_or(0) as isize,
            };
            avail.insert(*elt, numb - 1);
            if numb > 0 {
                matches += 1;
            }
        }
        calculate_ratio(matches, self.a.len() + self.b.len())
    }

    /// Upper bound on `quick_ratio`, from the lengths alone.
    fn real_quick_ratio(&self) -> f64 {
        let (la, lb) = (self.a.len(), self.b.len());
        calculate_ratio(la.min(lb), la + lb)
    }
}

/// python's `difflib.get_close_matches(word, possibilities, n, cutoff)`: up to
/// `n` possibilities whose `ratio` against `word` is at least `cutoff`, best
/// score first, equal scores ordered by the string that compares greater
/// first (python's `heapq.nlargest` over `(score, x)` tuples; a Rust `str`
/// orders by UTF-8 bytes, which is code-point order, as python's `str` does).
///
/// Python raises `ValueError` for `n == 0` or a cutoff outside `[0.0, 1.0]`;
/// no caller passes either, and this returns an empty list for them instead
/// of panicking.
pub fn get_close_matches<'a, I>(word: &str, possibilities: I, n: usize, cutoff: f64) -> Vec<&'a str>
where
    I: IntoIterator<Item = &'a str>,
{
    if n == 0 || !(0.0..=1.0).contains(&cutoff) {
        return Vec::new();
    }
    let mut s = SequenceMatcher::new(word);
    let mut result: Vec<(f64, &'a str)> = Vec::new();
    for x in possibilities {
        s.set_seq1(x);
        if s.real_quick_ratio() >= cutoff && s.quick_ratio() >= cutoff {
            let r = s.ratio();
            if r >= cutoff {
                result.push((r, x));
            }
        }
    }
    // nlargest(n, result) == sorted(result, reverse=True)[:n]. Tuples that
    // compare equal are identical, so the sort's stability cannot show.
    result.sort_unstable_by(|p, q| q.0.total_cmp(&p.0).then_with(|| q.1.cmp(p.1)));
    result.truncate(n);
    result.into_iter().map(|(_, x)| x).collect()
}

#[cfg(test)]
mod tests {
    use super::get_close_matches;

    /// A reader for exactly the vector schema: one JSON object per line whose
    /// values are a string or an array of strings. Anything else is an error,
    /// so a malformed vectors file fails loudly instead of testing less.
    struct Reader<'s> {
        c: std::iter::Peekable<std::str::Chars<'s>>,
    }

    impl Reader<'_> {
        fn ws(&mut self) {
            while matches!(self.c.peek(), Some(' ' | '\t' | '\n' | '\r')) {
                self.c.next();
            }
        }

        fn expect(&mut self, want: char) -> Result<(), String> {
            self.ws();
            match self.c.next() {
                Some(got) if got == want => Ok(()),
                got => Err(format!("expected {want:?}, got {got:?}")),
            }
        }

        fn hex4(&mut self) -> Result<u32, String> {
            let mut v = 0u32;
            for _ in 0..4 {
                let d = self.c.next().and_then(|h| h.to_digit(16));
                v = v * 16 + d.ok_or("bad \\u escape")?;
            }
            Ok(v)
        }

        fn string(&mut self) -> Result<String, String> {
            self.expect('"')?;
            let mut out = String::new();
            loop {
                match self.c.next().ok_or("unterminated string")? {
                    '"' => return Ok(out),
                    '\\' => match self.c.next().ok_or("unterminated escape")? {
                        '"' => out.push('"'),
                        '\\' => out.push('\\'),
                        '/' => out.push('/'),
                        'b' => out.push('\u{8}'),
                        'f' => out.push('\u{c}'),
                        'n' => out.push('\n'),
                        'r' => out.push('\r'),
                        't' => out.push('\t'),
                        'u' => {
                            let mut cp = self.hex4()?;
                            if (0xD800..0xDC00).contains(&cp) {
                                if self.c.next() != Some('\\') || self.c.next() != Some('u') {
                                    return Err("lone high surrogate".into());
                                }
                                let lo = self.hex4()?;
                                if !(0xDC00..0xE000).contains(&lo) {
                                    return Err("bad low surrogate".into());
                                }
                                cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                            }
                            out.push(char::from_u32(cp).ok_or("lone surrogate")?);
                        }
                        e => return Err(format!("bad escape \\{e}")),
                    },
                    ch => out.push(ch),
                }
            }
        }

        fn array(&mut self) -> Result<Vec<String>, String> {
            self.expect('[')?;
            let mut out = Vec::new();
            self.ws();
            if self.c.peek() == Some(&']') {
                self.c.next();
                return Ok(out);
            }
            loop {
                out.push(self.string()?);
                self.ws();
                match self.c.next() {
                    Some(',') => {}
                    Some(']') => return Ok(out),
                    got => return Err(format!("expected , or ], got {got:?}")),
                }
            }
        }
    }

    struct Case {
        word: String,
        possibilities: Vec<String>,
        expected: Vec<String>,
    }

    fn parse_line(line: &str) -> Result<Case, String> {
        let mut r = Reader {
            c: line.chars().peekable(),
        };
        let (mut word, mut poss, mut exp) = (None, None, None);
        r.expect('{')?;
        loop {
            let key = r.string()?;
            r.expect(':')?;
            match key.as_str() {
                "word" => word = Some(r.string()?),
                "possibilities" => poss = Some(r.array()?),
                "expected" => exp = Some(r.array()?),
                k => return Err(format!("unknown key {k:?}")),
            }
            r.ws();
            match r.c.next() {
                Some(',') => {}
                Some('}') => break,
                got => return Err(format!("expected , or }}, got {got:?}")),
            }
        }
        r.ws();
        if r.c.next().is_some() {
            return Err("trailing data".into());
        }
        Ok(Case {
            word: word.ok_or("missing word")?,
            possibilities: poss.ok_or("missing possibilities")?,
            expected: exp.ok_or("missing expected")?,
        })
    }

    #[test]
    fn reader_decodes_escapes() {
        let c = parse_line(
            r#"{"word": "a\"b\\c\n\u00e9\ud83d\ude00\t", "possibilities": [], "expected": ["x", ""]}"#,
        )
        .unwrap();
        assert_eq!(c.word, "a\"b\\c\n\u{e9}\u{1f600}\t");
        assert!(c.possibilities.is_empty());
        assert_eq!(c.expected, ["x", ""]);
        assert!(parse_line(r#"{"word": 1}"#).is_err());
    }

    #[test]
    fn matches_cpython_vectors() {
        let path = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../tests/difflib/vectors.jsonl"
        );
        let text = std::fs::read_to_string(path).expect("read tests/difflib/vectors.jsonl");
        let (mut total, mut bad) = (0usize, Vec::new());
        for (ln, line) in text.lines().enumerate() {
            if line.is_empty() {
                continue;
            }
            let case = parse_line(line).unwrap_or_else(|e| panic!("vectors.jsonl:{}: {e}", ln + 1));
            total += 1;
            let got = get_close_matches(
                &case.word,
                case.possibilities.iter().map(String::as_str),
                3,
                0.6,
            );
            if got != case.expected {
                bad.push(format!(
                    "line {}: word {:?}: got {:?}, want {:?}",
                    ln + 1,
                    case.word,
                    got,
                    case.expected
                ));
            }
        }
        assert!(total >= 2000, "only {total} vectors");
        assert!(
            bad.is_empty(),
            "{} of {total} vectors disagree with CPython:\n{}",
            bad.len(),
            bad.iter().take(10).cloned().collect::<Vec<_>>().join("\n")
        );
    }
}
