"""Hierarchical communities: structure, connectedness, determinism, stable ids (pure)."""

import itertools

from app.graphrag.communities import Community, community_id, detect, jaccard, reuse_summaries


def clique(names, w=1.0):
    return [(a, b, w) for a, b in itertools.combinations(names, 2)]


def two_cliques_with_a_bridge():
    a, b = [f"a{i}" for i in range(5)], [f"b{i}" for i in range(5)]
    return a + b, clique(a) + clique(b) + [("a0", "b0", 0.1)], a, b


def groups(communities, level=0):
    return sorted(c.members for c in communities if c.level == level)


def test_two_dense_groups_joined_by_a_weak_bridge_become_two_communities():
    nodes, edges, a, b = two_cliques_with_a_bridge()
    cs = detect(nodes, edges)
    assert groups(cs, 0) == [tuple(sorted(a)), tuple(sorted(b))]


def test_every_node_is_in_exactly_one_community_per_level():
    nodes, edges, _, _ = two_cliques_with_a_bridge()
    cs = detect(nodes, edges, min_size=1)
    for level in {c.level for c in cs}:
        members = [m for c in cs if c.level == level for m in c.members]
        assert sorted(members) == sorted(nodes)


def test_communities_are_connected_in_the_original_graph():
    # two triangles that share no edge but could be pulled together by a shared weighting
    nodes = ["x1", "x2", "x3", "y1", "y2", "y3"]
    edges = clique(["x1", "x2", "x3"]) + clique(["y1", "y2", "y3"])
    for c in detect(nodes, edges):
        adj = {n: set() for n in c.members}
        for a, b, _ in edges:
            if a in adj and b in adj:
                adj[a].add(b)
                adj[b].add(a)
        seen, stack = {c.members[0]}, [c.members[0]]
        while stack:
            for m in adj[stack.pop()] - seen:
                seen.add(m)
                stack.append(m)
        assert seen == set(c.members)


def ring_of_triangles(n):
    cl = [[f"c{i}n{j}" for j in range(3)] for i in range(n)]
    nodes = [x for c in cl for x in c]
    edges = [e for c in cl for e in clique(c)] + [(cl[i][0], cl[(i + 1) % n][1], 1.0) for i in range(n)]
    return nodes, edges


def test_a_hierarchy_has_coarser_levels_and_parents_contain_their_children():
    # Many small groups in a ring: neighbours merge into larger ones, then again. (Few groups would
    # not: modularity's resolution limit keeps a handful of dense groups apart however weakly linked.)
    nodes, edges = ring_of_triangles(40)
    cs = detect(nodes, edges, max_levels=3)
    per_level = {lv: [c for c in cs if c.level == lv] for lv in sorted({c.level for c in cs})}
    assert list(per_level) == [0, 1, 2]
    assert [len(per_level[lv]) for lv in per_level] == sorted((len(v) for v in per_level.values()), reverse=True)  # coarser = fewer
    assert all(c.size == 3 for c in per_level[0])
    by_id = {c.id: c for c in cs}
    for c in cs:
        if c.level < 2:
            assert c.parent_id in by_id, "every community below the top has a parent"
            parent = by_id[c.parent_id]
            assert parent.level == c.level + 1 and set(c.members) <= set(parent.members)
        else:
            assert c.parent_id is None


def test_a_few_dense_groups_stay_separate_however_weakly_linked():
    gs = [[f"g{i}n{j}" for j in range(4)] for i in range(4)]
    nodes = [n for g in gs for n in g]
    edges = [e for g in gs for e in clique(g, 5.0)] + [(gs[0][0], gs[1][0], 1.0), (gs[2][0], gs[3][0], 1.0)]
    cs = detect(nodes, edges)
    assert {c.level for c in cs} == {0} and sorted(c.size for c in cs) == [4, 4, 4, 4]


def test_same_graph_same_communities_whatever_the_input_order():
    nodes, edges, _, _ = two_cliques_with_a_bridge()
    first = detect(nodes, edges)
    for seed_order in range(4):
        shuffled_edges = edges[:]
        shuffled_nodes = nodes[:]
        import random
        random.Random(seed_order).shuffle(shuffled_edges)
        random.Random(seed_order + 10).shuffle(shuffled_nodes)
        assert detect(shuffled_nodes, shuffled_edges) == first


def test_ids_are_stable_hashes_of_members_and_level():
    assert community_id(0, ["b", "a"]) == community_id(0, ["a", "b"])
    assert community_id(0, ["a", "b"]) != community_id(1, ["a", "b"])
    nodes, edges, _, _ = two_cliques_with_a_bridge()
    assert [c.id for c in detect(nodes, edges)] == [c.id for c in detect(nodes, edges)]


def test_isolated_nodes_and_small_groups_are_dropped_by_min_size():
    nodes = ["a", "b", "c", "lonely", "pair1", "pair2"]
    edges = clique(["a", "b", "c"]) + [("pair1", "pair2", 1.0)]
    cs = detect(nodes, edges, min_size=3)
    assert groups(cs, 0) == [("a", "b", "c")]
    assert all("lonely" not in c.members for c in detect(nodes, edges, min_size=1) if c.size > 1)


def test_empty_and_edgeless_graphs():
    assert detect([], []) == []
    assert detect(["a", "b"], [], min_size=2) == []


def test_edges_to_unknown_nodes_and_non_positive_weights_are_ignored():
    cs = detect(["a", "b", "c"], [("a", "b", 1.0), ("b", "c", 1.0), ("a", "zzz", 5.0), ("a", "c", 0.0), ("a", "c", -1.0)])
    assert all("zzz" not in c.members for c in cs)


def test_jaccard_and_summary_reuse():
    assert jaccard("abcd", "abce") == 3 / 5
    old = {community_id(0, ["a", "b", "c", "d", "e"]): ("a", "b", "c", "d", "e"),
           community_id(0, ["x", "y", "z"]): ("x", "y", "z")}
    new = [
        Community(community_id(0, list("abcdf")), 0, tuple(sorted("abcdf"))),     # 4/6 overlap: below 0.8
        Community(community_id(0, list("abcde") + ["f"]), 0, tuple(sorted("abcdef"))),   # 5/6 = 0.83: reused
        Community(community_id(0, ["x", "y", "z"]), 0, ("x", "y", "z")),          # same id
        Community(community_id(1, list("abcde")), 1, tuple(sorted("abcde"))),     # other level: never matched
    ]
    out = reuse_summaries(new, old, threshold=0.8)
    assert out[new[1].id] == community_id(0, list("abcde"))
    assert out[new[2].id] == new[2].id
    assert new[0].id not in out and new[3].id not in out


def test_reuse_is_one_to_one():
    old = {community_id(0, list("abcde")): tuple("abcde")}
    new = [Community(community_id(0, list("abcdef")), 0, tuple("abcdef")),
           Community(community_id(0, list("abcdeg")), 0, tuple("abcdeg"))]
    assert len(reuse_summaries(new, old, threshold=0.8)) == 1
