"""Owned conversation attachment endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser
from app.core.main_agent_runtime import require_main_agent_runtime_enabled
from app.db import get_session
from app.schemas.attachment import ConversationAttachmentOut
from app.services.attachments import (
    MAX_ATTACHMENT_BYTES,
    AttachmentUpload,
    create_conversation_attachments,
    delete_conversation_attachment,
    list_conversation_attachments,
)
from app.services.conversations import get_conversation_thread

router = APIRouter(prefix="/brain/conversations", tags=["brain-attachments"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post(
    "/{thread_id}/attachments",
    response_model=list[ConversationAttachmentOut],
    status_code=status.HTTP_201_CREATED,
)
async def upload_attachments(
    thread_id: int,
    user: CurrentUser,
    session: SessionDep,
    files: Annotated[list[UploadFile], File(...)],
) -> list[ConversationAttachmentOut]:
    require_main_agent_runtime_enabled()
    thread = await get_conversation_thread(session, user, thread_id)
    uploads = [
        AttachmentUpload(
            filename=item.filename or "attachment",
            mime_type=item.content_type or "application/octet-stream",
            content=await item.read(MAX_ATTACHMENT_BYTES + 1),
        )
        for item in files
    ]
    rows = await create_conversation_attachments(
        session,
        user=user,
        thread=thread,
        uploads=uploads,
    )
    return [ConversationAttachmentOut.model_validate(row) for row in rows]


@router.get("/{thread_id}/attachments", response_model=list[ConversationAttachmentOut])
async def get_attachments(
    thread_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> list[ConversationAttachmentOut]:
    require_main_agent_runtime_enabled()
    thread = await get_conversation_thread(session, user, thread_id)
    rows = await list_conversation_attachments(session, user=user, thread=thread)
    return [ConversationAttachmentOut.model_validate(row) for row in rows]


@router.delete(
    "/{thread_id}/attachments/{attachment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_attachment(
    thread_id: int,
    attachment_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> None:
    require_main_agent_runtime_enabled()
    thread = await get_conversation_thread(session, user, thread_id)
    await delete_conversation_attachment(
        session,
        user=user,
        thread=thread,
        attachment_id=attachment_id,
    )
