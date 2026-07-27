"""In-process cancellation registry for live Agent generations.

The production API currently runs a single Uvicorn worker, so an in-memory
registry can cancel the exact request task without introducing a queue. The
client message id is scoped by organization and user so one member cannot
cancel another member's live request.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import monotonic


class GenerationStopped(asyncio.CancelledError):
    """Raised when a stop request arrived before generation became active."""


@dataclass
class _GenerationSlot:
    task: asyncio.Task[object] | None = None
    stop_requested: bool = False
    touched_at: float = 0.0


class GenerationControl:
    """Track and cancel one live generation per client message id."""

    _STALE_AFTER_SECONDS = 15 * 60

    def __init__(self) -> None:
        self._slots: dict[tuple[int, int, str], _GenerationSlot] = {}
        self._lock = asyncio.Lock()

    async def activate(self, org_id: int, user_id: int, client_message_id: str) -> None:
        key = (org_id, user_id, client_message_id)
        async with self._lock:
            self._prune()
            slot = self._slots.setdefault(key, _GenerationSlot())
            slot.touched_at = monotonic()
            if slot.stop_requested:
                raise GenerationStopped()
            slot.task = asyncio.current_task()

    async def request_stop(
        self, org_id: int, user_id: int, client_message_id: str
    ) -> None:
        key = (org_id, user_id, client_message_id)
        async with self._lock:
            self._prune()
            slot = self._slots.setdefault(key, _GenerationSlot())
            slot.stop_requested = True
            slot.touched_at = monotonic()
            task = slot.task
        if task is not None and not task.done():
            task.cancel()

    async def is_stop_requested(
        self, org_id: int, user_id: int, client_message_id: str
    ) -> bool:
        async with self._lock:
            slot = self._slots.get((org_id, user_id, client_message_id))
            return bool(slot and slot.stop_requested)

    async def raise_if_stopped(
        self, org_id: int, user_id: int, client_message_id: str
    ) -> None:
        if await self.is_stop_requested(org_id, user_id, client_message_id):
            raise GenerationStopped()

    async def finish(self, org_id: int, user_id: int, client_message_id: str) -> None:
        async with self._lock:
            self._slots.pop((org_id, user_id, client_message_id), None)

    def _prune(self) -> None:
        threshold = monotonic() - self._STALE_AFTER_SECONDS
        stale = [
            key
            for key, slot in self._slots.items()
            if slot.task is None and slot.touched_at < threshold
        ]
        for key in stale:
            self._slots.pop(key, None)


generation_control = GenerationControl()
