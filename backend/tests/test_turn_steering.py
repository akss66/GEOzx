import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models import Account, AgentRun, ConversationThread, ConversationTurn
from app.models.enums import Platform
from app.schemas.conversation import CreateConversationTurnRequest
from app.services.turn_steering import (
    TurnSteeringMode,
    _normalized_supplement_input,
    classify_turn_steering,
    resolve_turn_steering,
)


@pytest.mark.parametrize("message", ["只诊断，不生成策略", "只看数据"])
def test_unsupported_goal_supplements_are_rejected_before_revision_lineage(
    message: str,
) -> None:
    with pytest.raises(HTTPException) as captured:
        _normalized_supplement_input(
            message,
            source_input={"confirmed_review_artifact_id": 7, "cycle_days": 14},
        )

    assert captured.value.status_code == 422
    assert captured.value.detail["code"] == "UNSUPPORTED_OPERATION_ITERATION_GOAL"


def test_price_supplement_is_normalized_as_auditable_offer_terms_constraint() -> None:
    changed, merged = _normalized_supplement_input(
        "第一条不要讲价格",
        source_input={"cycle_days": 7, "topic_count": 5},
    )

    assert changed == {"offer_terms"}
    assert merged == {
        "cycle_days": 7,
        "topic_count": 5,
        "constraints": [
            {
                "constraint_type": "OFFER_TERMS",
                "raw_requirement": "第一条不要讲价格",
                "target_scope": {
                    "kind": "content_item_indexes",
                    "item_indexes": [1],
                },
            }
        ],
    }

    replay_changed, replay_merged = _normalized_supplement_input(
        "第一条不要讲价格",
        source_input=merged,
    )
    assert replay_changed == set()
    assert replay_merged == merged


@pytest.mark.parametrize(
    ("message", "expected_mode", "expected_target"),
    [
        ("第一条不要讲价格", TurnSteeringMode.SUPPLEMENT, 41),
        ("先停一下", TurnSteeringMode.STOP, 41),
        ("重新按获客目标规划", TurnSteeringMode.REPLACE_GOAL, 41),
        ("顺便看看昨天的数据", TurnSteeringMode.INDEPENDENT_QUERY, None),
    ],
)
def test_deterministic_turn_steering_examples(
    message: str,
    expected_mode: TurnSteeringMode,
    expected_target: int | None,
) -> None:
    decision = classify_turn_steering(message, active_turn_id=41)

    assert decision.mode is expected_mode
    assert decision.target_turn_id == expected_target
    assert decision.explanation


def test_ambiguous_message_defaults_to_independent_query() -> None:
    decision = classify_turn_steering("这个方向也许可以再想想", active_turn_id=41)

    assert decision.mode is TurnSteeringMode.INDEPENDENT_QUERY
    assert decision.target_turn_id is None
    assert decision.explanation == "已作为新的独立问题处理。"


@pytest.mark.parametrize(
    "message",
    [
        "停止旧方案，改为获客目标规划",
        "不要继续沿用旧方案，改为按转化目标规划",
    ],
)
def test_compound_replacement_takes_precedence_over_stop(message: str) -> None:
    decision = classify_turn_steering(message, active_turn_id=41)

    assert decision.mode is TurnSteeringMode.REPLACE_GOAL
    assert decision.target_turn_id == 41


@pytest.mark.parametrize(
    "message",
    ["重新查数据", "第一条视频的数据怎么样"],
)
def test_ordinary_queries_do_not_false_positive_as_steering(message: str) -> None:
    decision = classify_turn_steering(message, active_turn_id=41)

    assert decision.mode is TurnSteeringMode.INDEPENDENT_QUERY
    assert decision.target_turn_id is None


@pytest.mark.parametrize(
    "message",
    [
        "为什么上个任务停止了",
        "分析停止投放后的数据",
        "顺便看看为什么昨天的任务停止了",
        "另外问一下，暂停投放后数据怎样",
    ],
)
def test_non_imperative_stop_language_is_an_independent_query(message: str) -> None:
    decision = classify_turn_steering(message, active_turn_id=41)

    assert decision.mode is TurnSteeringMode.INDEPENDENT_QUERY
    assert decision.target_turn_id is None


def test_turn_request_exposes_optional_positive_target() -> None:
    request = CreateConversationTurnRequest(
        client_message_id="steering-contract",
        message="先停一下",
        target_turn_id=17,
    )

    assert request.target_turn_id == 17


async def test_database_rejects_cross_thread_steering_target(session, admin) -> None:
    await session.commit()
    await session.execute(text("PRAGMA foreign_keys = ON"))
    account = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="steering-fk",
    )
    session.add(account)
    await session.flush()
    first_thread = ConversationThread(
        org_id=admin.org_id,
        created_by_id=admin.id,
        account_id=account.id,
        title="first",
    )
    second_thread = ConversationThread(
        org_id=admin.org_id,
        created_by_id=admin.id,
        account_id=account.id,
        title="second",
    )
    target = ConversationTurn(
        thread=first_thread,
        org_id=admin.org_id,
        created_by_id=admin.id,
        client_message_id="target",
        user_input="original",
    )
    session.add_all([first_thread, second_thread, target])
    await session.flush()

    session.add(
        ConversationTurn(
            thread=second_thread,
            org_id=admin.org_id,
            created_by_id=admin.id,
            client_message_id="cross-thread-steering",
            user_input="stop it",
            target_turn_id=target.id,
            steering_mode=TurnSteeringMode.STOP.value,
        )
    )

    with pytest.raises(IntegrityError):
        await session.flush()


async def test_target_resolution_locks_run_before_turn(admin) -> None:
    thread = ConversationThread(id=7, org_id=admin.org_id, account_id=9, title="lock-order")
    target_turn = ConversationTurn(
        id=41,
        thread_id=thread.id,
        org_id=admin.org_id,
        created_by_id=admin.id,
        client_message_id="lock-target",
        user_input="active target",
        status="queued",
    )
    target_run = AgentRun(
        id=51,
        org_id=admin.org_id,
        requested_by_id=admin.id,
        thread_id=thread.id,
        turn_id=target_turn.id,
        client_message_id="lock-target",
        status="queued",
        request_payload={},
    )
    locked_entities: list[type] = []

    class RecordingSession:
        async def scalar(self, statement):
            entity = statement.column_descriptions[0].get("entity")
            if statement._for_update_arg is None:  # noqa: SLF001 - lock-order contract
                return None
            locked_entities.append(entity)
            return target_run if entity is AgentRun else target_turn

    resolved = await resolve_turn_steering(
        RecordingSession(),  # type: ignore[arg-type]
        admin,
        thread,
        CreateConversationTurnRequest(
            client_message_id="lock-steering",
            message="先停一下",
            target_turn_id=target_turn.id,
        ),
    )

    assert resolved.target_run is target_run
    assert resolved.target_turn is target_turn
    assert locked_entities == [AgentRun, ConversationTurn]
