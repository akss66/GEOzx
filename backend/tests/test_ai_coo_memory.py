from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.models import (
    Account,
    AccountClient,
    BrainTask,
    Client,
    ExperienceMemory,
    PlatformContentRecord,
    Project,
    ProjectAccount,
)
from app.models.enums import Platform
from app.services.ai_coo_memory import build_coo_memory_context


@pytest.mark.asyncio
async def test_coo_memory_is_scoped_bounded_and_uses_only_verified_experience(
    session,
    admin,
) -> None:
    client = Client(org_id=admin.org_id, name="家居客户")
    project = Project(org_id=admin.org_id, client=client, name="抖音获客项目")
    account = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="家居膜账号",
    )
    other_account = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="其他账号",
    )
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        title="提升咨询量",
        runtime_mode="coo_v1",
        thread_id="brain-task-memory",
    )
    session.add_all([client, project, account, other_account, task])
    await session.flush()
    session.add_all(
        [
            AccountClient(account_id=account.id, client_id=client.id),
            ProjectAccount(account_id=account.id, project_id=project.id),
            PlatformContentRecord(
                org_id=admin.org_id,
                account_id=account.id,
                platform=Platform.DOUYIN,
                title="门店施工案例",
                published_at=datetime(2026, 7, 26, 10, tzinfo=UTC),
                content_format="video",
            ),
            ExperienceMemory(
                org_id=admin.org_id,
                task_id=task.id,
                account_id=account.id,
                status="verified",
                industry="家居服务",
                action="增加真实案例内容",
                condition="本地高客单价服务",
                result="有效咨询提升",
                confidence=Decimal("0.90"),
                source_refs=[{"source_id": "metric:1"}],
                verification_method="manual_confirmation",
            ),
            ExperienceMemory(
                org_id=admin.org_id,
                task_id=task.id,
                account_id=account.id,
                status="candidate",
                industry="家居服务",
                action="未经验证动作",
                condition="未知",
                result="模型猜测",
                confidence=Decimal("0.50"),
                source_refs=[],
                verification_method="pending",
            ),
            ExperienceMemory(
                org_id=admin.org_id,
                task_id=task.id,
                account_id=other_account.id,
                status="verified",
                industry="其他行业",
                action="其他账号经验",
                condition="其他账号",
                result="不应读取",
                confidence=Decimal("0.90"),
                source_refs=[{"source_id": "metric:2"}],
                verification_method="manual_confirmation",
            ),
        ]
    )
    await session.commit()

    memory = await build_coo_memory_context(
        session,
        org_id=admin.org_id,
        account_id=account.id,
        client_ids=[client.id],
        project_ids=[project.id],
        situation_summary={"data_sufficiency": "partial"},
    )

    assert memory.business.org_name
    assert memory.business.clients == [{"id": client.id, "name": "家居客户"}]
    assert memory.business.projects == [
        {
            "id": project.id,
            "client_id": client.id,
            "name": "抖音获客项目",
            "description": "",
        }
    ]
    assert memory.account.nickname == "家居膜账号"
    assert memory.account.situation_summary["data_sufficiency"] == "partial"
    assert memory.content.recent_items[0]["title"] == "门店施工案例"
    assert [item.action for item in memory.experience.items] == [
        "增加真实案例内容"
    ]
