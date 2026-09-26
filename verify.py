#!/usr/bin/env python3
"""One-shot verification entrypoint for the ``verify`` compose service.

Runs, in order:
  1. the full pytest suite (dominator core, validation, HTTP layer);
  2. an application build/import check -- the FastAPI app and its routes
     load cleanly and every route's handler is importable;
  3. an HTTP smoke test against the running API service, covering the four
     required topologies: diamond bypass, serial critical point, parallel
     edges and an unreachable terminal.

Exits 0 only if every stage passes; any failure exits non-zero so the
container's status is a conclusive pass/fail.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

API_BASE_URL = os.environ.get("API_BASE_URL", "http://127.0.0.1:8000")

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"


def stage(name: str) -> None:
    print(f"\n=== verify stage: {name} ===", flush=True)


def run_pytest() -> bool:
    stage("1/3 code tests (pytest)")
    import pytest

    code = pytest.main(["-q", "--disable-warnings", "tests"])
    ok = code == 0
    print(f"[{PASS if ok else FAIL}] pytest exit code {code}", flush=True)
    return ok


def run_build_check() -> bool:
    stage("2/3 application build / import check")
    try:
        from app.main import app  # imports the whole ASGI application
        routes = {
            getattr(r, "path", None)
            for r in app.routes
        }
        assert "/health" in routes, "health route missing"
        assert "/api/audit" in routes, "audit route missing"
        # Exercise the ASGI app object construction (build artifact).
        assert callable(app)
        print(f"[{PASS}] application imports; routes: {sorted(r for r in routes if r)}",
              flush=True)
        return True
    except Exception as exc:  # noqa: BLE001 - report any build failure
        print(f"[{FAIL}] build check error: {exc!r}", flush=True)
        return False


def http_get(path: str):
    req = urllib.request.Request(API_BASE_URL + path, method="GET")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, json.load(resp)


def http_post(path: str, body: dict):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        API_BASE_URL + path, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def wait_for_health(attempts: int = 30, delay: float = 1.0) -> bool:
    import time

    for i in range(1, attempts + 1):
        try:
            status, body = http_get("/health")
            if status == 200 and body.get("status") == "available":
                print(f"[{PASS}] service healthy after {i} attempt(s)", flush=True)
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(delay)
    print(f"[{FAIL}] service did not become healthy at {API_BASE_URL}", flush=True)
    return False


def check(name: str, condition: bool, detail: str = "") -> bool:
    print(f"[{PASS if condition else FAIL}] {name}"
          + (f" -- {detail}" if detail and not condition else ""), flush=True)
    return condition


def run_http_smoke() -> bool:
    stage("3/3 HTTP smoke tests")
    if not wait_for_health():
        return False

    ok = True

    # --- Diamond bypass: two parallel branches merge before the terminal.
    status, body = http_post("/api/audit", {
        "nodes": ["R", "A", "B", "M", "T1"],
        "root": "R",
        "terminals": ["T1"],
        "edges": [["R", "A"], ["R", "B"], ["A", "M"], ["B", "M"], ["M", "T1"]],
    })
    idom = {d["node"]: d["immediate_dominator"] for d in body.get("dominators", [])}
    critical = {c["node"]: c["dominated_terminals"]
                for c in body.get("critical_relays", [])}
    ok &= check("diamond: HTTP 200", status == 200, f"status={status} body={body}")
    ok &= check("diamond: merge M bypass-dominated only by R",
                idom.get("M") == "R", f"idom(M)={idom.get('M')}")
    ok &= check("diamond: branches A/B are NOT single points",
                "A" not in critical and "B" not in critical, str(critical))
    ok &= check("diamond: merge M is the sole critical relay",
                critical == {"M": 1}, str(critical))

    # --- Serial chain: every relay is a single point of failure.
    status, body = http_post("/api/audit", {
        "nodes": ["R", "A", "B", "C", "T1"],
        "root": "R",
        "terminals": ["T1"],
        "edges": [["R", "A"], ["A", "B"], ["B", "C"], ["C", "T1"]],
    })
    critical = {c["node"]: c["dominated_terminals"]
                for c in body.get("critical_relays", [])}
    ok &= check("serial: HTTP 200", status == 200, f"status={status} body={body}")
    ok &= check("serial: A/B/C all critical with count 1",
                critical == {"A": 1, "B": 1, "C": 1}, str(critical))

    # --- Parallel edges: redundant wires, same relay still a SPOF.
    status, body = http_post("/api/audit", {
        "nodes": ["R", "A", "T1"],
        "root": "R",
        "terminals": ["T1"],
        "edges": [["R", "A"], ["R", "A"], ["A", "T1"], ["A", "T1"]],
    })
    idom = {d["node"]: d["immediate_dominator"] for d in body.get("dominators", [])}
    critical = {c["node"]: c["dominated_terminals"]
                for c in body.get("critical_relays", [])}
    ok &= check("parallel: HTTP 200", status == 200, f"status={status} body={body}")
    ok &= check("parallel: idom(A)=R and idom(T1)=A",
                idom.get("A") == "R" and idom.get("T1") == "A", str(idom))
    ok &= check("parallel: relay A still a single point (wires share relay)",
                critical == {"A": 1}, str(critical))

    # --- Unreachable terminal: reported, no dominator entry for it.
    status, body = http_post("/api/audit", {
        "nodes": ["R", "A", "T1", "TLOST"],
        "root": "R",
        "terminals": ["T1", "TLOST"],
        "edges": [["R", "A"], ["A", "T1"]],
    })
    idom = {d["node"] for d in body.get("dominators", [])}
    ok &= check("unreachable: HTTP 200", status == 200, f"status={status} body={body}")
    ok &= check("unreachable: TLOST listed",
                body.get("unreachable_terminals") == ["TLOST"],
                str(body.get("unreachable_terminals")))
    ok &= check("unreachable: TLOST has no dominator entry",
                "TLOST" not in idom, str(sorted(idom)))

    # --- Negative smoke: invalid input -> 422 locatable, no partial audit.
    status, body = http_post("/api/audit", {
        "nodes": ["R", "A"],
        "root": "R",
        "terminals": ["A"],
        "edges": [["R", "GHOST"]],
    })
    ok &= check("invalid: HTTP 422", status == 422, f"status={status}")
    types = [d.get("type") for d in body.get("details", [])]
    ok &= check("invalid: dangling reference locatable",
                "dangling_reference" in types and "terminal_has_outgoing_edge" not in types,
                str(body))
    ok &= check("invalid: no partial audit fields",
                "dominators" not in body and "critical_relays" not in body, str(body))

    return ok


def main() -> int:
    print("Relay audit verification", flush=True)
    print(f"Target API: {API_BASE_URL}", flush=True)

    results = {
        "pytest": run_pytest(),
        "build_check": run_build_check(),
        "http_smoke": run_http_smoke(),
    }

    print("\n=== verify summary ===", flush=True)
    for name, ok in results.items():
        print(f"  {name}: {PASS if ok else FAIL}", flush=True)

    overall = all(results.values())
    print(f"\nOVERALL: {PASS if overall else FAIL}", flush=True)
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
