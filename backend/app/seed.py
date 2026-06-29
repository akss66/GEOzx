"""初始数据种子：创建默认组织 + 管理员账号（幂等）。

用法（容器内）：docker compose exec backend python -m app.seed
凭证来自配置：ADMIN_EMAIL / ADMIN_PASSWORD / DEFAULT_ORG_NAME。
"""

import asyncio

from sqlalchemy import select

from app.config import settings
from app.core.security import hash_password
from app.db import async_session
from app.models import Org, User
from app.models.enums import UserRole


async def seed() -> None:
    async with async_session() as session:
        existing = await session.scalar(select(User).where(User.email == settings.admin_email))
        if existing is not None:
            print(f"[seed] 管理员已存在，跳过：{settings.admin_email}")
            return

        org = await session.scalar(select(Org))
        if org is None:
            org = Org(name=settings.default_org_name)
            session.add(org)
            await session.flush()

        session.add(
            User(
                org_id=org.id,
                email=settings.admin_email,
                hashed_password=hash_password(settings.admin_password),
                display_name="管理员",
                role=UserRole.ADMIN,
            )
        )
        await session.commit()
        print(f"[seed] 已创建管理员：{settings.admin_email} / 组织：{org.name}")


if __name__ == "__main__":
    asyncio.run(seed())
