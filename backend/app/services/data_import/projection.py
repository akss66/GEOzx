"""Persist field observations and resolve their deterministic winners."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DataFieldObservation, DataImportBatch, DataImportRow
from app.services.data_import.merge import (
    MergeCandidate,
    choose_winner,
    iter_present_fields,
    source_priority,
)


@dataclass(frozen=True, slots=True)
class FieldWinner:
    value: Any
    observation: DataFieldObservation


def encode_observation_value(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"kind": "bool", "value": value}
    if isinstance(value, int):
        return {"kind": "int", "value": value}
    if isinstance(value, float):
        return {"kind": "float", "value": value}
    if isinstance(value, datetime):
        return {"kind": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"kind": "date", "value": value.isoformat()}
    if isinstance(value, str):
        return {"kind": "string", "value": value}
    return {"kind": "json", "value": _json_safe(value)}


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    return value


def decode_observation_value(payload: Mapping[str, Any]) -> Any:
    value = payload.get("value")
    if payload.get("kind") == "date" and isinstance(value, str):
        return date.fromisoformat(value)
    if payload.get("kind") == "datetime" and isinstance(value, str):
        return datetime.fromisoformat(value)
    return value


async def record_and_resolve_fields(
    session: AsyncSession,
    *,
    batch: DataImportBatch,
    row: DataImportRow,
    domain: str,
    entity_key: str,
    stat_date: date,
    values: Mapping[str, Any],
) -> dict[str, FieldWinner]:
    if batch.confirmed_sequence is None:
        raise ValueError("batch confirmed_sequence is required before projection")

    for field_name, value in iter_present_fields(values):
        existing = await session.scalar(
            select(DataFieldObservation).where(
                DataFieldObservation.import_batch_id == batch.id,
                DataFieldObservation.import_row_id == row.id,
                DataFieldObservation.domain == domain,
                DataFieldObservation.entity_key == entity_key,
                DataFieldObservation.stat_date == stat_date,
                DataFieldObservation.field_name == field_name,
            )
        )
        if existing is not None:
            continue
        session.add(
            DataFieldObservation(
                org_id=batch.org_id,
                account_id=batch.account_id,
                import_batch_id=batch.id,
                import_row_id=row.id,
                domain=domain,
                entity_key=entity_key,
                stat_date=stat_date,
                field_name=field_name,
                value=encode_observation_value(value),
                source_kind=batch.source_kind,
                source_priority=source_priority(batch.source_kind),
                confirmed_sequence=batch.confirmed_sequence,
                active=True,
            )
        )
    await session.flush()

    observations = list(
        await session.scalars(
            select(DataFieldObservation).where(
                DataFieldObservation.org_id == batch.org_id,
                DataFieldObservation.account_id == batch.account_id,
                DataFieldObservation.domain == domain,
                DataFieldObservation.entity_key == entity_key,
                DataFieldObservation.stat_date == stat_date,
                DataFieldObservation.active.is_(True),
            )
        )
    )
    grouped: dict[str, list[DataFieldObservation]] = {}
    for observation in observations:
        grouped.setdefault(observation.field_name, []).append(observation)

    winners: dict[str, FieldWinner] = {}
    for field_name, candidates in grouped.items():
        by_id = {candidate.id: candidate for candidate in candidates}
        winner = choose_winner(
            MergeCandidate(
                value=decode_observation_value(candidate.value),
                source_kind=candidate.source_kind,
                source_priority=candidate.source_priority,
                confirmed_sequence=candidate.confirmed_sequence,
                observation_id=candidate.id,
                active=candidate.active,
            )
            for candidate in candidates
        )
        if winner is None:
            continue
        observation = by_id[winner.observation_id]
        winners[field_name] = FieldWinner(
            value=winner.value,
            observation=observation,
        )
    return winners


def newest_winner(winners: Mapping[str, FieldWinner]) -> FieldWinner | None:
    return max(
        winners.values(),
        key=lambda winner: (
            winner.observation.source_priority,
            winner.observation.confirmed_sequence,
            winner.observation.id,
        ),
        default=None,
    )
