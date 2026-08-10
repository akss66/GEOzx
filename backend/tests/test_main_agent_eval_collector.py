from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest

from app.models import (
    Account,
    AgentInvocation,
    AgentRun,
    AgentToolCall,
    BrainTask,
    ContentItem,
    ConversationThread,
    ConversationTurn,
    Deliverable,
    Event,
    SkillRun,
    ToolExecutionAttempt,
)
from app.models.enums import (
    AgentCode,
    AgentInvocationStatus,
    BrainTaskStatus,
    BrainTaskType,
    ContentStage,
    ContentStatus,
    DeliverableStatus,
    DeliverableType,
    Platform,
)
from app.orchestrator.skill_runtime import skill_input_hash
from evals.collector import EvaluationScopeError, collect_observation

SECRET_MARKERS = (
    "RAW_PROMPT_SECRET",
    "API_KEY_SECRET",
    "ERROR_DETAIL_SECRET",
    "PROVIDER_BODY_SECRET",
    "FOREIGN_ACCOUNT_SECRET",
)


@dataclass(frozen=True)
class PersistedScope:
    account_id: int
    thread_id: int
    turn_id: int


async def _persist_completed_analysis(session, admin) -> PersistedScope:
    account = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="eval-current-account",
    )
    foreign_account = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="eval-foreign-account",
    )
    session.add_all([account, foreign_account])
    await session.flush()

    thread = ConversationThread(
        org_id=admin.org_id,
        created_by_id=admin.id,
        account_id=account.id,
        title="evaluation thread",
    )
    session.add(thread)
    await session.flush()
    turn = ConversationTurn(
        thread_id=thread.id,
        org_id=admin.org_id,
        created_by_id=admin.id,
        client_message_id="eval:analysis-summary-01",
        user_input="最近30天账号表现怎么样？",
        assistant_response="最近30天播放量为700，建议继续观察7天。",
        intent={
            "mode": "skill",
            "intent": "explicit_skill",
            "skill_code": "account_data_analysis",
        },
        status="completed",
        route_ms=15,
        first_token_ms=120,
        completion_ms=800,
        total_ms=950,
        model_call_count=2,
        tool_call_count=1,
    )
    session.add(turn)
    await session.flush()

    content = ContentItem(
        created_by_id=admin.id,
        account_id=account.id,
        title="evaluation analysis",
        current_stage=ContentStage.OPERATION,
        status=ContentStatus.DRAFT,
    )
    foreign_content = ContentItem(
        created_by_id=admin.id,
        account_id=foreign_account.id,
        title="foreign evaluation analysis",
        current_stage=ContentStage.OPERATION,
        status=ContentStatus.DRAFT,
    )
    session.add_all([content, foreign_content])
    await session.flush()
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        content_item_id=content.id,
        title="evaluation analysis",
        type=BrainTaskType.REVIEW_OPTIMIZATION,
        status=BrainTaskStatus.COMPLETED,
        current_focus="completed analysis",
        runtime_mode="eval-fixture",
    )
    session.add(task)
    await session.flush()
    run = AgentRun(
        org_id=admin.org_id,
        requested_by_id=admin.id,
        task_id=task.id,
        thread_id=thread.id,
        turn_id=turn.id,
        client_message_id="eval:analysis-summary-01",
        status="completed",
        phase="completed",
        request_payload={"api_key": "API_KEY_SECRET", "prompt": "RAW_PROMPT_SECRET"},
        result_payload={
            "mode": "skill",
            "status": "completed",
            "provider_response": "PROVIDER_BODY_SECRET",
        },
        error_detail="ERROR_DETAIL_SECRET",
    )
    session.add(run)
    await session.flush()
    frozen_input = {"account_id": account.id, "days": 30}
    skill_run = SkillRun(
        org_id=admin.org_id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        task_id=task.id,
        idempotency_key="eval:account-data-analysis",
        skill_code="account_data_analysis",
        skill_version=1,
        status="completed",
        input_snapshot=frozen_input,
        input_hash=skill_input_hash(frozen_input),
        output_snapshot={"provider_body": "PROVIDER_BODY_SECRET"},
        quality_score=Decimal("0.9100"),
    )
    session.add(skill_run)
    await session.flush()

    failed_expert = AgentInvocation(
        task_id=task.id,
        run_id=run.id,
        skill_run_id=skill_run.id,
        thread_id=thread.id,
        turn_id=turn.id,
        step_key="analysis",
        attempt=0,
        agent_code=AgentCode.OPERATOR,
        agent_name="数据分析专家",
        status=AgentInvocationStatus.FAILED,
        input_summary="RAW_PROMPT_SECRET",
        output_summary="PROVIDER_BODY_SECRET",
        model="provider/model-a",
        token_count=50,
        cost=Decimal("0.1200"),
        failure_reason="ERROR_DETAIL_SECRET",
    )
    successful_expert = AgentInvocation(
        task_id=task.id,
        run_id=run.id,
        skill_run_id=skill_run.id,
        thread_id=thread.id,
        turn_id=turn.id,
        step_key="analysis",
        attempt=1,
        agent_code=AgentCode.OPERATOR,
        agent_name="数据分析专家",
        status=AgentInvocationStatus.DONE,
        input_summary="RAW_PROMPT_SECRET",
        output_summary="PROVIDER_BODY_SECRET",
        model="provider/model-a",
        token_count=80,
        cost=Decimal("0.1800"),
    )
    session.add_all([failed_expert, successful_expert])
    await session.flush()
    tool_call = AgentToolCall(
        org_id=admin.org_id,
        task_id=task.id,
        invocation_id=successful_expert.id,
        skill_run_id=skill_run.id,
        thread_id=thread.id,
        turn_id=turn.id,
        module="eval",
        agent_code=AgentCode.OPERATOR.value,
        tool_code="account.metrics_analysis",
        tool_name="Account Metrics Analysis",
        idempotency_key="eval:metrics",
        side_effect_level="read",
        status="completed",
        permission_mode="auto",
        requires_human_confirmation=False,
        input_summary="RAW_PROMPT_SECRET",
        output_summary="PROVIDER_BODY_SECRET",
        error="ERROR_DETAIL_SECRET",
        latency_ms=240,
        cost=Decimal("0.0100"),
        meta={"authorization": "API_KEY_SECRET"},
    )
    session.add(tool_call)
    await session.flush()
    session.add_all(
        [
            ToolExecutionAttempt(
                tool_call_id=tool_call.id,
                attempt_no=1,
                status="failed",
                error="ERROR_DETAIL_SECRET",
                meta={"provider_body": "PROVIDER_BODY_SECRET"},
            ),
            ToolExecutionAttempt(
                tool_call_id=tool_call.id,
                attempt_no=2,
                status="success",
                meta={"provider_body": "PROVIDER_BODY_SECRET"},
            ),
        ]
    )

    session.add_all(
        [
            Deliverable(
                content_item_id=content.id,
                thread_id=thread.id,
                turn_id=turn.id,
                run_id=run.id,
                skill_run_id=skill_run.id,
                agent_code=AgentCode.OPERATOR.value,
                type=DeliverableType.REVIEW_REPORT,
                version=1,
                status=DeliverableStatus.PENDING_REVIEW,
                payload={"artifact_type": "generic_review", "summary": "older generic result"},
            ),
            Deliverable(
                content_item_id=content.id,
                thread_id=thread.id,
                turn_id=turn.id,
                run_id=run.id,
                skill_run_id=skill_run.id,
                agent_code=AgentCode.OPERATOR.value,
                type=DeliverableType.REVIEW_REPORT,
                version=2,
                status=DeliverableStatus.PENDING_REVIEW,
                payload={
                    "artifact_type": "account_analysis_answer",
                    "answerability": "full",
                    "summary": "当前账号播放量为700",
                    "claims": ["performance_summary"],
                    "key_facts": [{"metric_code": "play", "current_value": 700, "unit": "count"}],
                    "recommendations": [
                        {"action": "继续测试开头", "metric": "play", "observation_days": 7}
                    ],
                    "evidence_refs": [
                        {
                            "account_id": account.id,
                            "metric_code": "play",
                            "value": 700,
                            "unit": "count",
                        }
                    ],
                    "raw_prompt": "RAW_PROMPT_SECRET",
                    "provider_body": "PROVIDER_BODY_SECRET",
                },
            ),
            Deliverable(
                content_item_id=foreign_content.id,
                thread_id=thread.id,
                turn_id=turn.id,
                run_id=run.id,
                skill_run_id=skill_run.id,
                agent_code=AgentCode.OPERATOR.value,
                type=DeliverableType.REVIEW_REPORT,
                version=1,
                status=DeliverableStatus.PENDING_REVIEW,
                payload={
                    "artifact_type": "account_analysis_answer",
                    "summary": "FOREIGN_ACCOUNT_SECRET",
                },
            ),
        ]
    )
    await session.commit()
    return PersistedScope(account_id=account.id, thread_id=thread.id, turn_id=turn.id)


async def _persist_completed_query(
    session,
    admin,
    *,
    persist_tool_event: bool = True,
) -> PersistedScope:
    account = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="eval-query-account",
    )
    session.add(account)
    await session.flush()
    thread = ConversationThread(
        org_id=admin.org_id,
        created_by_id=admin.id,
        account_id=account.id,
        title="query evaluation thread",
    )
    session.add(thread)
    await session.flush()
    turn = ConversationTurn(
        thread_id=thread.id,
        org_id=admin.org_id,
        created_by_id=admin.id,
        client_message_id="eval:data-exists-01",
        user_input="我现在账号有数据吗？",
        assistant_response="当前账号已有可用数据。",
        intent={
            "mode": "query",
            "intent": "account_data_query",
            "skill_code": "account_data_query",
        },
        status="completed",
    )
    session.add(turn)
    await session.flush()
    run = AgentRun(
        org_id=admin.org_id,
        requested_by_id=admin.id,
        thread_id=thread.id,
        turn_id=turn.id,
        client_message_id=turn.client_message_id,
        status="completed",
        phase="completed",
    )
    session.add(run)
    await session.flush()
    query_input = {"account_id": account.id, "days": 30}
    skill_run = SkillRun(
        org_id=admin.org_id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        task_id=None,
        idempotency_key="account-data-query:v1",
        skill_code="account_data_query",
        skill_version=1,
        status="completed",
        input_snapshot=query_input,
        input_hash=skill_input_hash(query_input),
        output_snapshot={
            "artifact_type": "account_analysis_answer",
            "answerability": "full",
            "summary": "当前账号已有可用数据。",
            "claims": ["data_exists"],
            "evidence_refs": [
                {
                    "account_id": account.id,
                    "metric_code": "play",
                    "value": 700,
                    "unit": "count",
                }
            ],
            "provider_body": "PROVIDER_BODY_SECRET",
        },
    )
    session.add(skill_run)
    await session.flush()
    if persist_tool_event:
        session.add(
            Event(
                type="brain.runtime.tool_completed",
                thread_id=thread.id,
                turn_id=turn.id,
                run_id=run.id,
                skill_run_id=skill_run.id,
                payload={
                    "org_id": admin.org_id,
                    "account_id": account.id,
                    "tool_code": "account.data_context",
                },
                idempotency_key=f"eval-query-tool:{turn.id}",
            )
        )
    await session.commit()
    return PersistedScope(account_id=account.id, thread_id=thread.id, turn_id=turn.id)


@pytest.mark.asyncio
async def test_collector_normalizes_one_scoped_turn(session, admin) -> None:
    scope = await _persist_completed_analysis(session, admin)

    observation = await collect_observation(
        session,
        case_id="analysis-summary-01",
        user_id=admin.id,
        account_id=scope.account_id,
        thread_id=scope.thread_id,
        turn_id=scope.turn_id,
    )

    assert observation.route_mode == "skill"
    assert observation.route_skill_code == "account_data_analysis"
    assert [item["tool_code"] for item in observation.tool_calls] == ["account.metrics_analysis"]
    assert observation.answer_payload["artifact_type"] == "account_analysis_answer"
    assert observation.answer_payload["summary"] == "当前账号播放量为700"
    assert observation.evidence_refs == (
        {"account_id": scope.account_id, "metric_code": "play", "value": 700, "unit": "count"},
    )
    assert observation.terminal_states == {
        "turn": "completed",
        "run": "completed",
        "skill": "completed",
    }


@pytest.mark.asyncio
async def test_collector_rejects_thread_from_another_account(session, admin) -> None:
    scope = await _persist_completed_analysis(session, admin)

    with pytest.raises(EvaluationScopeError):
        await collect_observation(
            session,
            case_id="scope-check",
            user_id=admin.id,
            account_id=scope.account_id + 1,
            thread_id=scope.thread_id,
            turn_id=scope.turn_id,
        )


@pytest.mark.asyncio
async def test_collector_rejects_turn_from_another_user(session, admin) -> None:
    scope = await _persist_completed_analysis(session, admin)

    with pytest.raises(EvaluationScopeError):
        await collect_observation(
            session,
            case_id="scope-check",
            user_id=admin.id + 1,
            account_id=scope.account_id,
            thread_id=scope.thread_id,
            turn_id=scope.turn_id,
        )


@pytest.mark.asyncio
async def test_collector_counts_tool_retries_and_all_expert_attempts(session, admin) -> None:
    scope = await _persist_completed_analysis(session, admin)

    observation = await collect_observation(
        session,
        case_id="analysis-summary-01",
        user_id=admin.id,
        account_id=scope.account_id,
        thread_id=scope.thread_id,
        turn_id=scope.turn_id,
    )

    assert observation.tool_calls[0]["retry_count"] == 1
    assert [item["attempt"] for item in observation.expert_invocations] == [0, 1]
    assert [item["status"] for item in observation.expert_invocations] == ["failed", "done"]


@pytest.mark.asyncio
async def test_collector_exposes_only_bounded_safe_fields(session, admin) -> None:
    scope = await _persist_completed_analysis(session, admin)

    observation = await collect_observation(
        session,
        case_id="analysis-summary-01",
        user_id=admin.id,
        account_id=scope.account_id,
        thread_id=scope.thread_id,
        turn_id=scope.turn_id,
    )
    serialized = observation.model_dump_json()

    assert set(observation.tool_calls[0]) == {
        "tool_code",
        "status",
        "latency_ms",
        "retry_count",
        "side_effect_level",
        "requires_human_confirmation",
    }
    assert set(observation.model_metadata) == {
        "models",
        "total_tokens",
        "total_cost",
    }
    for marker in SECRET_MARKERS:
        assert marker not in serialized


@pytest.mark.asyncio
async def test_collector_derives_query_payload_and_tool_from_scoped_skill_run(
    session,
    admin,
) -> None:
    scope = await _persist_completed_query(session, admin)

    observation = await collect_observation(
        session,
        case_id="data-exists-01",
        user_id=admin.id,
        account_id=scope.account_id,
        thread_id=scope.thread_id,
        turn_id=scope.turn_id,
    )

    assert observation.answer_payload["claims"] == ["data_exists"]
    assert observation.evidence_refs == (
        {"account_id": scope.account_id, "metric_code": "play", "value": 700, "unit": "count"},
    )
    assert observation.tool_calls == (
        {
            "tool_code": "account.data_context",
            "status": "completed",
            "latency_ms": None,
            "retry_count": 0,
            "side_effect_level": None,
            "requires_human_confirmation": None,
        },
    )
    assert "PROVIDER_BODY_SECRET" not in observation.model_dump_json()


@pytest.mark.asyncio
async def test_collector_does_not_invent_query_tool_without_persisted_event(
    session,
    admin,
) -> None:
    scope = await _persist_completed_query(session, admin, persist_tool_event=False)

    observation = await collect_observation(
        session,
        case_id="data-exists-01",
        user_id=admin.id,
        account_id=scope.account_id,
        thread_id=scope.thread_id,
        turn_id=scope.turn_id,
    )

    assert observation.tool_calls == ()
