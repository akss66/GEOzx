"""事件总线测试：订阅/分发注册表 + 发布入队（mock arq pool，无需 Redis）。"""

import asyncio
import json

import pytest

from app.core import events as ev


def test_arq_pool_is_recreated_for_a_new_event_loop(monkeypatch) -> None:
    created_pools: list[object] = []

    async def _fake_create_pool(_settings):
        pool = object()
        created_pools.append(pool)
        return pool

    monkeypatch.setattr(ev, "_pool", None)
    monkeypatch.setattr(ev, "_pool_loop", None, raising=False)
    monkeypatch.setattr(ev, "create_pool", _fake_create_pool)

    async def _get_twice():
        return await ev.get_arq_pool(), await ev.get_arq_pool()

    first_pool, reused_pool = asyncio.run(_get_twice())
    second_pool = asyncio.run(ev.get_arq_pool())

    assert reused_pool is first_pool
    assert first_pool is not second_pool
    assert created_pools == [first_pool, second_pool]


@pytest.mark.asyncio
async def test_subscribe_and_dispatch() -> None:
    received: list[dict] = []

    @ev.subscribe("t.unit")
    async def _h(event: dict) -> None:
        received.append(event)

    await ev.dispatch("t.unit", {"payload": {"x": 1}})
    assert received == [{"payload": {"x": 1}}]
    assert len(ev.handlers_for("t.unit")) == 1

    ev._handlers.pop("t.unit", None)  # 清理全局注册表，避免污染其他测试


@pytest.mark.asyncio
async def test_dispatch_no_handlers_is_noop() -> None:
    await ev.dispatch("t.none", {"payload": None})  # 不应抛错


@pytest.mark.asyncio
async def test_publish_event_enqueues(monkeypatch) -> None:
    jobs: list[tuple] = []

    class FakePool:
        async def enqueue_job(self, name, *args, **kwargs):
            jobs.append((name, args))

    async def _fake_pool():
        return FakePool()

    monkeypatch.setattr(ev, "get_arq_pool", _fake_pool)

    await ev.publish_event("unit.ping", payload={"m": "hi"}, content_item_id=5)

    assert jobs[0][0] == "process_event"
    job_arg = jobs[0][1][0]
    assert job_arg["type"] == "unit.ping"
    assert job_arg["payload"] == {"m": "hi"}
    assert job_arg["content_item_id"] == 5


@pytest.mark.asyncio
async def test_realtime_events_reuse_the_shared_redis_client(monkeypatch) -> None:
    published: list[tuple[str, str]] = []

    class FakeRedis:
        async def publish(self, channel: str, payload: str) -> None:
            published.append((channel, payload))

        async def aclose(self) -> None:
            raise AssertionError("shared Redis client must not be closed for every token")

    redis = FakeRedis()
    monkeypatch.setattr(ev, "get_redis", lambda: redis, raising=False)

    await ev.publish_realtime_event("brain.runtime.message_delta", {"delta": "你"})
    await ev.publish_realtime_event("brain.runtime.message_delta", {"delta": "好"})

    assert [channel for channel, _ in published] == [ev.EVENTS_CHANNEL, ev.EVENTS_CHANNEL]


@pytest.mark.asyncio
async def test_realtime_event_can_carry_the_persisted_event_id(monkeypatch) -> None:
    published: list[str] = []

    class FakeRedis:
        async def publish(self, _channel: str, payload: str) -> None:
            published.append(payload)

    monkeypatch.setattr(ev, "get_redis", lambda: FakeRedis(), raising=False)

    await ev.publish_realtime_event(
        "brain.runtime.message_done",
        {"task_id": 7},
        event_id=41,
    )

    assert json.loads(published[0])["id"] == 41


@pytest.mark.asyncio
async def test_realtime_runtime_event_exposes_one_normalized_turn_phase(monkeypatch) -> None:
    published: list[str] = []

    class FakeRedis:
        async def publish(self, _channel: str, payload: str) -> None:
            published.append(payload)

    monkeypatch.setattr(ev, "get_redis", lambda: FakeRedis(), raising=False)

    await ev.publish_realtime_event(
        "brain.runtime.subagent_started",
        {"thread_id": 81, "turn_id": 101},
    )

    assert json.loads(published[0])["payload"]["turn_phase"] == "consulting_experts"
