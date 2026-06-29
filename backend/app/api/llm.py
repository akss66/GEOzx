"""LLM 网关联调路由：POST /llm/ping（需登录）。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser
from app.db import get_session
from app.llm.gateway import LLMError, gateway
from app.schemas.llm import PingRequest, PingResponse, UsageOut

router = APIRouter(prefix="/llm", tags=["llm"])


@router.post("/ping", response_model=PingResponse)
async def llm_ping(
    body: PingRequest,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PingResponse:
    """经网关向模型发一条消息，返回结果与成本（用于联调与配置校验）。"""
    messages = [{"role": "user", "content": body.prompt}]
    try:
        result, cost = await gateway.chat(session, user.org_id, body.agent_code, messages)
    except LLMError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return PingResponse(
        model=result.model,
        content=result.content,
        usage=UsageOut(
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            total_tokens=result.total_tokens,
        ),
        cost_usd=cost,
    )
