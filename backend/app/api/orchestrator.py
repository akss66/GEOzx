"""编排路由：创建内容、启动流水线、看板视图、质量门审批。"""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.approval_audit import add_approval_decided, add_approval_requested
from app.core.auth import CurrentUser
from app.core.workspace_access import accessible_project_ids, require_project_access
from app.db import get_session
from app.models import (
    Account,
    AgentTask,
    AgentToolCall,
    BrainTask,
    ComplianceCheck,
    ContentItem,
    Deliverable,
    Event,
    GateApproval,
    MaterialAsset,
    Project,
    ProjectAccount,
)
from app.models.enums import (
    AccountStatus,
    BrainTaskStatus,
    BrainTaskType,
    ComplianceRisk,
    DeliverableStatus,
    GateStatus,
    GateType,
    MaterialStatus,
    Platform,
    WorkspaceRole,
)
from app.orchestrator.engine import engine
from app.schemas.brain import AgentToolCallOut
from app.schemas.deliverable import validate_payload
from app.schemas.material import MaterialAssetOut
from app.schemas.orchestrator import (
    AgentTaskOut,
    ApproveGateRequest,
    BoardOut,
    ComplianceCheckOut,
    ContentItemOut,
    ContentWorkspaceAccountOut,
    ContentWorkspaceOut,
    CreateContentItemRequest,
    CreateDeliverableRevisionRequest,
    DeliverableOut,
    GateApprovalOut,
    PendingGateOut,
    PublishCapabilityOut,
    PublishPackageOut,
    PublishReadinessFinding,
    PublishReadinessOut,
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


async def _content_item_for_user(
    session: AsyncSession,
    ci_id: int,
    user,
    roles: set[WorkspaceRole] | None = None,
) -> ContentItem:
    content_item = await session.get(ContentItem, ci_id)
    if content_item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="内容不存在")
    await require_project_access(session, user, content_item.project_id, roles=roles)
    return content_item


async def _content_workspace(
    session: AsyncSession,
    content_item: ContentItem,
) -> ContentWorkspaceOut:
    board = await _board(session, content_item.id)
    project = await session.get(Project, content_item.project_id)
    account = (
        await session.get(Account, content_item.account_id)
        if content_item.account_id is not None
        else None
    )
    materials = list(
        await session.scalars(
            select(MaterialAsset)
            .where(MaterialAsset.content_item_id == content_item.id)
            .order_by(MaterialAsset.id.desc())
        )
    )
    publish_calls = list(
        await session.scalars(
            select(AgentToolCall)
            .join(BrainTask, AgentToolCall.task_id == BrainTask.id)
            .where(
                BrainTask.content_item_id == content_item.id,
                AgentToolCall.module == "content_production",
            )
            .order_by(AgentToolCall.id.desc())
        )
    )
    return ContentWorkspaceOut(
        **board.model_dump(),
        project_name=project.name if project is not None else "未知项目",
        account=(
            ContentWorkspaceAccountOut(
                id=account.id,
                nickname=account.nickname,
                platform=account.platform,
                auth_status=account.auth_status,
            )
            if account is not None
            else None
        ),
        materials=[
            MaterialAssetOut(
                id=asset.id,
                content_item_id=asset.content_item_id,
                deliverable_id=asset.deliverable_id,
                kind=asset.kind,
                provider=asset.provider,
                status=asset.status,
                size_bytes=asset.size_bytes,
                file_url=(
                    f"/materials/{asset.id}/file" if asset.status == MaterialStatus.READY else None
                ),
                error=asset.error,
                created_at=asset.created_at,
            )
            for asset in materials
        ],
        publish_tool_calls=[AgentToolCallOut.model_validate(call) for call in publish_calls],
    )


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

    if content_item.account_id is None:
        findings.append(
            _finding(
                ComplianceRisk.BLOCK,
                "account.required",
                "Select an account before preparing a publish package.",
            )
        )
    else:
        account = await session.get(Account, content_item.account_id)
        if account is None or account.org_id != org_id:
            findings.append(
                _finding(
                    ComplianceRisk.BLOCK,
                    "account.missing",
                    "The selected account is not available.",
                )
            )
        elif account.platform != body.platform:
            findings.append(
                _finding(
                    ComplianceRisk.BLOCK,
                    "account.platform_mismatch",
                    "The selected account does not belong to the publish platform.",
                )
            )
        elif account.auth_status not in {"authorized", "manual"}:
            findings.append(
                _finding(
                    ComplianceRisk.BLOCK,
                    "account.authorization_required",
                    "Authorize the selected account before preparing a publish package.",
                )
            )

    if not title:
        findings.append(_finding(ComplianceRisk.BLOCK, "title.required", "Title is required."))
    elif len(title) > 30:
        findings.append(
            _finding(
                ComplianceRisk.WARN,
                "title.long",
                "Douyin publishing UI commonly truncates long titles; "
                "keep it within 30 chars when possible.",
            )
        )
    else:
        findings.append(_finding(ComplianceRisk.PASS, "title.ok", "Title is ready."))

    if len(body_text) > 1000:
        findings.append(
            _finding(
                ComplianceRisk.WARN, "body.long", "Body copy is long; review it before publishing."
            )
        )
    if len(topics) > 10:
        findings.append(
            _finding(ComplianceRisk.BLOCK, "topics.too_many", "At most 10 topics are allowed.")
        )
    for topic in topics:
        if len(topic) > 20:
            findings.append(
                _finding(
                    ComplianceRisk.WARN, "topic.long", f"Topic '{topic}' is longer than 20 chars."
                )
            )

    if body.scheduled_at is not None:
        now = (
            datetime.now(tz=body.scheduled_at.tzinfo)
            if body.scheduled_at.tzinfo
            else datetime.now()
        )
        if body.scheduled_at <= now:
            findings.append(
                _finding(
                    ComplianceRisk.BLOCK,
                    "schedule.past",
                    "Scheduled publish time must be in the future.",
                )
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
            findings.append(
                _finding(ComplianceRisk.PASS, "schedule.ok", "Scheduled time is valid.")
            )

    material_ids_to_load = set(body.material_ids)
    if body.cover_material_id is not None:
        material_ids_to_load.add(body.cover_material_id)

    if not body.material_ids:
        findings.append(
            _finding(
                ComplianceRisk.BLOCK,
                "material.required",
                "At least one ready video or image material is required.",
            )
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
                _finding(
                    ComplianceRisk.BLOCK,
                    "material.missing",
                    f"Material #{material_id} is not available.",
                )
            )
            continue
        if material.status != MaterialStatus.READY:
            findings.append(
                _finding(
                    ComplianceRisk.BLOCK,
                    "material.not_ready",
                    f"Material #{material_id} is not ready.",
                )
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
        supported = (
            SUPPORTED_VIDEO_EXTENSIONS if material.kind == "video" else SUPPORTED_IMAGE_EXTENSIONS
        )
        if not suffix:
            findings.append(
                _finding(
                    ComplianceRisk.BLOCK,
                    "material.path",
                    f"Material #{material_id} has no file path.",
                )
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
                _finding(
                    ComplianceRisk.PASS, "material.ok", f"Material #{material_id} is publishable."
                )
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
                _finding(
                    ComplianceRisk.PASS,
                    "cover.ok",
                    f"Cover material #{body.cover_material_id} is ready.",
                )
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
        "设置可见范围为 "
        f"{package.visibility}，评论开关为 {'开启' if package.allow_comment else '关闭'}。",
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
    project = await require_project_access(
        session,
        user,
        body.project_id,
        roles={WorkspaceRole.LEAD, WorkspaceRole.OPERATOR, WorkspaceRole.EDITOR},
    )
    if body.account_id is not None:
        account = await session.get(Account, body.account_id)
        if account is None or account.org_id != user.org_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="账号不存在")
        project_account_id = await session.scalar(
            select(ProjectAccount.id).where(
                ProjectAccount.project_id == project.id,
                ProjectAccount.account_id == account.id,
            )
        )
        linked = account.project_id == project.id or project_account_id is not None
        if not linked:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="账号未绑定当前项目",
            )
        if account.status != AccountStatus.ACTIVE:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="当前账号已停用",
            )
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
    project_ids = await accessible_project_ids(session, user)
    if not project_ids:
        return []
    q = (
        select(ContentItem)
        .where(ContentItem.project_id.in_(project_ids))
        .order_by(ContentItem.id.desc())
    )
    if project_id is not None:
        await require_project_access(session, user, project_id)
        q = q.where(ContentItem.project_id == project_id)
    rows = (await session.scalars(q)).all()
    return [ContentItemOut.model_validate(r) for r in rows]


@router.post("/content-items/{ci_id}/start", response_model=BoardOut)
async def start_pipeline(ci_id: int, user: CurrentUser, session: SessionDep) -> BoardOut:
    await _content_item_for_user(
        session,
        ci_id,
        user,
        roles={WorkspaceRole.LEAD, WorkspaceRole.OPERATOR, WorkspaceRole.EDITOR},
    )
    try:
        await engine.start(session, ci_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return await _board(session, ci_id)


@router.get("/content-items/{ci_id}", response_model=BoardOut)
async def get_board(ci_id: int, user: CurrentUser, session: SessionDep) -> BoardOut:
    await _content_item_for_user(session, ci_id, user)
    return await _board(session, ci_id)


@router.get("/content-items/{ci_id}/workspace", response_model=ContentWorkspaceOut)
async def get_content_workspace(
    ci_id: int, user: CurrentUser, session: SessionDep
) -> ContentWorkspaceOut:
    content_item = await _content_item_for_user(session, ci_id, user)
    return await _content_workspace(session, content_item)


@router.get("/content-items/{ci_id}/deliverables", response_model=list[DeliverableOut])
async def list_deliverable_history(
    ci_id: int, user: CurrentUser, session: SessionDep
) -> list[DeliverableOut]:
    """交付物全量历史（含 superseded 旧版），按 type + version 排序，供版本对比/回滚。"""
    await _content_item_for_user(session, ci_id, user)
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
    content_item = await _content_item_for_user(
        session,
        ci_id,
        user,
        roles={WorkspaceRole.LEAD, WorkspaceRole.OPERATOR, WorkspaceRole.EDITOR},
    )
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
        current_focus="Waiting for human publish confirmation"
        if ready
        else "Publish readiness blocked",
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
            "scheduled_at": publish_package.scheduled_at.isoformat()
            if publish_package.scheduled_at
            else None,
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
    await session.flush()
    if ready:
        await add_approval_requested(
            session,
            org_id=user.org_id,
            project_id=content_item.project_id,
            content_item_id=content_item.id,
            approval_kind="tool_call",
            source_id=tool_call.id,
            title=content_item.title,
            body="发布包已准备完成，等待人工确认后进入发布流程。",
        )
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
    await _content_item_for_user(
        session,
        ci_id,
        user,
        roles={WorkspaceRole.LEAD, WorkspaceRole.OPERATOR, WorkspaceRole.EDITOR},
    )
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
    deliverable = await session.get(Deliverable, deliverable_id)
    if deliverable is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="交付物不存在")
    await _content_item_for_user(
        session,
        deliverable.content_item_id,
        user,
        roles={WorkspaceRole.LEAD, WorkspaceRole.OPERATOR, WorkspaceRole.EDITOR},
    )
    try:
        d = await engine.rollback_deliverable(session, deliverable_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return await _board(session, d.content_item_id)


@router.post(
    "/deliverables/{deliverable_id}/revisions",
    response_model=DeliverableOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_deliverable_revision(
    deliverable_id: int,
    body: CreateDeliverableRevisionRequest,
    user: CurrentUser,
    session: SessionDep,
) -> DeliverableOut:
    source = await session.get(Deliverable, deliverable_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="交付物不存在")
    content_item = await _content_item_for_user(
        session,
        source.content_item_id,
        user,
        roles={WorkspaceRole.LEAD, WorkspaceRole.OPERATOR, WorkspaceRole.EDITOR},
    )
    try:
        payload = validate_payload(source.type, body.payload).model_dump()
    except (KeyError, ValidationError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="交付物内容不符合当前类型要求",
        ) from exc

    current = list(
        await session.scalars(
            select(Deliverable).where(
                Deliverable.content_item_id == source.content_item_id,
                Deliverable.type == source.type,
                Deliverable.status != DeliverableStatus.SUPERSEDED,
            )
        )
    )
    for row in current:
        row.status = DeliverableStatus.SUPERSEDED
    latest_version = await session.scalar(
        select(func.max(Deliverable.version)).where(
            Deliverable.content_item_id == source.content_item_id,
            Deliverable.type == source.type,
        )
    )
    revision = Deliverable(
        content_item_id=source.content_item_id,
        agent_code=source.agent_code,
        type=source.type,
        version=(latest_version or 0) + 1,
        status=DeliverableStatus.PENDING_REVIEW,
        payload=payload,
        note=body.note,
    )
    session.add(revision)
    session.add(
        Event(
            type="deliverable.revision.created",
            content_item_id=content_item.id,
            project_id=content_item.project_id,
            payload={
                "source_deliverable_id": source.id,
                "deliverable_type": source.type.value,
                "version": revision.version,
                "created_by": user.id,
            },
        )
    )
    await session.commit()
    await session.refresh(revision)
    return DeliverableOut.model_validate(revision)


@router.get("/gates", response_model=list[PendingGateOut])
async def list_pending_gates(user: CurrentUser, session: SessionDep) -> list[PendingGateOut]:
    """跨内容列出待审质量门（含内容标题 + 脚本合规门的合规预检结果），供审批中心用。"""
    project_ids = await accessible_project_ids(session, user)
    if not project_ids:
        return []
    rows = (
        await session.execute(
            select(GateApproval, ContentItem.title)
            .join(ContentItem, GateApproval.content_item_id == ContentItem.id)
            .where(
                GateApproval.status == GateStatus.PENDING,
                ContentItem.project_id.in_(project_ids),
            )
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
    gate = await session.get(GateApproval, approval_id)
    if gate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="质量门不存在")
    content_item = await _content_item_for_user(
        session,
        gate.content_item_id,
        user,
        roles={WorkspaceRole.LEAD, WorkspaceRole.REVIEWER},
    )
    try:
        ci = await engine.approve_gate(session, approval_id, user.id, body.approved, body.comment)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await add_approval_decided(
        session,
        org_id=user.org_id,
        project_id=content_item.project_id,
        content_item_id=content_item.id,
        approval_kind="gate",
        source_id=gate.id,
        title=content_item.title,
        approved=body.approved,
        actor_user_id=user.id,
        comment=body.comment,
    )
    await session.commit()
    return await _board(session, content_item.id if ci is None else ci.id)
