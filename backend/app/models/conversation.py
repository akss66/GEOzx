"""Long-lived conversation ownership and per-message turns."""

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import BigIntPK, JSONVariant, TimestampMixin

if TYPE_CHECKING:
    from app.models.agent_runtime import AgentRun


class ConversationThread(Base, TimestampMixin):
    """A durable conversation scoped to the user's active account context."""

    __tablename__ = "conversation_threads"

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
    )
    agent_runs: Mapped[list["AgentRun"]] = relationship(back_populates="thread")


class ConversationTurn(Base, TimestampMixin):
    """One immutable user message within a conversation thread."""

    __tablename__ = "conversation_turns"
    __table_args__ = (
        UniqueConstraint(
            "thread_id",
            "client_message_id",
            name="uq_conversation_turn_thread_client_message",
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

    thread: Mapped[ConversationThread] = relationship(back_populates="turns")
    agent_runs: Mapped[list["AgentRun"]] = relationship(back_populates="turn")
