"""Pure helpers for the corpus-wide and unanswerable graph eval questions.

No database, no LLM: `scripts/generate_graph_eval_extras.py` orchestrates.

* `theme_groups`: sets of chunks about one theme, spread over several files, the
  raw material of a corpus-wide question ("what are the main risks across the
  contracts?"). The theme is the seed chunk's most distinctive terms; the group
  is the best BM25 matches for them, one file at a time so a single long file
  cannot fill it.
* `answerable_from`: the top BM25 chunks for a question, shown to a verifier that
  must confirm an "unanswerable" question really has no answer in the corpus.
"""

from __future__ import annotations

import random
from collections import defaultdict

from evals.solvability import BM25, tokenize

THEME_TERMS = 12   # distinctive terms of the seed that define the theme
POOL = 30          # BM25 candidates considered for the group
MIN_FILES = 2
GROUP_SIZE = (4, 5)


def distinctive_terms(index: BM25, text: str, n: int = THEME_TERMS) -> list[str]:
    """The n highest-idf distinct terms of `text` that exist in the index."""
    terms = {t for t in tokenize(text) if t in index.idf and len(t) >= 4}
    return sorted(terms, key=lambda t: (-index.idf[t], t))[:n]


def theme_groups(
    chunks: list[dict], n_groups: int, rng: random.Random, *, size: tuple[int, int] = GROUP_SIZE
) -> list[list[dict]]:
    """Up to `n_groups` chunk groups, each of `size` chunks from >= MIN_FILES files.

    A chunk is used in at most one group. Chunks need `id`, `source_id`, `content`.
    """
    if not chunks:
        return []
    index = BM25({c["id"]: c["content"] for c in chunks})
    by_id = {c["id"]: c for c in chunks}
    seeds = list(chunks)
    rng.shuffle(seeds)
    used: set[str] = set()
    groups: list[list[dict]] = []
    for seed in seeds:
        if len(groups) >= n_groups:
            break
        if seed["id"] in used:
            continue
        terms = distinctive_terms(index, seed["content"])
        if len(terms) < 3:
            continue
        ranked = [by_id[i] for i in index.rank(" ".join(terms), POOL) if i not in used]
        # round-robin over files, best match first within a file
        per_file: dict[str, list[dict]] = defaultdict(list)
        for c in ranked:
            per_file[c["source_id"]].append(c)
        queues = [q for q in per_file.values()]
        group: list[dict] = []
        while len(group) < size[1] and any(queues):
            for q in queues:
                if q and len(group) < size[1]:
                    group.append(q.pop(0))
        if len(group) >= size[0] and len({c["source_id"] for c in group}) >= MIN_FILES:
            groups.append(group)
            used.update(c["id"] for c in group)
    return groups


def answerable_from(index: BM25, chunks: dict[str, str], question: str, k: int = 5) -> list[tuple[str, str]]:
    """(chunk id, text) of the k best lexical matches for `question`."""
    return [(i, chunks[i]) for i in index.rank(question, k)]
