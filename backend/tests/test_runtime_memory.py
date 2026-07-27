"""Runtime working-memory projection and compaction contracts."""

import json

import pytest

from app.llm.adapters import CompletionResult
from app.models import BrainTask, Event, RuntimeMemory, TaskBrief
from app.models.enums import BrainTaskStatus, BrainTaskType, Platform
from app.services.runtime_memory import (
    RuntimeMemoryCompactionError,
    RuntimeMemoryService,
)


class FakeMemoryLLM:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[list[dict]] = []

    async def chat(self, session, org_id, agent_code, messages):
        self.calls.append(messages)
        return (
            CompletionResult(
                content=self.content,
                model="memory-test-model",
                prompt_tokens=20,
                completion_tokens=30,
                total_tokens=50,
            ),
            0.0,
        )


def _memory_payload(*, next_step: str = "继续账号诊断") -> str:
    return json.dumps(
        {
            "goal": "诊断当前抖音账号",
            "scope": {
                "org_id": None,
                "client_id": None,
                "project_id": None,
                "account_ids": [7],
            },
            "constraints": ["发布前必须人工确认"],
            "decisions": ["先完成账号定位"],
            "evidence_refs": ["event:1"],
            "expert_findings": ["账号定位仍需补充数据"],
            "tool_results": [],
            "open_questions": ["目标用户是谁"],
            "next_step": next_step,
            "covered_event_ids": [],
        },
        ensure_ascii=False,
    )


async def _task_with_events(session, admin) -> BrainTask:
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        title="Runtime memory task",
        type=BrainTaskType.REVIEW_OPTIMIZATION,
        status=BrainTaskStatus.RUNNING,
        runtime_mode="langgraph",
        thread_id="memory-thread-1",
    )
    task.brief = TaskBrief(
        goal="诊断当前抖音账号",
        platforms=[Platform.DOUYIN.value],
        account_ids=[7],
        cycle="current",
        content_goal="账号诊断",
        risk_constraints=["发布前必须人工确认"],
        expected_outputs=["定位报告"],
        confirmation_actions=[],
    )
    session.add(task)
    await session.flush()
    session.add_all(
        [
            Event(
                type="brain.runtime.user_message",
                payload={
                    "task_id": task.id,
                    "thread_id": task.thread_id,
                    "message": "帮我诊断账号",
                    "api_key": "sk-must-never-enter-memory",
                },
            ),
            Event(
                type="brain.runtime.message_done",
                payload={
                    "task_id": task.id,
                    "thread_id": task.thread_id,
                    "agent_code": "00-decision",
                    "content": "我会先确认账号范围。",
                },
            ),
        ]
    )
    await session.commit()
    return task


@pytest.mark.asyncio
async def test_compaction_is_cursor_idempotent_and_redacts_sensitive_payloads(
    session, admin
) -> None:
    task = await _task_with_events(session, admin)
    llm = FakeMemoryLLM(_memory_payload())
    service = RuntimeMemoryService(llm=llm, event_threshold=1, char_threshold=1)

    first = await service.compact(session, task, force=True)
    second = await service.compact(session, task, force=True)

    assert first.id == second.id
    assert first.revision == 1
    assert len(llm.calls) == 1
    model_input = json.dumps(llm.calls[0], ensure_ascii=False)
    assert "sk-must-never-enter-memory" not in model_input
    assert first.snapshot["goal"] == "诊断当前抖音账号"
    assert first.snapshot["covered_event_ids"]


@pytest.mark.asyncio
async def test_build_context_combines_persisted_memory_with_recent_uncompacted_events(
    session, admin
) -> None:
    task = await _task_with_events(session, admin)
    service = RuntimeMemoryService(
        llm=FakeMemoryLLM(_memory_payload()),
        event_threshold=1,
        char_threshold=1,
    )
    memory = await service.compact(session, task, force=True)
    session.add(
        Event(
            type="brain.runtime.user_message",
            payload={
                "task_id": task.id,
                "thread_id": task.thread_id,
                "message": "目标用户是 25 到 35 岁数码爱好者",
            },
        )
    )
    await session.commit()

    messages = await service.build_runtime_context(
        session,
        task,
        current_message="",
        budget_chars=4000,
    )
    rendered = "\n".join(item["content"] for item in messages)

    assert memory.snapshot["next_step"] in rendered
    assert "25 到 35 岁数码爱好者" in rendered
    assert messages[0]["role"] == "system"


@pytest.mark.asyncio
async def test_invalid_compactor_output_preserves_the_previous_memory_revision(
    session, admin
) -> None:
    task = await _task_with_events(session, admin)
    llm = FakeMemoryLLM(_memory_payload())
    service = RuntimeMemoryService(llm=llm, event_threshold=1, char_threshold=1)
    previous = await service.compact(session, task, force=True)
    previous_cursor = previous.last_event_id
    session.add(
        Event(
            type="brain.runtime.user_message",
            payload={
                "task_id": task.id,
                "thread_id": task.thread_id,
                "message": "补充一个新要求",
            },
        )
    )
    await session.commit()
    llm.content = "not-json"

    with pytest.raises(RuntimeMemoryCompactionError):
        await service.compact(session, task, force=True)

    stored = await service.load(session, org_id=admin.org_id, task_id=task.id)
    assert stored is not None
    assert stored.revision == 1
    assert stored.last_event_id == previous_cursor


@pytest.mark.asyncio
async def test_runtime_memory_is_tenant_scoped(session, admin) -> None:
    task = await _task_with_events(session, admin)
    service = RuntimeMemoryService(
        llm=FakeMemoryLLM(_memory_payload()),
        event_threshold=1,
        char_threshold=1,
    )
    await service.compact(session, task, force=True)

    assert await service.load(session, org_id=admin.org_id, task_id=task.id)
    assert await service.load(session, org_id=admin.org_id + 999, task_id=task.id) is None
    assert (await session.get(RuntimeMemory, 1)) is not None
