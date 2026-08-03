"""Immutable, account-scoped input for one main-Agent capability execution."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class CapabilityRequest(BaseModel):
    """The single validated business request passed from routing into execution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    org_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    account_id: int = Field(gt=0)
    thread_id: int = Field(gt=0)
    turn_id: int = Field(gt=0)
    run_id: int = Field(gt=0)
    message: str = Field(min_length=1)
    requested_skill_code: str | None = Field(default=None, min_length=1, max_length=120)
    execution_preference: Literal["AUTO", "DISCUSS_ONLY", "FORMAL_TASK"] = "AUTO"
    structured_input: dict[str, JsonValue] = Field(default_factory=dict)
    constraints: list[str] = Field(default_factory=list)
    attachment_ids: list[int] = Field(default_factory=list)


__all__ = ["CapabilityRequest"]
