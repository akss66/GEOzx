from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path

import pytest

from app.config import settings
from app.core.security import create_access_token
from app.models import (
    AgentRun,
    AgentToolCall,
    BrainTask,
    ContentItem,
    Deliverable,
    SkillRun,
)
from app.models.enums import (
    AgentCode,
    BrainTaskStatus,
    BrainTaskType,
    ContentStage,
    ContentStatus,
    DeliverableStatus,
    DeliverableType,
)
from app.orchestrator.skill_runtime import SkillExecutionResult, skill_input_hash
from app.schemas.conversation import TurnExecutionMode, TurnRouteDecision
from app.worker import _execute_v2_conversation_run
from evals.case_loader import load_evaluation_cases
from evals.collector import collect_observation
from evals.models import EvaluationCase, EvaluationObservation
from evals.runner import EvaluationRunner

CASES = Path(__file__).parents[1] / "evals/cases/account_analysis_v1.json"


def _auth(user) -> dict[str, str]:
    token = create_access_token(str(user.id), user.role.value)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _evaluation_runtime(monkeypatch):
    async def no_queue(*, run_id: int) -> None:
        del run_id

    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    monkeypatch.setattr(settings, "main_agent_typed_runtime_enabled", True)
    monkeypatch.setattr("app.api.conversations.enqueue_agent_runtime", no_queue)
    monkeypatch.setattr(
        "app.services.turn_execution.brain_intelligence.can_answer_without_classification",
        lambda _message: False,
    )


class DeterministicMatrixHarness:
    def __init__(self) -> None:
        self.current_case: EvaluationCase | None = None
        self.query_accounts: list[int] = []

    def _case(self) -> EvaluationCase:
        if self.current_case is None:
            raise AssertionError("matrix case was not selected")
        return self.current_case

    def payload(self, account_id: int) -> dict[str, object]:
        case = self._case()
        metrics = case.expectation.required_evidence_metrics
        evidence = [
            {
                "account_id": account_id,
                "metric_code": metric,
                "value": 700,
                "unit": "count",
            }
            for metric in metrics
        ]
        key_facts = [
            {
                "metric_code": metrics[0],
                "current_value": 700,
                "unit": "count",
            }
        ] if metrics else []
        recommendations: list[dict[str, object]] = []
        if "recommendations_max_3" in case.expectation.required_claims:
            recommendations = [
                {"action": "测试前三秒开头", "metric": "play", "observation_days": 7}
            ]
        if "action_with_metric_and_days" in case.expectation.required_claims:
            recommendations = [
                {"action": "测试前三秒开头", "metric": "play", "observation_days": 7}
            ]
        return {
            "artifact_type": "account_analysis_answer",
            "answerability": case.expectation.expected_answerability,
            "summary": f"{case.case_id} deterministic result",
            "claims": list(case.expectation.required_claims),
            "key_facts": key_facts,
            "recommendations": recommendations,
            "evidence_refs": evidence,
            "account_id": account_id,
            "period": {"days": 30},
            "metrics": {},
            "sources": [],
            "coverage": {},
        }

    def query_adapter(self):
        harness = self

        class Adapter:
            async def invoke(self, name, params, context):
                assert name == "account.data_context"
                assert params == {"days": 30}
                harness.query_accounts.append(context.account_id)
                return harness.payload(context.account_id)

        return Adapter()

    async def classify(self, *_args, **_kwargs) -> TurnRouteDecision:
        case = self._case()
        mode = TurnExecutionMode(case.expectation.expected_mode)
        return TurnRouteDecision(
            mode=mode,
            intent="evaluation_case",
            confidence=1,
            reason="deterministic evaluation classifier",
            skill_code=case.expectation.expected_skill_code,
            requires_account_context=True,
            requires_operation_task=mode is TurnExecutionMode.SKILL,
        )

    async def skill_execute(self, session, **kwargs) -> SkillExecutionResult:
        case = self._case()
        user = kwargs["user"]
        thread = kwargs["thread"]
        turn = kwargs["turn"]
        run = kwargs["run"]
        skill_code = kwargs["skill_code"]
        payload = self.payload(thread.account_id)
        content = ContentItem(
            created_by_id=user.id,
            account_id=thread.account_id,
            title=f"{case.case_id} artifact",
            current_stage=ContentStage.OPERATION,
            status=ContentStatus.DRAFT,
        )
        session.add(content)
        await session.flush()
        is_conflict = case.case_id == "failure-business-conflict-02"
        needs_review = case.case_id == "failure-critic-04"
        task = BrainTask(
            org_id=user.org_id,
            created_by_id=user.id,
            content_item_id=content.id,
            title=turn.user_input,
            type=BrainTaskType.REVIEW_OPTIMIZATION,
            status=(BrainTaskStatus.FAILED if is_conflict else BrainTaskStatus.COMPLETED),
            current_focus="deterministic evaluation",
            runtime_mode="main-agent-eval",
        )
        session.add(task)
        await session.flush()
        run.task_id = task.id
        frozen_input = {"account_id": thread.account_id, "days": 30}
        status = "failed" if is_conflict else ("needs_review" if needs_review else "completed")
        skill_run = SkillRun(
            org_id=user.org_id,
            thread_id=thread.id,
            turn_id=turn.id,
            run_id=run.id,
            task_id=task.id,
            idempotency_key=f"eval:{case.case_id}:{turn.id}",
            skill_code=skill_code,
            skill_version=1,
            status=status,
            input_snapshot=frozen_input,
            input_hash=skill_input_hash(frozen_input),
            output_snapshot=payload,
            quality_score=Decimal("0.9000"),
            error_code=("BUSINESS_CONFLICT" if is_conflict else None),
        )
        session.add(skill_run)
        await session.flush()
        for tool_code in case.expectation.required_tools:
            session.add(
                AgentToolCall(
                    org_id=user.org_id,
                    task_id=task.id,
                    skill_run_id=skill_run.id,
                    thread_id=thread.id,
                    turn_id=turn.id,
                    module="evaluation",
                    agent_code=AgentCode.OPERATOR.value,
                    tool_code=tool_code,
                    tool_name=tool_code,
                    idempotency_key=f"eval:{case.case_id}:{turn.id}:{tool_code}",
                    side_effect_level="read",
                    status="completed",
                    permission_mode="auto",
                    requires_human_confirmation=False,
                    latency_ms=10,
                    cost=Decimal("0"),
                )
            )
        artifact_id: int | None = None
        if not is_conflict:
            deliverable = Deliverable(
                content_item_id=content.id,
                thread_id=thread.id,
                turn_id=turn.id,
                run_id=run.id,
                skill_run_id=skill_run.id,
                agent_code=AgentCode.OPERATOR.value,
                type=DeliverableType.REVIEW_REPORT,
                version=1,
                status=DeliverableStatus.PENDING_REVIEW,
                payload=payload,
            )
            session.add(deliverable)
            await session.flush()
            artifact_id = deliverable.id
        await session.commit()
        return SkillExecutionResult(
            status=status,
            skill_run_id=skill_run.id,
            task_id=task.id,
            artifact_id=artifact_id,
            artifact_type="account_analysis_answer",
            report=payload,
            response=(
                "检测到业务冲突，本轮未重试。"
                if is_conflict
                else f"{case.case_id} 已完成安全分析。"
            ),
            error_code=("BUSINESS_CONFLICT" if is_conflict else None),
        )


class ApiWorkerCaseExecutor:
    def __init__(self, *, client, session, admin, harness: DeterministicMatrixHarness) -> None:
        self.client = client
        self.session = session
        self.admin = admin
        self.harness = harness
        self.scopes: dict[str, tuple[int, int, int]] = {}

    async def _create_scope(self, case: EvaluationCase) -> tuple[int, int]:
        account = await self.client.post(
            "/accounts",
            headers=_auth(self.admin),
            json={"nickname": f"eval-{case.case_id}", "platform": "douyin"},
        )
        assert account.status_code == 201
        thread = await self.client.post(
            "/brain/conversations",
            headers=_auth(self.admin),
            json={"account_id": account.json()["id"], "title": case.case_id},
        )
        assert thread.status_code == 201
        return account.json()["id"], thread.json()["id"]

    async def execute(self, case: EvaluationCase) -> EvaluationObservation:
        self.harness.current_case = case
        account_id, thread_id = await self._create_scope(case)
        last_turn_id = 0
        for index, message in enumerate(case.messages):
            submitted = await self.client.post(
                f"/brain/conversations/{thread_id}/turns",
                headers=_auth(self.admin),
                json={
                    "client_message_id": f"eval:{case.case_id}:{index}",
                    "message": message,
                    "requested_skill_code": case.requested_skill_code,
                },
            )
            assert submitted.status_code == 202, submitted.text
            body = submitted.json()
            last_turn_id = body["turn"]["id"]
            run = await self.session.get(AgentRun, body["run"]["id"])
            assert run is not None
            await asyncio.wait_for(
                _execute_v2_conversation_run(
                    self.session,
                    run=run,
                    worker_id=f"main-agent-eval:{case.case_id}:{index}",
                ),
                timeout=10,
            )
        self.scopes[case.case_id] = (account_id, thread_id, last_turn_id)
        return await collect_observation(
            self.session,
            case_id=case.case_id,
            user_id=self.admin.id,
            account_id=account_id,
            thread_id=thread_id,
            turn_id=last_turn_id,
        )


@pytest.mark.asyncio
async def test_all_thirty_cases_run_through_real_api_worker_and_p0_gates(
    client,
    session,
    admin,
    monkeypatch,
) -> None:
    cases = load_evaluation_cases(CASES)
    harness = DeterministicMatrixHarness()
    executor = ApiWorkerCaseExecutor(
        client=client,
        session=session,
        admin=admin,
        harness=harness,
    )
    monkeypatch.setattr(
        "app.services.turn_execution.build_runtime_tool_adapter",
        harness.query_adapter,
    )
    monkeypatch.setattr(
        "app.services.turn_execution.brain_intelligence.classify_turn",
        harness.classify,
    )
    monkeypatch.setattr(
        "app.services.turn_execution.skill_runtime.execute",
        harness.skill_execute,
    )

    report = await EvaluationRunner(executor=executor).run(
        cases,
        mode="deterministic",
        git_commit="integration",
    )

    assert len(report.records) == 30
    failures = {
        record.case_id: record.failure_reasons for record in report.records if not record.passed
    }
    failed_records = {
        record.case_id: record for record in report.records if not record.passed
    }
    failure_report = "\n".join(
        (
            f"{case_id}: {', '.join(check_codes)}; "
            f"route={failed_records[case_id].observation.route_mode}/"
            f"{failed_records[case_id].observation.route_skill_code}; "
            f"states={failed_records[case_id].observation.terminal_states}"
        )
        for case_id, check_codes in sorted(failures.items())
    )
    assert failures == {}, failure_report
    assert report.passed is True
    assert "failure-projectless-01" in executor.scopes
    conflict = next(
        record for record in report.records if record.case_id == "failure-business-conflict-02"
    )
    assert conflict.observation.terminal_states["run"] == "failed"
    assert conflict.observation.tool_calls
    assert all(item["retry_count"] == 0 for item in conflict.observation.tool_calls)
