from datetime import UTC, datetime

import pytest

from app.models import (
    Account,
    AgentQualityScore,
    BrainTask,
    DecisionTrace,
    ReflectionRecord,
    StrategyPlan,
)
from app.models.enums import Platform


async def _token(client, email: str, password: str) -> str:
    response = await client.post("/auth/login", json={"email": email, "password": password})
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _task_with_ledgers(session, admin) -> tuple[Account, BrainTask]:
    account = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="真实测试账号",
    )
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        title="提升抖音账号获客能力",
        runtime_mode="coo_v1",
        thread_id="brain-task-api-test",
    )
    session.add_all([account, task])
    await session.flush()

    evidence = [
        {
            "source_type": "manual_confirmation",
            "source_id": "confirmation:1",
            "metric": "business_goal",
            "value": "提升有效咨询",
            "time_range": {"start": None, "end": None},
            "collected_at": datetime.now(UTC).isoformat(),
            "freshness": "fresh",
        }
    ]
    session.add_all(
        [
            StrategyPlan(
                org_id=admin.org_id,
                task_id=task.id,
                account_id=account.id,
                goal="提升有效咨询",
                status="draft",
                version=1,
                situation_snapshot={"data_sufficiency": "insufficient"},
                strategy={"period_days": 30},
                kpis=[{"metric": "qualified_leads"}],
                risks=["历史转化数据不足"],
                evidence_refs=evidence,
                rationale_summary="先补齐转化基线。",
            ),
            DecisionTrace(
                org_id=admin.org_id,
                task_id=task.id,
                account_id=account.id,
                trace_key="strategy-baseline-v1",
                goal="提升有效咨询",
                evidence_refs=evidence,
                alternatives=[{"key": "collect_baseline"}],
                selected_option={"key": "collect_baseline"},
                decision_reason="缺少真实转化基线。",
                action_summary="先采集基线再调整内容比例。",
            ),
            AgentQualityScore(
                org_id=admin.org_id,
                task_id=task.id,
                score=82,
                dimensions={"factual_accuracy": 90},
                issues=["数据样本较少"],
                suggestions=["扩大观察窗口"],
                passed=True,
                iteration=0,
                evidence_refs=evidence,
            ),
            ReflectionRecord(
                org_id=admin.org_id,
                task_id=task.id,
                account_id=account.id,
                status="pending_observation",
                goal_snapshot={"metric": "qualified_leads"},
                expected_outcome={},
                observed_outcome={},
                evidence_refs=[],
                diagnosis=[],
                conclusion="等待真实效果数据。",
                next_strategy={},
                experience_candidates=[],
            ),
        ]
    )
    await session.commit()
    return account, task


@pytest.mark.asyncio
async def test_ai_coo_task_ledgers_are_readable_without_exposing_raw_storage(
    client, session, admin
) -> None:
    account, task = await _task_with_ledgers(session, admin)
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)

    strategy = await client.get(f"/brain/tasks/{task.id}/strategy", headers=headers)
    decisions = await client.get(f"/brain/tasks/{task.id}/decisions", headers=headers)
    quality = await client.get(f"/brain/tasks/{task.id}/quality-scores", headers=headers)
    reflection = await client.get(f"/brain/tasks/{task.id}/reflection", headers=headers)

    assert strategy.status_code == 200
    assert strategy.json()["account_id"] == account.id
    assert strategy.json()["goal"] == "提升有效咨询"
    assert decisions.status_code == 200
    assert decisions.json()[0]["selected_option"]["key"] == "collect_baseline"
    assert quality.status_code == 200
    assert quality.json()[0]["score"] == 82
    assert reflection.status_code == 200
    assert reflection.json()["status"] == "pending_observation"


@pytest.mark.asyncio
async def test_account_situation_reports_insufficient_data_instead_of_fake_zeroes(
    client, session, admin
) -> None:
    account, _ = await _task_with_ledgers(session, admin)
    token = await _token(client, "admin@test.com", "admin-pw-123")

    response = await client.get(
        f"/accounts/{account.id}/situation",
        headers=_auth(token),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["account_id"] == account.id
    assert payload["data_sufficiency"] == "insufficient"
    assert payload["conclusion"] == "数据不足"
    assert payload["evidence_refs"] == []
    assert "账号指标快照" in payload["missing_data"]

