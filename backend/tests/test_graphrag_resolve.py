"""Entity resolution: stages, guards, clustering, merge log (no LLM, no DB)."""

import math
import random
import uuid

import pytest

from app.core.config import get_settings
from app.graphrag import resolve as R
from app.graphrag.resolve import (
    Cand, LLMPairJudge, can_link, candidates_from_extractions, make_cand, neighbour_pairs, resolve,
)
from app.llm.client import LLMError

T = uuid.UUID("11111111-1111-1111-1111-111111111111")


def cand(name, etype="COMPANY", aliases=(), desc=None, known_id=None, source="s1"):
    return make_cand(T, name, etype, aliases, description=desc, source_id=source, known_id=known_id)


def trigram_embed(texts):
    out = []
    for t in texts:
        v = [0.0] * 64
        s = f"  {t.split(' (')[0].casefold()}  "
        for i in range(len(s) - 2):
            v[hash(s[i:i + 3]) % 64] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / n for x in v])
    return out


def all_alike(texts):
    return [[1.0, 0.0] for _ in texts]


class Judge:
    def __init__(self, verdicts="same"):
        self.verdicts, self.seen = verdicts, []

    def judge(self, pairs):
        self.seen += [(a.name, b.name) for a, b in pairs]
        return [self.verdicts] * len(pairs) if isinstance(self.verdicts, str) else [self.verdicts(a, b) for a, b in pairs]


def names(res):
    return sorted(e.name for e in res.entities)


# --- exact and acronym -------------------------------------------------------------------


def test_exact_merge_crosses_company_and_organization_types():
    res = resolve([cand("Acme Corp", "COMPANY"), cand("ACME Corporation", "ORGANIZATION")])
    assert len(res.entities) == 1 and res.stats["merged_exact"] == 1
    assert {m.stage for m in res.merges} == {"exact"}


def test_different_type_groups_never_merge():
    assert len(resolve([cand("Mercury", "PRODUCT"), cand("Mercury", "PERSON")]).entities) == 2


def test_acronym_stage():
    res = resolve([cand("International Business Machines"), cand("IBM")])
    assert len(res.entities) == 1 and res.merges[0].stage == "acronym"


def test_two_entities_sharing_only_an_alias_do_not_merge():
    # "the Supplier" is a defined term of a different company in each contract
    res = resolve([cand("Acme Corp", aliases=["Supplier"]), cand("Beta Ltd", aliases=["Supplier"])])
    assert len(res.entities) == 2


def test_alias_matching_another_entitys_primary_name_merges():
    res = resolve([cand("International Business Machines", aliases=["IBM"]), cand("IBM")])
    assert len(res.entities) == 1


# --- guards ------------------------------------------------------------------------------


def test_numbered_names_never_merge_even_when_embeddings_and_judge_agree():
    a, b = cand("Project Alpha 1", "PROJECT"), cand("Project Alpha 2", "PROJECT")
    assert not can_link(a, b)
    judge = Judge("same")
    res = resolve([a, b], embed=all_alike, judge=judge)
    assert len(res.entities) == 2
    assert judge.seen == [] and res.stats["blocked_by_guard"] == 1      # blocked before any model call


@pytest.mark.parametrize("x,y", [("INV-1021", "INV-1022"), ("Phase 1", "Phase 2"), ("Section 4.1", "Section 4.2")])
def test_digit_variants_are_not_linkable(x, y):
    assert not can_link(cand(x, "DOCUMENT"), cand(y, "DOCUMENT"))


def test_same_digits_are_linkable_and_digit_free_vs_digit_is_not():
    assert can_link(cand("Project Alpha 1", "PROJECT"), cand("Alpha Project 1", "PROJECT"))
    assert not can_link(cand("Q3 Report", "DOCUMENT"), cand("Report", "DOCUMENT"))


def test_person_compatibility():
    sharma = cand("Rajesh Sharma", "PERSON")
    assert can_link(sharma, cand("Dr. Sharma", "PERSON"))
    assert can_link(sharma, cand("R. Sharma", "PERSON"))
    assert not can_link(sharma, cand("Priya Sharma", "PERSON"))
    assert not can_link(cand("Rajesh Kumar", "PERSON"), sharma)


def test_cluster_refuses_a_union_that_would_join_a_guarded_pair():
    # a bare surname judged the same as two different people must join at most one of them
    priya, rajesh, bare = cand("Priya Sharma", "PERSON"), cand("Rajesh Sharma", "PERSON"), cand("Sharma", "PERSON")
    res = resolve([priya, rajesh, bare], embed=all_alike, judge=Judge("same"))
    assert len(res.entities) == 2
    assert res.stats["refused_by_guard_or_cluster"] >= 1
    assert not any(e.name == "Sharma" for e in res.entities) or len(res.entities) == 2


# --- the semantic stage ------------------------------------------------------------------


def test_only_a_same_verdict_merges():
    pair = [cand("Dr. Sharma", "PERSON", desc="Heads the legal team."), cand("Rajesh Sharma", "PERSON", desc="Head of legal.")]
    assert len(resolve(pair, embed=all_alike, judge=Judge("same")).entities) == 1
    for verdict in ("different", "unsure"):
        res = resolve(pair, embed=all_alike, judge=Judge(verdict))
        assert len(res.entities) == 2 and res.stats[f"verdict_{verdict}"] == 1


def test_llm_stage_merges_are_logged_with_the_similarity():
    res = resolve([cand("Microsoft", desc="Software maker."), cand("MSFT", desc="Software maker, ticker MSFT.")],
                  embed=all_alike, judge=Judge("same"))
    assert len(res.entities) == 1
    assert res.merges[0].stage == "llm" and res.merges[0].score == pytest.approx(1.0)


def test_transitive_same_verdicts_form_one_cluster():
    cs = [cand("Alpha Systems Ltd"), cand("AlphaSys", desc="a"), cand("Alpha Sys Corporation", desc="b")]
    res = resolve(cs, embed=all_alike, judge=Judge("same"))
    assert len(res.entities) == 1 and len(res.id_map) == 3


def test_pairs_over_the_cap_are_counted_not_judged():
    cs = [cand(f"Org{chr(97 + i)}x", desc=str(i)) for i in range(6)]
    judge = Judge("unsure")
    res = resolve(cs, embed=all_alike, judge=judge, max_judge_pairs=3)
    assert len(judge.seen) == 3 and res.stats["pairs_over_cap"] > 0


def test_without_embed_or_judge_only_exact_and_acronym_run():
    res = resolve([cand("Microsoft"), cand("MSFT")])
    assert len(res.entities) == 2 and "judged" not in res.stats


# --- survivors, ids, determinism ---------------------------------------------------------


def test_known_graph_entity_is_the_survivor_so_ids_stay_stable():
    new = cand("Acme Corp")
    old = cand("Acme Corporation", known_id="00000000-0000-0000-0000-00000000aaaa")
    res = resolve([new, old])
    assert res.entities[0].id == "00000000-0000-0000-0000-00000000aaaa"
    assert res.id_map[new.id] == "00000000-0000-0000-0000-00000000aaaa"
    assert [m.merged_id for m in res.merges] == [new.id]


def test_merged_entity_keeps_all_names_chunks_and_the_most_mentioned_display_name():
    seen_three_times = [cand("Acme Corp", aliases=["Supplier"]) for _ in range(3)]
    seen_once = cand("ACME Corporation")
    seen_three_times[0].chunk_ids, seen_once.chunk_ids = {"c1"}, {"c2"}
    res = resolve([*seen_three_times, seen_once])                  # same type and key: one id, four mentions
    e = res.entities[0]
    assert len(res.entities) == 1 and res.stats["same_id_inputs_combined"] == 3
    assert e.name == "Acme Corp" and e.chunk_ids == {"c1", "c2"} and e.mentions == 4
    assert {"Supplier", "ACME Corporation"} <= set(e.names)


def test_every_input_id_maps_to_an_output_entity_and_result_is_order_independent():
    base = [cand("Acme Corp"), cand("ACME Corporation", "ORGANIZATION"), cand("International Business Machines"),
            cand("IBM"), cand("Project Alpha 1", "PROJECT"), cand("Project Alpha 2", "PROJECT"), cand("Beta Ltd")]
    first = resolve(base, embed=all_alike, judge=Judge("different"))
    ids = {e.id for e in first.entities}
    assert set(first.id_map) == {c.id for c in base} and set(first.id_map.values()) <= ids
    for seed in range(5):
        shuffled = base[:]
        random.Random(seed).shuffle(shuffled)
        again = resolve(shuffled, embed=all_alike, judge=Judge("different"))
        assert {frozenset(c.names[:1]) for c in again.entities} == {frozenset(c.names[:1]) for c in first.entities}
        assert len(again.entities) == len(first.entities)


# --- blocking ----------------------------------------------------------------------------


def test_neighbour_pairs_respect_group_threshold_and_k():
    cs = [cand("Acme Corp"), cand("Acme Corporation Inc", "ORGANIZATION"), cand("Zzz Holdings"), cand("Acme", "PERSON")]
    vecs = trigram_embed([R.cand_text(c) for c in cs])
    pairs = neighbour_pairs(cs, vecs, k=2, min_sim=0.0)
    ids = {c.id: c for c in cs}
    assert all(ids[a].group == ids[b].group for a, b, _ in pairs)       # PERSON never pairs with ORG
    assert neighbour_pairs(cs, vecs, k=2, min_sim=1.01) == []
    assert all(s >= 0.5 for _, _, s in neighbour_pairs(cs, vecs, k=3, min_sim=0.5))


def test_neighbour_pairs_refuses_a_group_over_the_cap(monkeypatch):
    monkeypatch.setattr(R, "MAX_BLOCK_GROUP", 2)
    cs = [cand(f"Org {n}") for n in ("a", "b", "c")]
    with pytest.raises(ValueError):
        neighbour_pairs(cs, [[1.0], [1.0], [1.0]])


# --- the LLM judge -----------------------------------------------------------------------


def _judge_with(monkeypatch, replies):
    calls = []

    def fake(client, model, system, user):
        calls.append(user)
        reply = replies[len(calls) - 1]
        if isinstance(reply, Exception):
            raise reply
        return reply, None, False

    monkeypatch.setattr(R, "cached_chat_json", fake)
    return LLMPairJudge(client=None, settings=get_settings()), calls


def test_llm_judge_batches_parses_and_defaults_to_unsure(monkeypatch):
    pairs = [(cand(f"A{i}"), cand(f"B{i}")) for i in range(10)]            # 10 pairs -> batches of 8 and 2
    replies = [
        {"verdicts": [{"id": 0, "verdict": "same", "reason": "spelling variant"}, {"id": 1, "verdict": "DIFFERENT"},
                      {"id": 2, "verdict": "maybe"}, {"id": 99, "verdict": "same"}, "junk"]},
        LLMError("boom"),
        {"verdicts": [{"id": 0, "verdict": "same", "reason": "same entity"}]},          # the confirmation of pair 0
    ]
    judge, calls = _judge_with(monkeypatch, replies)
    verdicts = judge.judge(pairs)
    assert len(calls) == 3 and len(verdicts) == 10
    assert verdicts[:4] == ["same", "different", "unsure", "unsure"]       # unknown verdict and missing ids -> unsure
    assert verdicts[8:] == ["unsure", "unsure"]                             # a failed batch merges nothing
    assert R.JUDGE_VERSION in R.LLMPairJudge.SYSTEM and "Default to different" in R.LLMPairJudge.SYSTEM
    assert judge.log[0] == ("A0", "B0", "same", "spelling variant")


def test_a_same_verdict_the_second_reviewer_rejects_becomes_unsure(monkeypatch):
    pairs = [(cand("YAML", "TECHNOLOGY"), cand("ZCQL", "TECHNOLOGY"))]
    replies = [{"verdicts": [{"id": 0, "verdict": "same", "reason": "both config"}]},
               {"verdicts": [{"id": 0, "verdict": "different", "reason": "a format and a query language"}]}]
    judge, calls = _judge_with(monkeypatch, replies)
    assert judge.judge(pairs) == ["unsure"] and judge.unconfirmed == 1 and len(calls) == 2
    assert "second reviewer" in judge.log[0][3]


def test_confirmation_can_be_switched_off(monkeypatch):
    judge, calls = _judge_with(monkeypatch, [{"verdicts": [{"id": 0, "verdict": "same"}]}])
    judge.confirm = False
    assert judge.judge([(cand("A"), cand("B"))]) == ["same"] and len(calls) == 1


# --- prefilter and variant guard -----------------------------------------------------------


@pytest.mark.parametrize("x,y,expected", [
    ("PostgreSQL", "Postgres", True),
    ("FastAPI service", "FastAPI", True),
    ("pub/sub system", "Publish/Subscribe systems", True),     # pub -> publish, sub -> subscribe
    ("International Business Machines", "IBM", True),
    ("Ethereum", "Ether", True),
    ("YAML", "ZCQL", False),
    ("mempool", "DuckDB", False),
    ("nano", "AllowedUsers", False),
])
def test_name_affinity(x, y, expected):
    assert R.name_affinity(cand(x, "TECHNOLOGY"), cand(y, "TECHNOLOGY")) is expected


def test_variant_qualifiers_block_a_merge():
    assert not can_link(cand("GPT-4o", "PRODUCT"), cand("GPT-4o-mini", "PRODUCT"))
    assert not can_link(cand("Llama Pro", "PRODUCT"), cand("Llama", "PRODUCT"))
    assert can_link(cand("GPT-4o mini", "PRODUCT"), cand("GPT-4o-mini", "PRODUCT"))


def embed_at_0_8(texts):
    return [[1.0, 0.0]] + [[0.8, 0.6] for _ in texts[1:]]              # cosine 0.8 to the first


def test_pair_without_name_affinity_is_not_judged_below_the_strong_similarity():
    judge = Judge("same")
    res = resolve([cand("YAML", "TECHNOLOGY"), cand("ZCQL", "TECHNOLOGY")], embed=embed_at_0_8, judge=judge)
    assert len(res.entities) == 2 and judge.seen == [] and res.stats["no_name_affinity"] == 1


def test_pair_without_affinity_is_judged_when_very_similar():
    judge = Judge("same")
    res = resolve([cand("Microsoft", desc="x"), cand("MSFT", desc="y")], embed=all_alike, judge=judge)   # cosine 1.0
    assert len(res.entities) == 1 and judge.seen


# --- from extraction results -------------------------------------------------------------


def test_candidates_from_extractions_combines_repeats_and_skips_failed_windows():
    from app.graphrag.extract import WindowResult, parse_reply
    from app.graph.ontology import get_ontology
    from app.graphrag.windows import ChunkRef, pack_windows

    def window(text, extraction_id):
        chunk = ChunkRef(id=f"{extraction_id}-c0", index=0, text=text, tokens=20)
        block = {"id": "b", "section_path": ["A"], "title": None, "chunks": [chunk]}
        return pack_windows([block], source_id=f"src-{extraction_id}", extraction_id=extraction_id, source_version=1)[0]

    w1, w2 = window("Acme Corp supplies parts.", "e1"), window("ACME Corp ships goods.", "e2")
    raw1 = {"entities": [{"name": "Acme Corp", "type": "COMPANY", "description": "A supplier."}]}
    raw2 = {"entities": [{"name": "ACME Corp", "type": "COMPANY"}]}
    ont = get_ontology()
    results = [WindowResult(w1, parse_reply(raw1, w1, ont)), WindowResult(w2, parse_reply(raw2, w2, ont)),
               WindowResult(window("x", "e3"), None, [], "failed")]
    cands = candidates_from_extractions(T, results)
    assert len(cands) == 1
    c = cands[0]
    assert c.mentions == 2 and c.source_ids == {"src-e1", "src-e2"} and c.descriptions == ["A supplier."]
    assert c.snippets and "Acme" in c.snippets[0].title()


@pytest.mark.parametrize("reply", [
    {"verdicts": 5}, {"verdicts": "same"}, {"verdicts": {"id": 0}}, {"verdicts": [None, 3, [], {"id": [0]}, {"id": "0"}]},
    [], "text", None,
])
def test_the_entity_judge_survives_replies_of_the_wrong_shape(monkeypatch, reply):
    judge, _ = _judge_with(monkeypatch, [reply])
    judge.confirm = False
    assert judge.judge([(cand("A"), cand("B"))]) == ["unsure"]          # nothing usable: nothing merges


def test_a_type_group_too_large_to_embed_is_paired_by_shared_name_words_instead_of_failing(monkeypatch):
    monkeypatch.setattr(R, "MAX_BLOCK_GROUP", 3)
    cands = [cand(n, "TECHNOLOGY") for n in ("Redis Cache", "Redis Cluster", "Kafka Broker", "Kafka Streams", "Postgres")]
    embedded = []

    def embed(texts):
        embedded.append(texts)
        return [[1.0, 0.0] for _ in texts]

    judge = Judge("different")
    res = resolve(cands, embed=embed, judge=judge)                    # 5 entities in one group > 3: must not raise
    assert res.stats["oversized_groups"] == 1 and embedded == []      # nothing was embedded for the oversized group
    assert {frozenset(p) for p in judge.seen} == {frozenset({"Redis Cache", "Redis Cluster"}), frozenset({"Kafka Broker", "Kafka Streams"})}


def test_a_word_in_many_names_does_not_pair_them_all():
    cands = [cand(f"Service {i}", "TECHNOLOGY") for i in range(60)]
    assert R.affinity_pairs(cands, max_bucket=40) == []               # "service" is in 60 names: says nothing
    assert len(R.affinity_pairs(cands[:5], max_bucket=40)) == 10


def test_two_entities_already_in_the_graph_are_not_judged_again():
    a = cand("Acme Holdings", known_id="00000000-0000-0000-0000-00000000aaa1")
    b = cand("Acme Group", known_id="00000000-0000-0000-0000-00000000aaa2")
    judge = Judge("same")
    res = resolve([a, b], embed=all_alike, judge=judge)
    assert judge.seen == [] and res.stats["known_pairs_skipped"] == 1 and len(res.entities) == 2
    fresh = cand("Acme Partners")
    resolve([a, fresh], embed=all_alike, judge=judge)
    assert judge.seen                                                  # a new entity against a known one is judged
