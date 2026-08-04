"""Account-authorized Artifact projection, versioning, and acceptance."""

import re
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from math import ceil
from typing import Any, TypeAlias

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
    ArtifactEvidenceGroup,
    ArtifactEvidenceSummary,
    ArtifactOut,
    ArtifactPageOut,
    ArtifactPagination,
    ArtifactPresentationOut,
    ArtifactQuality,
    ArtifactSection,
    ArtifactStatus,
    DeliverableActionCode,
    DeliverableActionOut,
    EvidenceRef,
    ScriptPresentationFormat,
)
from app.schemas.deliverable import get_schema, validate_payload
from app.services.deliverable_action_registry import SERVER_ACTIONS

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
_ARTIFACT_STATUS_LABELS: dict[ArtifactStatus, str] = {
    "draft": "草稿",
    "ready_for_review": "待确认",
    "accepted": "已确认",
    "revision_requested": "正在修改",
    "superseded": "历史版本",
}
_ACCOUNT_INSPECTION_ARTIFACT_TYPE = "account_inspection_report"
_DELIVERABLE_ARTIFACT_TYPES = {item.value: item for item in DeliverableType}
_BUSINESS_ARTIFACT_DATABASE_TYPES: dict[str, frozenset[DeliverableType]] = {
    "account_inspection_report": frozenset({DeliverableType.REVIEW_REPORT}),
    "account_positioning": frozenset({DeliverableType.POSITIONING_STRATEGY}),
    "positioning_strategy": frozenset({DeliverableType.POSITIONING_STRATEGY}),
    "topic_plan": frozenset({DeliverableType.TOPIC_PLAN}),
    "video_script": frozenset({DeliverableType.VIDEO_SCRIPT}),
    "visual_brief": frozenset({DeliverableType.ART_PROMPT}),
    "art_prompt": frozenset({DeliverableType.ART_PROMPT}),
    "video_asset": frozenset({DeliverableType.VIDEO_ASSET}),
    "edited_video": frozenset({DeliverableType.EDITED_VIDEO}),
    "content_calendar": frozenset({DeliverableType.PUBLISH_CALENDAR}),
    "publish_calendar": frozenset({DeliverableType.PUBLISH_CALENDAR}),
    "platform_publish_receipt": frozenset({DeliverableType.PUBLISH_CALENDAR}),
    "review_report": frozenset({DeliverableType.REVIEW_REPORT}),
    "engagement_review": frozenset({DeliverableType.REVIEW_REPORT}),
    "ad_plan": frozenset({DeliverableType.AD_PLAN}),
    "cs_record": frozenset({DeliverableType.CS_RECORD}),
    "operation_execution_plan": frozenset({DeliverableType.REVIEW_REPORT}),
}
_ACCOUNT_INSPECTION_FIELDS = {
    "data_sufficiency",
    "missing_data",
    "findings",
    "recommendations",
    "next_action",
    "participating_experts",
    "critic",
}
SafeBusinessValue: TypeAlias = str | list["SafeBusinessValue"] | dict[str, "SafeBusinessValue"]

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
_NON_SECTION_KEYS = {"title", "summary", "evidence_refs", "presentation_format"}
_SCRIPT_PRESENTATION_FORMATS = {"spoken", "storyboard", "product_video", "image_post", "live_flow"}
_SCRIPT_PRESENTATIONS: dict[ScriptPresentationFormat, tuple[str, str, str]] = {
    "spoken": ("口播拍摄稿", "口播稿", "查看口播拍摄稿"),
    "storyboard": ("分镜拍摄稿", "分镜拍摄稿", "查看分镜拍摄稿"),
    "product_video": ("产品视频拍摄稿", "产品视频拍摄稿", "查看产品视频拍摄稿"),
    "image_post": ("图文发布稿", "图文发布稿", "查看图文发布稿"),
    "live_flow": ("直播流程与话术稿", "直播流程与话术稿", "查看直播流程与话术稿"),
}
_FIXED_PRESENTATIONS: dict[str, tuple[str, str, str]] = {
    "account_inspection_report": ("账号诊断", "已完成当前账号运营诊断", "查看账号诊断"),
    "account_positioning": ("账号定位方案", "已整理当前账号定位方向", "查看账号定位方案"),
    "positioning_strategy": ("账号定位方案", "已整理当前账号定位方向", "查看账号定位方案"),
    "visual_brief": ("视觉制作说明", "已整理画面与素材要求", "查看视觉制作说明"),
    "art_prompt": ("视觉制作说明", "已整理画面与素材要求", "查看视觉制作说明"),
    "video_asset": ("视频素材清单", "已整理可用视频素材", "查看视频素材清单"),
    "edited_video": ("成片制作清单", "已整理剪辑与交付要求", "查看成片制作清单"),
    "platform_publish_receipt": ("发布记录", "已记录本次发布结果", "查看发布记录"),
    "review_report": ("运营复盘", "已完成本周期数据复盘", "查看运营复盘"),
    "engagement_review": ("互动复盘", "已整理近期互动反馈", "查看互动复盘"),
    "ad_plan": ("投放计划", "已整理投放目标与预算建议", "查看投放计划"),
    "cs_record": ("用户互动记录", "已整理用户反馈与回复建议", "查看用户互动记录"),
    "operation_execution_plan": ("本周运营执行计划", "已整理本周执行步骤", "查看运营执行计划"),
}
ActionSpec: TypeAlias = tuple[DeliverableActionCode, str, bool]
_EXPORT_ACTION: ActionSpec = ("export", "导出内容", False)
_ACTIONABLE_ARTIFACT_STATUSES: frozenset[ArtifactStatus] = frozenset(
    {"ready_for_review", "accepted"}
)
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
    thread_owner_id: int | None


def _normalize_artifact_type(
    artifact_type: str | DeliverableType | None,
) -> str | None:
    if artifact_type is None:
        return None
    value = artifact_type.value if isinstance(artifact_type, DeliverableType) else artifact_type
    if value in _BUSINESS_ARTIFACT_DATABASE_TYPES:
        return value
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail="Unsupported artifact type",
    )


def _normalize_artifact_types(
    artifact_type: str | DeliverableType | None,
    artifact_types: Collection[str] | None,
) -> frozenset[str] | None:
    values: list[str | DeliverableType] = []
    if artifact_type is not None:
        values.append(artifact_type)
    if artifact_types is not None:
        values.extend(artifact_types)
    if not values:
        return None
    return frozenset(_normalize_artifact_type(value) for value in values)


async def _business_artifact_type(
    session: AsyncSession,
    deliverable: Deliverable,
) -> str:
    if deliverable.type == DeliverableType.REVIEW_REPORT and deliverable.skill_run_id is not None:
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
    artifact_types: Collection[str] | None,
    artifact_status: ArtifactStatus | None,
    created_from: date | None,
    created_to: date | None,
    page: int,
    page_size: int,
) -> ArtifactPageOut:
    """List account artifacts, treating date bounds as inclusive UTC calendar days."""
    account = await require_account_access(session, user, account_id)
    filters = [ContentItem.account_id == account_id]
    requested_artifact_types = _normalize_artifact_types(artifact_type, artifact_types)
    if requested_artifact_types is not None:
        database_types = set().union(
            *(_BUSINESS_ARTIFACT_DATABASE_TYPES[item] for item in requested_artifact_types)
        )
        filters.append(Deliverable.type.in_(database_types))
    if artifact_status is not None:
        filters.append(Deliverable.status == _ARTIFACT_TO_STATUS[artifact_status])
    if created_from is not None:
        filters.append(
            Deliverable.created_at
            >= datetime.combine(created_from, time.min, tzinfo=UTC)
        )
    if created_to is not None:
        filters.append(
            Deliverable.created_at
            < datetime.combine(created_to + timedelta(days=1), time.min, tzinfo=UTC)
        )

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
            if requested_artifact_types is None or projected_type in requested_artifact_types:
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
            actor_user_id=user.id,
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
    account = await require_account_access(session, user, content.account_id, roles=roles)
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


async def get_artifact_out(session: AsyncSession, user: User, artifact_id: int) -> ArtifactOut:
    deliverable, content, provenance = await get_artifact(session, user, artifact_id)
    account_id = _require_content_account_id(content)
    return await project_artifact(
        session,
        deliverable,
        content=content,
        expected_org_id=user.org_id,
        expected_account_id=account_id,
        actor_user_id=user.id,
        provenance=provenance,
    )


async def create_artifact_revision_record(
    session: AsyncSession,
    user: User,
    *,
    artifact_id: int,
    payload: dict[str, Any],
    note: str | None,
) -> tuple[Deliverable, ContentItem, _ArtifactProvenance]:
    """Prepare a revision without committing or taking transaction ownership."""

    source, content, provenance = await _get_artifact_for_revision(
        session,
        user,
        artifact_id,
    )
    latest_version = await _require_latest_artifact_version(session, source)
    if source.status == DeliverableStatus.SUPERSEDED or source.version != latest_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="成果版本已更新，请刷新后重试",
        )

    validated_payload = _validate_revision_payload(source.type, payload, source.payload)
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
    try:
        async with session.begin_nested():
            session.add(revision)
            await session.flush()
    except IntegrityError as exc:
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
    return revision, content, provenance


async def create_artifact_revision(
    session: AsyncSession,
    user: User,
    *,
    artifact_id: int,
    payload: dict[str, Any],
    note: str | None,
) -> ArtifactOut:
    revision, content, _ = await create_artifact_revision_record(
        session,
        user,
        artifact_id=artifact_id,
        payload=payload,
        note=note,
    )
    await session.commit()
    await session.refresh(revision)
    account_id = _require_content_account_id(content)
    return await project_artifact(
        session,
        revision,
        content=content,
        expected_org_id=user.org_id,
        expected_account_id=account_id,
        actor_user_id=user.id,
    )


async def _get_artifact_for_revision(
    session: AsyncSession,
    user: User,
    artifact_id: int,
) -> tuple[Deliverable, ContentItem, _ArtifactProvenance]:
    """Authorize ownership, then surface corrupt copied lineage as a conflict."""

    deliverable = await session.get(Deliverable, artifact_id)
    if deliverable is None:
        raise _artifact_not_found()
    content = await session.get(ContentItem, deliverable.content_item_id)
    if content is None or content.account_id is None:
        raise _artifact_not_found()
    account = await require_account_access(
        session,
        user,
        content.account_id,
        roles=ARTIFACT_ACTION_ROLES,
    )
    provenance = await _load_valid_provenance(
        session,
        deliverable,
        content,
        expected_org_id=account.org_id,
        expected_account_id=account.id,
    )
    if provenance is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "ARTIFACT_LINEAGE_CONFLICT",
                "message": "成果来源链不一致，无法创建修订版本",
            },
        )
    return deliverable, content, provenance


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
    latest_version = await _require_latest_artifact_version(session, selected)
    if selected.status == DeliverableStatus.SUPERSEDED or selected.version != latest_version:
        raise _artifact_version_conflict(
            artifact_id=selected.id,
            selected_version=selected.version,
            latest_version=latest_version,
        )
    other_active = list(
        await session.scalars(
            select(Deliverable).where(
                Deliverable.content_item_id == selected.content_item_id,
                Deliverable.type == selected.type,
                Deliverable.id != selected.id,
                Deliverable.version < selected.version,
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
    await session.commit()
    if changed:
        await session.refresh(selected)
    account_id = _require_content_account_id(content)
    return await project_artifact(
        session,
        selected,
        content=content,
        expected_org_id=user.org_id,
        expected_account_id=account_id,
        actor_user_id=user.id,
        provenance=provenance,
    )


async def project_artifact(
    session: AsyncSession,
    deliverable: Deliverable,
    *,
    content: ContentItem | None = None,
    expected_org_id: int,
    expected_account_id: int,
    actor_user_id: int,
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
    artifact_status = _STATUS_TO_ARTIFACT[deliverable.status]
    presentation_format = _presentation_format(payload, business_artifact_type)
    quality_issues = _safe_issue_list(quality.issues or []) if quality is not None else []
    return ArtifactOut(
        id=deliverable.id,
        account_id=content.account_id,
        thread_id=deliverable.thread_id,
        turn_id=deliverable.turn_id,
        run_id=deliverable.run_id,
        skill_run_id=deliverable.skill_run_id,
        task_id=provenance.task_id,
        artifact_type=business_artifact_type,
        presentation_format=presentation_format,
        presentation=_artifact_presentation(
            business_artifact_type,
            payload,
            artifact_status=artifact_status,
            presentation_format=presentation_format,
        ),
        next_actions=_artifact_next_actions(
            business_artifact_type,
            artifact_status,
            deliverable_type=deliverable.type,
            has_thread=deliverable.thread_id is not None,
            actor_user_id=actor_user_id,
            thread_owner_id=provenance.thread_owner_id,
        ),
        title=_artifact_title(payload, content),
        version=deliverable.version,
        status=artifact_status,
        summary=_artifact_summary(payload, content),
        sections=_artifact_sections(
            deliverable.type,
            payload,
            business_artifact_type=business_artifact_type,
        ),
        evidence_refs=_evidence_refs(payload, quality),
        evidence_summary=_evidence_summary(payload, quality),
        quality=(
            ArtifactQuality(
                score=float(quality.score),
                passed=quality.passed,
                issues=quality_issues,
            )
            if quality is not None
            else None
        ),
        created_at=deliverable.created_at,
    )


def _validate_revision_payload(
    deliverable_type: DeliverableType,
    payload: dict[str, Any],
    source_payload: dict[str, Any],
) -> dict[str, Any]:
    schema = get_schema(deliverable_type)
    if schema is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="成果内容不符合当前类型要求",
        )
    normalized_payload = dict(payload)
    if (
        deliverable_type == DeliverableType.REVIEW_REPORT
        and "optimization_suggestions" not in normalized_payload
        and "recommendations" in normalized_payload
    ):
        normalized_payload["optimization_suggestions"] = normalized_payload[
            "recommendations"
        ]
    if (
        deliverable_type == DeliverableType.VIDEO_SCRIPT
        and "presentation_format" not in normalized_payload
        and isinstance(source_payload.get("presentation_format"), str)
    ):
        normalized_payload["presentation_format"] = source_payload["presentation_format"]
    business_payload = {
        key: normalized_payload[key]
        for key in schema.model_fields
        if key in normalized_payload
    }
    try:
        return validate_payload(deliverable_type, business_payload).model_dump(mode="json")
    except (KeyError, ValidationError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="成果内容不符合当前类型要求",
        ) from exc


def validate_complete_artifact_payload(
    deliverable_type: DeliverableType,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Validate a complete action payload without inheriting source-only fields."""

    return _validate_revision_payload(deliverable_type, payload, {})


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
        if turn is None or turn.org_id != expected_org_id or turn.thread_id != thread.id:
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
            or not _optional_link_matches(quality.skill_run_id, deliverable.skill_run_id)
        ):
            return None
        task_ids.add(quality.task_id)
        if quality.invocation_id is not None:
            invocation = await session.get(AgentInvocation, quality.invocation_id)
            if (
                invocation is None
                or invocation.task_id != quality.task_id
                or not _optional_link_matches(invocation.thread_id, deliverable.thread_id)
                or not _optional_link_matches(invocation.turn_id, deliverable.turn_id)
                or not _optional_link_matches(invocation.run_id, deliverable.run_id)
                or not _optional_link_matches(invocation.skill_run_id, deliverable.skill_run_id)
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
        if task is None or task.org_id != expected_org_id or task.content_item_id != content.id:
            return None

    return _ArtifactProvenance(
        quality=quality_rows[0] if quality_rows else None,
        task_id=task_id,
        thread_owner_id=thread.created_by_id if thread is not None else None,
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


def _presentation_format(
    payload: dict[str, Any], business_artifact_type: str
) -> ScriptPresentationFormat | None:
    if business_artifact_type != DeliverableType.VIDEO_SCRIPT.value:
        return None
    value = payload.get("presentation_format")
    return value if value in _SCRIPT_PRESENTATION_FORMATS else "storyboard"


def _artifact_presentation(
    artifact_type: str,
    payload: dict[str, Any],
    *,
    artifact_status: ArtifactStatus,
    presentation_format: ScriptPresentationFormat | None,
) -> ArtifactPresentationOut:
    if artifact_type == DeliverableType.VIDEO_SCRIPT.value:
        script_format = presentation_format or "storyboard"
        type_label, completion_noun, detail_action_label = _SCRIPT_PRESENTATIONS[
            script_format
        ]
        completion_label = f"已生成 1 条可直接拍摄的{completion_noun}"
    elif artifact_type == DeliverableType.TOPIC_PLAN.value:
        count = _structured_list_count(payload, "topics")
        type_label = "选题清单"
        completion_label = f"已规划 {count} 个可执行选题"
        detail_action_label = f"查看 {count} 个选题"
    elif artifact_type in {"content_calendar", DeliverableType.PUBLISH_CALENDAR.value}:
        count = _structured_list_count(payload, "items")
        type_label = "内容排期表"
        completion_label = f"已安排 {count} 条内容发布顺序"
        detail_action_label = f"查看 {count} 条发布安排"
    else:
        type_label, completion_label, detail_action_label = _FIXED_PRESENTATIONS.get(
            artifact_type,
            ("运营报告", "已生成运营报告", "查看完整报告"),
        )
    return ArtifactPresentationOut(
        type_label=type_label,
        completion_label=completion_label,
        status_label=_ARTIFACT_STATUS_LABELS[artifact_status],
        detail_action_label=detail_action_label,
    )


def _structured_list_count(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    return len(value) if isinstance(value, list) else 0


def _artifact_next_actions(
    artifact_type: str,
    artifact_status: ArtifactStatus,
    *,
    deliverable_type: DeliverableType,
    has_thread: bool,
    actor_user_id: int,
    thread_owner_id: int | None,
) -> list[DeliverableActionOut]:
    if artifact_status not in _ACTIONABLE_ARTIFACT_STATUSES:
        return []
    executable_specs: list[ActionSpec] = [
        (definition.code, definition.label, definition.requires_confirmation)
        for definition in SERVER_ACTIONS.values()
        if (
            definition.artifact_types is None
            or artifact_type in definition.artifact_types
        )
        and deliverable_type in definition.deliverable_types
        and artifact_status in definition.statuses
        and (
            not definition.requires_thread
            or (
                has_thread
                and thread_owner_id is not None
                and thread_owner_id == actor_user_id
            )
        )
    ]
    specs = (*executable_specs, _EXPORT_ACTION)
    return [
        DeliverableActionOut(
            code=code,
            label=label,
            requires_confirmation=requires_confirmation,
        )
        for code, label, requires_confirmation in specs
    ]


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
    section_payload = payload
    if business_artifact_type == _ACCOUNT_INSPECTION_ARTIFACT_TYPE:
        allowed_fields.update(_ACCOUNT_INSPECTION_FIELDS)
        section_payload = dict(payload)
        if not section_payload.get("recommendations") and section_payload.get(
            "optimization_suggestions"
        ):
            section_payload["recommendations"] = section_payload[
                "optimization_suggestions"
            ]
        section_payload.pop("optimization_suggestions", None)
    sections: list[ArtifactSection] = []
    for key, value in section_payload.items():
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


def _safe_business_value(value: Any) -> SafeBusinessValue | None:
    if isinstance(value, str):
        if _looks_like_internal_confirmation(value):
            return None
        return value
    if isinstance(value, list):
        cleaned: list[SafeBusinessValue] = [
            item for item in (_safe_business_value(item) for item in value) if item is not None
        ]
        return cleaned
    if isinstance(value, dict):
        cleaned_dict: dict[str, SafeBusinessValue] = {}
        for key, item in value.items():
            key_str = str(key)
            if _is_internal_key(key_str):
                continue
            cleaned_item = _safe_business_value(item)
            if cleaned_item is None:
                continue
            cleaned_dict[key_str] = cleaned_item
        return cleaned_dict
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


def _evidence_refs(payload: dict[str, Any], quality: AgentQualityScore | None) -> list[EvidenceRef]:
    candidates = _evidence_candidates(payload, quality)

    refs: list[EvidenceRef] = []
    seen: set[tuple[str, int]] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        kind = candidate.get("kind") or candidate.get("source_type")
        raw_id = candidate.get("id") or candidate.get("source_id")
        evidence_id = _safe_evidence_id(raw_id)
        if evidence_id is None:
            continue
        if not isinstance(kind, str) or not kind.strip() or _is_internal_key(kind):
            continue
        identity = (kind, evidence_id)
        if identity in seen:
            continue
        seen.add(identity)
        label = candidate.get("label") or candidate.get("metric") or f"{kind} #{evidence_id}"
        safe_label = str(label)
        if _looks_like_internal_confirmation(safe_label):
            safe_label = f"{kind} #{evidence_id}"
        refs.append(EvidenceRef(kind=kind, id=evidence_id, label=safe_label))
    return refs


_EVIDENCE_KIND_LABELS = {
    "field_observation": "账号数据字段",
    "data_import_batch": "数据导入批次",
    "account_metric_snapshot": "账号指标快照",
    "metric_snapshot": "账号指标快照",
    "specialist": "专家分析",
    "agent_invocation": "专家分析",
    "artifact": "已采用成果",
}


def _evidence_summary(
    payload: dict[str, Any], quality: AgentQualityScore | None
) -> ArtifactEvidenceSummary:
    groups: dict[str, dict[str, Any]] = {}
    seen: set[tuple[str, int]] = set()
    for candidate in _evidence_candidates(payload, quality):
        if not isinstance(candidate, dict):
            continue
        kind = candidate.get("kind") or candidate.get("source_type")
        evidence_id = _safe_evidence_id(candidate.get("id") or candidate.get("source_id"))
        if not isinstance(kind, str) or not kind.strip() or evidence_id is None:
            continue
        identity = (kind, evidence_id)
        if identity in seen:
            continue
        seen.add(identity)
        group = groups.setdefault(
            kind,
            {
                "kind": kind,
                "label": _EVIDENCE_KIND_LABELS.get(kind, "业务数据依据"),
                "count": 0,
                "metrics": set(),
                "periods": set(),
            },
        )
        group["count"] += 1
        metric = candidate.get("metric") or candidate.get("metric_name")
        if isinstance(metric, str) and metric.strip():
            group["metrics"].add(metric.strip())
        period = _candidate_period(candidate)
        if period:
            group["periods"].add(period)

    summaries = [
        ArtifactEvidenceGroup(
            kind=group["kind"],
            label=group["label"],
            count=group["count"],
            metric_count=len(group["metrics"]),
            period=(next(iter(group["periods"])) if len(group["periods"]) == 1 else None),
        )
        for group in groups.values()
    ]
    summaries.sort(key=lambda item: (-item.count, item.label, item.kind))
    return ArtifactEvidenceSummary(total=len(seen), groups=summaries)


def _evidence_candidates(
    payload: dict[str, Any], quality: AgentQualityScore | None
) -> list[Any]:
    candidates: list[Any] = []
    raw_payload_refs = payload.get("evidence_refs", [])
    if isinstance(raw_payload_refs, list):
        candidates.extend(raw_payload_refs)
    if quality is not None and isinstance(quality.evidence_refs, list):
        candidates.extend(quality.evidence_refs)
    return candidates


def _candidate_period(candidate: dict[str, Any]) -> str | None:
    explicit = candidate.get("period") or candidate.get("data_period")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    start = candidate.get("period_start")
    end = candidate.get("period_end")
    if isinstance(start, str) and start.strip() and isinstance(end, str) and end.strip():
        return f"{start.strip()} 至 {end.strip()}"
    return None


def _require_content_account_id(content: ContentItem) -> int:
    if content.account_id is None:
        raise _artifact_not_found()
    return content.account_id


def _safe_evidence_id(value: object) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, (str, bytes, bytearray)):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _safe_issue_list(values: list[Any]) -> list[str]:
    issues: list[str] = []
    for value in values:
        cleaned = _safe_business_value(value)
        if isinstance(cleaned, str):
            issues.append(cleaned)
    return issues


def _humanize_key(key: str) -> str:
    return key.replace("_", " ").strip()


async def _require_latest_artifact_version(
    session: AsyncSession,
    selected: Deliverable,
) -> int:
    locked_content_id = await session.scalar(
        select(ContentItem.id).where(ContentItem.id == selected.content_item_id).with_for_update()
    )
    if locked_content_id is None:
        raise _artifact_not_found()
    return int(
        (
            await session.scalar(
                select(func.max(Deliverable.version)).where(
                    Deliverable.content_item_id == selected.content_item_id,
                    Deliverable.type == selected.type,
                )
            )
        )
        or 0
    )


def _artifact_version_conflict(
    *,
    artifact_id: int,
    selected_version: int,
    latest_version: int,
) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "ARTIFACT_VERSION_CONFLICT",
            "message": "成果版本已更新，请刷新后重试",
            "details": {
                "artifact_id": artifact_id,
                "selected_version": selected_version,
                "latest_version": latest_version,
            },
        },
    )


def _artifact_not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="成果不存在",
    )
