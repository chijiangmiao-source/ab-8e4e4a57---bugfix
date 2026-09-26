"""Request validation and audit orchestration.

Validation is *all-or-nothing*: every semantic problem detectable in the
request is collected and reported with a precise location, and no audit
result is produced when the request is invalid -- the service never
returns a partial audit.
"""

from __future__ import annotations

from dataclasses import dataclass

from .dominators import compute_dominators, count_dominated_terminals

NODE_MIN = 2
NODE_MAX = 200_000
TERMINAL_MIN = 1
TERMINAL_MAX = 20_000
EDGE_MAX = 500_000


@dataclass
class ValidationDetail:
    """One locatable request problem."""

    loc: list[str | int]
    type: str
    message: str

    def to_dict(self) -> dict[str, object]:
        return {"loc": self.loc, "type": self.type, "message": self.message}


class AuditValidationError(ValueError):
    def __init__(self, details: list[ValidationDetail]):
        super().__init__("audit request validation failed")
        self.details = details


def audit_graph(payload: dict[str, object]) -> dict[str, object]:
    """Validate a request and run the dominator audit.

    Returns the JSON-serialisable response body.  Raises
    :class:`AuditValidationError` with locatable details on any invalid
    input.
    """
    details: list[ValidationDetail] = []

    raw_nodes = payload.get("nodes")
    raw_root = payload.get("root")
    raw_terminals = payload.get("terminals")
    raw_edges = payload.get("edges")

    # ---- Basic shape / presence (defensive; Pydantic handles most) -------
    if not isinstance(raw_nodes, list):
        raise AuditValidationError(
            [ValidationDetail(["nodes"], "missing_or_wrong_type",
                              "nodes must be an array of ASCII identifiers")]
        )
    if not isinstance(raw_terminals, list):
        raise AuditValidationError(
            [ValidationDetail(["terminals"], "missing_or_wrong_type",
                              "terminals must be an array")]
        )
    if not isinstance(raw_edges, list):
        raise AuditValidationError(
            [ValidationDetail(["edges"], "missing_or_wrong_type",
                              "edges must be an array of [source, target] pairs")]
        )
    if not isinstance(raw_root, str):
        raise AuditValidationError(
            [ValidationDetail(["root"], "missing_or_wrong_type",
                              "root must be a string node identifier")]
        )

    # ---- Cardinality limits ---------------------------------------------
    if not (NODE_MIN <= len(raw_nodes) <= NODE_MAX):
        details.append(ValidationDetail(
            ["nodes"], "invalid_count",
            f"nodes count must be between {NODE_MIN} and {NODE_MAX}",
        ))
    if not (TERMINAL_MIN <= len(raw_terminals) <= TERMINAL_MAX):
        details.append(ValidationDetail(
            ["terminals"], "invalid_count",
            f"terminals count must be between {TERMINAL_MIN} and {TERMINAL_MAX}",
        ))
    if len(raw_edges) > EDGE_MAX:
        details.append(ValidationDetail(
            ["edges"], "too_many_edges",
            f"edge count must not exceed {EDGE_MAX}",
        ))

    # ---- Nodes: ASCII identifiers, unique -------------------------------
    nodes: list[str] = []
    seen: set[str] = set()
    for i, node in enumerate(raw_nodes):
        if not isinstance(node, str):
            details.append(ValidationDetail(
                ["nodes", i], "wrong_type", "node identifier must be a string",
            ))
            continue
        if not node or not node.isascii():
            details.append(ValidationDetail(
                ["nodes", i], "not_ascii_identifier",
                "node identifier must be a non-empty ASCII string",
            ))
            continue
        if node in seen:
            # Every repeated occurrence is reported with its own index.
            details.append(ValidationDetail(
                ["nodes", i], "duplicate_identifier",
                f"duplicate node identifier {node!r}",
            ))
        else:
            seen.add(node)
        nodes.append(node)

    # ---- Root reference --------------------------------------------------
    if isinstance(raw_root, str) and raw_root not in seen:
        details.append(ValidationDetail(
            ["root"], "unknown_reference",
            f"root {raw_root!r} is not declared in nodes",
        ))

    # ---- Terminals: existing, unique ------------------------------------
    terminals: list[str] = []
    seen_terminals: set[str] = set()
    for i, term in enumerate(raw_terminals):
        if not isinstance(term, str):
            details.append(ValidationDetail(
                ["terminals", i], "wrong_type",
                "terminal identifier must be a string",
            ))
            continue
        if term not in seen:
            details.append(ValidationDetail(
                ["terminals", i], "unknown_reference",
                f"terminal {term!r} is not declared in nodes",
            ))
        if term in seen_terminals:
            details.append(ValidationDetail(
                ["terminals", i], "duplicate_identifier",
                f"duplicate terminal identifier {term!r}",
            ))
        else:
            seen_terminals.add(term)
        terminals.append(term)

    # ---- Edges: well-shaped pairs, endpoints valid, no self loops --------
    edges: list[tuple[str, str]] = []
    for i, edge in enumerate(raw_edges):
        if (
            not isinstance(edge, (list, tuple))
            or len(edge) != 2
            or not all(isinstance(x, str) for x in edge)
        ):
            details.append(ValidationDetail(
                ["edges", i], "malformed_edge",
                "edge must be a [source, target] pair of strings",
            ))
            continue
        src, dst = edge
        if src not in seen:
            details.append(ValidationDetail(
                ["edges", i, 0], "dangling_reference",
                f"edge source {src!r} is not declared in nodes",
            ))
        if dst not in seen:
            details.append(ValidationDetail(
                ["edges", i, 1], "dangling_reference",
                f"edge target {dst!r} is not declared in nodes",
            ))
        if src == dst:
            details.append(ValidationDetail(
                ["edges", i], "self_loop",
                f"self-loop on {src!r} is forbidden",
            ))
        edges.append((src, dst))

    # Terminals must be sinks: no outgoing edges.  Checked in a second
    # pass so a malformed edge above cannot mask the terminal rule.
    terminal_sources: set[str] = set()
    for i, (src, dst) in enumerate(edges):
        if src in seen_terminals and src not in terminal_sources:
            terminal_sources.add(src)
            details.append(ValidationDetail(
                ["edges", i, 0], "terminal_has_outgoing_edge",
                f"protected terminal {src!r} must not have outgoing edges",
            ))

    if details:
        raise AuditValidationError(details)

    # ---- Build the integer-id graph (parallel edges retained) ------------
    index = {label: i for i, label in enumerate(nodes)}
    n = len(nodes)
    adjacency: list[list[int]] = [[] for _ in range(n)]
    predecessors: list[list[int]] = [[] for _ in range(n)]
    for src, dst in edges:
        u, v = index[src], index[dst]
        adjacency[u].append(v)
        predecessors[v].append(u)

    root_id = index[raw_root]
    is_terminal = [False] * n
    for term in terminals:
        is_terminal[index[term]] = True

    # ---- Dominator analysis in one global pass ---------------------------
    result = compute_dominators(
        n, adjacency, predecessors, root_id, is_terminal, labels=nodes,
    )
    counts = count_dominated_terminals(result)

    # ---- Serialise, everything sorted by node identifier -----------------
    unreachable_terminals = sorted(
        label
        for label in terminals
        if not result.reachable[index[label]]
    )

    dominators: list[dict[str, object]] = []
    critical_relays: list[dict[str, object]] = []
    for node_id in sorted(
        (i for i in range(n) if result.reachable[i]),
        key=lambda i: nodes[i],
    ):
        label = nodes[node_id]
        idom_id = result.idom[node_id]
        dominators.append({
            "node": label,
            "immediate_dominator": None if idom_id == -1 else nodes[idom_id],
        })
        if node_id != root_id and not is_terminal[node_id] and counts[node_id] > 0:
            critical_relays.append({
                "node": label,
                "dominated_terminals": counts[node_id],
            })

    return {
        "root": raw_root,
        "reachable_node_count": len(result.vertex),
        "unreachable_terminals": unreachable_terminals,
        "dominators": dominators,
        "critical_relays": critical_relays,
    }
