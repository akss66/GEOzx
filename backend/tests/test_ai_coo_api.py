from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.models import (
    Account,
    AgentQualityScore,
    BrainTask,
    DecisionTrace,
    ExperienceMemory,
    LLMCall,
    OrchestrationPlan,
    ReflectionRecord,
    StrategyPlan,
    TaskBrief,
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
    task.brief = TaskBrief(
        goal="提升抖音账号获客能力",
        platforms=[Platform.DOUYIN.value],
        account_ids=[account.id],
        cycle="30 days",
        content_goal="提升有效咨询",
        risk_constraints=[],
        expected_outputs=["运营策略"],
        confirmation_actions=[],
    )
    task.plan = OrchestrationPlan(
        summary="AI COO 动态运营计划",
        steps=[],
        quality_gates=[],
        estimated_cost=0,
        requires_human_confirmation=True,
    )

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
                prompt_id="main-agent.strategy-planning",
                prompt_version="1.0.0",
                prompt_hash="strategy-hash",
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
                critic_prompt_id="main-agent.critic",
                critic_prompt_version="1.0.0",
                critic_prompt_hash="critic-hash",
                critic_model="deepseek-chat",
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


@pytest.mark.asyncio
async def test_runtime_response_includes_ai_coo_operating_summary(
    client, session, admin
) -> None:
    _account, task = await _task_with_ledgers(session, admin)
    token = await _token(client, "admin@test.com", "admin-pw-123")

    response = await client.get(
        f"/brain/tasks/{task.id}/runtime",
        headers=_auth(token),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["strategy"]["goal"] == "提升有效咨询"
    assert payload["decisions"][0]["selected_option"]["key"] == "collect_baseline"
    assert payload["quality_scores"][0]["score"] == 82
    assert payload["strategy"]["prompt_id"] == "main-agent.strategy-planning"
    assert payload["quality_scores"][0]["critic_prompt_id"] == "main-agent.critic"
    assert payload["reflection"]["status"] == "pending_observation"
    assert payload["operation_intelligence"]["task_id"] == task.id


@pytest.mark.asyncio
async def test_runtime_exposes_sanitized_llm_audit_to_admin_only(
    client,
    session,
    admin,
    member,
) -> None:
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=member.id,
        title="成员任务",
        runtime_mode="coo_v1",
        thread_id="brain-task-audit-test",
    )
    session.add(task)
    await session.flush()
    task.brief = TaskBrief(
        goal="成员任务",
        platforms=[Platform.DOUYIN.value],
        account_ids=[],
        cycle="7 days",
        content_goal="完成测试",
        risk_constraints=[],
        expected_outputs=[],
        confirmation_actions=[],
    )
    task.plan = OrchestrationPlan(
        summary="成员任务计划",
        steps=[],
        quality_gates=[],
        estimated_cost=0,
        requires_human_confirmation=False,
    )
    session.add(
        LLMCall(
            org_id=admin.org_id,
            created_by_id=member.id,
            task_id=task.id,
            trace_id="trace-audit-1",
            agent_code="01-positioning",
            prompt_id="expert.01-positioning",
            prompt_version="1.0.0",
            prompt_hash="prompt-hash",
            prompt_schema_version="positioning/v1",
            scope={"account_id": 2},
            budget={"max_tokens": 4096},
            provider="deepseek",
            model="deepseek-chat",
            prompt_tokens=120,
            completion_tokens=80,
            total_tokens=200,
            cost_usd=0.012,
            latency_ms=843,
            status="error",
            error="上游超时",
        )
    )
    await session.commit()

    member_token = await _token(client, "user@test.com", "user-pw-123")
    member_response = await client.get(
        f"/brain/tasks/{task.id}/runtime",
        headers=_auth(member_token),
    )
    assert member_response.status_code == 200
    assert member_response.json()["llm_calls"] == []

    admin_token = await _token(client, "admin@test.com", "admin-pw-123")
    admin_response = await client.get(
        f"/brain/tasks/{task.id}/runtime",
        headers=_auth(admin_token),
    )
    assert admin_response.status_code == 200
    audit = admin_response.json()["llm_calls"][0]
    assert audit["prompt_id"] == "expert.01-positioning"
    assert audit["prompt_version"] == "1.0.0"
    assert audit["prompt_hash"] == "prompt-hash"
    assert audit["model"] == "deepseek-chat"
    assert audit["total_tokens"] == 200
    assert audit["cost_usd"] == 0.012
    assert audit["latency_ms"] == 843
    assert audit["status"] == "error"
    assert audit["error"] == "上游超时"
    assert "scope" not in audit
    assert "budget" not in audit


@pytest.mark.asyncio
async def test_admin_can_verify_an_evidence_backed_experience_candidate(
    client, session, admin
) -> None:
    _account, task = await _task_with_ledgers(session, admin)
    reflection = await session.scalar(
        select(ReflectionRecord).where(ReflectionRecord.task_id == task.id)
    )
    assert reflection is not None
    reflection.status = "observed"
    reflection.experience_candidates = [
        {
            "key": "candidate-1",
            "industry": "家居服务",
            "action": "提高真实案例内容占比",
            "condition": "有效咨询不足",
            "result": "有效咨询提升 30%",
            "confidence": 0.8,
            "source_refs": [
                {
                    "source_type": "account_metric_snapshot",
                    "source_id": "snapshot:1",
                    "metric": "qualified_leads",
                    "value": 13,
                }
            ],
            "status": "candidate",
        }
    ]
    await session.commit()
    token = await _token(client, "admin@test.com", "admin-pw-123")

    response = await client.post(
        f"/brain/tasks/{task.id}/experience-candidates/candidate-1/verify",
        headers=_auth(token),
        json={"candidate_key": "candidate-1", "verification_note": "已由运营负责人核验"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "verified"
    assert payload["action"] == "提高真实案例内容占比"
    assert (
        await session.scalar(
            select(ExperienceMemory).where(ExperienceMemory.task_id == task.id)
        )
        is not None
    )

    memories = await client.get(
        "/experience-memories",
        headers=_auth(token),
    )
    assert memories.status_code == 200
    assert memories.json()[0]["task_id"] == task.id
    assert memories.json()[0]["status"] == "verified"


@pytest.mark.asyncio
async def test_task_observation_can_resume_through_stable_alias(
    client,
    session,
    admin,
) -> None:
    _account, task = await _task_with_ledgers(session, admin)
    token = await _token(client, "admin@test.com", "admin-pw-123")

    response = await client.post(
        f"/brain/tasks/{task.id}/resume-observation",
        headers=_auth(token),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "pending_observation"
