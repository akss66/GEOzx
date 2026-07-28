"""Account-authorized Artifact projection, versioning, and acceptance."""

from collections.abc import Collection
from math import ceil
from typing import Any

from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.workspace_access import require_account_access
from app.models import (
    AgentQualityScore,
    ContentItem,
    ConversationThread,
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
from app.schemas.deliverable import validate_payload

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
_PRIVATE_PAYLOAD_KEYS = {
    "acceptance_items",
    "acceptance_checklist",
    "prompt",
    "system_prompt",
    "model",
    "model_config",
    "raw_tool_logs",
    "tool_logs",
}
_NON_SECTION_KEYS = {"title", "summary", "evidence_refs"} | _PRIVATE_PAYLOAD_KEYS
_GENERIC_ACCEPTANCE_TEXT = "confirm that this item"


async def list_artifacts(
    session: AsyncSession,
    user: User,
    *,
    account_id: int,
    artifact_type: DeliverableType | None,
    artifact_status: ArtifactStatus | None,
    page: int,
    page_size: int,
) -> ArtifactPageOut:
    """List only artifacts whose ContentItem is explicitly bound to the selected account."""
    await require_account_access(session, user, account_id)
    filters = [ContentItem.account_id == account_id]
    if artifact_type is not None:
        filters.append(Deliverable.type == artifact_type)
    if artifact_status is not None:
        filters.append(Deliverable.status == _ARTIFACT_TO_STATUS[artifact_status])

    total = (
        await session.scalar(
            select(func.count(Deliverable.id))
            .join(ContentItem, ContentItem.id == Deliverable.content_item_id)
            .where(*filters)
        )
    ) or 0
    rows = list(
        await session.scalars(
            select(Deliverable)
            .join(ContentItem, ContentItem.id == Deliverable.content_item_id)
            .where(*filters)
            .order_by(Deliverable.created_at.desc(), Deliverable.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    data = [await project_artifact(session, row, expected_account_id=account_id) for row in rows]
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
) -> tuple[Deliverable, ContentItem]:
    deliverable = await session.get(Deliverable, artifact_id)
    if deliverable is None:
        raise _artifact_not_found()
    content = await session.get(ContentItem, deliverable.content_item_id)
    if content is None or content.account_id is None:
        raise _artifact_not_found()
    await require_account_access(session, user, content.account_id, roles=roles)
    await _require_matching_provenance_account(session, deliverable, content.account_id)
    return deliverable, content


async def get_artifact_out(
    session: AsyncSession, user: User, artifact_id: int
) -> ArtifactOut:
    deliverable, content = await get_artifact(session, user, artifact_id)
    return await project_artifact(
        session,
        deliverable,
        content=content,
        expected_account_id=content.account_id,
    )


async def create_artifact_revision(
    session: AsyncSession,
    user: User,
    *,
    artifact_id: int,
    payload: dict[str, Any],
    note: str | None,
) -> ArtifactOut:
    source, content = await get_artifact(
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
    if source.status == DeliverableStatus.SUPERSEDED or source.version != latest_version:
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
        expected_account_id=content.account_id,
    )


async def accept_artifact(
    session: AsyncSession,
    user: User,
    *,
    artifact_id: int,
) -> ArtifactOut:
    selected, content = await get_artifact(
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
        expected_account_id=content.account_id,
    )


async def project_artifact(
    session: AsyncSession,
    deliverable: Deliverable,
    *,
    content: ContentItem | None = None,
    expected_account_id: int | None = None,
) -> ArtifactOut:
    """Create the single Artifact identity consumed by list and detail surfaces."""
    content = content or await session.get(ContentItem, deliverable.content_item_id)
    if (
        content is None
        or content.account_id is None
        or (expected_account_id is not None and content.account_id != expected_account_id)
    ):
        raise _artifact_not_found()
    await _require_matching_provenance_account(session, deliverable, content.account_id)

    quality = await session.scalar(
        select(AgentQualityScore)
        .where(AgentQualityScore.deliverable_id == deliverable.id)
        .order_by(AgentQualityScore.iteration.desc(), AgentQualityScore.id.desc())
        .limit(1)
    )
    task_id = quality.task_id if quality is not None else None
    if task_id is None and deliverable.skill_run_id is not None:
        task_id = await session.scalar(
            select(SkillRun.task_id).where(SkillRun.id == deliverable.skill_run_id)
        )
    if task_id is None:
        task_id = await session.scalar(
            select(DeliverableAcceptance.task_id)
            .where(DeliverableAcceptance.deliverable_id == deliverable.id)
            .order_by(DeliverableAcceptance.id.desc())
            .limit(1)
        )

    payload = deliverable.payload if isinstance(deliverable.payload, dict) else {}
    return ArtifactOut(
        id=deliverable.id,
        account_id=content.account_id,
        thread_id=deliverable.thread_id,
        turn_id=deliverable.turn_id,
        run_id=deliverable.run_id,
        skill_run_id=deliverable.skill_run_id,
        task_id=task_id,
        artifact_type=deliverable.type.value,
        title=_artifact_title(payload, content),
        version=deliverable.version,
        status=_STATUS_TO_ARTIFACT[deliverable.status],
        summary=_artifact_summary(payload, deliverable, content),
        sections=_artifact_sections(payload),
        evidence_refs=_evidence_refs(payload, quality),
        quality=(
            ArtifactQuality(
                score=float(quality.score),
                passed=quality.passed,
                issues=list(quality.issues or []),
            )
            if quality is not None
            else None
        ),
        created_at=deliverable.created_at,
    )


def _validate_revision_payload(
    deliverable_type: DeliverableType, payload: dict[str, Any]
) -> dict[str, Any]:
    business_payload = {
        key: value
        for key, value in payload.items()
        if key not in _PRIVATE_PAYLOAD_KEYS and key != "evidence_refs"
    }
    try:
        return validate_payload(deliverable_type, business_payload).model_dump(mode="json")
    except (KeyError, ValidationError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="成果内容不符合当前类型要求",
        ) from exc


async def _require_matching_provenance_account(
    session: AsyncSession, deliverable: Deliverable, account_id: int
) -> None:
    if deliverable.thread_id is None:
        return
    thread_account_id = await session.scalar(
        select(ConversationThread.account_id).where(
            ConversationThread.id == deliverable.thread_id
        )
    )
    if thread_account_id != account_id:
        raise _artifact_not_found()


def _artifact_title(payload: dict[str, Any], content: ContentItem) -> str:
    title = payload.get("title")
    return title.strip() if isinstance(title, str) and title.strip() else content.title


def _artifact_summary(
    payload: dict[str, Any], deliverable: Deliverable, content: ContentItem
) -> str:
    summary = payload.get("summary")
    if isinstance(summary, str) and summary.strip():
        return summary.strip()
    if deliverable.note and deliverable.note.strip():
        return deliverable.note.strip()
    return content.title


def _artifact_sections(payload: dict[str, Any]) -> list[ArtifactSection]:
    sections: list[ArtifactSection] = []
    for key, value in payload.items():
        if key in _NON_SECTION_KEYS or value is None:
            continue
        safe_value = _safe_business_value(value)
        if safe_value is None:
            continue
        sections.append(
            ArtifactSection(
                key=key,
                title=_SECTION_TITLES.get(key, _humanize_key(key)),
                content=safe_value,
            )
        )
    return sections


def _safe_business_value(value: Any) -> str | list[Any] | dict[str, Any] | None:
    if isinstance(value, str):
        if _GENERIC_ACCEPTANCE_TEXT in value.lower():
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
            if key not in _PRIVATE_PAYLOAD_KEYS
            and (cleaned := _safe_business_value(item)) is not None
        }
    return str(value)


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
        if not isinstance(kind, str) or not kind.strip():
            continue
        identity = (kind, evidence_id)
        if identity in seen:
            continue
        seen.add(identity)
        label = candidate.get("label") or candidate.get("metric") or f"{kind} #{evidence_id}"
        refs.append(EvidenceRef(kind=kind, id=evidence_id, label=str(label)))
    return refs


def _humanize_key(key: str) -> str:
    return key.replace("_", " ").strip()


def _artifact_not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="成果不存在",
    )
