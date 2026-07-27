from app.orchestrator.agent_identity import (
    OPERATIONS_BRAIN_DISPLAY_NAME,
    with_operations_brain_public_identity,
)


def test_appends_public_identity_without_renaming_internal_role() -> None:
    prompt = with_operations_brain_public_identity("你是主 Agent，负责调度专家。")

    assert "你是主 Agent" in prompt
    assert "面向用户时统一使用“运营大脑”" in prompt
    assert OPERATIONS_BRAIN_DISPLAY_NAME == "运营大脑"
