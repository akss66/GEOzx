"""初始数据种子：创建默认组织 + 管理员账号（幂等）。

用法（容器内）：docker compose exec backend python -m app.seed
凭证来自配置：ADMIN_EMAIL / ADMIN_PASSWORD / DEFAULT_ORG_NAME。
"""

import asyncio

from sqlalchemy import select

from app.config import settings
from app.core.security import hash_password
from app.db import async_session
from app.models import ModelConfig, Org, User
from app.models.enums import UserRole

# 8 个 Agent 的默认模型绑定（v1 全部 DeepSeek；编导/运营/投流用 reasoner 做更强推理）。
# agent_code 与 orchestrator pipeline / prompts 文件名前缀一致。
_AGENT_MODELS: list[tuple[str, str, str | None]] = [
    ("00-decision", "deepseek-chat", None),
    ("00-router", "deepseek-v4-flash", None),
    ("01-positioning", "deepseek-chat", None),
    ("02-content", "deepseek-reasoner", "deepseek-chat"),
    ("03-art", "deepseek-chat", None),
    ("04-video", "deepseek-chat", None),
    ("05-editing", "deepseek-chat", None),
    ("06-operation", "deepseek-reasoner", "deepseek-chat"),
    ("07-ads", "deepseek-reasoner", None),
    ("08-service", "deepseek-chat", None),
]


async def _seed_model_configs(session, org_id: int) -> int:
    """为 org 补齐缺失的 per-Agent ModelConfig（幂等）。返回新建数量。"""
    existing = set(
        await session.scalars(select(ModelConfig.agent_code).where(ModelConfig.org_id == org_id))
    )
    created = 0
    for code, primary, fallback in _AGENT_MODELS:
        if code in existing:
            continue
        session.add(
            ModelConfig(
                org_id=org_id, agent_code=code, primary_model=primary, fallback_model=fallback
            )
        )
        created += 1
    return created


async def seed() -> None:
    async with async_session() as session:
        org = await session.scalar(select(Org))
        if org is None:
            org = Org(name=settings.default_org_name)
            session.add(org)
            await session.flush()

        existing = await session.scalar(select(User).where(User.email == settings.admin_email))
        if existing is None:
            session.add(
                User(
                    org_id=org.id,
                    email=settings.admin_email,
                    hashed_password=hash_password(settings.admin_password),
                    display_name="管理员",
                    role=UserRole.ADMIN,
                )
            )
            print(f"[seed] 已创建管理员：{settings.admin_email} / 组织：{org.name}")
        else:
            print(f"[seed] 管理员已存在，跳过：{settings.admin_email}")

        created = await _seed_model_configs(session, org.id)
        await session.commit()
        print(f"[seed] ModelConfig 新建 {created} 条（共 {len(_AGENT_MODELS)} 个 Agent）")


if __name__ == "__main__":
    asyncio.run(seed())
