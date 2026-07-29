from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.security import hash_password
from app.db import Base
from app.models import (
    Account,
    DataArtifact,
    DataConflict,
    DataImportBatch,
    DataImportRow,
    Org,
    PlatformContentRecord,
    User,
)
from app.models.enums import (
    ConflictStatus,
    ContentIdentityConfidence,
    ImportBatchStatus,
    ImportRowStatus,
    Platform,
)
from app.services.data_import.service import (
    RowMatchResolution,
    create_preview,
    resolve_row_match,
)
from tests.test_data_import_templates import WORK_LIST_HEADERS, workbook_bytes


@pytest.fixture
async def account(session, admin) -> Account:
    item = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="Preview fixture",
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


@pytest.fixture
async def other_account(session, admin) -> Account:
    item = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="Preview other fixture",
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


def _workbook_payload(*, title: str = "作品 A") -> bytes:
    return workbook_bytes(
        WORK_LIST_HEADERS,
        [[
            title,
            "2026-07-18 14:11:20",
            "1min-视频",
            "公开",
            "81",
            "0.087500",
            "0.375000",
            "-",
            "0.375000",
            "9.53",
            "6",
            "0",
            "3",
            "0",
            "3",
            "0",
        ]],
    )


async def _make_filebacked_sessionmaker(tmp_path: Path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'preview-transaction.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed_transaction_scope(session) -> tuple[int, int]:
    org = Org(name="Preview transaction org")
    admin = User(
        org=org,
        email="preview-transaction-admin@test.com",
        hashed_password=hash_password("preview-transaction-password"),
        display_name="Preview transaction admin",
    )
    session.add(admin)
    await session.flush()
    account = Account(
        org_id=org.id,
        platform=Platform.DOUYIN,
        nickname="Preview transaction account",
    )
    session.add(account)
    await session.commit()
    return admin.id, account.id


@pytest.mark.asyncio
async def test_create_preview_persists_durable_artifact_and_resolution_candidates(
    session, admin, account, monkeypatch, tmp_path
):
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    existing = PlatformContentRecord(
        org_id=admin.org_id,
        account_id=account.id,
        platform=Platform.DOUYIN,
        title="作品　A",
        published_at=datetime(2026, 7, 18, 14, 11, 20),
        identity_confidence=ContentIdentityConfidence.CONFIRMED,
    )
    session.add(existing)
    await session.commit()

    workbook_payload = _workbook_payload()
    batch = await create_preview(
        session,
        user=admin,
        account=account,
        filename="..\\works.xlsx",
        content=workbook_payload,
    )

    artifact = batch.artifacts[0]
    row = batch.rows[0]
    conflict = batch.conflicts[0]

    assert batch.status is ImportBatchStatus.PREVIEW_READY
    assert batch.row_count == 1
    assert artifact.filename == "works.xlsx"
    assert (
        artifact.storage_key
        == f"account-data/{admin.org_id}/{account.id}/{batch.id}/{artifact.sha256}.xlsx"
    )
    assert (tmp_path / artifact.storage_key).read_bytes() == workbook_payload
    assert row.status is ImportRowStatus.NEEDS_RESOLUTION
    assert row.platform_content_record_id is None
    assert row.candidate_content_ids == [existing.id]
    assert "作品 A" in row.raw_values.values()
    assert row.normalized_values["title"] == "作品 A"
    assert row.normalized_values["published_at"] == "2026-07-18T14:11:20"
    assert conflict.status is ConflictStatus.OPEN
    assert conflict.candidate_content_ids == [existing.id]


@pytest.mark.asyncio
async def test_create_preview_reuses_identical_hash_for_same_scope(
    session, admin, account, monkeypatch, tmp_path
):
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    payload = _workbook_payload()

    first = await create_preview(
        session,
        user=admin,
        account=account,
        filename="works.xlsx",
        content=payload,
    )
    second = await create_preview(
        session,
        user=admin,
        account=account,
        filename="renamed.xlsx",
        content=payload,
    )

    batch_count = await session.scalar(select(func.count()).select_from(DataImportBatch))
    artifact_count = await session.scalar(select(func.count()).select_from(DataArtifact))
    row_count = await session.scalar(select(func.count()).select_from(DataImportRow))

    assert second.id == first.id
    assert batch_count == 1
    assert artifact_count == 1
    assert row_count == 1


@pytest.mark.asyncio
async def test_create_preview_repairs_missing_artifact_for_identical_active_preview(
    session, admin, account, monkeypatch, tmp_path
):
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    payload = _workbook_payload()

    first = await create_preview(
        session,
        user=admin,
        account=account,
        filename="works.xlsx",
        content=payload,
    )
    artifact = first.artifacts[0]
    artifact_path = tmp_path / artifact.storage_key
    artifact_path.unlink()

    repaired = await create_preview(
        session,
        user=admin,
        account=account,
        filename="works.xlsx",
        content=payload,
    )

    assert repaired.id == first.id
    assert artifact_path.read_bytes() == payload
    assert await session.scalar(select(func.count()).select_from(DataImportBatch)) == 1
    assert await session.scalar(select(func.count()).select_from(DataArtifact)) == 1


@pytest.mark.asyncio
async def test_create_preview_allows_new_batch_after_commit_or_revoke(
    session, admin, account, monkeypatch, tmp_path
):
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    payload = _workbook_payload()

    first = await create_preview(
        session,
        user=admin,
        account=account,
        filename="works.xlsx",
        content=payload,
    )
    first.committed_at = datetime(2026, 7, 22, 12, 0, 0)
    await session.commit()

    second = await create_preview(
        session,
        user=admin,
        account=account,
        filename="works.xlsx",
        content=payload,
    )
    assert second.id != first.id

    second.revoked_at = datetime(2026, 7, 22, 12, 1, 0)
    await session.commit()
    third = await create_preview(
        session,
        user=admin,
        account=account,
        filename="works.xlsx",
        content=payload,
    )

    assert third.id != second.id


@pytest.mark.asyncio
async def test_create_preview_does_not_reuse_hash_across_accounts(
    session, admin, account, other_account, monkeypatch, tmp_path
):
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    payload = _workbook_payload()

    first = await create_preview(
        session,
        user=admin,
        account=account,
        filename="works.xlsx",
        content=payload,
    )
    second = await create_preview(
        session,
        user=admin,
        account=other_account,
        filename="works.xlsx",
        content=payload,
    )

    assert second.id != first.id
    assert len(first.artifacts) == 1
    assert len(second.artifacts) == 1


@pytest.mark.asyncio
async def test_create_preview_recovers_from_unique_conflict_and_reuses_winner(
    session, admin, account, monkeypatch, tmp_path
):
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    payload = _workbook_payload()
    winner = await create_preview(
        session,
        user=admin,
        account=account,
        filename="works.xlsx",
        content=payload,
    )

    from app.services.data_import import service as preview_service

    original_find_existing_preview = preview_service._find_existing_preview
    calls = {"count": 0}

    async def _delayed_find(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return None
        return await original_find_existing_preview(*args, **kwargs)

    monkeypatch.setattr(preview_service, "_find_existing_preview", _delayed_find)

    reused = await create_preview(
        session,
        user=admin,
        account=account,
        filename="works.xlsx",
        content=payload,
    )

    batch_count = await session.scalar(select(func.count()).select_from(DataImportBatch))
    assert reused.id == winner.id
    assert batch_count == 1
    assert len(list(tmp_path.rglob("*.xlsx"))) == 1


@pytest.mark.asyncio
async def test_create_preview_cleans_up_orphaned_file_when_commit_fails(
    session, admin, account, monkeypatch, tmp_path
):
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    payload = _workbook_payload()

    async def _boom(**_kwargs):
        raise RuntimeError("commit failed")

    monkeypatch.setattr("app.services.data_import.service._commit_preview_scope", _boom)

    with pytest.raises(RuntimeError, match="commit failed"):
        await create_preview(
            session,
            user=admin,
            account=account,
            filename="works.xlsx",
            content=payload,
        )

    assert list(tmp_path.rglob("*.xlsx")) == []


@pytest.mark.asyncio
async def test_create_preview_rolls_back_rows_and_artifacts_when_storage_write_fails(
    session, admin, account, monkeypatch, tmp_path
):
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))

    def _boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("app.services.data_import.service._write_artifact_atomic", _boom)

    with pytest.raises(OSError, match="disk full"):
        await create_preview(
            session,
            user=admin,
            account=account,
            filename="works.xlsx",
            content=_workbook_payload(),
        )

    assert list(tmp_path.rglob("*.xlsx")) == []
    assert await session.scalar(select(func.count()).select_from(DataImportBatch)) == 0
    assert await session.scalar(select(func.count()).select_from(DataArtifact)) == 0
    assert await session.scalar(select(func.count()).select_from(DataImportRow)) == 0


@pytest.mark.asyncio
async def test_create_preview_does_not_commit_caller_transaction_on_success(
    monkeypatch, tmp_path
):
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path / "storage"))
    engine, maker = await _make_filebacked_sessionmaker(tmp_path)

    try:
        async with maker() as caller:
            admin_id, account_id = await _seed_transaction_scope(caller)
            await caller.rollback()
            async with caller.begin():
                admin = await caller.get(User, admin_id)
                account = await caller.get(Account, account_id)
                assert admin is not None
                assert account is not None
                caller.add(
                    PlatformContentRecord(
                        org_id=admin.org_id,
                        account_id=account.id,
                        platform=Platform.DOUYIN,
                        external_content_id="caller-success-content",
                        canonical_share_url=(
                            "https://www.douyin.com/video/caller-success-content"
                        ),
                        title="Caller Success Content",
                        identity_confidence=ContentIdentityConfidence.CONFIRMED,
                    )
                )

                batch = await create_preview(
                    caller,
                    user=admin,
                    account=account,
                    filename="works.xlsx",
                    content=_workbook_payload(),
                )

                async with maker() as observer:
                    assert (
                        await observer.scalar(select(func.count()).select_from(DataImportBatch))
                    ) == 0
                    assert (
                        await observer.scalar(
                            select(func.count())
                            .select_from(PlatformContentRecord)
                            .where(
                                PlatformContentRecord.external_content_id
                                == "caller-success-content"
                            )
                        )
                    ) == 0

                assert batch.id is not None

            async with maker() as observer:
                assert (
                    await observer.scalar(select(func.count()).select_from(DataImportBatch))
                ) == 1
                assert (
                    await observer.scalar(
                        select(func.count())
                        .select_from(PlatformContentRecord)
                        .where(
                            PlatformContentRecord.external_content_id
                            == "caller-success-content"
                        )
                    )
                ) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_create_preview_failure_does_not_rollback_caller_transaction(
    monkeypatch, tmp_path
):
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path / "storage"))
    engine, maker = await _make_filebacked_sessionmaker(tmp_path)

    def _boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("app.services.data_import.service._write_artifact_atomic", _boom)

    try:
        async with maker() as caller:
            admin_id, account_id = await _seed_transaction_scope(caller)
            await caller.rollback()
            async with caller.begin():
                admin = await caller.get(User, admin_id)
                account = await caller.get(Account, account_id)
                assert admin is not None
                assert account is not None
                caller.add(
                    PlatformContentRecord(
                        org_id=admin.org_id,
                        account_id=account.id,
                        platform=Platform.DOUYIN,
                        external_content_id="caller-failure-content",
                        canonical_share_url=(
                            "https://www.douyin.com/video/caller-failure-content"
                        ),
                        title="Caller Failure Content",
                        identity_confidence=ContentIdentityConfidence.CONFIRMED,
                    )
                )

                with pytest.raises(OSError, match="disk full"):
                    await create_preview(
                        caller,
                        user=admin,
                        account=account,
                        filename="works.xlsx",
                        content=_workbook_payload(),
                    )

                await caller.flush()

                async with maker() as observer:
                    assert (
                        await observer.scalar(select(func.count()).select_from(DataImportBatch))
                    ) == 0
                    assert (
                        await observer.scalar(
                            select(func.count())
                            .select_from(PlatformContentRecord)
                            .where(
                                PlatformContentRecord.external_content_id
                                == "caller-failure-content"
                            )
                        )
                    ) == 0

            async with maker() as observer:
                assert (
                    await observer.scalar(select(func.count()).select_from(DataImportBatch))
                ) == 0
                assert (
                    await observer.scalar(select(func.count()).select_from(DataArtifact))
                ) == 0
                assert (
                    await observer.scalar(select(func.count()).select_from(DataImportRow))
                ) == 0
                assert (
                    await observer.scalar(
                        select(func.count())
                        .select_from(PlatformContentRecord)
                        .where(
                            PlatformContentRecord.external_content_id
                            == "caller-failure-content"
                        )
                    )
                ) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_resolve_row_match_is_idempotent_and_auditable(
    session, admin, account, monkeypatch, tmp_path
):
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    first = PlatformContentRecord(
        org_id=admin.org_id,
        account_id=account.id,
        platform=Platform.DOUYIN,
        title="作品 A",
        published_at=datetime(2026, 7, 18, 14, 11, 20),
        identity_confidence=ContentIdentityConfidence.CONFIRMED,
    )
    second = PlatformContentRecord(
        org_id=admin.org_id,
        account_id=account.id,
        platform=Platform.DOUYIN,
        title="作品　A",
        published_at=datetime(2026, 7, 18, 14, 11, 20),
        identity_confidence=ContentIdentityConfidence.CONFIRMED,
    )
    session.add_all([first, second])
    await session.commit()

    batch = await create_preview(
        session,
        user=admin,
        account=account,
        filename="works.xlsx",
        content=_workbook_payload(),
    )

    resolution = RowMatchResolution(selected_content_id=first.id, resolved_by=admin)
    first_result = await resolve_row_match(
        session,
        batch=batch,
        row_number=2,
        resolution=resolution,
    )
    second_result = await resolve_row_match(
        session,
        batch=batch,
        row_number=2,
        resolution=resolution,
    )
    conflict = await session.scalar(
        select(DataConflict).where(
            DataConflict.batch_id == batch.id,
            DataConflict.row_number == 2,
        )
    )

    assert first_result.id == second_result.id
    assert first_result.status is ImportRowStatus.READY
    assert first_result.platform_content_record_id == first.id
    assert first_result.resolution_outcome == "matched"
    assert first_result.resolved_by_id == admin.id
    assert first_result.resolved_at is not None
    assert first_result.candidate_content_ids == [first.id, second.id]
    assert conflict is not None
    assert conflict.status is ConflictStatus.RESOLVED
    assert conflict.resolved_by_id == admin.id
    assert conflict.resolved_at is not None


@pytest.mark.asyncio
async def test_resolve_row_match_records_explicit_no_match_and_rejects_later_changes(
    session, admin, account, monkeypatch, tmp_path
):
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    candidate = PlatformContentRecord(
        org_id=admin.org_id,
        account_id=account.id,
        platform=Platform.DOUYIN,
        title="作品 A",
        published_at=datetime(2026, 7, 18, 14, 11, 20),
        identity_confidence=ContentIdentityConfidence.CONFIRMED,
    )
    session.add(candidate)
    await session.commit()

    batch = await create_preview(
        session,
        user=admin,
        account=account,
        filename="works.xlsx",
        content=_workbook_payload(),
    )

    first_result = await resolve_row_match(
        session,
        batch=batch,
        row_number=2,
        resolution=RowMatchResolution(selected_content_id=None, resolved_by=admin),
    )
    replay_result = await resolve_row_match(
        session,
        batch=batch,
        row_number=2,
        resolution=RowMatchResolution(selected_content_id=None, resolved_by=admin),
    )

    assert first_result.id == replay_result.id
    assert first_result.status is ImportRowStatus.READY
    assert first_result.platform_content_record_id is None
    assert first_result.resolution_outcome == "no_match"
    assert first_result.resolved_by_id == admin.id
    assert first_result.resolved_at is not None

    with pytest.raises(ValueError, match="already resolved"):
        await resolve_row_match(
            session,
            batch=batch,
            row_number=2,
            resolution=RowMatchResolution(selected_content_id=candidate.id, resolved_by=admin),
        )


@pytest.mark.asyncio
async def test_resolve_row_match_rejects_rows_without_open_resolution_conflict(
    session, admin, account, monkeypatch, tmp_path
):
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    batch = await create_preview(
        session,
        user=admin,
        account=account,
        filename="works.xlsx",
        content=_workbook_payload(title="unmatched title"),
    )

    with pytest.raises(ValueError, match="needs_resolution"):
        await resolve_row_match(
            session,
            batch=batch,
            row_number=2,
            resolution=RowMatchResolution(selected_content_id=None, resolved_by=admin),
        )


@pytest.mark.asyncio
async def test_resolve_row_match_rejects_candidate_from_another_account(
    session, admin, account, other_account, monkeypatch, tmp_path
):
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    candidate = PlatformContentRecord(
        org_id=admin.org_id,
        account_id=account.id,
        platform=Platform.DOUYIN,
        title="作品 A",
        published_at=datetime(2026, 7, 18, 14, 11, 20),
        identity_confidence=ContentIdentityConfidence.CONFIRMED,
    )
    outsider = PlatformContentRecord(
        org_id=admin.org_id,
        account_id=other_account.id,
        platform=Platform.DOUYIN,
        title="作品 A",
        published_at=datetime(2026, 7, 18, 14, 11, 20),
        identity_confidence=ContentIdentityConfidence.CONFIRMED,
    )
    session.add_all([candidate, outsider])
    await session.commit()

    batch = await create_preview(
        session,
        user=admin,
        account=account,
        filename="works.xlsx",
        content=_workbook_payload(),
    )

    with pytest.raises(ValueError, match="candidate"):
        await resolve_row_match(
            session,
            batch=batch,
            row_number=2,
            resolution=RowMatchResolution(selected_content_id=outsider.id, resolved_by=admin),
        )
