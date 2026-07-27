import pytest

from app.orchestrator.agent_kernel import (
    AgentKernelPolicyError,
    KernelAction,
    expert_kernel_policy,
    main_kernel_policy,
)


def test_main_agent_is_the_only_actor_allowed_to_dispatch_experts() -> None:
    main = main_kernel_policy()
    expert = expert_kernel_policy(tool_allowlist={"account.profile"})

    main.authorize(KernelAction.DISPATCH_EXPERTS, expert_codes=["01-positioning"])

    with pytest.raises(
        AgentKernelPolicyError,
        match="specialist cannot dispatch specialists",
    ):
        expert.authorize(
            KernelAction.DISPATCH_EXPERTS,
            expert_codes=["02-content-director"],
        )


def test_specialist_can_only_call_allowlisted_tools() -> None:
    policy = expert_kernel_policy(
        tool_allowlist={"account.profile", "account.metrics_summary"}
    )

    policy.authorize(KernelAction.CALL_TOOLS, tool_codes=["account.profile"])

    with pytest.raises(AgentKernelPolicyError, match="tool is not allowlisted"):
        policy.authorize(KernelAction.CALL_TOOLS, tool_codes=["publish.execute"])


def test_specialist_cannot_communicate_with_user_directly() -> None:
    policy = expert_kernel_policy(tool_allowlist=set())

    with pytest.raises(AgentKernelPolicyError, match="action is not allowed"):
        policy.authorize(KernelAction.ASK_USER)


def test_kernel_policy_enforces_round_and_tool_budgets() -> None:
    policy = expert_kernel_policy(
        tool_allowlist={"account.profile"},
        max_rounds=3,
        max_tool_calls=2,
    )

    policy.assert_budget(round_index=3, tool_call_count=2)

    with pytest.raises(AgentKernelPolicyError, match="round budget exhausted"):
        policy.assert_budget(round_index=4, tool_call_count=2)

    with pytest.raises(AgentKernelPolicyError, match="tool budget exhausted"):
        policy.assert_budget(round_index=3, tool_call_count=3)


def test_kernel_policy_exports_prompt_safe_context() -> None:
    policy = expert_kernel_policy(
        tool_allowlist={"account.profile", "account.metrics_summary"},
        max_rounds=4,
        max_tool_calls=5,
    )

    assert policy.as_context() == {
        "actor": "specialist",
        "allowed_actions": ["blocked", "call_tools", "finish", "respond"],
        "tool_allowlist": ["account.metrics_summary", "account.profile"],
        "max_rounds": 4,
        "max_tool_calls": 5,
    }
