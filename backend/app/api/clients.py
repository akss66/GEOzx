from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AdminUser, CurrentUser
from app.core.workspace_access import accessible_client_ids
from app.db import get_session
from app.models import Client
from app.models.enums import ClientStatus
from app.schemas.client import ClientOut, CreateClientRequest, UpdateClientRequest

router = APIRouter(prefix="/clients", tags=["clients"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("", response_model=list[ClientOut])
async def list_clients(user: CurrentUser, session: SessionDep) -> list[ClientOut]:
    ids = await accessible_client_ids(session, user)
    if not ids:
        return []
    rows = await session.scalars(
        select(Client).where(Client.id.in_(ids)).order_by(Client.id)
    )
    return [ClientOut.model_validate(row) for row in rows]


@router.post("", response_model=ClientOut, status_code=status.HTTP_201_CREATED)
async def create_client(
    body: CreateClientRequest, admin: AdminUser, session: SessionDep
) -> ClientOut:
    client = Client(org_id=admin.org_id, name=body.name)
    session.add(client)
    await session.commit()
    await session.refresh(client)
    return ClientOut.model_validate(client)


@router.delete("/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_client(
    client_id: int,
    admin: AdminUser,
    session: SessionDep,
) -> None:
    client = await session.get(Client, client_id)
    if client is None or client.org_id != admin.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="客户不存在")
    client.status = ClientStatus.ARCHIVED
    await session.commit()


@router.patch("/{client_id}", response_model=ClientOut)
async def update_client(
    client_id: int, body: UpdateClientRequest, admin: AdminUser, session: SessionDep
) -> ClientOut:
    client = await session.get(Client, client_id)
    if client is None or client.org_id != admin.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="客户不存在")
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(client, key, value)
    await session.commit()
    await session.refresh(client)
    return ClientOut.model_validate(client)
