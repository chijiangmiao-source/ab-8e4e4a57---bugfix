"""Property-style tests for the dominator core.

A deliberately simple per-node deletion reference implementation is used
here (the *service core* must not do that; the test oracle may) to
cross-check Lengauer-Tarjan on hand-built and random graphs.
"""

from __future__ import annotations

import random

import pytest

from app.dominators import compute_dominators, count_dominated_terminals


def build(edges, n, root=0):
    adj = [[] for _ in range(n)]
    pre = [[] for _ in range(n)]
    for u, v in edges:
        adj[u].append(v)
        pre[v].append(u)
    terms = [False] * n
    return compute_dominators(n, adj, pre, root, terms, [str(i) for i in range(n)])


def reachable_set(adj, root, banned=-1):
    seen = {root} if root != banned else set()
    stack = [root] if root != banned else []
    while stack:
        u = stack.pop()
        for v in adj[u]:
            if v != banned and v not in seen:
                seen.add(v)
                stack.append(v)
    return seen


def brute_force_idom(edges, n, root=0):
    """Reference: dominators via node deletion."""
    adj = [[] for _ in range(n)]
    for u, v in edges:
        adj[u].append(v)
    reach = reachable_set(adj, root)

    def dominates(u, v):
        if u == v:
            return True
        return v not in reachable_set(adj, root, banned=u)

    idom = [-1] * n
    for v in reach:
        if v == root:
            continue
        strict = [u for u in reach if u != v and dominates(u, v)]
        # idom(v): the strict dominator dominated by every other one.
        for u in strict:
            if all(w == u or dominates(w, u) for w in strict):
                idom[v] = u
                break
    return reach, idom


def assert_matches_bruteforce(edges, n, root=0):
    result = build(edges, n, root)
    reach, expected_idom = brute_force_idom(edges, n, root)
    assert set(i for i, r in enumerate(result.reachable) if r) == reach
    assert result.idom == expected_idom


def test_single_root_only():
    r = build([], 2, root=0)
    assert r.reachable == [True, False]
    assert r.idom == [-1, -1]


def test_diamond_bypass():
    # 0 -> 1 -> 3 and 0 -> 2 -> 3 ; terminal 4 behind merge 3
    edges = [(0, 1), (1, 3), (0, 2), (2, 3), (3, 4)]
    r = build(edges, 5)
    # 1 and 2 bypass each other: only root and 3 dominate 3/4.
    assert r.idom[1] == 0
    assert r.idom[2] == 0
    assert r.idom[3] == 0
    assert r.idom[4] == 3

    n = 5
    is_terminal = [False] * n
    is_terminal[4] = True
    res2 = compute_dominators(
        n, _adj(n, edges), _pre(n, edges), 0,
        is_terminal, [str(i) for i in range(n)],
    )
    counts = count_dominated_terminals(res2)
    # Bypassed relays 1 and 2 dominate no terminal; merge point 3 does.
    assert counts[1] == 0
    assert counts[2] == 0
    assert counts[3] == 1
    assert counts[0] == 1


def _adj(n, edges):
    adj = [[] for _ in range(n)]
    for u, v in edges:
        adj[u].append(v)
    return adj


def _pre(n, edges):
    pre = [[] for _ in range(n)]
    for u, v in edges:
        pre[v].append(u)
    return pre


def test_serial_chain():
    n = 6
    edges = [(i, i + 1) for i in range(n - 1)]
    r = build(edges, n)
    for v in range(1, n):
        assert r.idom[v] == v - 1

    is_terminal = [False] * n
    is_terminal[5] = True
    res2 = compute_dominators(
        n, _adj(n, edges), _pre(n, edges), 0,
        is_terminal, [str(i) for i in range(n)],
    )
    counts = count_dominated_terminals(res2)
    assert counts == [1, 1, 1, 1, 1, 1]


def test_parallel_edges_do_not_create_bypass_or_spof():
    # Two parallel edges 0 -> 1, then 1 -> 4 (terminal).
    edges = [(0, 1), (0, 1), (1, 4)]
    n = 5
    is_terminal = [False] * n
    is_terminal[4] = True
    r = compute_dominators(
        n, _adj(n, edges), _pre(n, edges), 0,
        is_terminal, [str(i) for i in range(n)],
    )
    assert r.idom[1] == 0
    assert r.idom[4] == 1
    counts = count_dominated_terminals(r)
    assert counts[1] == 1  # parallel wires share the relay: still a SPOF

    # Parallel edges straight to a terminal mean no relay dominates it.
    edges2 = [(0, 4), (0, 4)]
    r2 = compute_dominators(
        n, _adj(n, edges2), _pre(n, edges2), 0,
        is_terminal, [str(i) for i in range(n)],
    )
    assert r2.idom[4] == 0


def test_unreachable_nodes():
    edges = [(0, 1), (1, 3)]  # node 2 unreachable
    r = build(edges, 4)
    assert r.reachable == [True, True, False, True]
    assert r.idom[2] == -1
    assert r.idom[3] == 1


def test_deep_chain_is_iterative_no_recursion():
    # A 30 000 node chain would blow a naive recursive DFS/LT
    # implementation (Python's default recursion limit is 1 000).
    n = 30_000
    edges = [(i, i + 1) for i in range(n - 1)]
    r = build(edges, n)
    assert r.idom[n - 1] == n - 2
    assert len(r.vertex) == n


def test_classic_lt_example():
    # Graph from the Lengauer-Tarjan paper (Figure 1-ish), root R=0.
    # R->A,B,C ; A->D ; B->A,D ; C->B,D ; D->E ; E->B,F,G ;
    # F->G,I ; G->I ; H->E,G ; I->H
    edges = [
        (0, 1), (0, 2), (0, 3),
        (1, 4),
        (2, 1), (2, 4),
        (3, 2), (3, 4),
        (4, 5),
        (5, 2), (5, 6), (5, 7),
        (6, 7), (6, 9),
        (7, 9),
        (8, 5), (8, 7),
        (9, 8),
    ]
    assert_matches_bruteforce(edges, 10)


@pytest.mark.parametrize("seed", range(40))
def test_random_graphs_match_bruteforce(seed):
    rng = random.Random(seed)
    n = rng.randint(2, 40)
    edges = set()
    # Dense-ish random directed graph, no self loops.
    for u in range(n):
        for v in range(n):
            if u != v and rng.random() < 0.18:
                edges.add((u, v))
    edge_list = list(edges)
    assert_matches_bruteforce(edge_list, n, root=0)


def test_random_graphs_with_parallel_edges():
    rng = random.Random(1234)
    for _ in range(20):
        n = rng.randint(2, 30)
        base = set()
        for u in range(n):
            for v in range(n):
                if u != v and rng.random() < 0.2:
                    base.add((u, v))
        # Duplicate a random subset of edges.
        edges = list(base)
        for e in list(base):
            if rng.random() < 0.3:
                edges.append(e)
        rng.shuffle(edges)
        assert_matches_bruteforce(edges, n, root=0)


def test_terminal_counts_random():
    rng = random.Random(99)
    for _ in range(30):
        n = rng.randint(2, 25)
        edges = [
            (u, v)
            for u in range(n) for v in range(n)
            if u != v and rng.random() < 0.2
        ]
        adj, pre = _adj(n, edges), _pre(n, edges)
        terminals = [rng.random() < 0.3 for _ in range(n)]
        terminals[0] = False
        r = compute_dominators(
            n, adj, pre, 0, terminals, [str(i) for i in range(n)]
        )
        counts = count_dominated_terminals(r)
        # Reference counts via deletion reachability.
        for u in range(n):
            expected = 0
            if r.reachable[u]:
                for t in range(n):
                    if terminals[t] and t in reachable_set(adj, 0):
                        if t not in reachable_set(adj, 0, banned=u) or u == t:
                            expected += 1
            else:
                expected = counts[u]  # unreachable: not asserted here
            if r.reachable[u]:
                assert counts[u] == expected, (u, counts[u], expected)
