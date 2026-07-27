from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import storage
from app.core.auth import CurrentUser
from app.core.workspace_access import require_account_access
from app.db import get_session
from app.models import DataImportBatch, DataImportRow
from app.models.enums import WorkspaceRole
from app.schemas.account_data import (
    AccountDataStatusOut,
    ImportArtifactOut,
    ImportBatchListOut,
    ImportBatchOut,
    ImportBatchSummaryOut,
    ImportConflictOut,
    ImportRowOut,
    ManualPreviewRequest,
    ResolveImportRowRequest,
)
from app.services.data_import.parser import ParseFailure
from app.services.data_import.service import (
    DataImportBatchNotFoundError,
    DataImportCommitConflictError,
    DataImportDeleteConflictError,
    DataImportRevokeConflictError,
    DataImportStateError,
    RowMatchResolution,
    account_status_summary,
    commit_batch,
    create_manual_preview,
    create_preview,
    delete_batch_permanently,
    list_scoped_batches,
    load_scoped_artifact,
    load_scoped_batch,
    resolve_row_match,
    revoke_batch,
)

router = APIRouter(prefix="/account-data", tags=["account-data"])
MAX_SCREENSHOT_BYTES = 5 * 1024 * 1024

SessionDep = Annotated[AsyncSession, Depends(get_session)]

OPERATE_ROLES = {WorkspaceRole.LEAD, WorkspaceRole.OPERATOR}
REVOKE_ROLES = {WorkspaceRole.LEAD}


def _artifact_out(account_id: int, batch_id: int, artifact) -> ImportArtifactOut:
    return ImportArtifactOut(
        id=artifact.id,
        filename=artifact.filename,
        content_type=artifact.content_type,
        byte_size=artifact.byte_size,
        sha256=artifact.sha256,
        download_url=f"/account-data/{account_id}/imports/{batch_id}/artifacts/{artifact.id}",
    )


def _row_out(row: DataImportRow) -> ImportRowOut:
    return ImportRowOut.model_validate(row)


def _conflict_out(conflict) -> ImportConflictOut:
    return ImportConflictOut(
        id=conflict.id,
        row_number=conflict.row_number,
        status=conflict.status,
        field_name=conflict.field_name,
        conflict_code=conflict.conflict_code,
        message=conflict.message,
        candidate_content_ids=list(conflict.candidate_content_ids or []),
        resolved_by_id=conflict.resolved_by_id,
        resolved_at=conflict.resolved_at,
    )


def _batch_summary_out(batch: DataImportBatch) -> ImportBatchSummaryOut:
    return ImportBatchSummaryOut.model_validate(batch)


def _batch_out(batch: DataImportBatch) -> ImportBatchOut:
    return ImportBatchOut(
        **_batch_summary_out(batch).model_dump(),
        artifacts=[_artifact_out(batch.account_id, batch.id, item) for item in batch.artifacts],
        rows=[_row_out(item) for item in batch.rows],
        conflicts=[_conflict_out(item) for item in batch.conflicts],
    )


def _bad_request(detail: str, *, status_code: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(status_code=status_code, detail=detail)


@router.post(
    "/{account_id}/imports",
    response_model=ImportBatchOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_import(
    account_id: int,
    user: CurrentUser,
    session: SessionDep,
    file: Annotated[UploadFile, File(...)],
) -> ImportBatchOut:
    account = await require_account_access(session, user, account_id, roles=OPERATE_ROLES)
    try:
        content = await file.read()
        batch = await create_preview(
            session,
            user=user,
            account=account,
            filename=file.filename or "upload.xlsx",
            content=content,
        )
        await session.commit()
    except ParseFailure as exc:
        raise _bad_request(str(exc), status_code=status.HTTP_422_UNPROCESSABLE_ENTITY) from exc
    except ValueError as exc:
        raise _bad_request(str(exc)) from exc
    return _batch_out(batch)


@router.post(
    "/{account_id}/manual-previews",
    response_model=ImportBatchOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_manual_data_preview(
    account_id: int,
    user: CurrentUser,
    session: SessionDep,
    payload: Annotated[str, Form(...)],
    screenshot: Annotated[UploadFile | None, File()] = None,
) -> ImportBatchOut:
    account = await require_account_access(session, user, account_id, roles=OPERATE_ROLES)
    try:
        request = ManualPreviewRequest.model_validate_json(payload)
        screenshot_content = (
            await screenshot.read(MAX_SCREENSHOT_BYTES + 1)
            if screenshot is not None
            else None
        )
        batch = await create_manual_preview(
            session,
            user=user,
            account=account,
            payload=request.model_dump(mode="json"),
            screenshot_filename=screenshot.filename if screenshot is not None else None,
            screenshot_content=screenshot_content,
        )
        await session.commit()
    except ValidationError as exc:
        raise _bad_request(
            "Manual data fields are invalid",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        ) from exc
    except ValueError as exc:
        raise _bad_request(str(exc), status_code=status.HTTP_422_UNPROCESSABLE_ENTITY) from exc
    return _batch_out(batch)


@router.get("/{account_id}/imports", response_model=ImportBatchListOut)
async def list_imports(
    account_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> ImportBatchListOut:
    account = await require_account_access(session, user, account_id)
    rows = await list_scoped_batches(session, org_id=user.org_id, account_id=account.id)
    return ImportBatchListOut(items=[_batch_summary_out(item) for item in rows])


@router.get("/{account_id}/imports/{batch_id}", response_model=ImportBatchOut)
async def get_import_batch(
    account_id: int,
    batch_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> ImportBatchOut:
    account = await require_account_access(session, user, account_id)
    try:
        batch = await load_scoped_batch(
            session,
            org_id=user.org_id,
            account_id=account.id,
            batch_id=batch_id,
        )
    except DataImportBatchNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="import batch does not exist",
        ) from exc
    return _batch_out(batch)


@router.patch("/{account_id}/imports/{batch_id}/rows/{row_number}", response_model=ImportRowOut)
async def resolve_import_row(
    account_id: int,
    batch_id: int,
    row_number: int,
    body: ResolveImportRowRequest,
    user: CurrentUser,
    session: SessionDep,
) -> ImportRowOut:
    account = await require_account_access(session, user, account_id, roles=OPERATE_ROLES)
    try:
        batch = await load_scoped_batch(
            session,
            org_id=user.org_id,
            account_id=account.id,
            batch_id=batch_id,
        )
        row = await resolve_row_match(
            session,
            batch=batch,
            row_number=row_number,
            resolution=RowMatchResolution(
                selected_content_id=body.selected_content_id,
                resolved_by=user,
                confirmed=body.confirmed,
            ),
        )
    except DataImportBatchNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="import batch does not exist",
        ) from exc
    except ValueError as exc:
        raise _bad_request(str(exc), status_code=status.HTTP_409_CONFLICT) from exc
    return _row_out(row)


@router.post("/{account_id}/imports/{batch_id}/commit", response_model=ImportBatchOut)
async def commit_import_batch(
    account_id: int,
    batch_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> ImportBatchOut:
    account = await require_account_access(session, user, account_id, roles=OPERATE_ROLES)
    try:
        batch = await commit_batch(
            session,
            org_id=user.org_id,
            account_id=account.id,
            batch_id=batch_id,
            actor=user,
        )
    except DataImportBatchNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="import batch does not exist",
        ) from exc
    except DataImportCommitConflictError as exc:
        raise _bad_request(str(exc), status_code=status.HTTP_409_CONFLICT) from exc
    except DataImportStateError as exc:
        raise _bad_request(str(exc), status_code=status.HTTP_409_CONFLICT) from exc
    return _batch_out(batch)


@router.post("/{account_id}/imports/{batch_id}/revoke", response_model=ImportBatchOut)
async def revoke_import_batch(
    account_id: int,
    batch_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> ImportBatchOut:
    account = await require_account_access(session, user, account_id, roles=REVOKE_ROLES)
    try:
        batch = await revoke_batch(
            session,
            org_id=user.org_id,
            account_id=account.id,
            batch_id=batch_id,
            actor=user,
        )
    except DataImportBatchNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="import batch does not exist",
        ) from exc
    except DataImportRevokeConflictError as exc:
        raise _bad_request(str(exc), status_code=status.HTTP_409_CONFLICT) from exc
    except DataImportStateError as exc:
        raise _bad_request(str(exc), status_code=status.HTTP_409_CONFLICT) from exc
    return _batch_out(batch)


@router.delete(
    "/{account_id}/imports/{batch_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_import_batch(
    account_id: int,
    batch_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> Response:
    account = await require_account_access(session, user, account_id, roles=REVOKE_ROLES)
    try:
        await delete_batch_permanently(
            session,
            org_id=user.org_id,
            account_id=account.id,
            batch_id=batch_id,
            actor=user,
        )
    except DataImportBatchNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="import batch does not exist",
        ) from exc
    except DataImportDeleteConflictError as exc:
        raise _bad_request(str(exc), status_code=status.HTTP_409_CONFLICT) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{account_id}/imports/{batch_id}/artifacts/{artifact_id}")
async def download_import_artifact(
    account_id: int,
    batch_id: int,
    artifact_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> FileResponse:
    account = await require_account_access(session, user, account_id)
    try:
        artifact = await load_scoped_artifact(
            session,
            org_id=user.org_id,
            account_id=account.id,
            batch_id=batch_id,
            artifact_id=artifact_id,
        )
    except DataImportBatchNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="artifact does not exist",
        ) from exc
    path = storage.resolve(artifact.storage_key)
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="artifact does not exist")
    return FileResponse(path, media_type=artifact.content_type, filename=artifact.filename)


@router.get("/{account_id}/status", response_model=AccountDataStatusOut)
async def get_account_data_status(
    account_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> AccountDataStatusOut:
    account = await require_account_access(session, user, account_id)
    payload = await account_status_summary(session, org_id=user.org_id, account_id=account.id)
    return AccountDataStatusOut.model_validate(payload)
