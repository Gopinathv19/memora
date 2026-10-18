"""The unified pipeline's knobs reach RetrievalParams through the API schema."""

import pytest
from pydantic import ValidationError

from app.graphrag.params import RetrievalParams
from app.schemas.graphrag import GraphRagParams


def test_defaults_match_the_retrieval_params_defaults():
    got = GraphRagParams().to_params()
    default = RetrievalParams()
    for name in ("unified_analyser", "unified_weights", "unified_widen", "unified_multi_hop", "atom_k",
                 "walk_passage_weight", "answer_llm_check"):
        assert getattr(got, name) == getattr(default, name), name


def test_overrides_are_carried_through():
    got = GraphRagParams(unified_analyser=False, unified_weights={"walk": 0.0}, unified_widen=False,
                         unified_multi_hop=True, atom_k=5, walk_passage_weight=0.5, answer_llm_check=True).to_params()
    assert (got.unified_analyser, got.unified_weights, got.unified_widen, got.unified_multi_hop) == (False, {"walk": 0.0}, False, True)
    assert (got.atom_k, got.walk_passage_weight, got.answer_llm_check) == (5, 0.5, True)


@pytest.mark.parametrize("bad", [{"unified_weights": {"bogus": 1}}, {"unified_weights": {"walk": 9}},
                                 {"unified_weights": {"walk": -1}}, {"atom_k": 0}, {"atom_k": 500}, {"walk_passage_weight": 3}])
def test_out_of_range_values_are_rejected(bad):
    with pytest.raises(ValidationError):
        GraphRagParams(**bad)
