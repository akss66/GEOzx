"""Long-lived conversation ownership and per-message turns."""

from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import BigIntPK, JSONVariant, TimestampMixin

if TYPE_CHECKING:
    from app.models.agent_runtime import AgentRun


class ConversationThread(Base, TimestampMixin):
    """A durable conversation scoped to the user's active account context."""

    __tablename__ = "conversation_threads"
    __table_args__ = (UniqueConstraint("id", "org_id", name="uq_conversation_thread_id_org"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True
    )
    client_id: Mapped[int | None] = mapped_column(
        ForeignKey("clients.id", ondelete="SET NULL"), index=True, nullable=True
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), index=True, nullable=True
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), default="", nullable=False)

    turns: Mapped[list["ConversationTurn"]] = relationship(
        back_populates="thread",
        cascade="all, delete-orphan",
        foreign_keys="ConversationTurn.thread_id",
    )
    agent_runs: Mapped[list["AgentRun"]] = relationship(
        back_populates="thread",
        foreign_keys="AgentRun.thread_id",
    )


class ConversationTurn(Base, TimestampMixin):
    """One immutable user message within a conversation thread."""

    __tablename__ = "conversation_turns"
    __table_args__ = (
        UniqueConstraint(
            "thread_id",
            "client_message_id",
            name="uq_conversation_turn_thread_client_message",
        ),
        UniqueConstraint(
            "id",
            "thread_id",
            "org_id",
            name="uq_conversation_turn_id_thread_org",
        ),
        UniqueConstraint(
            "id",
            "thread_id",
            name="uq_conversation_turn_id_thread",
        ),
        ForeignKeyConstraint(
            ["thread_id", "org_id"],
            ["conversation_threads.id", "conversation_threads.org_id"],
            name="fk_conversation_turn_thread_org",
        ),
        CheckConstraint(
            "status IN ("
            "'queued', 'running', 'retry_wait', 'waiting_permission', "
            "'waiting_decision', 'waiting_user', 'completed', 'blocked', "
            "'failed', 'dead_letter', 'cancelled', 'stopped'"
            ")",
            name="ck_conversation_turns_status",
        ),
        CheckConstraint("route_ms IS NULL OR route_ms >= 0", name="ck_conversation_turns_route_ms"),
        CheckConstraint(
            "first_token_ms IS NULL OR first_token_ms >= 0",
            name="ck_conversation_turns_first_token_ms",
        ),
        CheckConstraint(
            "completion_ms IS NULL OR completion_ms >= 0",
            name="ck_conversation_turns_completion_ms",
        ),
        CheckConstraint("total_ms IS NULL OR total_ms >= 0", name="ck_conversation_turns_total_ms"),
        CheckConstraint(
            "model_call_count IS NULL OR model_call_count >= 0",
            name="ck_conversation_turns_model_call_count",
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    thread_id: Mapped[int] = mapped_column(
        ForeignKey("conversation_threads.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True
    )
    client_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    user_input: Mapped[str] = mapped_column(Text, nullable=False)
    assistant_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    intent: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    status: Mapped[str] = mapped_column(
        String(40),
        default="queued",
        server_default="queued",
        index=True,
        nullable=False,
    )
    route_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_token_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_call_count: Mapped[int | None] = mapped_column(
        Integer,
        default=0,
        nullable=True,
    )

    thread: Mapped[ConversationThread] = relationship(
        back_populates="turns",
        foreign_keys=[thread_id],
    )
    agent_runs: Mapped[list["AgentRun"]] = relationship(
        back_populates="turn",
        foreign_keys="AgentRun.turn_id",
    )
