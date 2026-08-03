"""Account-, owner-, and thread-scoped conversation attachment contracts."""

import pytest
from sqlalchemy import select

from app.config import settings
from app.core.security import create_access_token
from app.models import Account, AgentRun, ConversationAttachment
from app.models.enums import Platform


@pytest.fixture(autouse=True)
def _stub_runtime_queue(monkeypatch):
    async def enqueue_agent_runtime(*, run_id: int) -> None:
        del run_id

    monkeypatch.setattr(
        "app.api.conversations.enqueue_agent_runtime",
        enqueue_agent_runtime,
    )
    monkeypatch.setattr(settings, "main_agent_typed_runtime_enabled", True)


def _auth(user) -> dict[str, str]:
    token = create_access_token(str(user.id), user.role.value)
    return {"Authorization": f"Bearer {token}"}


async def _thread(client, session, user, name: str) -> dict:
    account = Account(org_id=user.org_id, platform=Platform.DOUYIN, nickname=name)
    session.add(account)
    await session.commit()
    response = await client.post(
        "/brain/conversations",
        headers=_auth(user),
        json={"account_id": account.id, "title": name},
    )
    assert response.status_code == 201
    return response.json()


@pytest.mark.asyncio
async def test_attachment_upload_is_owner_and_thread_scoped(
    client, session, admin, member, monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    monkeypatch.setattr(settings, "storage_local_dir", str(tmp_path))
    thread = await _thread(client, session, admin, "attachments")

    uploaded = await client.post(
        f"/brain/conversations/{thread['id']}/attachments",
        headers=_auth(admin),
        files=[("files", ("brief.txt", b"campaign objective", "text/plain"))],
    )

    assert uploaded.status_code == 201
    attachment = uploaded.json()[0]
    assert attachment["filename"] == "brief.txt"
    assert attachment["scan_status"] == "clean"
    assert attachment["parse_status"] == "ready"
    assert attachment["parsed_context"]["text"] == "campaign objective"
    denied_list = await client.get(
        f"/brain/conversations/{thread['id']}/attachments",
        headers=_auth(member),
    )
    denied_delete = await client.delete(
        f"/brain/conversations/{thread['id']}/attachments/{attachment['id']}",
        headers=_auth(member),
    )
    assert denied_list.status_code == 404
    assert denied_delete.status_code == 404


@pytest.mark.asyncio
async def test_typed_runtime_flag_blocks_attachment_routes(
    client, session, admin, monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    monkeypatch.setattr(settings, "storage_local_dir", str(tmp_path))
    thread = await _thread(client, session, admin, "attachment-feature-flag")
    monkeypatch.setattr(settings, "main_agent_typed_runtime_enabled", False)

    uploaded = await client.post(
        f"/brain/conversations/{thread['id']}/attachments",
        headers=_auth(admin),
        files=[("files", ("brief.txt", b"campaign objective", "text/plain"))],
    )
    listed = await client.get(
        f"/brain/conversations/{thread['id']}/attachments",
        headers=_auth(admin),
    )

    assert uploaded.status_code == 503
    assert listed.status_code == 503
    assert uploaded.json()["detail"]["code"] == "MAIN_AGENT_TYPED_RUNTIME_DISABLED"


@pytest.mark.asyncio
async def test_turn_attachment_resolution_fails_closed_for_wrong_or_unready_scope(
    client, session, admin, monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    monkeypatch.setattr(settings, "storage_local_dir", str(tmp_path))
    source = await _thread(client, session, admin, "source-attachments")
    target = await _thread(client, session, admin, "target-attachments")
    uploaded = await client.post(
        f"/brain/conversations/{source['id']}/attachments",
        headers=_auth(admin),
        files=[("files", ("context.txt", b"trusted context", "text/plain"))],
    )
    attachment_id = uploaded.json()[0]["id"]

    wrong_thread = await client.post(
        f"/brain/conversations/{target['id']}/turns",
        headers=_auth(admin),
        json={
            "client_message_id": "wrong-attachment-thread",
            "message": "use this attachment",
            "attachment_ids": [attachment_id],
        },
    )
    assert wrong_thread.status_code == 404

    row = await session.get(ConversationAttachment, attachment_id)
    assert row is not None
    row.parse_status = "pending"
    await session.commit()
    unready = await client.post(
        f"/brain/conversations/{source['id']}/turns",
        headers=_auth(admin),
        json={
            "client_message_id": "unready-attachment",
            "message": "use this attachment",
            "attachment_ids": [attachment_id],
        },
    )
    assert unready.status_code == 409


@pytest.mark.asyncio
async def test_ready_attachment_context_is_frozen_into_run_request(
    client, session, admin, monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    monkeypatch.setattr(settings, "storage_local_dir", str(tmp_path))
    thread = await _thread(client, session, admin, "ready-attachment")
    uploaded = await client.post(
        f"/brain/conversations/{thread['id']}/attachments",
        headers=_auth(admin),
        files=[("files", ("notes.txt", b"use proof points", "text/plain"))],
    )
    attachment_id = uploaded.json()[0]["id"]

    submitted = await client.post(
        f"/brain/conversations/{thread['id']}/turns",
        headers=_auth(admin),
        json={
            "client_message_id": "ready-attachment-turn",
            "message": "draft a script",
            "attachment_ids": [attachment_id, attachment_id],
        },
    )

    assert submitted.status_code == 202
    run = await session.scalar(
        select(AgentRun).where(AgentRun.client_message_id == "ready-attachment-turn")
    )
    assert run is not None
    assert run.request_payload["attachment_ids"] == [attachment_id]
    assert run.request_payload["attachment_contexts"] == [
        {
            "id": attachment_id,
            "filename": "notes.txt",
            "mime_type": "text/plain",
            "parsed_context": {"text": "use proof points"},
        }
    ]
