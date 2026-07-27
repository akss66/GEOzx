import asyncio
from datetime import date, datetime

import pytest
from sqlalchemy import UniqueConstraint, event, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.core.security import hash_password
from app.db import Base
from app.models import (
    Account,
    AccountMetricSnapshot,
    AudienceProfileItem,
    AudienceProfileSnapshot,
    BenchmarkSnapshot,
    DataArtifact,
    DataConflict,
    DataImportBatch,
    DataImportRow,
    MetricSnapshot,
    Org,
    PlatformContentRecord,
    User,
)
from app.models.enums import (
    ConflictStatus,
    ContentIdentityConfidence,
    DataSourceKind,
    ImportBatchStatus,
    ImportRowStatus,
    MetricSource,
    Platform,
)
from app.services.data_import import service as data_import_service
from app.services.data_import.service import commit_batch


@pytest.fixture
async def account(session, admin) -> Account:
    item = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="Account data model fixture",
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


@pytest.mark.asyncio
async def test_import_batch_owns_artifact_and_staging_rows(session, admin, account):
    batch = DataImportBatch(
        org_id=admin.org_id,
        account_id=account.id,
        created_by_id=admin.id,
        source_kind=DataSourceKind.PLATFORM_EXPORT,
        status=ImportBatchStatus.PREVIEW_READY,
        template_code="douyin_work_list_v1",
        content_sha256="d" * 64,
    )
    session.add(batch)
    await session.flush()

    artifact = DataArtifact(
        org_id=admin.org_id,
        account_id=account.id,
        batch_id=batch.id,
        filename="works.xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        byte_size=2048,
        sha256="a" * 64,
        storage_key="account-data/1/2/3/works.xlsx",
    )
    row = DataImportRow(
        org_id=admin.org_id,
        account_id=account.id,
        batch_id=batch.id,
        row_number=1,
        status=ImportRowStatus.READY,
        raw_values={"title": "Video A"},
        normalized_values={"play": 81},
        weak_fingerprint="work-list-video-a",
    )
    content = PlatformContentRecord(
        org_id=admin.org_id,
        account_id=account.id,
        platform=Platform.DOUYIN,
        external_content_id="7299001",
        share_url="https://www.douyin.com/video/7299001",
        canonical_share_url="https://www.douyin.com/video/7299001",
        title="Video A",
        published_at=datetime(2026, 7, 18, 14, 11, 20),
        identity_confidence=ContentIdentityConfidence.CONFIRMED,
        weak_fingerprint="work-list-video-a",
    )
    session.add_all([artifact, row, content])
    await session.flush()

    account_snapshot = AccountMetricSnapshot(
        org_id=admin.org_id,
        account_id=account.id,
        import_batch_id=batch.id,
        source_kind=DataSourceKind.PLATFORM_EXPORT,
        stat_date=date(2026, 7, 18),
        follower_count=1200,
        total_play=9000,
    )
    audience_snapshot = AudienceProfileSnapshot(
        org_id=admin.org_id,
        account_id=account.id,
        import_batch_id=batch.id,
        source_kind=DataSourceKind.PLATFORM_EXPORT,
        stat_date=date(2026, 7, 18),
        dimension="gender",
        total_audience=1000,
    )
    benchmark = BenchmarkSnapshot(
        org_id=admin.org_id,
        account_id=account.id,
        import_batch_id=batch.id,
        source_kind=DataSourceKind.PLATFORM_EXPORT,
        stat_date=date(2026, 7, 18),
        benchmark_code="track_median",
        metric_code="play",
        metric_value=81.0,
    )
    conflict = DataConflict(
        org_id=admin.org_id,
        account_id=account.id,
        batch_id=batch.id,
        row_number=1,
        status=ConflictStatus.OPEN,
        field_name="platform_content_record_id",
        conflict_code="multiple_candidates",
        message="Manual resolution required",
    )
    metric = MetricSnapshot(
        org_id=admin.org_id,
        account_id=account.id,
        source=MetricSource.DOUYIN,
        stat_date=date(2026, 7, 18),
        play=81,
        exposure=120,
        completion_rate=0.0875,
        like_rate=0.05,
        comment_rate=0.01,
        share_rate=0.01,
        follower_delta=0,
        import_batch_id=batch.id,
        platform_content_record_id=content.id,
        like_count=4,
    )
    session.add_all([account_snapshot, audience_snapshot, benchmark, conflict, metric])
    await session.flush()
    audience_item = AudienceProfileItem(
        org_id=admin.org_id,
        account_id=account.id,
        snapshot_id=audience_snapshot.id,
        label="female",
        value="0.63",
        rank=1,
    )
    session.add(audience_item)
    await session.commit()

    loaded_batch = await session.scalar(
        select(DataImportBatch)
        .options(
            selectinload(DataImportBatch.artifacts),
            selectinload(DataImportBatch.rows),
        )
        .where(DataImportBatch.id == batch.id)
    )
    assert loaded_batch is not None
    assert loaded_batch.id is not None
    assert [item.filename for item in loaded_batch.artifacts] == ["works.xlsx"]
    assert [item.row_number for item in loaded_batch.rows] == [1]
    assert metric.import_batch_id == batch.id
    assert metric.platform_content_record_id == content.id
    assert audience_item.label == "female"
    assert benchmark.metric_code == "play"
    assert conflict.status is ConflictStatus.OPEN


@pytest.mark.asyncio
async def test_platform_content_identity_is_strong_but_weak_fingerprint_is_not_unique(
    session, admin, account
):
    first = PlatformContentRecord(
        org_id=admin.org_id,
        account_id=account.id,
        platform=Platform.DOUYIN,
        external_content_id="content-1",
        share_url="https://v.douyin.com/content-1",
        canonical_share_url="https://v.douyin.com/content-1",
        title="Shared fingerprint A",
        identity_confidence=ContentIdentityConfidence.CONFIRMED,
        weak_fingerprint="same-fingerprint",
    )
    second = PlatformContentRecord(
        org_id=admin.org_id,
        account_id=account.id,
        platform=Platform.DOUYIN,
        external_content_id="content-2",
        share_url="https://v.douyin.com/content-2",
        canonical_share_url="https://v.douyin.com/content-2",
        title="Shared fingerprint B",
        identity_confidence=ContentIdentityConfidence.CONFIRMED,
        weak_fingerprint="same-fingerprint",
    )
    session.add_all([first, second])
    await session.commit()
    org_id = admin.org_id
    account_id = account.id

    duplicate_external_id = PlatformContentRecord(
        org_id=org_id,
        account_id=account_id,
        platform=Platform.DOUYIN,
        external_content_id="content-1",
        title="Duplicate external id",
        identity_confidence=ContentIdentityConfidence.CONFIRMED,
    )
    session.add(duplicate_external_id)
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()

    duplicate_share_url = PlatformContentRecord(
        org_id=org_id,
        account_id=account_id,
        platform=Platform.DOUYIN,
        share_url="https://V.DOUYIN.com:443/content-2/",
        canonical_share_url="https://v.douyin.com/content-2",
        title="Duplicate canonical share url",
        identity_confidence=ContentIdentityConfidence.CONFIRMED,
    )
    session.add(duplicate_share_url)
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


@pytest.mark.asyncio
async def test_platform_content_record_tracks_canonical_batch_provenance(
    session, admin, account
):
    batch = DataImportBatch(
        org_id=admin.org_id,
        account_id=account.id,
        created_by_id=admin.id,
        source_kind=DataSourceKind.PLATFORM_EXPORT,
        status=ImportBatchStatus.COMMITTED,
        template_code="douyin_work_list_v1",
        content_sha256="e" * 64,
    )
    session.add(batch)
    await session.flush()
    content = PlatformContentRecord(
        org_id=admin.org_id,
        account_id=account.id,
        platform=Platform.DOUYIN,
        external_content_id="canonical-content-1",
        share_url="https://v.douyin.com/canonical-content-1",
        canonical_share_url="https://v.douyin.com/canonical-content-1",
        title="Canonical content",
        identity_confidence=ContentIdentityConfidence.CONFIRMED,
        canonical_import_batch_id=batch.id,
    )
    session.add(content)
    batch_id = batch.id
    await session.commit()
    content_id = content.id
    session.expire_all()

    stored = await session.get(PlatformContentRecord, content_id)

    assert stored is not None
    assert stored.canonical_import_batch_id == batch_id


def test_metric_snapshot_account_data_columns_are_nullable_and_foreign_keyed():
    constraints = {
        constraint.name: constraint
        for constraint in MetricSnapshot.__table__.foreign_key_constraints
    }
    check_constraints = {
        constraint.name: constraint.sqltext.text
        for constraint in MetricSnapshot.__table__.constraints
        if getattr(constraint, "sqltext", None) is not None
    }
    import_batch_fk = constraints["fk_metric_snapshots_import_batch_scope"]
    content_fk = constraints["fk_metric_snapshots_content_scope"]

    assert [column.name for column in import_batch_fk.columns] == [
        "org_id",
        "account_id",
        "import_batch_id",
    ]
    assert [element.target_fullname for element in import_batch_fk.elements] == [
        "data_import_batches.org_id",
        "data_import_batches.account_id",
        "data_import_batches.id",
    ]
    assert import_batch_fk.ondelete == "CASCADE"
    assert [column.name for column in content_fk.columns] == [
        "org_id",
        "account_id",
        "platform_content_record_id",
    ]
    assert [element.target_fullname for element in content_fk.elements] == [
        "platform_content_records.org_id",
        "platform_content_records.account_id",
        "platform_content_records.id",
    ]
    assert content_fk.ondelete == "CASCADE"
    assert (
        check_constraints["ck_metric_snapshots_account_required_for_source_links"]
        == "(import_batch_id IS NULL AND platform_content_record_id IS NULL) "
        "OR account_id IS NOT NULL"
    )
    for column_name in (
        "import_batch_id",
        "platform_content_record_id",
        "like_count",
        "comment_count",
        "share_count",
        "favorite_count",
        "cover_click_rate",
        "avg_watch_time_seconds",
    ):
        assert MetricSnapshot.__table__.c[column_name].nullable is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("linked_field", "linked_value_factory"),
    [
        ("import_batch_id", lambda batch, content: batch.id),
        ("platform_content_record_id", lambda batch, content: content.id),
    ],
)
async def test_metric_snapshot_requires_account_when_source_links_are_set(
    linked_field, linked_value_factory
):
    engine = create_async_engine("sqlite+aiosqlite://")

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db_session:
        org = Org(name=f"Metric scope {linked_field}")
        owner = User(
            org=org,
            email=f"{linked_field}@test.com",
            hashed_password=hash_password("metric-scope-password"),
            display_name="Metric scope owner",
        )
        db_session.add(owner)
        await db_session.flush()
        account = Account(
            org_id=org.id,
            platform=Platform.DOUYIN,
            nickname=f"Metric scope {linked_field}",
        )
        db_session.add(account)
        await db_session.flush()
        batch = DataImportBatch(
            org_id=org.id,
            account_id=account.id,
            created_by_id=owner.id,
            source_kind=DataSourceKind.PLATFORM_EXPORT,
            status=ImportBatchStatus.COMMITTED,
            template_code="douyin_work_list_v1",
            content_sha256="f" * 64,
        )
        db_session.add(batch)
        await db_session.flush()
        content = PlatformContentRecord(
            org_id=org.id,
            account_id=account.id,
            platform=Platform.DOUYIN,
            external_content_id=f"metric-scope-{linked_field}",
            canonical_share_url=f"https://www.douyin.com/video/metric-scope-{linked_field}",
            identity_confidence=ContentIdentityConfidence.CONFIRMED,
            canonical_import_batch_id=batch.id,
        )
        db_session.add(content)
        await db_session.commit()

        metric = MetricSnapshot(
            org_id=org.id,
            account_id=None,
            source=MetricSource.DOUYIN,
            stat_date=date(2026, 7, 22),
            play=10,
            exposure=20,
            completion_rate=0.2,
            like_rate=0.1,
            comment_rate=0.05,
            share_rate=0.02,
            follower_delta=1,
            **{linked_field: linked_value_factory(batch, content)},
        )
        db_session.add(metric)

        with pytest.raises(IntegrityError):
            await db_session.commit()

    await engine.dispose()


@pytest.mark.asyncio
async def test_database_rejects_cross_scope_account_data_links():
    engine = create_async_engine("sqlite+aiosqlite://")

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db_session:
        first_org = Org(name="First scope org")
        second_org = Org(name="Second scope org")
        first_owner = User(
            org=first_org,
            email="first-scope-owner@test.com",
            hashed_password=hash_password("first-scope-password"),
            display_name="First scope owner",
        )
        second_owner = User(
            org=second_org,
            email="second-scope-owner@test.com",
            hashed_password=hash_password("second-scope-password"),
            display_name="Second scope owner",
        )
        db_session.add_all([first_owner, second_owner])
        await db_session.flush()
        first_account = Account(
            org_id=first_org.id,
            platform=Platform.DOUYIN,
            nickname="First scoped account",
        )
        second_account = Account(
            org_id=second_org.id,
            platform=Platform.DOUYIN,
            nickname="Second scoped account",
        )
        db_session.add_all([first_account, second_account])
        await db_session.flush()
        first_batch = DataImportBatch(
            org_id=first_org.id,
            account_id=first_account.id,
            created_by_id=first_owner.id,
            source_kind=DataSourceKind.PLATFORM_EXPORT,
            status=ImportBatchStatus.PREVIEW_READY,
            template_code="douyin_work_list_v1",
            content_sha256="1" * 64,
        )
        second_batch = DataImportBatch(
            org_id=second_org.id,
            account_id=second_account.id,
            created_by_id=second_owner.id,
            source_kind=DataSourceKind.PLATFORM_EXPORT,
            status=ImportBatchStatus.PREVIEW_READY,
            template_code="douyin_work_list_v1",
            content_sha256="2" * 64,
        )
        db_session.add_all([first_batch, second_batch])
        await db_session.flush()
        second_content = PlatformContentRecord(
            org_id=second_org.id,
            account_id=second_account.id,
            platform=Platform.DOUYIN,
            external_content_id="second-content",
            canonical_share_url="https://www.douyin.com/video/second-content",
            identity_confidence=ContentIdentityConfidence.CONFIRMED,
            canonical_import_batch_id=second_batch.id,
        )
        db_session.add(second_content)
        await db_session.commit()
        first_org_id = first_org.id
        first_account_id = first_account.id
        first_batch_id = first_batch.id
        second_batch_id = second_batch.id
        second_content_id = second_content.id

        db_session.add(
            DataArtifact(
                org_id=first_org_id,
                account_id=first_account_id,
                batch_id=second_batch_id,
                filename="cross-account.xlsx",
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                byte_size=1024,
                sha256="b" * 64,
                storage_key="account-data/first/cross-account.xlsx",
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.commit()
        await db_session.rollback()

        db_session.add(
            DataImportRow(
                org_id=first_org_id,
                account_id=first_account_id,
                batch_id=first_batch_id,
                row_number=1,
                status=ImportRowStatus.READY,
                platform_content_record_id=second_content_id,
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.commit()

    await engine.dispose()


@pytest.mark.asyncio
async def test_account_owned_import_data_survives_user_deletion_and_cascades_with_account():
    engine = create_async_engine("sqlite+aiosqlite://")

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db_session:
        org = Org(name="Account data cascade org")
        owner = User(
            org=org,
            email="account-data-owner@test.com",
            hashed_password=hash_password("owner-password"),
            display_name="Owner",
        )
        db_session.add(owner)
        await db_session.flush()
        account = Account(
            org_id=org.id,
            platform=Platform.DOUYIN,
            nickname="Imported account",
        )
        db_session.add(account)
        await db_session.flush()
        batch = DataImportBatch(
            org_id=org.id,
            account_id=account.id,
            created_by_id=owner.id,
            source_kind=DataSourceKind.PLATFORM_EXPORT,
            status=ImportBatchStatus.PREVIEW_READY,
            template_code="douyin_work_list_v1",
            content_sha256="3" * 64,
        )
        db_session.add(batch)
        await db_session.flush()
        artifact = DataArtifact(
            org_id=org.id,
            account_id=account.id,
            batch_id=batch.id,
            filename="works.xlsx",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            byte_size=2048,
            sha256="c" * 64,
            storage_key="account-data/keep/works.xlsx",
        )
        content = PlatformContentRecord(
            org_id=org.id,
            account_id=account.id,
            platform=Platform.DOUYIN,
            external_content_id="content-keep",
            canonical_share_url="https://www.douyin.com/video/content-keep",
            identity_confidence=ContentIdentityConfidence.CONFIRMED,
            canonical_import_batch_id=batch.id,
        )
        db_session.add_all([artifact, content])
        await db_session.flush()
        row = DataImportRow(
            org_id=org.id,
            account_id=account.id,
            batch_id=batch.id,
            row_number=1,
            status=ImportRowStatus.COMMITTED,
            platform_content_record_id=content.id,
        )
        snapshot = AccountMetricSnapshot(
            org_id=org.id,
            account_id=account.id,
            import_batch_id=batch.id,
            source_kind=DataSourceKind.PLATFORM_EXPORT,
            stat_date=date(2026, 7, 22),
            follower_count=100,
        )
        audience_snapshot = AudienceProfileSnapshot(
            org_id=org.id,
            account_id=account.id,
            import_batch_id=batch.id,
            source_kind=DataSourceKind.PLATFORM_EXPORT,
            stat_date=date(2026, 7, 22),
            dimension="gender",
            total_audience=50,
        )
        benchmark = BenchmarkSnapshot(
            org_id=org.id,
            account_id=account.id,
            import_batch_id=batch.id,
            source_kind=DataSourceKind.PLATFORM_EXPORT,
            stat_date=date(2026, 7, 22),
            benchmark_code="median",
            metric_code="play",
            metric_value=99.0,
        )
        conflict = DataConflict(
            org_id=org.id,
            account_id=account.id,
            batch_id=batch.id,
            row_number=1,
            status=ConflictStatus.OPEN,
            field_name="title",
            conflict_code="mismatch",
            message="Needs review",
        )
        db_session.add_all([row, snapshot, audience_snapshot, benchmark, conflict])
        await db_session.flush()
        audience_item = AudienceProfileItem(
            org_id=org.id,
            account_id=account.id,
            snapshot_id=audience_snapshot.id,
            label="female",
            value="0.63",
            rank=1,
        )
        db_session.add(audience_item)
        await db_session.commit()
        owner_id = owner.id
        batch_id = batch.id
        artifact_id = artifact.id
        content_id = content.id
        row_id = row.id
        snapshot_id = snapshot.id
        audience_snapshot_id = audience_snapshot.id
        audience_item_id = audience_item.id
        benchmark_id = benchmark.id
        conflict_id = conflict.id
        account_id = account.id

        await db_session.delete(owner)
        await db_session.commit()
        db_session.expire_all()
        stored_batch = await db_session.get(DataImportBatch, batch_id)
        stored_artifact = await db_session.get(DataArtifact, artifact_id)
        stored_content = await db_session.get(PlatformContentRecord, content_id)
        stored_row = await db_session.get(DataImportRow, row_id)
        stored_snapshot = await db_session.get(AccountMetricSnapshot, snapshot_id)
        stored_audience_snapshot = await db_session.get(
            AudienceProfileSnapshot, audience_snapshot_id
        )
        stored_audience_item = await db_session.get(AudienceProfileItem, audience_item_id)
        stored_benchmark = await db_session.get(BenchmarkSnapshot, benchmark_id)
        stored_conflict = await db_session.get(DataConflict, conflict_id)
        assert stored_batch is not None
        assert stored_batch.created_by_id is None
        assert stored_artifact is not None
        assert stored_content is not None
        assert stored_row is not None
        assert stored_snapshot is not None
        assert stored_audience_snapshot is not None
        assert stored_audience_item is not None
        assert stored_benchmark is not None
        assert stored_conflict is not None
        assert await db_session.get(User, owner_id) is None

        await db_session.delete(account)
        await db_session.commit()
        db_session.expire_all()
        assert await db_session.get(Account, account_id) is None
        assert await db_session.get(DataImportBatch, batch_id) is None
        assert await db_session.get(DataArtifact, artifact_id) is None
        assert await db_session.get(PlatformContentRecord, content_id) is None
        assert await db_session.get(DataImportRow, row_id) is None
        assert await db_session.get(AccountMetricSnapshot, snapshot_id) is None
        assert await db_session.get(AudienceProfileSnapshot, audience_snapshot_id) is None
        assert await db_session.get(AudienceProfileItem, audience_item_id) is None
        assert await db_session.get(BenchmarkSnapshot, benchmark_id) is None
        assert await db_session.get(DataConflict, conflict_id) is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_active_preview_identity_is_unique_until_commit_or_revoke(session, admin, account):
    first = DataImportBatch(
        org_id=admin.org_id,
        account_id=account.id,
        created_by_id=admin.id,
        source_kind=DataSourceKind.PLATFORM_EXPORT,
        status=ImportBatchStatus.PREVIEW_READY,
        template_code="douyin_work_list_v1",
        content_sha256="4" * 64,
    )
    session.add(first)
    await session.commit()

    duplicate = DataImportBatch(
        org_id=admin.org_id,
        account_id=account.id,
        created_by_id=admin.id,
        source_kind=DataSourceKind.PLATFORM_EXPORT,
        status=ImportBatchStatus.PREVIEW_READY,
        template_code="douyin_work_list_v1",
        content_sha256="4" * 64,
    )
    session.add(duplicate)
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()

    first.committed_at = datetime(2026, 7, 22, 12, 0, 0)
    await session.commit()
    session.add(duplicate)
    await session.commit()
    await session.refresh(duplicate)
    assert duplicate.id is not None


def test_platform_content_record_has_import_row_identity_guard_for_retry_safe_projection():
    columns = PlatformContentRecord.__table__.c
    constraints = {
        constraint.name: constraint
        for constraint in PlatformContentRecord.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert "canonical_import_row_number" in columns
    constraint = constraints["uq_platform_content_records_import_row_identity"]
    assert [column.name for column in constraint.columns] == [
        "account_id",
        "canonical_import_batch_id",
        "canonical_import_row_number",
    ]


def test_metric_snapshot_has_unique_import_projection_identity():
    constraints = {
        constraint.name: constraint
        for constraint in MetricSnapshot.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    constraint = constraints["uq_metric_snapshots_import_projection"]
    assert [column.name for column in constraint.columns] == [
        "account_id",
        "import_batch_id",
        "platform_content_record_id",
        "stat_date",
    ]


@pytest.mark.asyncio
async def test_concurrent_commit_is_retry_idempotent_for_same_batch(tmp_path, monkeypatch):
    database_path = tmp_path / "account-data-concurrent-commit.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database_path.as_posix()}",
        connect_args={"timeout": 30},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as setup_session:
        org = Org(name="Concurrent import org")
        actor = User(
            org=org,
            email="concurrent-import@test.com",
            hashed_password=hash_password("import-pw-123"),
            display_name="Concurrent import actor",
        )
        setup_session.add(actor)
        await setup_session.flush()
        account = Account(
            org_id=org.id,
            platform=Platform.DOUYIN,
            nickname="Concurrent import account",
        )
        setup_session.add(account)
        await setup_session.flush()
        batch = DataImportBatch(
            org_id=org.id,
            account_id=account.id,
            created_by_id=actor.id,
            source_kind=DataSourceKind.PLATFORM_EXPORT,
            status=ImportBatchStatus.PREVIEW_READY,
            template_code="douyin_work_list_v1",
            content_sha256="8" * 64,
        )
        setup_session.add(batch)
        await setup_session.flush()
        setup_session.add(
            DataImportRow(
                org_id=org.id,
                account_id=account.id,
                batch_id=batch.id,
                row_number=2,
                status=ImportRowStatus.READY,
                    raw_values={"title": "Concurrent work"},
                    normalized_values={
                        "title": "Concurrent work",
                        "published_at": "2026-07-18T14:11:20",
                        "play": 81,
                    "completion_rate": 0.0875,
                    "like_count": 6,
                    "comment_count": 3,
                    "share_count": 0,
                    "favorite_count": 0,
                    "cover_click_rate": None,
                    "avg_watch_time_seconds": 9.53,
                    "follower_delta": 0,
                },
                projected_target_ids=[],
                weak_fingerprint="concurrent-work|2026-07-18t14:11:20",
            )
        )
        await setup_session.commit()
        org_id = org.id
        account_id = account.id
        actor_id = actor.id
        batch_id = batch.id

    barrier = asyncio.Barrier(2)
    real_project = data_import_service._project_row_targets

    async def _synchronized_project(*args, **kwargs):
        await barrier.wait()
        return await real_project(*args, **kwargs)

    monkeypatch.setattr(data_import_service, "_project_row_targets", _synchronized_project)

    async def _run_commit():
        async with maker() as db_session:
            actor = await db_session.get(User, actor_id)
            assert actor is not None
            return await commit_batch(
                db_session,
                org_id=org_id,
                account_id=account_id,
                batch_id=batch_id,
                actor=actor,
            )

    await asyncio.wait_for(asyncio.gather(_run_commit(), _run_commit()), timeout=30)

    async with maker() as assert_session:
        content_rows = list(
            await assert_session.scalars(
                select(PlatformContentRecord).where(
                    PlatformContentRecord.canonical_import_batch_id == batch_id
                )
            )
        )
        metric_rows = list(
            await assert_session.scalars(
                select(MetricSnapshot).where(MetricSnapshot.import_batch_id == batch_id)
            )
        )
        stored_batch = await assert_session.get(DataImportBatch, batch_id)
        assert stored_batch is not None
        assert stored_batch.status is ImportBatchStatus.COMMITTED
        assert len(content_rows) == 1
        assert len(metric_rows) == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_postgresql_mutation_queries_request_row_locks(monkeypatch):
    captured: list[object] = []
    batch = DataImportBatch(org_id=1, account_id=2, id=3)
    row = DataImportRow(
        org_id=1,
        account_id=2,
        batch_id=3,
        row_number=2,
        status=ImportRowStatus.COMMITTED,
        projected_target_ids=[{"kind": "platform_content_record", "id": 9, "action": "created"}],
    )
    batch.rows = [row]

    class _FakeBind:
        dialect = postgresql.dialect()

    class _FakeSession:
        def get_bind(self):
            return _FakeBind()

        async def scalar(self, statement):
            captured.append(statement)
            compiled = str(statement.compile(dialect=postgresql.dialect()))
            if "FROM data_import_batches" in compiled:
                return batch
            if "platform_content_records" in compiled:
                return PlatformContentRecord(
                    id=9,
                    org_id=1,
                    account_id=2,
                    platform=Platform.DOUYIN,
                    canonical_import_batch_id=99,
                    identity_confidence=ContentIdentityConfidence.CONFIRMED,
                )
            return 3

        async def scalars(self, statement):
            captured.append(statement)
            if "data_import_rows" in str(statement.compile(dialect=postgresql.dialect())):
                return [row]
            if "data_conflicts" in str(statement.compile(dialect=postgresql.dialect())):
                return []
            return []

    async def _fake_load_scoped_batch(*args, **kwargs):
        return batch

    monkeypatch.setattr(data_import_service, "load_scoped_batch", _fake_load_scoped_batch)
    session = _FakeSession()

    await data_import_service._load_mutation_batch(
        session,
        org_id=1,
        account_id=2,
        batch_id=3,
    )
    await data_import_service._find_revoke_conflicts(session=session, batch=batch)

    compiled = [str(statement.compile(dialect=postgresql.dialect())) for statement in captured]
    assert any("data_import_batches" in item and "FOR UPDATE" in item for item in compiled)
    assert any("data_import_rows" in item and "FOR UPDATE" in item for item in compiled)
    assert any("data_conflicts" in item and "FOR UPDATE" in item for item in compiled)
    assert any("platform_content_records" in item and "FOR UPDATE" in item for item in compiled)


@pytest.mark.asyncio
async def test_postgresql_load_mutation_batch_refreshes_authoritative_locked_batch(monkeypatch):
    captured: list[object] = []
    committed_relationships: list[tuple[object, str, object]] = []
    stale_batch = DataImportBatch(
        org_id=1,
        account_id=2,
        id=3,
        status=ImportBatchStatus.PREVIEW_READY,
    )
    authoritative_batch = DataImportBatch(
        org_id=1,
        account_id=2,
        id=3,
        status=ImportBatchStatus.COMMITTED,
        committed_at=datetime(2026, 7, 22, 9, 30),
    )
    authoritative_row = DataImportRow(
        org_id=1,
        account_id=2,
        batch_id=3,
        row_number=2,
        status=ImportRowStatus.READY,
    )
    authoritative_conflict = DataConflict(
        org_id=1,
        account_id=2,
        batch_id=3,
        row_number=2,
        field_name="manual_confirmation",
        status=ConflictStatus.OPEN,
    )

    class _FakeBind:
        dialect = postgresql.dialect()

    class _FakeSession:
        def get_bind(self):
            return _FakeBind()

        async def scalar(self, statement):
            captured.append(statement)
            compiled = str(statement.compile(dialect=postgresql.dialect()))
            if "FROM data_import_batches" in compiled:
                return authoritative_batch
            return None

        async def scalars(self, statement):
            captured.append(statement)
            compiled = str(statement.compile(dialect=postgresql.dialect()))
            if "FROM data_import_rows" in compiled:
                return [authoritative_row]
            if "FROM data_conflicts" in compiled:
                return [authoritative_conflict]
            return []

    def _record_committed_value(instance, key, value):
        committed_relationships.append((instance, key, value))
        instance.__dict__[key] = value

    async def _fake_load_scoped_batch(*args, **kwargs):
        return stale_batch

    monkeypatch.setattr(data_import_service, "load_scoped_batch", _fake_load_scoped_batch)
    monkeypatch.setattr(data_import_service, "set_committed_value", _record_committed_value)
    loaded = await data_import_service._load_mutation_batch(
        _FakeSession(),
        org_id=1,
        account_id=2,
        batch_id=3,
    )

    assert loaded is authoritative_batch
    assert loaded.status is ImportBatchStatus.COMMITTED
    assert loaded.committed_at == datetime(2026, 7, 22, 9, 30)
    assert committed_relationships == [
        (authoritative_batch, "rows", [authoritative_row]),
        (authoritative_batch, "conflicts", [authoritative_conflict]),
    ]
    batch_query = next(
        str(statement.compile(dialect=postgresql.dialect()))
        for statement in captured
        if "FROM data_import_batches" in str(statement.compile(dialect=postgresql.dialect()))
    )
    assert "data_import_batches.status" in batch_query
    assert "FOR UPDATE" in batch_query


@pytest.mark.asyncio
async def test_commit_retry_no_ops_when_locked_batch_is_already_committed(monkeypatch):
    original_timestamp = datetime(2026, 7, 22, 10, 0)
    original_targets = [{"kind": "metric_snapshot", "id": 11, "action": "created"}]
    row = DataImportRow(
        org_id=1,
        account_id=2,
        batch_id=3,
        row_number=2,
        status=ImportRowStatus.COMMITTED,
        projected_target_ids=list(original_targets),
    )
    batch = DataImportBatch(
        org_id=1,
        account_id=2,
        id=3,
        status=ImportBatchStatus.COMMITTED,
        committed_at=original_timestamp,
    )
    batch.rows = [row]
    actor = User(org_id=1, id=9)
    project_calls = 0

    async def _fake_load_mutation_batch(*args, **kwargs):
        return batch

    async def _fake_load_scoped_batch(*args, **kwargs):
        return batch

    async def _unexpected_project(*args, **kwargs):
        nonlocal project_calls
        project_calls += 1
        raise AssertionError("commit retry should not project rows again")

    monkeypatch.setattr(data_import_service, "_load_mutation_batch", _fake_load_mutation_batch)
    monkeypatch.setattr(data_import_service, "load_scoped_batch", _fake_load_scoped_batch)
    monkeypatch.setattr(data_import_service, "_project_row_targets", _unexpected_project)

    replayed = await commit_batch(
        None,
        org_id=1,
        account_id=2,
        batch_id=3,
        actor=actor,
    )

    assert replayed is batch
    assert batch.committed_at == original_timestamp
    assert row.projected_target_ids == original_targets
    assert project_calls == 0


@pytest.mark.asyncio
async def test_revoke_retry_no_ops_when_locked_batch_is_already_revoked(monkeypatch):
    original_timestamp = datetime(2026, 7, 22, 11, 0)
    original_targets = [{"kind": "metric_snapshot", "id": 12, "action": "created"}]
    row = DataImportRow(
        org_id=1,
        account_id=2,
        batch_id=3,
        row_number=2,
        status=ImportRowStatus.REVOKED,
        projected_target_ids=list(original_targets),
    )
    batch = DataImportBatch(
        org_id=1,
        account_id=2,
        id=3,
        status=ImportBatchStatus.REVOKED,
        committed_at=datetime(2026, 7, 22, 10, 0),
        revoked_at=original_timestamp,
    )
    batch.rows = [row]
    actor = User(org_id=1, id=9)
    revoke_conflict_checks = 0
    delete_calls = 0

    async def _fake_load_mutation_batch(*args, **kwargs):
        return batch

    async def _fake_load_scoped_batch(*args, **kwargs):
        return batch

    async def _unexpected_conflicts(*args, **kwargs):
        nonlocal revoke_conflict_checks
        revoke_conflict_checks += 1
        raise AssertionError("revoke retry should not re-check conflicts")

    async def _unexpected_delete(*args, **kwargs):
        nonlocal delete_calls
        delete_calls += 1
        raise AssertionError("revoke retry should not delete projections again")

    monkeypatch.setattr(data_import_service, "_load_mutation_batch", _fake_load_mutation_batch)
    monkeypatch.setattr(data_import_service, "load_scoped_batch", _fake_load_scoped_batch)
    monkeypatch.setattr(data_import_service, "_find_revoke_conflicts", _unexpected_conflicts)
    monkeypatch.setattr(data_import_service, "_delete_row_targets", _unexpected_delete)

    replayed = await data_import_service.revoke_batch(
        None,
        org_id=1,
        account_id=2,
        batch_id=3,
        actor=actor,
    )

    assert replayed is batch
    assert batch.revoked_at == original_timestamp
    assert row.projected_target_ids == original_targets
    assert revoke_conflict_checks == 0
    assert delete_calls == 0
