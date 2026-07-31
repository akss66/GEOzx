"""Backfill legacy account-data imports into canonical field observations."""

from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy import select

from app.db import async_session
from app.models import Account
from app.services.data_import.backfill import backfill_account_observations


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill committed account-data batches for one organization.",
    )
    parser.add_argument("--org-id", type=int, required=True)
    parser.add_argument("--account-id", type=int)
    parser.add_argument("--batch-size", type=int, default=100)
    return parser.parse_args()


async def run(*, org_id: int, account_id: int | None, batch_size: int) -> None:
    async with async_session() as session:
        account_query = (
            select(Account.id)
            .where(Account.org_id == org_id)
            .order_by(Account.id)
        )
        if account_id is not None:
            account_query = account_query.where(Account.id == account_id)
        account_ids = list(await session.scalars(account_query))
        for current_account_id in account_ids:
            result = await backfill_account_observations(
                session,
                org_id=org_id,
                account_id=current_account_id,
                batch_size=batch_size,
            )
            print(
                json.dumps(
                    {
                        "org_id": org_id,
                        "account_id": current_account_id,
                        "processed_batches": result.processed_batches,
                        "skipped_batches": result.skipped_batches,
                        "completed": result.completed,
                    },
                    ensure_ascii=False,
                )
            )


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(
        run(
            org_id=args.org_id,
            account_id=args.account_id,
            batch_size=args.batch_size,
        )
    )
