"""Hierarchical communities over a subject's entity graph (plan Step 4).

FalkorDB 4.22 has no Leiden (its procedures are WCC, label propagation, PageRank, betweenness,
shortest paths), and the backend has no numpy, networkx or igraph. This is a small, deterministic,
pure-Python Louvain with a connectedness repair:

* **Levels.** Each Louvain pass groups the previous pass's groups, so level 0 is the finest
  partition and each higher level is coarser (at most `max_levels`). A level identical to the
  one below is dropped.
* **Connected.** Plain Louvain can return a community that is not connected. Leiden's main
  guarantee is that it never does; here every group is split into connected components of
  the original graph, so no community is a pile of strangers joined through a removed node.
* **Deterministic.** Nodes are visited in an order shuffled by a seeded generator over sorted
  input and ties go to the smaller community id, so the same graph always gives the same
  communities.
* **Stable ids.** A community's id is a hash of its sorted members, and `reuse_summaries`
  maps a new community to an old one when their members overlap enough (Jaccard), so a rebuild
  after a small change keeps the summaries it already paid for.

Only weights decide the grouping; the caller chooses what an edge means (a typed relation, a
shared fact, a shared chunk). `min_size` drops groups too small to summarise from the output.
"""

from __future__ import annotations

import hashlib
import random
from collections import defaultdict
from dataclasses import dataclass

MIN_GAIN = 1e-9


@dataclass(frozen=True)
class Community:
    id: str
    level: int                       # 0 = finest
    members: tuple[str, ...]         # sorted entity ids
    parent_id: str | None = None     # the community one level up that contains it

    @property
    def size(self) -> int:
        return len(self.members)


def community_id(level: int, members, scope: str = "") -> str:
    """Stable id: a hash of the members, the level and the scope (a subject).

    The scope matters because community ids are unique across a tenant's graph and two
    subjects can have identical member sets.
    """
    digest = hashlib.sha1((scope + "|" + "|".join(sorted(members)) + f"@{level}").encode()).hexdigest()
    return f"cm{level}-{digest[:16]}"


def _adjacency(nodes: list[str], edges) -> dict[str, dict[str, float]]:
    adj: dict[str, dict[str, float]] = {n: {} for n in nodes}
    for a, b, w in edges:
        if a not in adj or b not in adj or w <= 0:
            continue
        if a == b:
            adj[a][a] = adj[a].get(a, 0.0) + w
        else:
            adj[a][b] = adj[a].get(b, 0.0) + w
            adj[b][a] = adj[b].get(a, 0.0) + w
    return adj


def _degree(adj, n) -> float:
    return sum(w for m, w in adj[n].items() if m != n) + 2 * adj[n].get(n, 0.0)


def _local_moving(adj: dict[str, dict[str, float]], rng: random.Random, resolution: float) -> tuple[dict[str, str], bool]:
    """One Louvain phase: move nodes to the neighbouring community with the best modularity gain."""
    nodes = sorted(adj)
    comm = {n: n for n in nodes}
    deg = {n: _degree(adj, n) for n in nodes}
    m2 = sum(deg.values())
    if m2 == 0:
        return comm, False
    tot = dict(deg)                      # total degree per community
    improved_any = False
    order = nodes[:]
    rng.shuffle(order)
    moved = True
    while moved:
        moved = False
        for n in order:
            old = comm[n]
            links: dict[str, float] = defaultdict(float)
            for m, w in adj[n].items():
                if m != n:
                    links[comm[m]] += w
            tot[old] -= deg[n]
            best, best_gain = old, links.get(old, 0.0) - resolution * tot[old] * deg[n] / m2
            for c in sorted(links):
                gain = links[c] - resolution * tot[c] * deg[n] / m2
                if gain > best_gain + MIN_GAIN:
                    best, best_gain = c, gain
            tot[best] += deg[n]
            if best != old:
                comm[n] = best
                moved = improved_any = True
    return comm, improved_any


def _aggregate(adj: dict[str, dict[str, float]], comm: dict[str, str]) -> dict[str, dict[str, float]]:
    new: dict[str, dict[str, float]] = {c: {} for c in set(comm.values())}
    for a in adj:
        for b, w in adj[a].items():
            ca, cb = comm[a], comm[b]
            if a == b:
                new[ca][ca] = new[ca].get(ca, 0.0) + w                  # an existing self loop, counted once
            elif a < b:                                                 # each undirected edge once
                if ca == cb:
                    new[ca][ca] = new[ca].get(ca, 0.0) + w
                else:
                    new[ca][cb] = new[ca].get(cb, 0.0) + w
                    new[cb][ca] = new[cb].get(ca, 0.0) + w
    return new


def _connected_split(groups: list[list[str]], adj: dict[str, dict[str, float]]) -> list[list[str]]:
    """Split every group into the connected components it has in the original graph."""
    out = []
    for group in groups:
        members, seen = set(group), set()
        for start in sorted(group):
            if start in seen:
                continue
            stack, comp = [start], []
            seen.add(start)
            while stack:
                n = stack.pop()
                comp.append(n)
                for m in adj[n]:
                    if m in members and m not in seen:
                        seen.add(m)
                        stack.append(m)
            out.append(sorted(comp))
    return out


def detect(
    nodes: list[str], edges, *, max_levels: int = 3, min_size: int = 2, seed: int = 7,
    resolution: float = 1.0, scope: str = "",
) -> list[Community]:
    """Communities at up to `max_levels` levels. `edges`: (a, b, weight) undirected triples."""
    nodes = sorted(set(nodes))
    base = _adjacency(nodes, edges)
    if not nodes:
        return []
    rng = random.Random(seed)
    member_of = {n: n for n in nodes}          # original node -> its group label at the current level
    adj = base
    level_groups: list[list[list[str]]] = []
    while len(level_groups) < max_levels:
        comm, improved = _local_moving(adj, rng, resolution)
        if not improved:
            break
        member_of = {n: comm[member_of[n]] for n in nodes}
        grouped: dict[str, list[str]] = defaultdict(list)
        for n, label in member_of.items():
            grouped[label].append(n)
        groups = _connected_split(list(grouped.values()), base)
        if level_groups and sorted(map(tuple, groups)) == sorted(map(tuple, level_groups[-1])):
            break                                # nothing coarser came out of this pass
        level_groups.append(groups)
        adj = _aggregate(adj, comm)
        if len(adj) <= 1:
            break

    communities: list[Community] = []
    ids_by_level: list[dict[str, str]] = []      # node -> community id, per level
    for level, groups in enumerate(level_groups):
        node_to_id: dict[str, str] = {}
        for g in groups:
            cid = community_id(level, g, scope)
            for n in g:
                node_to_id[n] = cid
        ids_by_level.append(node_to_id)
    for level, groups in enumerate(level_groups):
        for g in sorted(groups, key=lambda g: (-len(g), g[0])):
            if len(g) < min_size:
                continue
            parent = ids_by_level[level + 1][g[0]] if level + 1 < len(level_groups) else None
            communities.append(Community(ids_by_level[level][g[0]], level, tuple(g), parent))
    # a parent that was itself dropped for size cannot be referenced
    kept = {c.id for c in communities}
    return [c if c.parent_id in kept or c.parent_id is None else Community(c.id, c.level, c.members, None)
            for c in communities]


def jaccard(a, b) -> float:
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if a | b else 0.0


def reuse_summaries(new: list[Community], old: dict[str, tuple[str, ...]], threshold: float = 0.8) -> dict[str, str]:
    """new community id -> old community id whose members overlap by at least `threshold`.

    One-to-one, best overlap first, same level only (an id encodes its level), so a summary is
    never attached to two communities. Communities with an identical id are matched by id.
    """
    pairs = []
    for c in new:
        if c.id in old:
            pairs.append((1.0, c.id, c.id))
            continue
        for oid, members in old.items():
            if oid.split("-")[0] == c.id.split("-")[0]:
                j = jaccard(c.members, members)
                if j >= threshold:
                    pairs.append((j, c.id, oid))
    pairs.sort(key=lambda p: (-p[0], p[1], p[2]))
    out: dict[str, str] = {}
    taken: set[str] = set()
    for _, cid, oid in pairs:
        if cid not in out and oid not in taken:
            out[cid] = oid
            taken.add(oid)
    return out
