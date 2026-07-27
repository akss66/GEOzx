import asyncio

import pytest

from app.orchestrator.generation_control import GenerationControl


@pytest.mark.asyncio
async def test_generation_stop_is_isolated_by_user() -> None:
    control = GenerationControl()
    started = asyncio.Event()

    async def run_generation() -> None:
        await control.activate(1, 7, "turn-1")
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await control.finish(1, 7, "turn-1")

    generation = asyncio.create_task(run_generation())
    await started.wait()

    await control.request_stop(1, 8, "turn-1")
    await asyncio.sleep(0)
    assert not generation.done()

    await control.request_stop(1, 7, "turn-1")
    with pytest.raises(asyncio.CancelledError):
        await generation
