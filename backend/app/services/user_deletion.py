"""Two-phase, transaction-protected permanent user deletion."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Never

import jwt
from fastapi import HTTPException, status
from sqlalchemy import Numeric, delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    AccountMembership,
    AdminSecurityCredential,
    AgentInvocation,
    AgentRun,
    AgentTask,
    AgentToolCall,
    BrainTask,
    ClientMembership,
    ComplianceCheck,
    ContentItem,
    ConversationThread,
    DataImportBatch,
    Deliverable,
    DeliverableAcceptance,
    Event,
    GateApproval,
    KnowledgeCitation,
    KnowledgeEntry,
    KnowledgeSuggestion,
    LLMCall,
    MaterialAsset,
    MatrixDistributionItem,
    MatrixDistributionPlan,
    MetricSnapshot,
    Notification,
    OptimizationSuggestion,
    OrchestrationPlan,
    Project,
    ProjectMembership,
    RunRevision,
    SkillRun,
    TaskBrief,
    ToolExecutionAttempt,
    User,
    UserDeletionPreviewReservation,
)
from app.models.enums import UserRole
from app.services.admin_security import verify_secondary_password
from app.services.runtime_locking import (
    RuntimeLockConflict,
    RuntimeRootLock,
    lock_runtime_root_forest,
)

PREVIEW_TTL_MINUTES = 5
PREVIEW_PURPOSE = "user_deletion_preview"
RECEIPT_EVENT_TYPE = "user.permanently_deleted"
SECONDARY_AUTH_EVENT_TYPE = "user.deletion_secondary_password_auth"
RESERVATION_UNIQUE_CONSTRAINT = (
    "uq_user_deletion_preview_reservations_org_operation"
)

SELF_DELETION_CODE = "USER_SELF_DELETION_FORBIDDEN"
LAST_ADMIN_CODE = "LAST_ACTIVE_ADMIN"

_USER_REFERENCE_KEYS = {
    "actor_id",
    "actor_user_id",
    "target_id",
    "target_user_id",
    "creator_id",
    "creator_user_id",
    "created_by_id",
    "reviewer_id",
    "reviewer_user_id",
    "reviewed_by_id",
    "approver_id",
    "approver_user_id",
    "approved_by_id",
    "decided_by",
}


@dataclass(frozen=True)
class DeletionImpact:
    counts: dict[str, int]
    version_digest: str
    blockers: tuple[str, ...]
    manifest: dict[str, tuple[tuple[int, str | None], ...]]

    @property
    def record_ids(self) -> dict[str, tuple[int, ...]]:
        return {
            category: tuple(record_id for record_id, _version in rows)
            for category, rows in self.manifest.items()
        }


@dataclass(frozen=True)
class DeletionPreviewClaims:
    actor_id: int
    target_user_id: int
    organization_id: int
    operation_id: str
    impact_hash: str


@dataclass(frozen=True)
class DeletionReceipt:
    operation_id: str
    deleted_at: datetime
    counts: dict[str, int]


@dataclass(frozen=True)
class RuntimeDeletionFootprint:
    run_ids: tuple[int, ...]
    skill_ids: tuple[int, ...]
    revision_ids: tuple[int, ...]
    deliverable_ids: tuple[int, ...]
    invocation_ids: tuple[int, ...]
    tool_call_ids: tuple[int, ...]
    attempt_ids: tuple[int, ...]
    extra_deliverable_ids: tuple[int, ...]


def _business_error(http_status: int, code: str, message: str) -> Never:
    raise HTTPException(
        status_code=http_status,
        detail={"code": code, "message": message},
    )


def _version_text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return normalized.isoformat()
    return str(value)


def _row_fingerprint(row) -> str:
    def canonical(value, *, numeric: bool = False):
        if numeric and value is not None:
            return format(Decimal(str(value)).normalize(), "f")
        if isinstance(value, Decimal):
            return format(value.normalize(), "f")
        if isinstance(value, datetime):
            return _version_text(value)
        if isinstance(value, dict):
            return {key: canonical(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [canonical(item) for item in value]
        return value

    values = {
        column.name: canonical(
            getattr(row, column.name), numeric=isinstance(column.type, Numeric)
        )
        for column in row.__table__.columns
        if column.name != "id"
    }
    serialized = json.dumps(
        values,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


async def _snapshot(session: AsyncSession, model, condition) -> tuple[tuple[int, str | None], ...]:
    rows = list(
        await session.scalars(select(model).where(condition).order_by(model.id))
    )
    return tuple((row.id, _row_fingerprint(row)) for row in rows)


def _ids(manifest: dict[str, tuple[tuple[int, str | None], ...]], category: str) -> tuple[int, ...]:
    return tuple(record_id for record_id, _version in manifest.get(category, ()))


def _id_condition(column, values: tuple[int, ...]):
    return column.in_(values) if values else column.in_([])


def _same_identifier(value, expected: int) -> bool:
    return not isinstance(value, bool) and str(value) == str(expected)


def _payload_references_user(payload, user_id: int) -> bool:
    if not isinstance(payload, dict):
        return False
    for key, value in payload.items():
        if key in _USER_REFERENCE_KEYS and _same_identifier(value, user_id):
            return True
        if isinstance(value, dict) and _payload_references_user(value, user_id):
            return True
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and _payload_references_user(item, user_id):
                    return True
    return False


def _payload_references_owned_root(
    payload,
    *,
    brain_task_ids: set[int],
    matrix_plan_ids: set[int],
    knowledge_entry_ids: set[int],
    knowledge_suggestion_ids: set[int],
) -> bool:
    if not isinstance(payload, dict):
        return False
    for key, value in payload.items():
        if key in {"task_id", "source_task_id"} and any(
            _same_identifier(value, item_id) for item_id in brain_task_ids
        ):
            return True
        if key in {"matrix_plan_id", "plan_id"} and any(
            _same_identifier(value, item_id) for item_id in matrix_plan_ids
        ):
            return True
        if key in {"entry_id", "knowledge_entry_id"} and any(
            _same_identifier(value, item_id) for item_id in knowledge_entry_ids
        ):
            return True
        if key == "suggestion_id" and any(
            _same_identifier(value, item_id) for item_id in knowledge_suggestion_ids
        ):
            return True
        if (
            key == "source_id"
            and payload.get("approval_kind") == "matrix_plan"
            and any(_same_identifier(value, item_id) for item_id in matrix_plan_ids)
        ):
            return True
        if isinstance(value, dict) and _payload_references_owned_root(
            value,
            brain_task_ids=brain_task_ids,
            matrix_plan_ids=matrix_plan_ids,
            knowledge_entry_ids=knowledge_entry_ids,
            knowledge_suggestion_ids=knowledge_suggestion_ids,
        ):
            return True
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and _payload_references_owned_root(
                    item,
                    brain_task_ids=brain_task_ids,
                    matrix_plan_ids=matrix_plan_ids,
                    knowledge_entry_ids=knowledge_entry_ids,
                    knowledge_suggestion_ids=knowledge_suggestion_ids,
                ):
                    return True
    return False


async def _deletion_blockers(
    session: AsyncSession, actor: User, target: User
) -> tuple[str, ...]:
    blockers: list[str] = []
    if target.role == UserRole.ADMIN and target.is_active:
        active_admin_ids = tuple(
            await session.scalars(
                select(User.id).where(
                    User.org_id == target.org_id,
                    User.role == UserRole.ADMIN,
                    User.is_active.is_(True),
                )
            )
        )
        if len(active_admin_ids) <= 1:
            blockers.append(LAST_ADMIN_CODE)
    if target.id == actor.id:
        blockers.append(SELF_DELETION_CODE)
    return tuple(blockers)


async def build_deletion_impact(
    session: AsyncSession,
    *,
    actor: User,
    target: User,
) -> DeletionImpact:
    """Build the exact explicit-ownership deletion manifest and version digest."""

    manifest: dict[str, tuple[tuple[int, str | None], ...]] = {}
    manifest["users"] = await _snapshot(session, User, User.id == target.id)
    manifest["brain_tasks"] = await _snapshot(
        session, BrainTask, BrainTask.created_by_id == target.id
    )
    manifest["content_items"] = await _snapshot(
        session, ContentItem, ContentItem.created_by_id == target.id
    )
    manifest["matrix_distribution_plans"] = await _snapshot(
        session,
        MatrixDistributionPlan,
        MatrixDistributionPlan.created_by_id == target.id,
    )
    manifest["knowledge_entries"] = await _snapshot(
        session, KnowledgeEntry, KnowledgeEntry.created_by_id == target.id
    )
    manifest["llm_calls"] = await _snapshot(
        session, LLMCall, LLMCall.created_by_id == target.id
    )

    task_ids = _ids(manifest, "brain_tasks")
    content_ids = _ids(manifest, "content_items")
    plan_ids = _ids(manifest, "matrix_distribution_plans")
    entry_ids = _ids(manifest, "knowledge_entries")

    manifest["task_briefs"] = await _snapshot(
        session, TaskBrief, _id_condition(TaskBrief.task_id, task_ids)
    )
    manifest["orchestration_plans"] = await _snapshot(
        session, OrchestrationPlan, _id_condition(OrchestrationPlan.task_id, task_ids)
    )
    manifest["agent_invocations"] = await _snapshot(
        session, AgentInvocation, _id_condition(AgentInvocation.task_id, task_ids)
    )
    invocation_ids = _ids(manifest, "agent_invocations")
    manifest["agent_tool_calls"] = await _snapshot(
        session, AgentToolCall, _id_condition(AgentToolCall.task_id, task_ids)
    )
    manifest["deliverable_acceptances"] = await _snapshot(
        session,
        DeliverableAcceptance,
        _id_condition(DeliverableAcceptance.task_id, task_ids),
    )

    manifest["deliverables"] = await _snapshot(
        session, Deliverable, _id_condition(Deliverable.content_item_id, content_ids)
    )
    deliverable_ids = _ids(manifest, "deliverables")
    if deliverable_ids:
        linked_acceptances = await _snapshot(
            session,
            DeliverableAcceptance,
            _id_condition(DeliverableAcceptance.deliverable_id, deliverable_ids),
        )
        manifest["deliverable_acceptances"] = tuple(
            sorted(set(manifest["deliverable_acceptances"]) | set(linked_acceptances))
        )
    manifest["agent_tasks"] = await _snapshot(
        session, AgentTask, _id_condition(AgentTask.content_item_id, content_ids)
    )
    manifest["gate_approvals"] = await _snapshot(
        session,
        GateApproval,
        or_(
            _id_condition(GateApproval.content_item_id, content_ids),
            GateApproval.decided_by == target.id,
        ),
    )
    manifest["compliance_checks"] = await _snapshot(
        session,
        ComplianceCheck,
        _id_condition(ComplianceCheck.content_item_id, content_ids),
    )
    manifest["material_assets"] = await _snapshot(
        session, MaterialAsset, _id_condition(MaterialAsset.content_item_id, content_ids)
    )
    material_ids = _ids(manifest, "material_assets")
    manifest["optimization_suggestions"] = await _snapshot(
        session,
        OptimizationSuggestion,
        _id_condition(OptimizationSuggestion.content_item_id, content_ids),
    )
    manifest["metric_snapshots"] = await _snapshot(
        session, MetricSnapshot, _id_condition(MetricSnapshot.content_item_id, content_ids)
    )
    manifest["matrix_distribution_items"] = await _snapshot(
        session,
        MatrixDistributionItem,
        or_(
            _id_condition(MatrixDistributionItem.plan_id, plan_ids),
            _id_condition(MatrixDistributionItem.material_id, material_ids),
        ),
    )
    manifest["knowledge_citations"] = await _snapshot(
        session,
        KnowledgeCitation,
        or_(
            _id_condition(KnowledgeCitation.entry_id, entry_ids),
            _id_condition(KnowledgeCitation.task_id, task_ids),
            _id_condition(KnowledgeCitation.invocation_id, invocation_ids),
        ),
    )
    manifest["knowledge_suggestions"] = await _snapshot(
        session,
        KnowledgeSuggestion,
        or_(
            _id_condition(KnowledgeSuggestion.source_task_id, task_ids),
            _id_condition(KnowledgeSuggestion.source_deliverable_id, deliverable_ids),
        ),
    )
    suggestion_ids = set(_ids(manifest, "knowledge_suggestions"))
    manifest["knowledge_reviews_redacted"] = tuple(
        row
        for row in await _snapshot(
            session,
            KnowledgeSuggestion,
            KnowledgeSuggestion.reviewed_by_id == target.id,
        )
        if row[0] not in suggestion_ids
    )

    manifest["client_memberships"] = await _snapshot(
        session, ClientMembership, ClientMembership.user_id == target.id
    )
    manifest["project_memberships"] = await _snapshot(
        session, ProjectMembership, ProjectMembership.user_id == target.id
    )
    manifest["account_memberships"] = await _snapshot(
        session, AccountMembership, AccountMembership.user_id == target.id
    )
    manifest["notifications"] = await _snapshot(
        session, Notification, Notification.user_id == target.id
    )
    manifest["admin_security_credentials"] = await _snapshot(
        session,
        AdminSecurityCredential,
        AdminSecurityCredential.user_id == target.id,
    )
    manifest["data_import_batches_created_by_redacted"] = await _snapshot(
        session,
        DataImportBatch,
        DataImportBatch.created_by_id == target.id,
    )

    owned_content_ids = set(content_ids)
    owned_task_ids = set(task_ids)
    owned_plan_ids = set(plan_ids)
    owned_entry_ids = set(entry_ids)
    owned_suggestion_ids = suggestion_ids
    matching_events = []
    event_rows = await session.stream_scalars(
        select(Event).order_by(Event.id).execution_options(yield_per=500)
    )
    async for event_row in event_rows:
        if (
            event_row.content_item_id in owned_content_ids
            or _payload_references_user(event_row.payload, target.id)
            or _payload_references_owned_root(
                event_row.payload,
                brain_task_ids=owned_task_ids,
                matrix_plan_ids=owned_plan_ids,
                knowledge_entry_ids=owned_entry_ids,
                knowledge_suggestion_ids=owned_suggestion_ids,
            )
        ):
            matching_events.append(
                (event_row.id, _row_fingerprint(event_row))
            )
    manifest["events"] = tuple(matching_events)

    counts = {category: len(rows) for category, rows in manifest.items()}
    counts["cost_records"] = (
        counts["llm_calls"]
        + counts["agent_invocations"]
        + counts["agent_tool_calls"]
    )
    digest_source = {
        "counts": counts,
        "records": manifest,
        "target": {
            "id": target.id,
            "updated_at": _version_text(target.updated_at),
        },
    }
    version_digest = hashlib.sha256(
        json.dumps(
            digest_source,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()
    return DeletionImpact(
        counts=counts,
        version_digest=version_digest,
        blockers=await _deletion_blockers(session, actor, target),
        manifest=manifest,
    )


def issue_deletion_preview_token(
    *,
    actor: User,
    target: User,
    operation_id: str,
    impact: DeletionImpact,
    now: datetime | None = None,
) -> tuple[str, datetime]:
    issued_at = now or datetime.now(UTC)
    expires_at = issued_at + timedelta(minutes=PREVIEW_TTL_MINUTES)
    payload = {
        "purpose": PREVIEW_PURPOSE,
        "actor_id": actor.id,
        "target_user_id": target.id,
        "organization_id": actor.org_id,
        "operation_id": operation_id,
        "impact_hash": impact.version_digest,
        "iat": issued_at,
        "exp": expires_at,
    }
    return (
        jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm),
        expires_at,
    )


def _decode_preview_token(token: str) -> DeletionPreviewClaims:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={
                "require": [
                    "purpose",
                    "actor_id",
                    "target_user_id",
                    "organization_id",
                    "operation_id",
                    "impact_hash",
                    "iat",
                    "exp",
                ]
            },
        )
    except jwt.ExpiredSignatureError:
        _business_error(
            status.HTTP_409_CONFLICT,
            "USER_DELETION_PREVIEW_EXPIRED",
            "Deletion preview has expired; create a new preview",
        )
    except jwt.PyJWTError:
        _business_error(
            status.HTTP_409_CONFLICT,
            "USER_DELETION_PREVIEW_INVALID",
            "Deletion preview is invalid",
        )
    try:
        if payload["purpose"] != PREVIEW_PURPOSE:
            raise ValueError
        return DeletionPreviewClaims(
            actor_id=int(payload["actor_id"]),
            target_user_id=int(payload["target_user_id"]),
            organization_id=int(payload["organization_id"]),
            operation_id=str(payload["operation_id"]),
            impact_hash=str(payload["impact_hash"]),
        )
    except (KeyError, TypeError, ValueError):
        _business_error(
            status.HTTP_409_CONFLICT,
            "USER_DELETION_PREVIEW_INVALID",
            "Deletion preview is invalid",
        )


async def _preview_was_used(
    session: AsyncSession,
    *,
    organization_id: int,
    operation_id: str,
    actor_id: int,
) -> bool:
    reservation_id = await session.scalar(
        select(UserDeletionPreviewReservation.id).where(
            UserDeletionPreviewReservation.organization_id == organization_id,
            UserDeletionPreviewReservation.operation_id == operation_id,
        )
    )
    if reservation_id is not None:
        return True
    receipts = list(
        await session.scalars(
            select(Event).where(Event.type == RECEIPT_EVENT_TYPE).order_by(Event.id)
        )
    )
    return any(
        isinstance(receipt.payload, dict)
        and receipt.payload.get("operation_id") == operation_id
        and receipt.payload.get("actor_id") == actor_id
        for receipt in receipts
    )


async def _reserve_preview(
    session: AsyncSession,
    *,
    organization_id: int,
    operation_id: str,
) -> None:
    values = {
        "organization_id": organization_id,
        "operation_id": operation_id,
    }
    dialect_name = session.get_bind().dialect.name
    if dialect_name == "postgresql":
        statement = (
            postgresql_insert(UserDeletionPreviewReservation)
            .values(**values)
            .on_conflict_do_nothing(constraint=RESERVATION_UNIQUE_CONSTRAINT)
            .returning(UserDeletionPreviewReservation.id)
        )
    elif dialect_name == "sqlite":
        statement = (
            sqlite_insert(UserDeletionPreviewReservation)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=[
                    UserDeletionPreviewReservation.organization_id,
                    UserDeletionPreviewReservation.operation_id,
                ]
            )
            .returning(UserDeletionPreviewReservation.id)
        )
    else:
        raise RuntimeError(
            f"Unsupported deletion reservation dialect: {dialect_name}"
        )

    with session.no_autoflush:
        reservation_id = await session.scalar(statement)
    if reservation_id is None:
        _business_error(
            status.HTTP_409_CONFLICT,
            "USER_DELETION_PREVIEW_USED",
            "Deletion preview has already been used",
        )


def _raise_blocker(blockers: tuple[str, ...]) -> None:
    if LAST_ADMIN_CODE in blockers:
        _business_error(
            status.HTTP_409_CONFLICT,
            LAST_ADMIN_CODE,
            "The organization must retain an active administrator",
        )
    if SELF_DELETION_CODE in blockers:
        _business_error(
            status.HTTP_409_CONFLICT,
            SELF_DELETION_CODE,
            "Administrators cannot permanently delete themselves",
        )


def _map_secondary_password_error(exc: HTTPException) -> Never:
    mapping = {
        "Secondary password is not configured": "SECONDARY_PASSWORD_NOT_CONFIGURED",
        "Secondary password cooldown is active": "SECONDARY_PASSWORD_COOLDOWN",
        "Secondary password is temporarily locked": "SECONDARY_PASSWORD_LOCKED",
        "Invalid secondary password": "SECONDARY_PASSWORD_INVALID",
    }
    code = mapping.get(str(exc.detail), "SECONDARY_PASSWORD_INVALID")
    _business_error(exc.status_code, code, str(exc.detail))


def _secondary_password_error_code(exc: HTTPException) -> str:
    return {
        "Secondary password is not configured": "SECONDARY_PASSWORD_NOT_CONFIGURED",
        "Secondary password cooldown is active": "SECONDARY_PASSWORD_COOLDOWN",
        "Secondary password is temporarily locked": "SECONDARY_PASSWORD_LOCKED",
        "Invalid secondary password": "SECONDARY_PASSWORD_INVALID",
    }.get(str(exc.detail), "SECONDARY_PASSWORD_INVALID")


async def _authenticate_deletion_request(
    session: AsyncSession,
    *,
    actor_id: int,
    actor_org_id: int,
    target_user_id: int,
    operation_id: str,
    secondary_password: str,
) -> None:
    """Verify and audit in a transaction independent of runtime deletion."""

    auth_actor = await session.scalar(
        select(User).where(User.id == actor_id, User.org_id == actor_org_id)
    )
    if auth_actor is None:
        _business_error(
            status.HTTP_409_CONFLICT,
            "USER_DELETION_PREVIEW_INVALID",
            "Deletion preview does not match this operation",
        )
    try:
        await verify_secondary_password(
            session,
            auth_actor,
            secondary_password,
            commit_on_success=False,
        )
    except HTTPException as exc:
        # Invalid-password counters are committed by the verifier. Other
        # authentication failures may leave a read transaction open.
        if session.in_transaction():
            await session.rollback()
        session.add(
            Event(
                type=SECONDARY_AUTH_EVENT_TYPE,
                org_id=actor_org_id,
                payload={
                    "actor_id": actor_id,
                    "deletion_subject_id": target_user_id,
                    "operation_id": operation_id,
                    "outcome": "failure",
                    "error_code": _secondary_password_error_code(exc),
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            )
        )
        await session.commit()
        _map_secondary_password_error(exc)

    session.add(
        Event(
            type=SECONDARY_AUTH_EVENT_TYPE,
            org_id=actor_org_id,
            payload={
                "actor_id": actor_id,
                "deletion_subject_id": target_user_id,
                "operation_id": operation_id,
                "outcome": "success",
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )
    )
    await session.commit()


async def _discover_runtime_deletion_footprint(
    session: AsyncSession,
    *,
    impact: DeletionImpact,
    target_user_id: int,
    organization_id: int,
) -> RuntimeDeletionFootprint:
    """Build the Run-root closure of every runtime row affected by deletion."""

    ids = impact.record_ids
    task_ids = ids["brain_tasks"]
    impacted_deliverable_ids = ids["deliverables"]
    impacted_invocation_ids = ids["agent_invocations"]
    impacted_tool_ids = ids["agent_tool_calls"]
    with session.no_autoflush:
        impacted_deliverables = list(
            await session.scalars(
                select(Deliverable)
                .where(Deliverable.id.in_(impacted_deliverable_ids))
                .order_by(Deliverable.id)
            )
        )
        impacted_invocations = list(
            await session.scalars(
                select(AgentInvocation)
                .where(AgentInvocation.id.in_(impacted_invocation_ids))
                .order_by(AgentInvocation.id)
            )
        )
        impacted_tools = list(
            await session.scalars(
                select(AgentToolCall)
                .where(AgentToolCall.id.in_(impacted_tool_ids))
                .order_by(AgentToolCall.id)
            )
        )
        referenced_skill_ids = tuple(
            sorted(
                {
                    row.skill_run_id
                    for row in impacted_deliverables
                    if row.skill_run_id is not None
                }
                | {
                    row.skill_run_id
                    for row in impacted_invocations
                    if row.skill_run_id is not None
                }
                | {
                    row.skill_run_id
                    for row in impacted_tools
                    if row.skill_run_id is not None
                }
            )
        )
        referenced_invocation_ids = tuple(
            sorted(
                {
                    row.invocation_id
                    for row in impacted_tools
                    if row.invocation_id is not None
                }
            )
        )
        referenced_skills = list(
            await session.scalars(
                select(SkillRun)
                .where(SkillRun.id.in_(referenced_skill_ids))
                .order_by(SkillRun.id)
            )
        )
        referenced_invocations = list(
            await session.scalars(
                select(AgentInvocation)
                .where(AgentInvocation.id.in_(referenced_invocation_ids))
                .order_by(AgentInvocation.id)
            )
        )
        referenced_skill_by_id = {row.id: row for row in referenced_skills}
        referenced_invocation_by_id = {row.id: row for row in referenced_invocations}
        for deliverable in impacted_deliverables:
            if deliverable.skill_run_id is None:
                continue
            skill = referenced_skill_by_id.get(deliverable.skill_run_id)
            if skill is None or deliverable.run_id != skill.run_id:
                raise RuntimeLockConflict(
                    "runtime deletion Deliverable SkillRun lineage mismatch"
                )
        for invocation in impacted_invocations:
            if invocation.skill_run_id is None:
                continue
            skill = referenced_skill_by_id.get(invocation.skill_run_id)
            if skill is None or invocation.run_id != skill.run_id:
                raise RuntimeLockConflict(
                    "runtime deletion AgentInvocation SkillRun lineage mismatch"
                )
        for tool in impacted_tools:
            linked_run_ids: set[int] = set()
            linked_skill = (
                referenced_skill_by_id.get(tool.skill_run_id)
                if tool.skill_run_id is not None
                else None
            )
            linked_invocation = (
                referenced_invocation_by_id.get(tool.invocation_id)
                if tool.invocation_id is not None
                else None
            )
            if linked_skill is not None:
                linked_run_ids.add(linked_skill.run_id)
            if linked_invocation is not None and linked_invocation.run_id is not None:
                linked_run_ids.add(linked_invocation.run_id)
            if (
                (tool.skill_run_id is not None or tool.invocation_id is not None)
                and len(linked_run_ids) != 1
            ):
                raise RuntimeLockConflict(
                    "runtime deletion AgentToolCall lineage mismatch"
                )
        seed_run_ids = (
            {row.run_id for row in impacted_deliverables if row.run_id is not None}
            | {row.run_id for row in impacted_invocations if row.run_id is not None}
            | {row.run_id for row in referenced_skills if row.run_id is not None}
            | {row.run_id for row in referenced_invocations if row.run_id is not None}
        )
        seed_run_ids.update(
            await session.scalars(
                select(AgentRun.id).where(
                    or_(
                        AgentRun.task_id.in_(task_ids),
                        AgentRun.requested_by_id == target_user_id,
                    )
                )
            )
        )
        revision_links: list[RunRevision] = []
        while True:
            revision_links = list(
                await session.scalars(
                    select(RunRevision)
                    .where(
                        (RunRevision.source_run_id.in_(seed_run_ids))
                        | (RunRevision.revision_run_id.in_(seed_run_ids))
                    )
                    .order_by(RunRevision.id)
                )
            )
            expanded_run_ids = seed_run_ids | {
                endpoint
                for row in revision_links
                for endpoint in (row.source_run_id, row.revision_run_id)
            }
            if expanded_run_ids == seed_run_ids:
                break
            seed_run_ids = expanded_run_ids
        run_ids = tuple(sorted(seed_run_ids))
        runs = list(
            await session.scalars(
                select(AgentRun).where(AgentRun.id.in_(run_ids)).order_by(AgentRun.id)
            )
        )
        if len(runs) != len(run_ids):
            raise RuntimeLockConflict("runtime deletion AgentRun set changed")
        if any(run.org_id != organization_id for run in runs):
            raise RuntimeLockConflict("runtime deletion crosses organization boundary")

        thread_ids = tuple(sorted({run.thread_id for run in runs if run.thread_id is not None}))
        threads = {
            row.id: row
            for row in await session.scalars(
                select(ConversationThread)
                .where(ConversationThread.id.in_(thread_ids))
                .order_by(ConversationThread.id)
            )
        }
        if len(threads) != len(thread_ids) or any(
            thread.org_id != organization_id for thread in threads.values()
        ):
            raise RuntimeLockConflict("runtime deletion Thread boundary mismatch")
        run_by_id = {run.id: run for run in runs}
        content_ids = tuple(
            sorted({row.content_item_id for row in impacted_deliverables if row.run_id is not None})
        )
        contents = {
            row.id: row
            for row in await session.scalars(
                select(ContentItem)
                .where(ContentItem.id.in_(content_ids))
                .order_by(ContentItem.id)
            )
        }
        project_ids = tuple(
            sorted(
                {
                    row.project_id
                    for row in contents.values()
                    if row.project_id is not None
                }
            )
        )
        projects = {
            row.id: row
            for row in await session.scalars(
                select(Project)
                .where(Project.id.in_(project_ids))
                .order_by(Project.id)
            )
        }
        if len(contents) != len(content_ids) or any(
            content.project_id is None
            or projects.get(content.project_id) is None
            or projects[content.project_id].org_id != organization_id
            for content in contents.values()
        ):
            raise RuntimeLockConflict("runtime deletion Content boundary mismatch")
        for deliverable in impacted_deliverables:
            if deliverable.run_id is None:
                continue
            run = run_by_id.get(deliverable.run_id)
            content = contents.get(deliverable.content_item_id)
            if run is None or content is None:
                raise RuntimeLockConflict("runtime deletion Deliverable lineage mismatch")
            thread = threads.get(run.thread_id) if run.thread_id is not None else None
            if (
                thread is not None
                and content.account_id is not None
                and content.account_id != thread.account_id
            ):
                raise RuntimeLockConflict("runtime deletion crosses account boundary")

        skills = list(
            await session.scalars(
                select(SkillRun).where(SkillRun.run_id.in_(run_ids)).order_by(SkillRun.id)
            )
        )
        revisions = tuple(row.id for row in revision_links)
        deliverables = list(
            await session.scalars(
                select(Deliverable).where(Deliverable.run_id.in_(run_ids)).order_by(Deliverable.id)
            )
        )
        invocations = list(
            await session.scalars(
                select(AgentInvocation)
                .where(AgentInvocation.run_id.in_(run_ids))
                .order_by(AgentInvocation.id)
            )
        )
        skill_ids = tuple(row.id for row in skills)
        invocation_ids = tuple(row.id for row in invocations)
        tools = list(
            await session.scalars(
                select(AgentToolCall)
                .where(
                    or_(
                        AgentToolCall.skill_run_id.in_(skill_ids),
                        AgentToolCall.invocation_id.in_(invocation_ids),
                    )
                )
                .order_by(AgentToolCall.id)
            )
        )
        tool_ids = tuple(row.id for row in tools)
        attempt_ids = tuple(
            await session.scalars(
                select(ToolExecutionAttempt.id)
                .where(ToolExecutionAttempt.tool_call_id.in_(tool_ids))
                .order_by(ToolExecutionAttempt.id)
            )
        )
    impacted_deliverable_id_set = set(impacted_deliverable_ids)
    return RuntimeDeletionFootprint(
        run_ids=run_ids,
        skill_ids=skill_ids,
        revision_ids=revisions,
        deliverable_ids=tuple(row.id for row in deliverables),
        invocation_ids=invocation_ids,
        tool_call_ids=tool_ids,
        attempt_ids=attempt_ids,
        extra_deliverable_ids=tuple(
            row.id for row in deliverables if row.id in impacted_deliverable_id_set
        ),
    )


def _validate_runtime_deletion_forest(
    footprint: RuntimeDeletionFootprint,
    tokens: tuple[RuntimeRootLock, ...],
) -> None:
    if tuple(token.run_id for token in tokens) != footprint.run_ids:
        raise RuntimeLockConflict("runtime deletion forest roots changed")
    locked_skill_ids = {
        skill_id
        for token in tokens
        for skill_id in (token.root_skill_run_id, *token.child_skill_run_ids)
        if skill_id is not None
    }
    # Keep the direction explicit for every family: the post-lock affected set
    # must be fully contained in the acquired forest proof.
    locked_sets = {
        "RunRevision": {row for token in tokens for row in token.run_revision_ids},
        "Deliverable": {row for token in tokens for row in token.deliverable_ids},
        "AgentInvocation": {row for token in tokens for row in token.invocation_ids},
        "AgentToolCall": {row for token in tokens for row in token.tool_call_ids},
        "ToolExecutionAttempt": {row for token in tokens for row in token.attempt_ids},
    }
    affected_sets = {
        "RunRevision": set(footprint.revision_ids),
        "Deliverable": set(footprint.deliverable_ids),
        "AgentInvocation": set(footprint.invocation_ids),
        "AgentToolCall": set(footprint.tool_call_ids),
        "ToolExecutionAttempt": set(footprint.attempt_ids),
    }
    if not set(footprint.skill_ids).issubset(locked_skill_ids):
        raise RuntimeLockConflict("runtime deletion SkillRun family changed")
    for label, affected in affected_sets.items():
        if not affected.issubset(locked_sets[label]):
            raise RuntimeLockConflict(f"runtime deletion {label} family changed")


async def _delete_ids(
    session: AsyncSession,
    model,
    record_ids: tuple[int, ...],
) -> None:
    if record_ids:
        await session.execute(
            delete(model)
            .where(model.id.in_(record_ids))
            .execution_options(synchronize_session=False)
        )


async def _delete_owned_records(session: AsyncSession, impact: DeletionImpact) -> None:
    ids = impact.record_ids
    delete_order = (
        (Event, "events"),
        (KnowledgeCitation, "knowledge_citations"),
        (KnowledgeSuggestion, "knowledge_suggestions"),
        (MatrixDistributionItem, "matrix_distribution_items"),
        (DeliverableAcceptance, "deliverable_acceptances"),
        (AgentToolCall, "agent_tool_calls"),
        (AgentInvocation, "agent_invocations"),
        (OrchestrationPlan, "orchestration_plans"),
        (TaskBrief, "task_briefs"),
        (GateApproval, "gate_approvals"),
        (ComplianceCheck, "compliance_checks"),
        (AgentTask, "agent_tasks"),
        (OptimizationSuggestion, "optimization_suggestions"),
        (MetricSnapshot, "metric_snapshots"),
        (MaterialAsset, "material_assets"),
        (Deliverable, "deliverables"),
        (MatrixDistributionPlan, "matrix_distribution_plans"),
        (KnowledgeEntry, "knowledge_entries"),
        (BrainTask, "brain_tasks"),
        (ContentItem, "content_items"),
        (LLMCall, "llm_calls"),
        (ClientMembership, "client_memberships"),
        (ProjectMembership, "project_memberships"),
        (AccountMembership, "account_memberships"),
        (Notification, "notifications"),
        (AdminSecurityCredential, "admin_security_credentials"),
    )
    for model, category in delete_order:
        await _delete_ids(session, model, ids[category])

    review_ids = ids["knowledge_reviews_redacted"]
    if review_ids:
        await session.execute(
            update(KnowledgeSuggestion)
            .where(KnowledgeSuggestion.id.in_(review_ids))
            .values(reviewed_by_id=None, reviewed_at=None, review_note=None)
            .execution_options(synchronize_session=False)
        )
    redacted_batch_ids = ids["data_import_batches_created_by_redacted"]
    if redacted_batch_ids:
        await session.execute(
            update(DataImportBatch)
            .where(DataImportBatch.id.in_(redacted_batch_ids))
            .values(created_by_id=None)
            .execution_options(synchronize_session=False)
        )
    await _delete_ids(session, User, ids["users"])


async def execute_permanent_deletion(
    session: AsyncSession,
    *,
    actor: User,
    target_user_id: int,
    preview_token: str,
    secondary_password: str,
) -> DeletionReceipt:
    actor_id = actor.id
    actor_org_id = actor.org_id
    claims = _decode_preview_token(preview_token)
    if (
        claims.actor_id != actor_id
        or claims.target_user_id != target_user_id
        or claims.organization_id != actor_org_id
    ):
        _business_error(
            status.HTTP_409_CONFLICT,
            "USER_DELETION_PREVIEW_INVALID",
            "Deletion preview does not match this operation",
        )
    if await _preview_was_used(
        session,
        organization_id=actor_org_id,
        operation_id=claims.operation_id,
        actor_id=actor_id,
    ):
        _business_error(
            status.HTTP_409_CONFLICT,
            "USER_DELETION_PREVIEW_USED",
            "Deletion preview has already been used",
        )

    target = await session.scalar(
        select(User).where(User.id == target_user_id, User.org_id == actor_org_id)
    )
    if target is None:
        _business_error(
            status.HTTP_409_CONFLICT,
            "USER_DELETION_PREVIEW_STALE",
            "Deletion target changed; create a new preview",
        )
    impact = await build_deletion_impact(session, actor=actor, target=target)
    _raise_blocker(impact.blockers)
    if impact.version_digest != claims.impact_hash:
        _business_error(
            status.HTTP_409_CONFLICT,
            "USER_DELETION_PREVIEW_STALE",
            "Deletion impact changed; create a new preview",
        )

    # End all preview/discovery reads before password hashing. Authentication
    # and its durable audit form their own transaction, independent of runtime
    # locks and the later destructive transaction.
    await session.rollback()
    try:
        await _authenticate_deletion_request(
            session,
            actor_id=actor_id,
            actor_org_id=actor_org_id,
            target_user_id=target_user_id,
            operation_id=claims.operation_id,
            secondary_password=secondary_password,
        )
    except HTTPException:
        raise
    except Exception:
        await session.rollback()
        _business_error(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "USER_DELETION_TRANSACTION_FAILED",
            "Permanent deletion authentication failed safely",
        )

    try:
        if await _preview_was_used(
            session,
            organization_id=actor_org_id,
            operation_id=claims.operation_id,
            actor_id=actor_id,
        ):
            _business_error(
                status.HTTP_409_CONFLICT,
                "USER_DELETION_PREVIEW_USED",
                "Deletion preview has already been used",
            )
        deletion_actor = await session.scalar(
            select(User).where(User.id == actor_id, User.org_id == actor_org_id)
        )
        target = await session.scalar(
            select(User).where(User.id == target_user_id, User.org_id == actor_org_id)
        )
        if deletion_actor is None or target is None:
            _business_error(
                status.HTTP_409_CONFLICT,
                "USER_DELETION_PREVIEW_STALE",
                "Deletion target changed; create a new preview",
            )
        impact = await build_deletion_impact(
            session, actor=deletion_actor, target=target
        )
        _raise_blocker(impact.blockers)
        if impact.version_digest != claims.impact_hash:
            _business_error(
                status.HTTP_409_CONFLICT,
                "USER_DELETION_PREVIEW_STALE",
                "Deletion impact changed; create a new preview",
            )
        discovered_footprint = await _discover_runtime_deletion_footprint(
            session,
            impact=impact,
            target_user_id=target_user_id,
            organization_id=actor_org_id,
        )
        forest = await lock_runtime_root_forest(
            session,
            run_ids=discovered_footprint.run_ids,
            extra_deliverable_ids=discovered_footprint.extra_deliverable_ids,
        )
        await _reserve_preview(
            session,
            organization_id=actor_org_id,
            operation_id=claims.operation_id,
        )
        await session.execute(
            select(User.id)
            .where(
                User.org_id == actor_org_id,
                User.role == UserRole.ADMIN,
                User.is_active.is_(True),
            )
            .order_by(User.id)
            .with_for_update()
        )
        deletion_actor = await session.scalar(
            select(User)
            .where(User.id == actor_id, User.org_id == actor_org_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        target = await session.scalar(
            select(User)
            .where(User.id == target_user_id, User.org_id == actor_org_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if deletion_actor is None or target is None:
            _business_error(
                status.HTTP_409_CONFLICT,
                "USER_DELETION_PREVIEW_STALE",
                "Deletion target changed; create a new preview",
            )
        # Decisions come from a post-lock snapshot; the earlier impact is only
        # discovery and may not authorize deletion after lock contention.
        impact = await build_deletion_impact(
            session, actor=deletion_actor, target=target
        )
        _raise_blocker(impact.blockers)
        if impact.version_digest != claims.impact_hash:
            _business_error(
                status.HTTP_409_CONFLICT,
                "USER_DELETION_PREVIEW_STALE",
                "Deletion impact changed; create a new preview",
            )
        locked_footprint = await _discover_runtime_deletion_footprint(
            session,
            impact=impact,
            target_user_id=target_user_id,
            organization_id=actor_org_id,
        )
        if locked_footprint != discovered_footprint:
            _business_error(
                status.HTTP_409_CONFLICT,
                "USER_DELETION_PREVIEW_STALE",
                "Deletion runtime changed; create a new preview",
            )
        _validate_runtime_deletion_forest(locked_footprint, forest)
        await _delete_owned_records(session, impact)
        deleted_at = datetime.now(UTC)
        session.add(
            Event(
                type=RECEIPT_EVENT_TYPE,
                payload={
                    "actor_id": actor_id,
                    "operation_id": claims.operation_id,
                    "timestamp": deleted_at.isoformat(),
                    "counts": impact.counts,
                },
            )
        )
        await session.flush()
        await session.commit()
    except HTTPException:
        await session.rollback()
        raise
    except RuntimeLockConflict:
        await session.rollback()
        _business_error(
            status.HTTP_409_CONFLICT,
            "USER_DELETION_PREVIEW_STALE",
            "Deletion runtime changed; create a new preview",
        )
    except Exception:
        await session.rollback()
        _business_error(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "USER_DELETION_TRANSACTION_FAILED",
            "Permanent deletion failed and was rolled back",
        )
    return DeletionReceipt(
        operation_id=claims.operation_id,
        deleted_at=deleted_at,
        counts=impact.counts,
    )
