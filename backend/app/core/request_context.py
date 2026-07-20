"""Request-local identity context for records created below API boundaries."""

from contextvars import ContextVar, Token

_acting_user_id: ContextVar[int | None] = ContextVar("acting_user_id", default=None)


def get_acting_user_id() -> int | None:
    return _acting_user_id.get()


def set_acting_user(user_id: int) -> Token[int | None]:
    return _acting_user_id.set(user_id)


def reset_acting_user(token: Token[int | None]) -> None:
    _acting_user_id.reset(token)
