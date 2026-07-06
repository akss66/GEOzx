"""事件总线测试：订阅/分发注册表 + 发布入队（mock arq pool，无需 Redis）。"""

import pytest

from app.core import events as ev


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
