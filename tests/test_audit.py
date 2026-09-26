"""Tests for request validation and audit response assembly."""

from __future__ import annotations

import pytest

from app.audit import AuditValidationError, audit_graph


def base_payload(**over):
    payload = {
        "nodes": ["R", "A", "B", "C", "T1", "T2"],
        "root": "R",
        "terminals": ["T1", "T2"],
        "edges": [
            ["R", "A"], ["R", "B"],
            ["A", "C"], ["B", "C"], ["C", "T1"],
        ],
    }
    payload.update(over)
    return payload


def test_diamond_bypass_audit():
    # R->A->C->T1 and R->B->C->T1; T2 unreachable.
    out = audit_graph(base_payload())
    idom = {d["node"]: d["immediate_dominator"] for d in out["dominators"]}
    assert idom["R"] is None
    assert idom["A"] == "R"
    assert idom["B"] == "R"
    assert idom["C"] == "R"      # merge point is dominated only by root
    assert idom["T1"] == "C"
    # T2 is unreachable: absent from dominators, listed as unreachable.
    assert "T2" not in idom
    assert out["unreachable_terminals"] == ["T2"]
    # Bypassed A and B dominate no terminal; C is the single critical relay.
    critical = {c["node"]: c["dominated_terminals"] for c in out["critical_relays"]}
    assert critical == {"C": 1}


def test_serial_critical_points():
    payload = base_payload(edges=[["R", "A"], ["A", "B"], ["B", "T1"]])
    out = audit_graph(payload)
    critical = {c["node"]: c["dominated_terminals"] for c in out["critical_relays"]}
    # T2 unreachable; A and B are both serial single points of failure.
    assert critical == {"A": 1, "B": 1}
    assert out["unreachable_terminals"] == ["T2"]


def test_parallel_edges():
    payload = base_payload(
        edges=[["R", "A"], ["R", "A"], ["A", "T1"]]
    )
    out = audit_graph(payload)
    idom = {d["node"]: d["immediate_dominator"] for d in out["dominators"]}
    assert idom["A"] == "R"
    assert idom["T1"] == "A"
    critical = {c["node"]: c["dominated_terminals"] for c in out["critical_relays"]}
    assert critical == {"A": 1}


def test_results_sorted_by_identifier():
    nodes = ["z", "a", "m", "root", "t"]
    payload = {
        "nodes": nodes,
        "root": "root",
        "terminals": ["t"],
        "edges": [["root", "z"], ["z", "t"], ["root", "a"], ["a", "m"]],
    }
    out = audit_graph(payload)
    names = [d["node"] for d in out["dominators"]]
    assert names == sorted(names)
    assert out["unreachable_terminals"] == []
    # z dominates t; a/m do not.
    critical = {c["node"]: c["dominated_terminals"] for c in out["critical_relays"]}
    assert critical == {"z": 1}


def test_root_can_feed_terminal_directly():
    payload = base_payload(edges=[["R", "T1"]])
    out = audit_graph(payload)
    critical = {c["node"] for c in out["critical_relays"]}
    assert critical == set()
    assert out["unreachable_terminals"] == ["T2"]


def expect_invalid(payload, *expected_types):
    with pytest.raises(AuditValidationError) as exc:
        audit_graph(payload)
    types = [d.type for d in exc.value.details]
    for t in expected_types:
        assert t in types, types
    return exc.value.details


def test_dangling_edge_reference():
    payload = base_payload(edges=[["R", "GHOST"]])
    details = expect_invalid(payload, "dangling_reference")
    loc = [d for d in details if d.type == "dangling_reference"][0].loc
    assert loc == ["edges", 0, 1]


def test_unknown_root():
    expect_invalid(base_payload(root="NOPE"), "unknown_reference")


def test_duplicate_node_identifier():
    payload = base_payload(nodes=["R", "A", "A", "B", "C", "T1", "T2"])
    expect_invalid(payload, "duplicate_identifier")


def test_duplicate_terminal_identifier():
    payload = base_payload(terminals=["T1", "T1"])
    expect_invalid(payload, "duplicate_identifier")


def test_unknown_terminal_reference():
    payload = base_payload(terminals=["GHOST"])
    expect_invalid(payload, "unknown_reference")


def test_terminal_with_outgoing_edge():
    payload = base_payload(
        edges=[["R", "T1"], ["T1", "A"], ["A", "T2"]]
    )
    details = expect_invalid(payload, "terminal_has_outgoing_edge")
    loc = [d for d in details if d.type == "terminal_has_outgoing_edge"][0].loc
    assert loc == ["edges", 1, 0]


def test_self_loop_forbidden():
    payload = base_payload(edges=[["R", "A"], ["A", "A"]])
    expect_invalid(payload, "self_loop")


def test_non_ascii_identifier():
    payload = base_payload(nodes=["R", "A", "B", "C", "T1", "端"])
    expect_invalid(payload, "not_ascii_identifier")


def test_multiple_errors_reported_together_no_partial_audit():
    payload = {
        "nodes": ["R", "A", "A"],          # duplicate
        "root": "GHOST",                    # dangling root
        "terminals": ["R", "GHOST2"],       # unknown terminal (twice-ish)
        "edges": [["X", "Y"], ["R", "R"]],  # dangling endpoints + self loop
    }
    with pytest.raises(AuditValidationError) as exc:
        audit_graph(payload)
    types = {d.type for d in exc.value.details}
    assert "duplicate_identifier" in types
    assert "unknown_reference" in types
    assert "dangling_reference" in types
    assert "self_loop" in types
    # Every detail is locatable.
    for d in exc.value.details:
        assert d.loc and d.message


def test_cardinality_limits():
    payload = {"nodes": ["R"], "root": "R", "terminals": [], "edges": []}
    expect_invalid(payload, "invalid_count")


def test_malformed_edge_shape():
    payload = base_payload(edges=[["R"]])
    expect_invalid(payload, "malformed_edge")


def test_many_terminals_counts():
    # R -> A -> T1..T1000, plus bypass branch that reaches none of them.
    terms = [f"T{i}" for i in range(1000)]
    nodes = ["R", "A", "B"] + terms
    edges = [["R", "A"], ["R", "B"]] + [["A", t] for t in terms]
    payload = {"nodes": nodes, "root": "R", "terminals": terms, "edges": edges}
    out = audit_graph(payload)
    critical = {c["node"]: c["dominated_terminals"] for c in out["critical_relays"]}
    assert critical == {"A": 1000}
