"""事件演示路由：发布一个 demo 事件（需登录），用于联调事件链路。"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.auth import CurrentUser
from app.core.events import publish_event

router = APIRouter(prefix="/events", tags=["events"])


class DemoEventRequest(BaseModel):
    message: str = "hello"


@router.post("/demo")
async def demo_event(body: DemoEventRequest, user: CurrentUser) -> dict[str, str]:
    await publish_event("demo.ping", payload={"message": body.message, "by": user.email})
    return {"status": "enqueued", "type": "demo.ping"}
