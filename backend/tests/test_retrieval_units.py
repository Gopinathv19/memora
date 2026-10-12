"""Pure unit tests for the retrieval hot path: no database, no provider.

Run on their own with ``pytest --noconftest tests/test_retrieval_units.py``;
the shared conftest signs a console user into a real PostgreSQL, which these
tests do not need.
"""

import math
import random
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from app.retrieval.mmr import _cosine, mmr_select
from app.services.credential_service import (
    _LAST_USE_WRITE_INTERVAL_SECONDS,
    _last_use_written,
    _should_record_use,
)
from app.services.retrieval_service import _parse_vector, _vector_literal


@dataclass
class _Cand:
    """The slice of Candidate that MMR reads."""

    name: str
    similarity: float
    vector: list = field(default_factory=list)


def _reference_mmr(query_vector, candidates, top_k, lambda_):
    """The original implementation, kept verbatim as the oracle: recompute
    every pairwise cosine on every round."""
    if top_k <= 0 or not candidates:
        return []
    remaining = list(candidates)
    selected = []
    while remaining and len(selected) < top_k:
        best = None
        best_score = -math.inf
        for cand in remaining:
            redundancy = max((_cosine(cand.vector, sel.vector) for sel in selected), default=0.0)
            score = lambda_ * cand.similarity - (1.0 - lambda_) * redundancy
            if score > best_score:
                best_score = score
                best = cand
        if best is None:
            break
        selected.append(best)
        remaining.remove(best)
    return selected


def _random_candidates(rng: random.Random, n: int, dim: int) -> list[_Cand]:
    out = []
    for i in range(n):
        vec = [rng.gauss(0, 1) for _ in range(dim)]
        norm = math.sqrt(sum(x * x for x in vec))
        vec = [x / norm for x in vec]
        out.append(_Cand(name=f"c{i}", similarity=rng.uniform(0.2, 0.95), vector=vec))
    # Near-duplicates: MMR must push the second copy down.
    out.append(_Cand(name="dup-a", similarity=0.9, vector=list(out[0].vector)))
    out.append(_Cand(name="dup-b", similarity=0.9, vector=list(out[0].vector)))
    # A zero vector, which the cosine guards against.
    out.append(_Cand(name="zero", similarity=0.5, vector=[0.0] * dim))
    return out


class TestMMRMatchesReference:
    @pytest.mark.parametrize("seed", range(8))
    @pytest.mark.parametrize("lambda_", [0.0, 0.5, 0.7, 1.0])
    def test_same_selection_same_order(self, seed, lambda_):
        rng = random.Random(seed)
        cands = _random_candidates(rng, n=30, dim=48)
        query = [rng.gauss(0, 1) for _ in range(48)]
        expected = _reference_mmr(query, cands, top_k=10, lambda_=lambda_)
        got = mmr_select(query, cands, top_k=10, lambda_=lambda_)
        assert [c.name for c in got] == [c.name for c in expected]

    def test_top_k_larger_than_pool_returns_everything(self):
        rng = random.Random(1)
        cands = _random_candidates(rng, n=5, dim=8)
        got = mmr_select([1.0] * 8, cands, top_k=50, lambda_=0.7)
        assert len(got) == len(cands)
        assert set(c.name for c in got) == set(c.name for c in cands)

    def test_empty_and_zero_k(self):
        assert mmr_select([1.0], [], top_k=5, lambda_=0.7) == []
        assert mmr_select([1.0], [_Cand("a", 0.9, [1.0])], top_k=0, lambda_=0.7) == []

    def test_duplicate_is_demoted(self):
        base = [1.0, 0.0, 0.0, 0.0]
        other = [0.0, 1.0, 0.0, 0.0]
        cands = [
            _Cand("best", 0.95, base),
            _Cand("copy-of-best", 0.94, list(base)),
            _Cand("different", 0.60, other),
        ]
        got = mmr_select(base, cands, top_k=2, lambda_=0.7)
        assert [c.name for c in got] == ["best", "different"]

    def test_ties_go_to_the_earlier_candidate(self):
        v1 = [1.0, 0.0]
        v2 = [0.0, 1.0]
        cands = [_Cand("first", 0.8, v1), _Cand("second", 0.8, v2)]
        got = mmr_select(v1, cands, top_k=1, lambda_=0.7)
        assert got[0].name == "first"


class TestVectorText:
    def test_roundtrip(self):
        vec = [0.0, 1.0, -2.5, 3.141592653589793, 1e-7, -1e-7, 123456.789]
        assert _parse_vector(_vector_literal(vec)) == vec

    def test_pgvector_output_format(self):
        assert _parse_vector("[1,2,3]") == [1.0, 2.0, 3.0]
        assert _parse_vector(" [0.5,-0.25] ") == [0.5, -0.25]

    def test_empty(self):
        assert _parse_vector(None) == []
        assert _parse_vector("") == []
        assert _parse_vector("[]") == []


class TestLastUsedThrottle:
    def test_first_use_is_recorded_then_throttled(self):
        _last_use_written.clear()
        cred = uuid.uuid4()
        t0 = datetime.now(UTC)
        assert _should_record_use(cred, t0) is True
        assert _should_record_use(cred, t0 + timedelta(seconds=1)) is False
        assert _should_record_use(cred, t0 + timedelta(seconds=_LAST_USE_WRITE_INTERVAL_SECONDS - 1)) is False
        assert _should_record_use(cred, t0 + timedelta(seconds=_LAST_USE_WRITE_INTERVAL_SECONDS + 1)) is True

    def test_credentials_are_independent(self):
        _last_use_written.clear()
        a, b = uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        assert _should_record_use(a, now) is True
        assert _should_record_use(b, now) is True
        assert _should_record_use(a, now) is False
