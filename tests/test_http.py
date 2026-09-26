"""HTTP-level tests for the FastAPI application."""

from __future__ import annotations

import pytest

from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def payload(**over):
    p = {
        "nodes": ["R", "A", "B", "C", "T1", "T2"],
        "root": "R",
        "terminals": ["T1", "T2"],
        "edges": [
            ["R", "A"], ["R", "B"],
            ["A", "C"], ["B", "C"], ["C", "T1"],
        ],
    }
    p.update(over)
    return p


def test_health_reports_available(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "available"


def test_audit_diamond(client):
    resp = client.post("/api/audit", json=payload())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["unreachable_terminals"] == ["T2"]
    critical = {c["node"]: c["dominated_terminals"] for c in body["critical_relays"]}
    assert critical == {"C": 1}
    idom = {d["node"]: d["immediate_dominator"] for d in body["dominators"]}
    assert idom["C"] == "R"  # diamond bypass protects the merge point


def test_audit_serial(client):
    resp = client.post("/api/audit", json=payload(
        edges=[["R", "A"], ["A", "B"], ["B", "T1"]]
    ))
    assert resp.status_code == 200
    critical = {c["node"]: c["dominated_terminals"]
                for c in resp.json()["critical_relays"]}
    assert critical == {"A": 1, "B": 1}


def test_audit_parallel_edges(client):
    resp = client.post("/api/audit", json=payload(
        edges=[["R", "A"], ["R", "A"], ["A", "T1"]]
    ))
    assert resp.status_code == 200
    critical = {c["node"]: c["dominated_terminals"]
                for c in resp.json()["critical_relays"]}
    assert critical == {"A": 1}


def test_validation_error_is_locatable_and_has_no_partial_body(client):
    resp = client.post("/api/audit", json=payload(
        edges=[["R", "A"], ["GHOST", "T1"]]
    ))
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "validation_failed"
    dangling = [d for d in body["details"] if d["type"] == "dangling_reference"]
    assert dangling and dangling[0]["loc"] == ["edges", 1, 0]
    assert "critical_relays" not in body and "dominators" not in body


def test_malformed_json_body(client):
    resp = client.post(
        "/api/audit",
        content="{not json",
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_json"


def test_json_object_required(client):
    resp = client.post("/api/audit", json=[1, 2, 3])
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_request"
