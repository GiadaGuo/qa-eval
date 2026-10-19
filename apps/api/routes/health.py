# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-19 11:26:47
# @Description: 
# ====================================================

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["Health"])


class HealthResponse(BaseModel):
    status: str
    service: str


@router.get("/health", response_model=HealthResponse)
def health_check() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="qa-eval-api",
    )
