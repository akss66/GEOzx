from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Client
from app.models.enums import ClientStatus


async def get_or_create_default_client(session: AsyncSession, org_id: int) -> Client:
    clients = (
        await session.scalars(
            select(Client)
            .where(Client.org_id == org_id, Client.status == ClientStatus.ACTIVE)
            .order_by(Client.id)
        )
    ).all()
    if len(clients) == 1:
        return clients[0]
    named = next((client for client in clients if client.name == "默认客户"), None)
    if named is not None:
        return named
    client = Client(org_id=org_id, name="默认客户")
    session.add(client)
    await session.flush()
    return client
