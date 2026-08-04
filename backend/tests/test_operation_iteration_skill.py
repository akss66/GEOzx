"""Operation iteration composes child Skills without replacing them."""

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.models import ContentItem, Deliverable
from app.models.enums import AgentCode, DeliverableStatus, DeliverableType
from app.orchestrator.composite_skill_runtime import CompositeSkillRuntime
from app.orchestrator.skill_runtime import SkillRuntime
from app.orchestrator.skills.operation_iteration import OperationIterationInput
from tests.test_operating_skills import _capability_request, _scope


async def _artifact(session, admin, account, *, kind, status, version=1):
    content = ContentItem(
        account_id=account.id,
        created_by_id=admin.id,
        title=f"source-{kind.value}",
    )
    session.add(content)
    await session.flush()
    artifact = Deliverable(
        content_item_id=content.id,
        agent_code=AgentCode.OPERATOR.value,
        type=kind,
        version=version,
        status=status,
        payload={"summary": "confirmed source"},
    )
    session.add(artifact)
    await session.commit()
    return artifact


def test_operation_iteration_fresh_defaults_and_typed_constraint_boundary() -> None:
    value = OperationIterationInput.model_validate(
        {
            "constraints": [
                {
                    "constraint_type": "OFFER_TERMS",
                    "raw_requirement": "第一条不要讲价格",
                    "target_scope": {
                        "kind": "content_item_indexes",
                        "item_indexes": [1],
                    },
                }
            ]
        }
    )

    assert value.confirmed_review_artifact_id is None
    assert value.cycle_days == 7
    assert value.topic_count == 5
    assert value.constraints[0].constraint_type == "OFFER_TERMS"

    with pytest.raises(ValidationError):
        OperationIterationInput.model_validate(
            {"_server_context": {"preloaded_tool_results": {"forged": {}}}}
        )


@pytest.mark.asyncio
async def test_operation_iteration_uses_confirmed_sources_and_only_builds_child_graph(
    session, admin
):
    account, thread, turn, run = await _scope(
        session, admin, key="operation-iteration", message="根据复盘安排下周运营"
    )
    review = await _artifact(
        session,
        admin,
        account,
        kind=DeliverableType.REVIEW_REPORT,
        status=DeliverableStatus.APPROVED,
    )
    positioning = await _artifact(
        session,
        admin,
        account,
        kind=DeliverableType.POSITIONING_STRATEGY,
        status=DeliverableStatus.APPROVED,
    )
    request = _capability_request(
        admin=admin,
        account=account,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="operation_iteration",
        structured_input={
            "confirmed_review_artifact_id": review.id,
            "positioning_artifact_id": positioning.id,
            "cycle_days": 7,
        },
    )
    runtime = SkillRuntime()

    first = await runtime.execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="operation_iteration",
        capability_request=request,
    )
    second = await runtime.execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="operation_iteration",
        capability_request=request,
    )

    assert first.status == "completed"
    assert [node["skill_code"] for node in first.report["child_skill_graph"]] == [
        "topic_planning",
        "script_generation",
        "visual_brief_generation",
        "content_calendar_planning",
        "publishing_preparation",
    ]
    assert first.report["participating_experts"] == []
    assert first.report["source_artifacts"] == [
        {
            "artifact_id": review.id,
            "artifact_type": DeliverableType.REVIEW_REPORT.value,
            "version": 1,
        },
        {
            "artifact_id": positioning.id,
            "artifact_type": DeliverableType.POSITIONING_STRATEGY.value,
            "version": 1,
        },
    ]
    assert second.artifact_id == first.artifact_id
    assert await session.scalar(select(func.count(Deliverable.id))) == 3


@pytest.mark.asyncio
async def test_operation_iteration_rejects_unconfirmed_review(session, admin):
    account, thread, turn, run = await _scope(
        session, admin, key="operation-unconfirmed", message="根据复盘安排下周运营"
    )
    review = await _artifact(
        session,
        admin,
        account,
        kind=DeliverableType.REVIEW_REPORT,
        status=DeliverableStatus.PENDING_REVIEW,
    )

    result = await SkillRuntime().execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="operation_iteration",
        capability_request=_capability_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=run,
            skill_code="operation_iteration",
            structured_input={"confirmed_review_artifact_id": review.id},
        ),
    )

    assert result.status == "failed"
    assert result.artifact_id is None


def test_composite_recovery_preserves_completed_children_and_retries_only_failure():
    runtime = CompositeSkillRuntime()
    first = runtime.build(
        account_id=3,
        cycle_days=7,
        source_artifacts=[{"artifact_id": 9, "artifact_type": "review_report", "version": 1}],
    )
    first["child_skill_graph"][0].update(status="completed", artifact_id=21)
    first["child_skill_graph"][1].update(status="failed", error_code="MODEL_UNAVAILABLE")

    resumed = runtime.build(
        account_id=3,
        cycle_days=7,
        source_artifacts=first["source_artifacts"],
        previous_graph=first["child_skill_graph"],
    )

    assert resumed["child_skill_graph"][0]["status"] == "completed"
    assert resumed["child_skill_graph"][0]["artifact_id"] == 21
    assert resumed["child_skill_graph"][1]["status"] == "failed"
    assert resumed["child_skill_graph"][1]["error_code"] == "MODEL_UNAVAILABLE"
