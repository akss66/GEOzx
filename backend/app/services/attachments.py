"""Safe storage and fail-closed resolution for conversation attachments."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePath
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import storage
from app.models import ConversationAttachment, ConversationThread, User
from app.schemas.attachment import AttachmentContext

MAX_ATTACHMENT_FILES = 5
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_PARSED_TEXT_CHARS = 20_000
ALLOWED_MIME_TYPES = frozenset(
    {
        "text/plain",
        "text/csv",
        "application/json",
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "image/jpeg",
        "image/png",
        "image/webp",
    }
)


@dataclass(frozen=True)
class AttachmentUpload:
    filename: str
    mime_type: str
    content: bytes


async def create_conversation_attachments(
    session: AsyncSession,
    *,
    user: User,
    thread: ConversationThread,
    uploads: list[AttachmentUpload],
) -> list[ConversationAttachment]:
    if thread.org_id != user.org_id or thread.created_by_id != user.id:
        raise _not_found()
    if not uploads or len(uploads) > MAX_ATTACHMENT_FILES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"每次只能上传 1 至 {MAX_ATTACHMENT_FILES} 个附件",
        )
    written_keys: list[str] = []
    rows: list[ConversationAttachment] = []
    try:
        for upload in uploads:
            filename = PurePath(upload.filename).name.strip() or "attachment"
            mime_type = upload.mime_type.lower().strip()
            if mime_type not in ALLOWED_MIME_TYPES:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"不支持的附件类型：{mime_type or 'unknown'}",
                )
            if not upload.content or len(upload.content) > MAX_ATTACHMENT_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="附件不能为空且单文件不得超过 10 MB",
                )
            if b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE" in upload.content:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="附件安全扫描未通过",
                )
            digest = hashlib.sha256(upload.content).hexdigest()
            extension = Path(filename).suffix.lower()[:16]
            storage_key = (
                f"conversation-attachments/{user.org_id}/{thread.account_id}/"
                f"{thread.id}/{uuid4().hex}{extension}"
            )
            storage.save_bytes(storage_key, upload.content)
            written_keys.append(storage_key)
            row = ConversationAttachment(
                org_id=user.org_id,
                created_by_id=user.id,
                account_id=thread.account_id,
                thread_id=thread.id,
                filename=filename[:255],
                mime_type=mime_type,
                size_bytes=len(upload.content),
                storage_key=storage_key,
                sha256=digest,
                scan_status="clean",
                parse_status="ready",
                parsed_context=_parse_context(filename, mime_type, upload.content),
            )
            session.add(row)
            rows.append(row)
        await session.commit()
        for row in rows:
            await session.refresh(row)
        return rows
    except Exception:
        await session.rollback()
        for key in written_keys:
            storage.resolve(key).unlink(missing_ok=True)
        raise


async def list_conversation_attachments(
    session: AsyncSession,
    *,
    user: User,
    thread: ConversationThread,
) -> list[ConversationAttachment]:
    if thread.org_id != user.org_id or thread.created_by_id != user.id:
        raise _not_found()
    return list(
        await session.scalars(
            select(ConversationAttachment)
            .where(ConversationAttachment.thread_id == thread.id)
            .order_by(ConversationAttachment.created_at, ConversationAttachment.id)
        )
    )


async def delete_conversation_attachment(
    session: AsyncSession,
    *,
    user: User,
    thread: ConversationThread,
    attachment_id: int,
) -> None:
    row = await session.scalar(
        select(ConversationAttachment).where(
            ConversationAttachment.id == attachment_id,
            ConversationAttachment.org_id == user.org_id,
            ConversationAttachment.created_by_id == user.id,
            ConversationAttachment.account_id == thread.account_id,
            ConversationAttachment.thread_id == thread.id,
        )
    )
    if row is None:
        raise _not_found()
    key = row.storage_key
    await session.delete(row)
    await session.commit()
    storage.resolve(key).unlink(missing_ok=True)


async def resolve_attachment_contexts(
    session: AsyncSession,
    *,
    user: User,
    thread: ConversationThread,
    attachment_ids: list[int],
) -> list[AttachmentContext]:
    ordered_ids = list(dict.fromkeys(item for item in attachment_ids if item > 0))
    if not ordered_ids:
        return []
    rows = list(
        await session.scalars(
            select(ConversationAttachment).where(
                ConversationAttachment.id.in_(ordered_ids),
                ConversationAttachment.org_id == user.org_id,
                ConversationAttachment.created_by_id == user.id,
                ConversationAttachment.account_id == thread.account_id,
                ConversationAttachment.thread_id == thread.id,
            )
        )
    )
    by_id = {row.id: row for row in rows}
    if len(by_id) != len(ordered_ids):
        raise _not_found()
    ordered = [by_id[item] for item in ordered_ids]
    if any(row.scan_status != "clean" or row.parse_status != "ready" for row in ordered):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "ATTACHMENT_NOT_READY",
                "message": "附件仍在安全扫描或解析中，请稍后重试",
            },
        )
    return [
        AttachmentContext(
            id=row.id,
            filename=row.filename,
            mime_type=row.mime_type,
            parsed_context=dict(row.parsed_context or {}),
        )
        for row in ordered
    ]


def _parse_context(filename: str, mime_type: str, content: bytes) -> dict:
    if mime_type in {"text/plain", "text/csv", "application/json"}:
        text = content.decode("utf-8-sig", errors="replace")[:MAX_PARSED_TEXT_CHARS]
        if mime_type == "application/json":
            try:
                json.loads(text)
            except json.JSONDecodeError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"JSON 附件无法解析：{filename}",
                ) from exc
        return {"text": text}
    return {"summary": f"已附加文件：{filename}"}


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="附件不存在")
