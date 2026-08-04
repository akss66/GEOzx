"""DB-owned checkpoint freshness policy tests."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from app.models import (
    Account,
    AccountMetricSnapshot,
    AudienceProfileItem,
    AudienceProfileSnapshot,
    BenchmarkSnapshot,
    DataArtifact,
    DataConflict,
    DataFieldObservation,
    DataImportBatch,
    MetricSnapshot,
    PlatformContentRecord,
)
from app.models.enums import (
    AccountStatus,
    ConflictStatus,
    ContentIdentityConfidence,
    DataSourceKind,
    ImportBatchStatus,
    MetricSource,
    Platform,
)
from app.orchestrator.checkpoint_graph_contracts import require_checkpoint_graph_contract
from app.orchestrator.runtime_scope import RuntimeScope
from app.schemas.run_revision import FreshnessStamp, StageDataEnvelope
from app.services import checkpoint_freshness
from app.services.checkpoint_freshness import (
    assess_checkpoint_freshness,
    get_freshness_validator,
    load_transaction_db_now,
    require_freshness_validator,
)


class _DatabaseStampValidator:
    key = "account-snapshot/v1"

    def __init__(self, stamp: FreshnessStamp) -> None:
        self.stamp = stamp
        self.calls = 0

    async def current_stamp(self, session, *, scope, step, input, db_now):
        self.calls += 1
        assert session is not None
        assert isinstance(scope, RuntimeScope)
        assert input.schema_version == step.input_schema_version
        return self.stamp


def _scope() -> RuntimeScope:
    return RuntimeScope(
        org_id=1,
        user_id=2,
        account_id=3,
        thread_id=4,
        turn_id=5,
        run_id=6,
        task_id=7,
        skill_run_id=8,
    )


async def test_immutable_step_needs_no_validator(session) -> None:
    step = require_checkpoint_graph_contract("operation_iteration", 1).steps[2]
    envelope = StageDataEnvelope(schema_version=step.input_schema_version, data={})

    verdict = await assess_checkpoint_freshness(
        session,
        scope=_scope(),
        step=step,
        input=envelope,
        source_stamp=None,
    )

    assert verdict.kind == "reusable"
    assert verdict.reason is None


async def test_missing_freshness_validator_fails_closed(session, monkeypatch) -> None:
    step = require_checkpoint_graph_contract("operation_iteration", 1).steps[0]
    envelope = StageDataEnvelope(schema_version=step.input_schema_version, data={})
    monkeypatch.setattr(checkpoint_freshness, "_VALIDATORS", {})

    verdict = await assess_checkpoint_freshness(
        session,
        scope=_scope(),
        step=step,
        input=envelope,
        source_stamp=None,
    )

    assert verdict.kind == "full_recompute"
    assert verdict.reason == "freshness_validator_missing"
    assert get_freshness_validator(step.freshness_policy_key or "") is None
    with pytest.raises(LookupError, match="freshness_validator_missing"):
        require_freshness_validator(step.freshness_policy_key or "")


@pytest.mark.parametrize(
    ("now_delta", "current_hash", "kind", "reason"),
    [
        (timedelta(0), "a" * 64, "reusable", None),
        (timedelta(microseconds=1), "a" * 64, "full_recompute", "freshness_expired"),
        (timedelta(0), "b" * 64, "full_recompute", "freshness_watermark_changed"),
    ],
)
async def test_freshness_uses_db_time_and_exact_watermark(
    session, monkeypatch, now_delta, current_hash, kind, reason
) -> None:
    step = require_checkpoint_graph_contract("operation_iteration", 1).steps[0]
    expiry = datetime(2026, 8, 4, 12, tzinfo=UTC)
    source = FreshnessStamp(
        policy_key=step.freshness_policy_key,
        watermark_hash="a" * 64,
        expires_at=expiry,
    )
    current = FreshnessStamp(
        policy_key=step.freshness_policy_key,
        watermark_hash=current_hash,
        expires_at=expiry + timedelta(hours=1),
    )
    validator = _DatabaseStampValidator(current)
    monkeypatch.setattr(checkpoint_freshness, "_VALIDATORS", {validator.key: validator})

    async def _db_now(_session):
        return expiry + now_delta

    monkeypatch.setattr(checkpoint_freshness, "load_transaction_db_now", _db_now)
    verdict = await assess_checkpoint_freshness(
        session,
        scope=_scope(),
        step=step,
        input=StageDataEnvelope(schema_version=step.input_schema_version, data={}),
        source_stamp=source,
    )

    assert verdict.kind == kind
    assert verdict.reason == reason
    assert validator.calls == 1


async def test_transaction_db_clock_is_loaded_from_database(session) -> None:
    db_now = await load_transaction_db_now(session)

    assert isinstance(db_now, datetime)
    assert db_now.tzinfo is not None


async def test_registered_validator_derives_watermark_only_from_database(session, admin) -> None:
    account = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="freshness-before",
        status=AccountStatus.ACTIVE,
    )
    session.add(account)
    await session.flush()
    step = require_checkpoint_graph_contract("operation_iteration", 1).steps[0]
    validator = require_freshness_validator(step.freshness_policy_key or "")
    scope = RuntimeScope(
        org_id=admin.org_id,
        user_id=admin.id,
        account_id=account.id,
        thread_id=1,
        turn_id=1,
        run_id=1,
        task_id=1,
        skill_run_id=1,
    )
    input_value = StageDataEnvelope(schema_version=step.input_schema_version, data={})
    db_now = await load_transaction_db_now(session)

    before = await validator.current_stamp(
        session, scope=scope, step=step, input=input_value, db_now=db_now
    )
    account.nickname = "freshness-after"
    await session.flush()
    after = await validator.current_stamp(
        session, scope=scope, step=step, input=input_value, db_now=db_now
    )

    assert before.policy_key == step.freshness_policy_key
    assert before.watermark_hash != after.watermark_hash


async def test_account_watermark_tracks_import_authority_and_versions(session, admin) -> None:
    account = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="freshness-authority",
        status=AccountStatus.ACTIVE,
    )
    session.add(account)
    await session.flush()
    step = require_checkpoint_graph_contract("operation_iteration", 1).steps[0]
    validator = require_freshness_validator(step.freshness_policy_key or "")
    scope = RuntimeScope(
        org_id=admin.org_id,
        user_id=admin.id,
        account_id=account.id,
        thread_id=1,
        turn_id=1,
        run_id=1,
        task_id=1,
        skill_run_id=1,
    )
    input_value = StageDataEnvelope(
        schema_version=step.input_schema_version,
        data={"query_window": {"start": "2026-07-01", "end": "2026-07-31"}},
    )
    db_now = await load_transaction_db_now(session)

    async def _watermark() -> str:
        stamp = await validator.current_stamp(
            session, scope=scope, step=step, input=input_value, db_now=db_now
        )
        return stamp.watermark_hash

    hashes = [await _watermark()]
    batch = DataImportBatch(
        org_id=admin.org_id,
        account_id=account.id,
        created_by_id=admin.id,
        source_kind=DataSourceKind.PLATFORM_EXPORT,
        status=ImportBatchStatus.PREVIEW_READY,
        template_code="douyin_work_list_v1",
        content_sha256="b" * 64,
        parser_version=1,
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 31),
    )
    session.add(batch)
    await session.flush()
    hashes.append(await _watermark())

    artifact = DataArtifact(
        org_id=admin.org_id,
        account_id=account.id,
        batch_id=batch.id,
        filename="works.xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        byte_size=1024,
        sha256="c" * 64,
        storage_key="account-data/works.xlsx",
    )
    session.add(artifact)
    await session.flush()
    hashes.append(await _watermark())

    observation = DataFieldObservation(
        org_id=admin.org_id,
        account_id=account.id,
        import_batch_id=batch.id,
        domain="content",
        entity_key="video-1",
        stat_date=date(2026, 7, 15),
        field_name="views",
        value={"number": 10},
        source_kind=DataSourceKind.PLATFORM_EXPORT,
        source_priority=10,
        confirmed_sequence=1,
        active=True,
    )
    session.add(observation)
    await session.flush()
    hashes.append(await _watermark())

    conflict = DataConflict(
        org_id=admin.org_id,
        account_id=account.id,
        batch_id=batch.id,
        row_number=1,
        status=ConflictStatus.OPEN,
        field_name="views",
        conflict_code="value_mismatch",
        message="Values differ",
    )
    session.add(conflict)
    await session.flush()
    hashes.append(await _watermark())

    batch.parser_version = 2
    await session.flush()
    hashes.append(await _watermark())

    assert len(set(hashes)) == len(hashes)


async def test_account_watermark_tracks_every_account_data_view_snapshot_source(
    session, admin
) -> None:
    account = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="freshness-view-sources",
        status=AccountStatus.ACTIVE,
    )
    session.add(account)
    await session.flush()
    batch = DataImportBatch(
        org_id=admin.org_id,
        account_id=account.id,
        created_by_id=admin.id,
        source_kind=DataSourceKind.PLATFORM_EXPORT,
        status=ImportBatchStatus.COMMITTED,
        template_code="douyin_work_list_v1",
        content_sha256="d" * 64,
        parser_version=2,
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 31),
        committed_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    session.add(batch)
    await session.flush()
    content = PlatformContentRecord(
        org_id=admin.org_id,
        account_id=account.id,
        platform=Platform.DOUYIN,
        source_kind=DataSourceKind.PLATFORM_EXPORT,
        canonical_import_batch_id=batch.id,
        canonical_import_row_number=1,
        external_content_id="video-1",
        title="Video 1",
        content_format="video",
        review_status="reviewed",
        identity_confidence=ContentIdentityConfidence.CONFIRMED,
    )
    session.add(content)
    await session.flush()
    step = require_checkpoint_graph_contract("operation_iteration", 1).steps[0]
    validator = require_freshness_validator(step.freshness_policy_key or "")
    scope = RuntimeScope(
        org_id=admin.org_id,
        user_id=admin.id,
        account_id=account.id,
        thread_id=1,
        turn_id=1,
        run_id=1,
        task_id=1,
        skill_run_id=1,
    )
    input_value = StageDataEnvelope(
        schema_version=step.input_schema_version,
        data={"query_window": {"start": "2026-07-01", "end": "2026-07-31"}},
    )
    db_now = await load_transaction_db_now(session)

    async def _stamp() -> FreshnessStamp:
        return await validator.current_stamp(
            session, scope=scope, step=step, input=input_value, db_now=db_now
        )

    async def _assert_full_fallback(previous: FreshnessStamp) -> FreshnessStamp:
        current = await _stamp()
        assert current.watermark_hash != previous.watermark_hash
        verdict = await assess_checkpoint_freshness(
            session,
            scope=scope,
            step=step,
            input=input_value,
            source_stamp=previous,
        )
        assert verdict.kind == "full_recompute"
        assert verdict.reason == "freshness_watermark_changed"
        return current

    previous = await _stamp()
    metric = MetricSnapshot(
        org_id=admin.org_id,
        account_id=account.id,
        import_batch_id=batch.id,
        platform_content_record_id=content.id,
        source=MetricSource.DOUYIN,
        stat_date=date(2026, 7, 15),
        title="Video 1",
        play=100,
        exposure=200,
        completion_rate=0.5,
        like_rate=0.1,
        comment_rate=0.02,
        share_rate=0.01,
        follower_delta=3,
    )
    session.add(metric)
    await session.flush()
    previous = await _assert_full_fallback(previous)

    account_snapshot = AccountMetricSnapshot(
        org_id=admin.org_id,
        account_id=account.id,
        import_batch_id=batch.id,
        source_kind=DataSourceKind.PLATFORM_EXPORT,
        stat_date=date(2026, 7, 15),
        follower_count=1200,
        total_play=9000,
    )
    session.add(account_snapshot)
    await session.flush()
    previous = await _assert_full_fallback(previous)

    audience = AudienceProfileSnapshot(
        org_id=admin.org_id,
        account_id=account.id,
        import_batch_id=batch.id,
        source_kind=DataSourceKind.PLATFORM_EXPORT,
        stat_date=date(2026, 7, 15),
        dimension="gender",
        total_audience=1000,
    )
    session.add(audience)
    await session.flush()
    previous = await _assert_full_fallback(previous)

    audience_item = AudienceProfileItem(
        org_id=admin.org_id,
        account_id=account.id,
        snapshot_id=audience.id,
        label="female",
        value="female",
        ratio=0.6,
        rank=1,
        meta={"source": "import"},
    )
    session.add(audience_item)
    await session.flush()
    previous = await _assert_full_fallback(previous)

    benchmark = BenchmarkSnapshot(
        org_id=admin.org_id,
        account_id=account.id,
        import_batch_id=batch.id,
        source_kind=DataSourceKind.PLATFORM_EXPORT,
        stat_date=date(2026, 7, 15),
        benchmark_code="track_median",
        metric_code="play",
        metric_value=80.0,
        sample_size=20,
        meta={"version": 1},
    )
    session.add(benchmark)
    await session.flush()
    previous = await _assert_full_fallback(previous)

    content.review_status = "needs_review"
    await session.flush()
    previous = await _assert_full_fallback(previous)

    metric.play = 101
    await session.flush()
    previous = await _assert_full_fallback(previous)
    account_snapshot.follower_count = 1201
    await session.flush()
    previous = await _assert_full_fallback(previous)
    audience.total_audience = 1001
    await session.flush()
    previous = await _assert_full_fallback(previous)
    audience_item.ratio = 0.61
    await session.flush()
    previous = await _assert_full_fallback(previous)
    benchmark.metric_value = 81.0
    await session.flush()
    await _assert_full_fallback(previous)
