"""FastAPI application: emergency beam-stop relay network audit."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import __version__
from .audit import AuditValidationError, audit_graph

app = FastAPI(
    title="Relay Network Dominator Audit",
    version=__version__,
    description=(
        "Computes immediate dominators of relay nodes from the root "
        "controller and reports which non-terminal relays are single "
        "points of failure for protected terminals."
    ),
)


@app.exception_handler(AuditValidationError)
async def audit_validation_exception_handler(
    request: Request, exc: AuditValidationError
) -> JSONResponse:
    # 422 with locatable details; no partial audit is ever returned.
    return JSONResponse(
        status_code=422,
        content={
            "error": "validation_failed",
            "details": [d.to_dict() for d in exc.details],
        },
    )


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    """Liveness/readiness probe.

    Reflects actual service availability: a successful response means the
    process is up and the analysis core is importable and ready to serve
    requests.
    """
    # Importing the core eagerly proves the analysis module is loaded;
    # the endpoint itself being served proves the event loop is healthy.
    from . import dominators  # noqa: F401

    return {"status": "available", "service": "relay-audit", "version": __version__}


@app.post("/api/audit", tags=["audit"])
async def run_audit(request: Request) -> dict[str, Any]:
    """Run the dominator audit on a relay network description.

    The body is validated as a whole (unknown/dangling references,
    duplicate identifiers, terminals with outgoing edges, self loops,
    cardinality limits ...); on failure HTTP 422 carries locatable
    details and no partial audit is returned.
    """
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(  # type: ignore[return-value]
            status_code=400,
            content={
                "error": "invalid_json",
                "details": [{
                    "loc": ["body"],
                    "type": "invalid_json",
                    "message": "request body must be a JSON object",
                }],
            },
        )
    if not isinstance(payload, dict):
        return JSONResponse(  # type: ignore[return-value]
            status_code=400,
            content={
                "error": "invalid_request",
                "details": [{
                    "loc": ["body"],
                    "type": "wrong_type",
                    "message": "request body must be a JSON object",
                }],
            },
        )
    return audit_graph(payload)
