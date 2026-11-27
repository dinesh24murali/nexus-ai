from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.health import run_checks

router = APIRouter(tags=["health"])


@router.get("/healthz", summary="Liveness probe")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", summary="Readiness probe (Postgres, Qdrant reachable; Langfuse optional)")
async def readyz(request: Request) -> JSONResponse:
    status, checks = await run_checks(request.app.state.readiness_checks)
    return JSONResponse(
        {"status": status, "checks": checks}, status_code=503 if status == "unavailable" else 200
    )
