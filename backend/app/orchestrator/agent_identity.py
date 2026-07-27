"""对外展示身份与内部 Agent 角色之间的边界。"""

OPERATIONS_BRAIN_DISPLAY_NAME = "运营大脑"

_PUBLIC_IDENTITY_INSTRUCTION = """
## 对外称谓
面向用户时统一使用“运营大脑”指代你自己，不展示“主 Agent”这一内部架构术语。
用户原始输入必须原样保留，不得改写用户对该术语的引用。
""".strip()


def with_operations_brain_public_identity(prompt: str) -> str:
    return f"{prompt.rstrip()}\n\n{_PUBLIC_IDENTITY_INSTRUCTION}"
