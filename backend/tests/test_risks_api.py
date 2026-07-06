"""风险队列 API：整合质量门、授权、模型失败、平台回流异常。"""

import pytest

from app.models import Account, ContentItem, GateApproval, LLMCall, Project
from app.models.enums import GateStatus, GateType, Platform


async def _token(client, email: str, password: str) -> str:
    resp = await client.post("/auth/login", json={"email": email, "password": password})
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_risk_queue_collects_gate_auth_model_and_sync_risks(client, admin, session):
    project = Project(org_id=admin.org_id, name="P")
    session.add(project)
    await session.flush()
    content = ContentItem(project_id=project.id, title="内容A")
    session.add(content)
    await session.flush()
    session.add_all(
        [
            GateApproval(
                content_item_id=content.id,
                gate=GateType.SCRIPT_COMPLIANCE,
                status=GateStatus.PENDING,
            ),
            Account(
                org_id=admin.org_id,
                nickname="授权过期号",
                platform=Platform.DOUYIN,
                auth={"auth_status": "expired", "data_sync_status": "failed"},
            ),
            LLMCall(
                org_id=admin.org_id,
                agent_code="02-content-director",
                provider="deepseek",
                model="deepseek-chat",
                status="error",
                error="rate limit",
            ),
        ]
    )
    await session.commit()

    token = await _token(client, "admin@test.com", "admin-pw-123")
    resp = await client.get("/risks/queue", headers=_auth(token))

    assert resp.status_code == 200
    body = resp.json()
    categories = [row["category"] for row in body]
    assert categories[0] == "quality_gate"
    assert set(categories) == {"quality_gate", "account_auth", "data_sync", "model_failure"}
    assert any(row["title"] == "脚本合规待审批" for row in body)
    assert any(row["title"] == "授权过期号授权过期" for row in body)
    assert any(row["title"] == "授权过期号平台回流失败" for row in body)
    model_risk = next(row for row in body if row["category"] == "model_failure")
    assert model_risk["severity"] == "high"
    assert model_risk["source"] == "deepseek-chat"
