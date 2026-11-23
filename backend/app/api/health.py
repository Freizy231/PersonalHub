"""健康检查接口。

M1 验收要求：GET /api/v1/health 返回后端状态。此接口刻意不访问数据库、
不读取秘密、不调用外部服务，因此可以在任何阶段安全地用于连通性验证。
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app import __version__

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    status: str = Field(description="健康状态，固定为 ok")
    service: str = Field(description="服务名")
    version: str = Field(description="应用版本")
    timestamp: str = Field(description="服务器当前 UTC 时间，ISO 8601")


@router.get("/health", response_model=HealthResponse, summary="健康检查")
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="personal-hub-backend",
        version=__version__,
        timestamp=datetime.now(UTC).isoformat(),
    )
