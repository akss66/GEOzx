from __future__ import annotations

import json
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import UploadFile

from app.api import account_data as account_data_api


@pytest.mark.asyncio
async def test_upload_import_commits_before_returning_success(monkeypatch):
    account = SimpleNamespace(id=2)
    batch = object()
    response = object()
    session = AsyncMock()
    user = SimpleNamespace(org_id=1)

    async def require_account(*args, **kwargs):
        return account

    async def create_preview(*args, **kwargs):
        return batch

    monkeypatch.setattr(account_data_api, "require_account_access", require_account)
    monkeypatch.setattr(account_data_api, "create_preview", create_preview)
    monkeypatch.setattr(account_data_api, "_batch_out", lambda value: response)

    result = await account_data_api.upload_import(
        account_id=2,
        user=user,
        session=session,
        file=UploadFile(filename="works.xlsx", file=BytesIO(b"workbook")),
    )

    assert result is response
    session.commit.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_manual_preview_commits_before_returning_success(monkeypatch):
    account = SimpleNamespace(id=2)
    batch = object()
    response = object()
    session = AsyncMock()
    user = SimpleNamespace(org_id=1)
    payload = {
        "data_domain": "account_period_totals",
        "stat_date": "2026-07-21",
        "period_start": "2026-07-15",
        "period_end": "2026-07-21",
        "account_metrics": {"total_play": 578},
    }

    async def require_account(*args, **kwargs):
        return account

    async def create_manual_preview(*args, **kwargs):
        return batch

    monkeypatch.setattr(account_data_api, "require_account_access", require_account)
    monkeypatch.setattr(account_data_api, "create_manual_preview", create_manual_preview)
    monkeypatch.setattr(account_data_api, "_batch_out", lambda value: response)

    result = await account_data_api.create_manual_data_preview(
        account_id=2,
        user=user,
        session=session,
        payload=json.dumps(payload),
        screenshot=None,
    )

    assert result is response
    session.commit.assert_awaited_once_with()
