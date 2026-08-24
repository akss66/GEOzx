"""PostgreSQL regression gate for deleting accounts with imported metrics."""

import asyncio
import os
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.accounts import delete_account
from app.db import Base
from app.models import Account, DataImportBatch, MetricSnapshot, Org, User
from app.models.enums import (
    DataSourceKind,
    ImportBatchStatus,
    MetricSource,
    Platform,
    UserRole,
)


def _postgres_url() -> str:
    return (
        os.environ["TEST_POSTGRES_URL"]
        .replace("postgresql+psycopg://", "postgresql+asyncpg://")
        .replace("postgresql://", "postgresql+asyncpg://")
    )


@pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is required for the account deletion gate",
)
def test_postgres_account_delete_removes_source_linked_metrics_before_cascade() -> None:
    async def exercise() -> None:
        schema = f"account_delete_{uuid4().hex}"
        admin_engine = create_async_engine(_postgres_url())
        engine = None
        try:
            async with admin_engine.begin() as connection:
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            engine = create_async_engine(
                _postgres_url(),
                connect_args={"server_settings": {"search_path": schema}},
            )
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)

            async with sessions() as session:
                org = Org(name=f"Account deletion {schema}")
                admin = User(
                    org=org,
                    email=f"{schema}@test.invalid",
                    hashed_password="unused",
                    display_name="Account deletion admin",
                    role=UserRole.ADMIN,
                )
                session.add(admin)
                await session.flush()
                account = Account(
                    org_id=org.id,
                    nickname="PostgreSQL account deletion",
                    platform=Platform.DOUYIN,
                )
                session.add(account)
                await session.flush()
                batch = DataImportBatch(
                    org_id=org.id,
                    account_id=account.id,
                    created_by_id=admin.id,
                    source_kind=DataSourceKind.PLATFORM_EXPORT,
                    status=ImportBatchStatus.COMMITTED,
                    template_code="douyin_work_list_v1",
                    content_sha256="b" * 64,
                )
                session.add(batch)
                await session.flush()
                metric = MetricSnapshot(
                    org_id=org.id,
                    account_id=account.id,
                    import_batch_id=batch.id,
                    source=MetricSource.DOUYIN,
                    stat_date=date(2026, 7, 7),
                    title="PostgreSQL deletion regression",
                )
                session.add(metric)
                await session.commit()
                account_id = account.id
                batch_id = batch.id
                metric_id = metric.id

                await delete_account(account_id, admin, session)

            async with sessions() as verification:
                assert await verification.get(Account, account_id) is None
                assert await verification.get(DataImportBatch, batch_id) is None
                assert await verification.get(MetricSnapshot, metric_id) is None
                assert await verification.scalar(
                    select(MetricSnapshot.id).where(MetricSnapshot.account_id == account_id)
                ) is None
        finally:
            if engine is not None:
                await engine.dispose()
            async with admin_engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await admin_engine.dispose()

    asyncio.run(exercise())
