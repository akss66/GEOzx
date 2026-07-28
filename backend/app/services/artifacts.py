"""Account-authorized Artifact projection, versioning, and acceptance."""

import re
from collections.abc import Collection
from dataclasses import dataclass
from math import ceil
from typing import Any

from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.workspace_access import require_account_access
from app.models import (
    Account,
    AgentInvocation,
    AgentQualityScore,
    AgentRun,
    BrainTask,
    ContentItem,
    ConversationThread,
    ConversationTurn,
    Deliverable,
    DeliverableAcceptance,
    SkillRun,
    User,
)
from app.models.enums import DeliverableStatus, DeliverableType, WorkspaceRole
from app.schemas.artifacts import (
    ArtifactOut,
    ArtifactPageOut,
    ArtifactPagination,
    ArtifactQuality,
    ArtifactSection,
    ArtifactStatus,
    EvidenceRef,
)
from app.schemas.deliverable import get_schema, validate_payload

ARTIFACT_ACTION_ROLES = {
    WorkspaceRole.LEAD,
    WorkspaceRole.OPERATOR,
    WorkspaceRole.EDITOR,
}

_STATUS_TO_ARTIFACT: dict[DeliverableStatus, ArtifactStatus] = {
    DeliverableStatus.DRAFT: "draft",
    DeliverableStatus.PENDING_REVIEW: "ready_for_review",
    DeliverableStatus.APPROVED: "accepted",
    DeliverableStatus.REJECTED: "revision_requested",
    DeliverableStatus.SUPERSEDED: "superseded",
}
_ARTIFACT_TO_STATUS = {value: key for key, value in _STATUS_TO_ARTIFACT.items()}
_ACCOUNT_INSPECTION_ARTIFACT_TYPE = "account_inspection_report"
_DELIVERABLE_ARTIFACT_TYPES = {item.value: item for item in DeliverableType}
_ACCOUNT_INSPECTION_FIELDS = {
    "data_sufficiency",
    "missing_data",
    "findings",
    "recommendations",
    "next_action",
    "participating_experts",
    "critic",
}

_SECTION_TITLES = {
    "period": "复盘周期",
    "account_persona": "账号人设",
    "target_audience": "目标受众",
    "differentiation": "差异化定位",
    "content_pillars": "内容支柱",
    "theme": "选题主题",
    "topics": "选题清单",
    "posting_notes": "发布建议",
    "items": "发布安排",
    "operating_notes": "运营说明",
    "hook": "开场钩子",
    "scenes": "脚本分镜",
    "duration_seconds": "建议时长",
    "bgm_suggestion": "配乐建议",
    "visual_style": "视觉风格",
    "prompts": "画面提示",
    "negative_prompt": "排除元素",
    "aspect_ratio": "画面比例",
    "tool": "生成工具",
    "clips": "视频片段",
    "resolution": "成片分辨率",
    "notes": "制作说明",
    "video_url": "成片地址",
    "gen_task_id": "生成任务",
    "gen_status": "生成状态",
    "cut_plan": "剪辑方案",
    "captions": "字幕文案",
    "transitions": "转场方式",
    "deliverables": "交付清单",
    "platform_variants": "平台版本",
    "key_metrics": "核心指标",
    "highlights": "亮点表现",
    "issues": "主要问题",
    "optimization_suggestions": "优化建议",
    "objective": "投放目标",
    "budget_strategy": "预算策略",
    "creative_directions": "创意方向",
    "risk_controls": "风险控制",
    "measurement": "衡量方式",
    "common_questions": "常见问题",
    "sentiment": "用户情绪",
    "response_guidelines": "回复指引",
    "content_opportunities": "内容机会",
}
_SECTION_TITLES.update(
    {
        "data_sufficiency": "数据充分度",
        "missing_data": "缺失数据",
        "findings": "体检发现",
        "recommendations": "优化建议",
        "next_action": "下一步行动",
        "participating_experts": "参与专家",
        "critic": "质量审核",
    }
)
_NON_SECTION_KEYS = {"title", "summary", "evidence_refs"}
_INTERNAL_KEY_MARKERS = {
    "acceptance",
    "checklist",
    "debug",
    "kernel",
    "policy",
    "prompt",
    "trace",
}
_INTERNAL_COMPOUND_KEYS = {
    "model_config",
    "raw_tool_log",
    "raw_tool_logs",
    "tool_log",
    "tool_logs",
}
_INTERNAL_COMPACT_MARKERS = {
    "acceptance",
    "checklist",
    "debug",
    "kernel",
    "modelconfig",
    "policy",
    "prompt",
    "rawtoollog",
    "toollog",
    "trace",
}
_CONFIRMATION_PATTERNS = (
    re.compile(
        r"\b(?:please\s+)?confirm\b.{0,120}\b"
        r"(?:that|this|the|selected|item|account|artifact)\b",
        re.IGNORECASE,
    ),
    re.compile(r"(?:请|麻烦)?确认(?:该|此|当前|所选)(?:项|成果|账号|内容)"),
)


@dataclass(frozen=True)
class _ArtifactProvenance:
    quality: AgentQualityScore | None
    task_id: int | None


def _normalize_artifact_type(
    artifact_type: str | DeliverableType | None,
) -> str | None:
    if artifact_type is None:
        return None
    value = (
        artifact_type.value
        if isinstance(artifact_type, DeliverableType)
        else artifact_type
    )
    if (
        value == _ACCOUNT_INSPECTION_ARTIFACT_TYPE
        or value in _DELIVERABLE_ARTIFACT_TYPES
    ):
        return value
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail="Unsupported artifact type",
    )


async def _business_artifact_type(
    session: AsyncSession,
    deliverable: Deliverable,
) -> str:
    if (
        deliverable.type == DeliverableType.REVIEW_REPORT
        and deliverable.skill_run_id is not None
    ):
        skill_run = await session.get(SkillRun, deliverable.skill_run_id)
        if skill_run is not None and skill_run.skill_code == "account_inspection":
            return _ACCOUNT_INSPECTION_ARTIFACT_TYPE
    return deliverable.type.value


async def list_artifacts(
    session: AsyncSession,
    user: User,
    *,
    account_id: int,
    artifact_type: str | DeliverableType | None,
    artifact_status: ArtifactStatus | None,
    page: int,
    page_size: int,
) -> ArtifactPageOut:
    """List only artifacts whose ContentItem is explicitly bound to the selected account."""
    account = await require_account_access(session, user, account_id)
    filters = [ContentItem.account_id == account_id]
    requested_artifact_type = _normalize_artifact_type(artifact_type)
    if requested_artifact_type is not None:
        database_type = (
            DeliverableType.REVIEW_REPORT
            if requested_artifact_type == _ACCOUNT_INSPECTION_ARTIFACT_TYPE
            else _DELIVERABLE_ARTIFACT_TYPES[requested_artifact_type]
        )
        filters.append(Deliverable.type == database_type)
    if artifact_status is not None:
        filters.append(Deliverable.status == _ARTIFACT_TO_STATUS[artifact_status])

    candidates = list(
        (
            await session.execute(
                select(Deliverable, ContentItem)
                .join(ContentItem, ContentItem.id == Deliverable.content_item_id)
                .where(*filters)
                .order_by(Deliverable.created_at.desc(), Deliverable.id.desc())
            )
        ).all()
    )
    valid: list[tuple[Deliverable, ContentItem, _ArtifactProvenance]] = []
    for deliverable, content in candidates:
        provenance = await _load_valid_provenance(
            session,
            deliverable,
            content,
            expected_org_id=account.org_id,
            expected_account_id=account.id,
        )
        if provenance is not None:
            projected_type = await _business_artifact_type(session, deliverable)
            if (
                requested_artifact_type is None
                or projected_type == requested_artifact_type
            ):
                valid.append((deliverable, content, provenance))

    total = len(valid)
    start = (page - 1) * page_size
    selected = valid[start : start + page_size]
    data = [
        await project_artifact(
            session,
            deliverable,
            content=content,
            expected_org_id=account.org_id,
            expected_account_id=account.id,
            provenance=provenance,
        )
        for deliverable, content, provenance in selected
    ]
    return ArtifactPageOut(
        data=data,
        pagination=ArtifactPagination(
            page=page,
            page_size=page_size,
            total=total,
            pages=ceil(total / page_size) if total else 0,
        ),
    )


async def get_artifact(
    session: AsyncSession,
    user: User,
    artifact_id: int,
    *,
    roles: Collection[WorkspaceRole] | None = None,
) -> tuple[Deliverable, ContentItem, _ArtifactProvenance]:
    deliverable = await session.get(Deliverable, artifact_id)
    if deliverable is None:
        raise _artifact_not_found()
    content = await session.get(ContentItem, deliverable.content_item_id)
    if content is None or content.account_id is None:
        raise _artifact_not_found()
    account = await require_account_access(
        session, user, content.account_id, roles=roles
    )
    provenance = await _load_valid_provenance(
        session,
        deliverable,
        content,
        expected_org_id=account.org_id,
        expected_account_id=account.id,
    )
    if provenance is None:
        raise _artifact_not_found()
    return deliverable, content, provenance


async def get_artifact_out(
    session: AsyncSession, user: User, artifact_id: int
) -> ArtifactOut:
    deliverable, content, provenance = await get_artifact(session, user, artifact_id)
    return await project_artifact(
        session,
        deliverable,
        content=content,
        expected_org_id=user.org_id,
        expected_account_id=content.account_id,
        provenance=provenance,
    )


async def create_artifact_revision(
    session: AsyncSession,
    user: User,
    *,
    artifact_id: int,
    payload: dict[str, Any],
    note: str | None,
) -> ArtifactOut:
    source, content, _ = await get_artifact(
        session,
        user,
        artifact_id,
        roles=ARTIFACT_ACTION_ROLES,
    )
    latest_version = (
        await session.scalar(
            select(func.max(Deliverable.version)).where(
                Deliverable.content_item_id == source.content_item_id,
                Deliverable.type == source.type,
            )
        )
    ) or 0
    if (
        source.status == DeliverableStatus.SUPERSEDED
        or source.version != latest_version
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="成果版本已更新，请刷新后重试",
        )

    validated_payload = _validate_revision_payload(source.type, payload)
    revision = Deliverable(
        content_item_id=source.content_item_id,
        thread_id=source.thread_id,
        turn_id=source.turn_id,
        run_id=source.run_id,
        skill_run_id=source.skill_run_id,
        agent_code=source.agent_code,
        type=source.type,
        version=latest_version + 1,
        status=DeliverableStatus.PENDING_REVIEW,
        payload=validated_payload,
        note=note,
    )
    session.add(revision)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="成果版本已更新，请刷新后重试",
        ) from exc

    active_rows = list(
        await session.scalars(
            select(Deliverable).where(
                Deliverable.content_item_id == source.content_item_id,
                Deliverable.type == source.type,
                Deliverable.id != revision.id,
                Deliverable.status != DeliverableStatus.SUPERSEDED,
            )
        )
    )
    for row in active_rows:
        row.status = DeliverableStatus.SUPERSEDED
    await session.commit()
    await session.refresh(revision)
    return await project_artifact(
        session,
        revision,
        content=content,
        expected_org_id=user.org_id,
        expected_account_id=content.account_id,
    )


async def accept_artifact(
    session: AsyncSession,
    user: User,
    *,
    artifact_id: int,
) -> ArtifactOut:
    selected, content, provenance = await get_artifact(
        session,
        user,
        artifact_id,
        roles=ARTIFACT_ACTION_ROLES,
    )
    other_active = list(
        await session.scalars(
            select(Deliverable).where(
                Deliverable.content_item_id == selected.content_item_id,
                Deliverable.type == selected.type,
                Deliverable.id != selected.id,
                Deliverable.status != DeliverableStatus.SUPERSEDED,
            )
        )
    )
    for row in other_active:
        row.status = DeliverableStatus.SUPERSEDED
    changed = bool(other_active)
    if selected.status != DeliverableStatus.APPROVED:
        selected.status = DeliverableStatus.APPROVED
        changed = True
    if changed:
        await session.commit()
        await session.refresh(selected)
    return await project_artifact(
        session,
        selected,
        content=content,
        expected_org_id=user.org_id,
        expected_account_id=content.account_id,
        provenance=provenance,
    )


async def project_artifact(
    session: AsyncSession,
    deliverable: Deliverable,
    *,
    content: ContentItem | None = None,
    expected_org_id: int,
    expected_account_id: int,
    provenance: _ArtifactProvenance | None = None,
) -> ArtifactOut:
    """Create the single Artifact identity consumed by list and detail surfaces."""
    content = content or await session.get(ContentItem, deliverable.content_item_id)
    if content is None or content.account_id != expected_account_id:
        raise _artifact_not_found()
    provenance = provenance or await _load_valid_provenance(
        session,
        deliverable,
        content,
        expected_org_id=expected_org_id,
        expected_account_id=expected_account_id,
    )
    if provenance is None:
        raise _artifact_not_found()
    quality = provenance.quality

    raw_payload = deliverable.payload if isinstance(deliverable.payload, dict) else {}
    business_artifact_type = await _business_artifact_type(session, deliverable)
    payload = _safe_payload(
        deliverable.type,
        raw_payload,
        business_artifact_type=business_artifact_type,
    )
    quality_issues = (
        _safe_business_value(list(quality.issues or [])) if quality is not None else []
    )
    return ArtifactOut(
        id=deliverable.id,
        account_id=content.account_id,
        thread_id=deliverable.thread_id,
        turn_id=deliverable.turn_id,
        run_id=deliverable.run_id,
        skill_run_id=deliverable.skill_run_id,
        task_id=provenance.task_id,
        artifact_type=business_artifact_type,
        title=_artifact_title(payload, content),
        version=deliverable.version,
        status=_STATUS_TO_ARTIFACT[deliverable.status],
        summary=_artifact_summary(payload, content),
        sections=_artifact_sections(
            deliverable.type,
            payload,
            business_artifact_type=business_artifact_type,
        ),
        evidence_refs=_evidence_refs(payload, quality),
        quality=(
            ArtifactQuality(
                score=float(quality.score),
                passed=quality.passed,
                issues=quality_issues if isinstance(quality_issues, list) else [],
            )
            if quality is not None
            else None
        ),
        created_at=deliverable.created_at,
    )


def _validate_revision_payload(
    deliverable_type: DeliverableType, payload: dict[str, Any]
) -> dict[str, Any]:
    schema = get_schema(deliverable_type)
    if schema is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="成果内容不符合当前类型要求",
        )
    business_payload = {
        key: payload[key] for key in schema.model_fields if key in payload
    }
    try:
        return validate_payload(deliverable_type, business_payload).model_dump(
            mode="json"
        )
    except (KeyError, ValidationError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="成果内容不符合当前类型要求",
        ) from exc


async def _load_valid_provenance(
    session: AsyncSession,
    deliverable: Deliverable,
    content: ContentItem,
    *,
    expected_org_id: int,
    expected_account_id: int,
) -> _ArtifactProvenance | None:
    """Return provenance only when every non-null link stays in one account chain."""
    if content.account_id != expected_account_id:
        return None
    account = await session.get(Account, expected_account_id)
    if account is None or account.org_id != expected_org_id:
        return None

    thread: ConversationThread | None = None
    if deliverable.thread_id is not None:
        thread = await session.get(ConversationThread, deliverable.thread_id)
        if (
            thread is None
            or thread.org_id != expected_org_id
            or thread.account_id != expected_account_id
        ):
            return None

    turn: ConversationTurn | None = None
    if deliverable.turn_id is not None:
        if thread is None:
            return None
        turn = await session.get(ConversationTurn, deliverable.turn_id)
        if (
            turn is None
            or turn.org_id != expected_org_id
            or turn.thread_id != thread.id
        ):
            return None

    run: AgentRun | None = None
    if deliverable.run_id is not None:
        if thread is None or turn is None:
            return None
        run = await session.get(AgentRun, deliverable.run_id)
        if (
            run is None
            or run.org_id != expected_org_id
            or run.thread_id != thread.id
            or run.turn_id != turn.id
        ):
            return None

    skill_run: SkillRun | None = None
    if deliverable.skill_run_id is not None:
        if thread is None or turn is None or run is None:
            return None
        skill_run = await session.get(SkillRun, deliverable.skill_run_id)
        if (
            skill_run is None
            or skill_run.org_id != expected_org_id
            or skill_run.thread_id != thread.id
            or skill_run.turn_id != turn.id
            or skill_run.run_id != run.id
        ):
            return None

    task_ids: set[int] = set()
    if run is not None and run.task_id is not None:
        task_ids.add(run.task_id)
    if skill_run is not None and skill_run.task_id is not None:
        task_ids.add(skill_run.task_id)

    quality_rows = list(
        await session.scalars(
            select(AgentQualityScore)
            .where(AgentQualityScore.deliverable_id == deliverable.id)
            .order_by(AgentQualityScore.iteration.desc(), AgentQualityScore.id.desc())
        )
    )
    for quality in quality_rows:
        if (
            quality.org_id != expected_org_id
            or not _optional_link_matches(quality.thread_id, deliverable.thread_id)
            or not _optional_link_matches(quality.turn_id, deliverable.turn_id)
            or not _optional_link_matches(quality.run_id, deliverable.run_id)
            or not _optional_link_matches(
                quality.skill_run_id, deliverable.skill_run_id
            )
        ):
            return None
        task_ids.add(quality.task_id)
        if quality.invocation_id is not None:
            invocation = await session.get(AgentInvocation, quality.invocation_id)
            if (
                invocation is None
                or invocation.task_id != quality.task_id
                or not _optional_link_matches(
                    invocation.thread_id, deliverable.thread_id
                )
                or not _optional_link_matches(invocation.turn_id, deliverable.turn_id)
                or not _optional_link_matches(invocation.run_id, deliverable.run_id)
                or not _optional_link_matches(
                    invocation.skill_run_id, deliverable.skill_run_id
                )
            ):
                return None

    acceptance_rows = list(
        await session.scalars(
            select(DeliverableAcceptance).where(
                DeliverableAcceptance.deliverable_id == deliverable.id
            )
        )
    )
    task_ids.update(row.task_id for row in acceptance_rows)
    if len(task_ids) > 1:
        return None
    task_id = next(iter(task_ids), None)
    if task_id is not None:
        task = await session.get(BrainTask, task_id)
        if (
            task is None
            or task.org_id != expected_org_id
            or task.content_item_id != content.id
        ):
            return None

    return _ArtifactProvenance(
        quality=quality_rows[0] if quality_rows else None,
        task_id=task_id,
    )


def _optional_link_matches(value: int | None, expected: int | None) -> bool:
    return value is None or value == expected


def _artifact_title(payload: dict[str, Any], content: ContentItem) -> str:
    title = payload.get("title")
    return title.strip() if isinstance(title, str) and title.strip() else content.title


def _artifact_summary(payload: dict[str, Any], content: ContentItem) -> str:
    summary = payload.get("summary")
    if isinstance(summary, str) and summary.strip():
        return summary.strip()
    return content.title


def _safe_payload(
    deliverable_type: DeliverableType,
    payload: dict[str, Any],
    *,
    business_artifact_type: str,
) -> dict[str, Any]:
    schema = get_schema(deliverable_type)
    allowed_fields = set(schema.model_fields) if schema is not None else set()
    if business_artifact_type == _ACCOUNT_INSPECTION_ARTIFACT_TYPE:
        allowed_fields.update(_ACCOUNT_INSPECTION_FIELDS)
    safe: dict[str, Any] = {}
    for key in allowed_fields:
        if key not in payload:
            continue
        cleaned = _safe_business_value(payload[key])
        if cleaned is not None:
            safe[key] = cleaned
    if isinstance(payload.get("evidence_refs"), list):
        safe["evidence_refs"] = payload["evidence_refs"]
    return safe


def _artifact_sections(
    deliverable_type: DeliverableType,
    payload: dict[str, Any],
    *,
    business_artifact_type: str,
) -> list[ArtifactSection]:
    schema = get_schema(deliverable_type)
    allowed_fields = set(schema.model_fields) if schema is not None else set()
    if business_artifact_type == _ACCOUNT_INSPECTION_ARTIFACT_TYPE:
        allowed_fields.update(_ACCOUNT_INSPECTION_FIELDS)
    sections: list[ArtifactSection] = []
    for key, value in payload.items():
        if key not in allowed_fields or key in _NON_SECTION_KEYS or value is None:
            continue
        sections.append(
            ArtifactSection(
                key=key,
                title=_SECTION_TITLES.get(key, _humanize_key(key)),
                content=value,
            )
        )
    return sections


def _safe_business_value(value: Any) -> str | list[Any] | dict[str, Any] | None:
    if isinstance(value, str):
        if _looks_like_internal_confirmation(value):
            return None
        return value
    if isinstance(value, list):
        cleaned = [
            item
            for item in (_safe_business_value(item) for item in value)
            if item is not None
        ]
        return cleaned
    if isinstance(value, dict):
        return {
            str(key): cleaned
            for key, item in value.items()
            if not _is_internal_key(str(key))
            and (cleaned := _safe_business_value(item)) is not None
        }
    return str(value)


def _is_internal_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
    if normalized in _INTERNAL_COMPOUND_KEYS:
        return True
    tokens = set(normalized.split("_"))
    compact = normalized.replace("_", "")
    return bool(tokens & _INTERNAL_KEY_MARKERS) or any(
        marker in compact for marker in _INTERNAL_COMPACT_MARKERS
    )


def _looks_like_internal_confirmation(value: str) -> bool:
    normalized = " ".join(value.split())
    return any(pattern.search(normalized) for pattern in _CONFIRMATION_PATTERNS)


def _evidence_refs(
    payload: dict[str, Any], quality: AgentQualityScore | None
) -> list[EvidenceRef]:
    candidates: list[Any] = []
    raw_payload_refs = payload.get("evidence_refs", [])
    if isinstance(raw_payload_refs, list):
        candidates.extend(raw_payload_refs)
    if quality is not None and isinstance(quality.evidence_refs, list):
        candidates.extend(quality.evidence_refs)

    refs: list[EvidenceRef] = []
    seen: set[tuple[str, int]] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        kind = candidate.get("kind") or candidate.get("source_type")
        raw_id = candidate.get("id") or candidate.get("source_id")
        try:
            evidence_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if not isinstance(kind, str) or not kind.strip() or _is_internal_key(kind):
            continue
        identity = (kind, evidence_id)
        if identity in seen:
            continue
        seen.add(identity)
        label = (
            candidate.get("label")
            or candidate.get("metric")
            or f"{kind} #{evidence_id}"
        )
        safe_label = str(label)
        if _looks_like_internal_confirmation(safe_label):
            safe_label = f"{kind} #{evidence_id}"
        refs.append(EvidenceRef(kind=kind, id=evidence_id, label=safe_label))
    return refs


def _humanize_key(key: str) -> str:
    return key.replace("_", " ").strip()


def _artifact_not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="成果不存在",
    )
