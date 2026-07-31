"""Validated, immutable provenance for Operations Brain V3 writes."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Account,
    AgentRun,
    BrainTask,
    ContentItem,
    ConversationThread,
    ConversationTurn,
    SkillRun,
    User,
)


class RuntimeScopeConflict(RuntimeError):
    """A stable business conflict raised before a cross-scope write is flushed."""

    code = "RUNTIME_SCOPE_CONFLICT"


@dataclass(frozen=True)
class RuntimeScope:
    """One canonical provenance graph shared by all V3 runtime writers."""

    org_id: int
    user_id: int
    account_id: int
    thread_id: int
    turn_id: int
    run_id: int
    task_id: int
    skill_run_id: int | None = None

    def __post_init__(self) -> None:
        required = (
            self.org_id,
            self.user_id,
            self.account_id,
            self.thread_id,
            self.turn_id,
            self.run_id,
            self.task_id,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in required
        ):
            raise RuntimeScopeConflict("runtime scope is incomplete")
        if self.skill_run_id is not None and (
            isinstance(self.skill_run_id, bool)
            or not isinstance(self.skill_run_id, int)
            or self.skill_run_id <= 0
        ):
            raise RuntimeScopeConflict("runtime skill scope is invalid")

    def as_dict(self) -> dict[str, int | None]:
        return asdict(self)

    @classmethod
    async def from_conversation(
        cls,
        session: AsyncSession,
        *,
        user: User,
        thread: ConversationThread,
        turn: ConversationTurn,
        run: AgentRun,
    ) -> RuntimeScope:
        """Build the conversation portion from explicit persisted entities."""

        if user.id is None or run.task_id is None:
            raise RuntimeScopeConflict("conversation runtime scope is incomplete")
        if (
            thread.org_id != user.org_id
            or thread.created_by_id != user.id
            or turn.org_id != user.org_id
            or turn.created_by_id != user.id
            or turn.thread_id != thread.id
            or run.org_id != user.org_id
            or run.requested_by_id != user.id
            or run.thread_id != thread.id
            or run.turn_id != turn.id
        ):
            raise RuntimeScopeConflict("conversation runtime ownership does not match")
        scope = cls(
            org_id=user.org_id,
            user_id=user.id,
            account_id=thread.account_id,
            thread_id=thread.id,
            turn_id=turn.id,
            run_id=run.id,
            task_id=run.task_id,
        )
        await scope.validate(session)
        return scope

    async def bind_task(
        self,
        session: AsyncSession,
        task: BrainTask,
    ) -> RuntimeScope:
        if task.id != self.task_id or task.org_id != self.org_id:
            raise RuntimeScopeConflict("runtime task scope does not match")
        bound = replace(self, task_id=task.id)
        await bound.validate(session)
        return bound

    async def bind_skill(
        self,
        session: AsyncSession,
        skill_run: SkillRun,
    ) -> RuntimeScope:
        if (
            skill_run.org_id != self.org_id
            or skill_run.task_id != self.task_id
            or skill_run.run_id != self.run_id
            or skill_run.thread_id != self.thread_id
            or skill_run.turn_id != self.turn_id
        ):
            raise RuntimeScopeConflict("runtime SkillRun scope does not match")
        bound = replace(self, skill_run_id=skill_run.id)
        await bound.validate(session)
        return bound

    async def validate(self, session: AsyncSession) -> None:
        """Reload and validate the entire canonical graph."""

        user = await session.get(User, self.user_id)
        account = await session.get(Account, self.account_id)
        thread = await session.get(ConversationThread, self.thread_id)
        turn = await session.get(ConversationTurn, self.turn_id)
        run = await session.get(AgentRun, self.run_id)
        task = await session.get(BrainTask, self.task_id)
        if (
            user is None
            or user.org_id != self.org_id
            or account is None
            or account.org_id != self.org_id
            or thread is None
            or thread.org_id != self.org_id
            or thread.created_by_id != self.user_id
            or thread.account_id != self.account_id
            or turn is None
            or turn.org_id != self.org_id
            or turn.created_by_id != self.user_id
            or turn.thread_id != self.thread_id
            or run is None
            or run.org_id != self.org_id
            or run.requested_by_id != self.user_id
            or run.thread_id != self.thread_id
            or run.turn_id != self.turn_id
            or run.task_id != self.task_id
            or task is None
            or task.org_id != self.org_id
        ):
            raise RuntimeScopeConflict("runtime scope graph does not match")

        if task.content_item_id is None:
            raise RuntimeScopeConflict("runtime task has no account-scoped content")
        content = await session.get(ContentItem, task.content_item_id)
        if content is None or content.account_id != self.account_id:
            raise RuntimeScopeConflict("runtime task account scope does not match")

        if self.skill_run_id is None:
            return
        skill_run = await session.get(SkillRun, self.skill_run_id)
        if (
            skill_run is None
            or skill_run.org_id != self.org_id
            or skill_run.task_id != self.task_id
            or skill_run.run_id != self.run_id
            or skill_run.thread_id != self.thread_id
            or skill_run.turn_id != self.turn_id
        ):
            raise RuntimeScopeConflict("runtime SkillRun graph does not match")
