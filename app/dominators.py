"""Immediate dominator analysis.

The emergency stop signal flows from the root controller along directed
edges to protected terminals.  A terminal *v* loses contact with the root
when relay *u* fails **iff** *u* dominates *v*: every directed path from
the root to *v* passes through *u*.  The *immediate dominator* (idom) of a
reachable node is the unique strict dominator closest to it.

Implementation notes
--------------------
* Reachability and a DFS order are obtained with one **iterative** depth
  first search, so graphs tens of thousands of nodes deep never hit Python's
  recursion limit.
* Immediate dominators are computed with the classic Lengauer-Tarjan
  union/find algorithm in O(E alpha(V)).  The dominator tree is built in a
  single global pass over the reachable subgraph -- never one
  reachability rerun per node -- and the ``eval``/``compress`` steps are
  written iteratively.
* No graph-algorithm library is used; the data structure code in this
  module only relies on Python's built-in types.

The graph is represented internally by integer node ids; ``labels`` maps
each id back to the caller's ASCII node identifier.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DominatorResult:
    """Result of a dominator analysis.

    All arrays are indexed by internal node id ``0 .. n-1``.

    Attributes
    ----------
    labels:
        Original ASCII identifier of each node id.
    idom:
        Node id of the immediate dominator, or ``-1`` for the root and for
        unreachable nodes.
    reachable:
        ``True`` for nodes reachable from the root.
    is_terminal:
        ``True`` for protected terminals.
    discovery:
        DFS discovery number of each reachable node (``-1`` if
        unreachable); the root is ``0``.
    vertex:
        Node ids in DFS discovery order (``vertex[0]`` is the root); only
        reachable nodes appear.
    """

    labels: list[str]
    idom: list[int]
    reachable: list[bool]
    is_terminal: list[bool]
    discovery: list[int]
    vertex: list[int]


def compute_dominators(
    node_count: int,
    adjacency: list[list[int]],
    predecessors: list[list[int]],
    root: int,
    is_terminal: list[bool],
    labels: list[str],
) -> DominatorResult:
    """Compute immediate dominators of every node reachable from ``root``.

    Parameters
    ----------
    node_count:
        Number of nodes; ids are ``0 .. node_count-1``.
    adjacency, predecessors:
        Forward and reverse adjacency lists. Parallel edges may be kept;
        they do not change dominators.
    root:
        Root controller node id.
    is_terminal:
        Per-id protected-terminal flags, copied through to the result.
    labels:
        Per-id original ASCII identifiers.

    Returns
    -------
    DominatorResult
        Nodes unreachable from the root have ``idom == -1`` and
        ``reachable is False``.
    """

    n = node_count

    # ---- Iterative DFS: discover reachable nodes, build the DFS tree -----
    # discovery[u]: DFS discovery number, -1 while unseen
    # vertex[i]:   node discovered at DFS number i
    # parent[u]:   DFS-tree parent (node id), -1 for the root
    discovery = [-1] * n
    vertex: list[int] = []
    parent = [-1] * n
    reachable = [False] * n

    discovery[root] = 0
    vertex.append(root)
    reachable[root] = True

    # Stack of (node, next index into its adjacency list).
    stack: list[tuple[int, int]] = [(root, 0)]
    while stack:
        u, edge_index = stack[-1]
        succs = adjacency[u]
        if edge_index < len(succs):
            v = succs[edge_index]
            stack[-1] = (u, edge_index + 1)
            if discovery[v] == -1:
                reachable[v] = True
                parent[v] = u
                discovery[v] = len(vertex)
                vertex.append(v)
                stack.append((v, 0))
        else:
            stack.pop()

    r = len(vertex)  # number of reachable nodes

    # ---- Lengauer-Tarjan structures, indexed by node id -----------------
    # semi[u] doubles as the semidominator's DFS number once computed.
    semi = discovery[:]
    ancestor = [-1] * n
    label = list(range(n))  # eval() candidate with minimum semi value
    idom = [-1] * n
    bucket: list[list[int]] = [[] for _ in range(n)]

    def compress(v: int) -> None:
        """Iterative path compression propagating minimum-semi labels.

        Walk the ancestor chain to its top, then replay it in reverse
        updating labels -- the recursive LT ``compress`` unrolled, so a
        long DFS-tree chain never overflows Python's recursion limit.

        The walk must always reach the *current* chain top (the ancestor
        whose own ancestor is unset): ancestors are linked as the main
        loop progresses, so any shortcut cached from an earlier call can
        go stale and would freeze labels against an outdated tree.
        """
        path: list[int] = []
        a = ancestor[v]
        while ancestor[a] != -1:
            path.append(a)
            a = ancestor[a]
        # Replay from the top down: every label is folded against
        # already-compressed ancestors, then shortcut straight to the top.
        while path:
            u = path.pop()
            if semi[label[ancestor[u]]] < semi[label[u]]:
                label[u] = label[ancestor[u]]
            ancestor[u] = a

    def eval(v: int) -> int:
        if ancestor[v] == -1:
            return label[v]
        compress(v)
        if semi[label[ancestor[v]]] >= semi[label[v]]:
            return label[v]
        return label[ancestor[v]]

    # Process DFS numbers in reverse discovery order (root skipped).
    for i in range(r - 1, 0, -1):
        w = vertex[i]

        # Semidominator: min semi(eval(v)) over reachable predecessors v.
        semi_w = semi[w]
        for v in predecessors[w]:
            if not reachable[v]:
                continue
            u = eval(v)
            candidate = semi[u]
            if candidate < semi_w:
                semi_w = candidate
        semi[w] = semi_w
        bucket[vertex[semi_w]].append(w)
        ancestor[w] = parent[w]  # link(parent[w], w)

        # Immediate-dominator candidates bucketed at w's DFS parent.
        p = parent[w]
        for v in bucket[p]:
            u = eval(v)
            # LT step 3: smaller semi defers resolution (idom(v)=u, fixed
            # in step 4); otherwise p is v's immediate dominator.
            idom[v] = u if semi[u] < semi[v] else p
        bucket[p] = []

    # Final pass in discovery order: resolve deferred idom links.
    for i in range(1, r):
        w = vertex[i]
        if idom[w] != vertex[semi[w]]:
            idom[w] = idom[idom[w]]
    idom[root] = -1

    return DominatorResult(
        labels=labels,
        idom=idom,
        reachable=reachable,
        is_terminal=is_terminal,
        discovery=discovery,
        vertex=vertex,
    )


def count_dominated_terminals(result: DominatorResult) -> list[int]:
    """Count protected terminals dominated by each reachable node.

    A terminal is dominated by exactly the nodes on the dominator-tree
    path from the root to it, so subtree sums of the terminal indicator
    yield every count in O(V).  Accumulation runs in reverse DFS
    discovery order: a dominator-tree child always has a larger discovery
    number than its parent, making this a valid post-order with no
    recursion.
    """
    counts = [1 if term else 0 for term in result.is_terminal]

    for i in range(len(result.vertex) - 1, 0, -1):
        v = result.vertex[i]
        counts[result.idom[v]] += counts[v]

    return counts
