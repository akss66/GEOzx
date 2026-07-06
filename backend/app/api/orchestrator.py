"""编排路由：创建内容、启动流水线、看板视图、质量门审批。"""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser
from app.db import get_session
from app.models import (
    AgentTask,
    AgentToolCall,
    BrainTask,
    ComplianceCheck,
    ContentItem,
    Deliverable,
    GateApproval,
    MaterialAsset,
    Project,
)
from app.models.enums import (
    BrainTaskStatus,
    BrainTaskType,
    ComplianceRisk,
    GateStatus,
    GateType,
    MaterialStatus,
    Platform,
)
from app.orchestrator.engine import engine
from app.schemas.orchestrator import (
    AgentTaskOut,
    ApproveGateRequest,
    BoardOut,
    ComplianceCheckOut,
    ContentItemOut,
    CreateContentItemRequest,
    DeliverableOut,
    GateApprovalOut,
    PendingGateOut,
    PublishCapabilityOut,
    PublishReadinessFinding,
    PublishReadinessOut,
    PublishPackageOut,
    PublishReadinessRequest,
    RerunStageRequest,
)

router = APIRouter(tags=["orchestrator"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".webm", ".flv", ".wmv"}
SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
MIN_SCHEDULE_LEAD_TIME = timedelta(hours=2)
PUBLISH_SUPPORTED_FIELDS = [
    "title",
    "body",
    "topics",
    "material_ids",
    "cover_material_id",
    "scheduled_at",
    "visibility",
    "allow_comment",
]


def _publish_capabilities() -> list[PublishCapabilityOut]:
    return [
        PublishCapabilityOut(
            platform=platform,
            content_types=["video", "image_text"],
            supported_fields=PUBLISH_SUPPORTED_FIELDS,
            execution_mode="manual_checklist",
            permission_status="prepare_only",
            browser_runner_enabled=False,
        )
        for platform in (Platform.DOUYIN, Platform.XIAOHONGSHU, Platform.SHIPINHAO)
    ]


async def _board(session: AsyncSession, ci_id: int) -> BoardOut:
    ci = await session.get(ContentItem, ci_id)
    if ci is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="内容不存在")
    tasks = (
        await session.scalars(
            select(AgentTask).where(AgentTask.content_item_id == ci_id).order_by(AgentTask.id)
        )
    ).all()
    deliverables = (
        await session.scalars(
            select(Deliverable).where(Deliverable.content_item_id == ci_id).order_by(Deliverable.id)
        )
    ).all()
    gates = (
        await session.scalars(
            select(GateApproval)
            .where(GateApproval.content_item_id == ci_id)
            .order_by(GateApproval.id)
        )
    ).all()
    checks = (
        await session.scalars(
            select(ComplianceCheck)
            .where(ComplianceCheck.content_item_id == ci_id)
            .order_by(ComplianceCheck.id)
        )
    ).all()
    return BoardOut(
        content_item=ContentItemOut.model_validate(ci),
        tasks=[AgentTaskOut.model_validate(t) for t in tasks],
        deliverables=[DeliverableOut.model_validate(d) for d in deliverables],
        gates=[GateApprovalOut.model_validate(g) for g in gates],
        compliance=[ComplianceCheckOut.model_validate(c) for c in checks],
    )


async def _get_owned_content_item(
    session: AsyncSession, ci_id: int, org_id: int
) -> ContentItem:
    row = await session.execute(
        select(ContentItem, Project.org_id)
        .join(Project, ContentItem.project_id == Project.id)
        .where(ContentItem.id == ci_id)
    )
    found = row.first()
    if found is None or found[1] != org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="content item not found")
    return found[0]


def _finding(level: ComplianceRisk, code: str, message: str) -> PublishReadinessFinding:
    return PublishReadinessFinding(level=level, code=code, message=message)


def _path_suffix(value: str | None) -> str:
    if not value:
        return ""
    parsed = urlparse(value)
    path = parsed.path if parsed.scheme else value
    return Path(path).suffix.lower()


def _readiness_risk(findings: list[PublishReadinessFinding]) -> ComplianceRisk:
    if any(item.level == ComplianceRisk.BLOCK for item in findings):
        return ComplianceRisk.BLOCK
    if any(item.level == ComplianceRisk.WARN for item in findings):
        return ComplianceRisk.WARN
    return ComplianceRisk.PASS


async def _build_publish_readiness_findings(
    session: AsyncSession,
    body: PublishReadinessRequest,
    content_item: ContentItem,
    org_id: int,
) -> list[PublishReadinessFinding]:
    findings: list[PublishReadinessFinding] = []
    title = body.title.strip()
    body_text = body.body.strip()
    topics = [topic.strip() for topic in body.topics if topic.strip()]

    if not title:
        findings.append(_finding(ComplianceRisk.BLOCK, "title.required", "Title is required."))
    elif len(title) > 30:
        findings.append(
            _finding(
                ComplianceRisk.WARN,
                "title.long",
                "Douyin publishing UI commonly truncates long titles; keep it within 30 chars when possible.",
            )
        )
    else:
        findings.append(_finding(ComplianceRisk.PASS, "title.ok", "Title is ready."))

    if len(body_text) > 1000:
        findings.append(
            _finding(ComplianceRisk.WARN, "body.long", "Body copy is long; review it before publishing.")
        )
    if len(topics) > 10:
        findings.append(_finding(ComplianceRisk.BLOCK, "topics.too_many", "At most 10 topics are allowed."))
    for topic in topics:
        if len(topic) > 20:
            findings.append(
                _finding(ComplianceRisk.WARN, "topic.long", f"Topic '{topic}' is longer than 20 chars.")
            )

    if body.scheduled_at is not None:
        now = datetime.now(tz=body.scheduled_at.tzinfo) if body.scheduled_at.tzinfo else datetime.now()
        if body.scheduled_at <= now:
            findings.append(
                _finding(ComplianceRisk.BLOCK, "schedule.past", "Scheduled publish time must be in the future.")
            )
        elif body.scheduled_at <= now + MIN_SCHEDULE_LEAD_TIME:
            findings.append(
                _finding(
                    ComplianceRisk.BLOCK,
                    "schedule.too_soon",
                    "Scheduled publish time must be at least 2 hours from now.",
                )
            )
        else:
            findings.append(_finding(ComplianceRisk.PASS, "schedule.ok", "Scheduled time is valid."))

    material_ids_to_load = set(body.material_ids)
    if body.cover_material_id is not None:
        material_ids_to_load.add(body.cover_material_id)

    if not body.material_ids:
        findings.append(
            _finding(ComplianceRisk.BLOCK, "material.required", "At least one ready video or image material is required.")
        )

    materials = []
    if material_ids_to_load:
        materials = (
            await session.scalars(
                select(MaterialAsset).where(
                    MaterialAsset.org_id == org_id,
                    MaterialAsset.id.in_(material_ids_to_load),
                )
            )
        ).all()
    material_by_id = {material.id: material for material in materials}

    for material_id in body.material_ids:
        material = material_by_id.get(material_id)
        if material is None or material.content_item_id != content_item.id:
            findings.append(
                _finding(ComplianceRisk.BLOCK, "material.missing", f"Material #{material_id} is not available.")
            )
            continue
        if material.status != MaterialStatus.READY:
            findings.append(
                _finding(ComplianceRisk.BLOCK, "material.not_ready", f"Material #{material_id} is not ready.")
            )
            continue
        if material.kind not in {"video", "image"}:
            findings.append(
                _finding(
                    ComplianceRisk.BLOCK,
                    "material.kind",
                    f"Material #{material_id} must be video or image.",
                )
            )
            continue

        suffix = _path_suffix(material.local_path or material.source_url)
        supported = SUPPORTED_VIDEO_EXTENSIONS if material.kind == "video" else SUPPORTED_IMAGE_EXTENSIONS
        if not suffix:
            findings.append(
                _finding(ComplianceRisk.BLOCK, "material.path", f"Material #{material_id} has no file path.")
            )
        elif suffix not in supported:
            findings.append(
                _finding(
                    ComplianceRisk.BLOCK,
                    "material.extension",
                    f"Material #{material_id} uses unsupported extension '{suffix}'.",
                )
            )
        else:
            findings.append(
                _finding(ComplianceRisk.PASS, "material.ok", f"Material #{material_id} is publishable.")
            )

    if body.cover_material_id is not None:
        cover = material_by_id.get(body.cover_material_id)
        if cover is None or cover.content_item_id != content_item.id:
            findings.append(
                _finding(
                    ComplianceRisk.BLOCK,
                    "cover.missing",
                    f"Cover material #{body.cover_material_id} is not available.",
                )
            )
        elif cover.status != MaterialStatus.READY:
            findings.append(
                _finding(
                    ComplianceRisk.BLOCK,
                    "cover.not_ready",
                    f"Cover material #{body.cover_material_id} is not ready.",
                )
            )
        elif cover.kind not in {"image", "video"}:
            findings.append(
                _finding(
                    ComplianceRisk.BLOCK,
                    "cover.kind",
                    f"Cover material #{body.cover_material_id} must be image or video.",
                )
            )
        else:
            findings.append(
                _finding(ComplianceRisk.PASS, "cover.ok", f"Cover material #{body.cover_material_id} is ready.")
            )

    return findings


def _publish_content_type(materials: list[MaterialAsset], material_ids: list[int]) -> str:
    selected = [material for material in materials if material.id in set(material_ids)]
    if any(material.kind == "video" for material in selected):
        return "video"
    return "image_text"


def _manual_publish_steps(package: PublishPackageOut) -> list[str]:
    platform_label = {
        Platform.DOUYIN: "抖音创作者服务中心",
        Platform.XIAOHONGSHU: "小红书创作服务平台",
        Platform.SHIPINHAO: "视频号助手",
    }[package.platform]
    schedule_text = (
        f"设置定时发布时间：{package.scheduled_at.isoformat()}"
        if package.scheduled_at
        else "按审批结论选择立即发布或手动设置发布时间"
    )
    return [
        f"打开{platform_label}，确认当前账号与发布包账号一致。",
        f"上传素材：{', '.join(f'#{material_id}' for material_id in package.material_ids)}。",
        f"填写标题：{package.title}。",
        "粘贴正文与话题，并核对平台规则提示。",
        f"设置可见范围为 {package.visibility}，评论开关为 {'开启' if package.allow_comment else '关闭'}。",
        schedule_text,
        "发布前再次核对封面、素材、标题、话题和合规提示。",
    ]


def _publish_package(
    body: PublishReadinessRequest,
    content_item: ContentItem,
    materials: list[MaterialAsset],
) -> PublishPackageOut:
    package = PublishPackageOut(
        platform=body.platform,
        account_id=content_item.account_id,
        content_type=_publish_content_type(materials, body.material_ids),
        title=body.title.strip(),
        body=body.body.strip(),
        topics=[topic.strip() for topic in body.topics if topic.strip()],
        scheduled_at=body.scheduled_at,
        material_ids=body.material_ids,
        cover_material_id=body.cover_material_id,
        visibility=body.visibility,
        allow_comment=body.allow_comment,
        execution_mode="manual_checklist",
        manual_steps=[],
    )
    package.manual_steps = _manual_publish_steps(package)
    return package


@router.get("/publish-capabilities", response_model=list[PublishCapabilityOut])
async def list_publish_capabilities(user: CurrentUser) -> list[PublishCapabilityOut]:
    return _publish_capabilities()


async def _load_publish_materials(
    session: AsyncSession,
    body: PublishReadinessRequest,
    org_id: int,
) -> list[MaterialAsset]:
    material_ids_to_load = set(body.material_ids)
    if body.cover_material_id is not None:
        material_ids_to_load.add(body.cover_material_id)
    if not material_ids_to_load:
        return []
    return (
        await session.scalars(
            select(MaterialAsset).where(
                MaterialAsset.org_id == org_id,
                MaterialAsset.id.in_(material_ids_to_load),
            )
        )
    ).all()


@router.post("/content-items", response_model=ContentItemOut, status_code=status.HTTP_201_CREATED)
async def create_content_item(
    body: CreateContentItemRequest, user: CurrentUser, session: SessionDep
) -> ContentItemOut:
    project = await session.get(Project, body.project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")
    ci = ContentItem(project_id=body.project_id, account_id=body.account_id, title=body.title)
    session.add(ci)
    await session.commit()
    await session.refresh(ci)
    return ContentItemOut.model_validate(ci)


@router.get("/content-items", response_model=list[ContentItemOut])
async def list_content_items(
    user: CurrentUser,
    session: SessionDep,
    project_id: Annotated[int | None, Query()] = None,
) -> list[ContentItemOut]:
    q = select(ContentItem).order_by(ContentItem.id.desc())
    if project_id is not None:
        q = q.where(ContentItem.project_id == project_id)
    rows = (await session.scalars(q)).all()
    return [ContentItemOut.model_validate(r) for r in rows]


@router.post("/content-items/{ci_id}/start", response_model=BoardOut)
async def start_pipeline(ci_id: int, user: CurrentUser, session: SessionDep) -> BoardOut:
    try:
        await engine.start(session, ci_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return await _board(session, ci_id)


@router.get("/content-items/{ci_id}", response_model=BoardOut)
async def get_board(ci_id: int, user: CurrentUser, session: SessionDep) -> BoardOut:
    return await _board(session, ci_id)


@router.get("/content-items/{ci_id}/deliverables", response_model=list[DeliverableOut])
async def list_deliverable_history(
    ci_id: int, user: CurrentUser, session: SessionDep
) -> list[DeliverableOut]:
    """交付物全量历史（含 superseded 旧版），按 type + version 排序，供版本对比/回滚。"""
    rows = (
        await session.scalars(
            select(Deliverable)
            .where(Deliverable.content_item_id == ci_id)
            .order_by(Deliverable.type, Deliverable.version)
        )
    ).all()
    return [DeliverableOut.model_validate(d) for d in rows]


@router.post("/content-items/{ci_id}/publish-readiness", response_model=PublishReadinessOut)
async def check_publish_readiness(
    ci_id: int,
    body: PublishReadinessRequest,
    user: CurrentUser,
    session: SessionDep,
) -> PublishReadinessOut:
    content_item = await _get_owned_content_item(session, ci_id, user.org_id)
    findings = await _build_publish_readiness_findings(session, body, content_item, user.org_id)
    risk = _readiness_risk(findings)
    ready = risk != ComplianceRisk.BLOCK
    materials = await _load_publish_materials(session, body, user.org_id)
    publish_package = _publish_package(body, content_item, materials)

    task = BrainTask(
        org_id=user.org_id,
        content_item_id=content_item.id,
        title=f"Publish readiness: {content_item.title}",
        type=BrainTaskType.CONTENT_CREATION,
        status=BrainTaskStatus.PENDING_ACCEPTANCE if ready else BrainTaskStatus.FAILED,
        progress=90 if ready else 0,
        current_focus="Waiting for human publish confirmation" if ready else "Publish readiness blocked",
    )
    session.add(task)
    await session.flush()

    tool_call = AgentToolCall(
        org_id=user.org_id,
        task_id=task.id,
        module="content_production",
        agent_code="06-operator",
        tool_code="publish_package_prepare",
        tool_name="Publish Package Prepare",
        status="waiting_approval" if ready else "failed",
        permission_mode="confirm",
        requires_human_confirmation=ready,
        input_summary=f"{body.platform.value} publish check for content item #{content_item.id}",
        output_summary="Publish package ready for manual confirmation"
        if ready
        else "Blocked by publish readiness checks",
        error=None if ready else "Publish readiness blocked",
        meta={
            "content_item_id": content_item.id,
            "content_title": content_item.title,
            "platform": body.platform.value,
            "publish_title": publish_package.title,
            "body_length": len(publish_package.body),
            "topics": publish_package.topics,
            "scheduled_at": publish_package.scheduled_at.isoformat() if publish_package.scheduled_at else None,
            "material_ids": publish_package.material_ids,
            "cover_material_id": publish_package.cover_material_id,
            "visibility": publish_package.visibility,
            "allow_comment": publish_package.allow_comment,
            "publish_package": publish_package.model_dump(mode="json"),
            "risk": risk.value,
            "findings": [finding.model_dump(mode="json") for finding in findings],
        },
    )
    session.add(tool_call)
    await session.commit()
    await session.refresh(tool_call)

    return PublishReadinessOut(
        content_item_id=content_item.id,
        platform=body.platform,
        ready=ready,
        risk=risk,
        package=publish_package,
        findings=findings,
        tool_call=tool_call,
    )


@router.post("/content-items/{ci_id}/rerun", response_model=BoardOut)
async def rerun_stage(
    ci_id: int, body: RerunStageRequest, user: CurrentUser, session: SessionDep
) -> BoardOut:
    """重跑某阶段 Agent，产新版交付物（旧版自动 superseded）。"""
    try:
        await engine.rerun_stage(session, ci_id, body.stage)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return await _board(session, ci_id)


@router.post("/deliverables/{deliverable_id}/rollback", response_model=BoardOut)
async def rollback_deliverable(
    deliverable_id: int, user: CurrentUser, session: SessionDep
) -> BoardOut:
    """回滚到指定历史版本（设回 approved，其余同 type 版本 superseded）。"""
    try:
        d = await engine.rollback_deliverable(session, deliverable_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return await _board(session, d.content_item_id)


@router.get("/gates", response_model=list[PendingGateOut])
async def list_pending_gates(user: CurrentUser, session: SessionDep) -> list[PendingGateOut]:
    """跨内容列出待审质量门（含内容标题 + 脚本合规门的合规预检结果），供审批中心用。"""
    rows = (
        await session.execute(
            select(GateApproval, ContentItem.title)
            .join(ContentItem, GateApproval.content_item_id == ContentItem.id)
            .where(GateApproval.status == GateStatus.PENDING)
            .order_by(GateApproval.id)
        )
    ).all()
    out: list[PendingGateOut] = []
    for g, title in rows:
        compliance = None
        if g.gate == GateType.SCRIPT_COMPLIANCE:
            check = await session.scalar(
                select(ComplianceCheck)
                .where(ComplianceCheck.content_item_id == g.content_item_id)
                .order_by(ComplianceCheck.id.desc())
            )
            if check is not None:
                compliance = ComplianceCheckOut.model_validate(check)
        out.append(
            PendingGateOut(
                id=g.id,
                gate=g.gate,
                status=g.status,
                content_item_id=g.content_item_id,
                content_title=title,
                created_at=g.created_at,
                compliance=compliance,
            )
        )
    return out


@router.post("/gates/{approval_id}/approve", response_model=BoardOut)
async def approve_gate(
    approval_id: int,
    body: ApproveGateRequest,
    user: CurrentUser,
    session: SessionDep,
) -> BoardOut:
    try:
        ci = await engine.approve_gate(session, approval_id, user.id, body.approved, body.comment)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return await _board(session, ci.id)
