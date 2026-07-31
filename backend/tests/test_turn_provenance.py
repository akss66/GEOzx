import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import (
    Account,
    AgentInvocation,
    AgentQualityScore,
    AgentRun,
    AgentToolCall,
    BrainTask,
    ContentItem,
    ConversationThread,
    ConversationTurn,
    DecisionTrace,
    Deliverable,
    Event,
    Org,
    ReflectionRecord,
    SkillRun,
    StrategyPlan,
    User,
)
from app.models.enums import (
    AgentCode,
    AgentInvocationStatus,
    DeliverableStatus,
    DeliverableType,
    Platform,
)


@pytest.fixture()
def session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as database:
        yield database
    Base.metadata.drop_all(engine)


def _source_scope(
    session: Session,
    *,
    suffix: str = "default",
) -> tuple[
    ConversationThread,
    ConversationTurn,
    AgentRun,
    SkillRun,
    BrainTask,
    ContentItem,
]:
    org = Org(name=f"provenance org {suffix}")
    user = User(
        org=org,
        email=f"operator-{suffix}@example.com",
        hashed_password="not-used",
        display_name="Operator",
    )
    account = Account(
        org=org,
        platform=Platform.DOUYIN,
        nickname="Traceable account",
    )
    session.add_all([user, account])
    session.flush()

    thread = ConversationThread(
        org_id=org.id,
        created_by_id=user.id,
        account_id=account.id,
        title="Account diagnosis",
    )
    session.add(thread)
    session.flush()
    turn = ConversationTurn(
        thread_id=thread.id,
        org_id=org.id,
        created_by_id=user.id,
        client_message_id=f"turn-provenance-{suffix}",
        user_input="Diagnose this account.",
    )
    task = BrainTask(org_id=org.id, title="Diagnose account")
    content_item = ContentItem(
        account_id=account.id,
        title="Diagnosis result",
    )
    session.add_all([turn, task, content_item])
    session.flush()

    run = AgentRun(
        org_id=org.id,
        requested_by_id=user.id,
        task_id=task.id,
        thread_id=thread.id,
        turn_id=turn.id,
        client_message_id=f"turn-provenance-{suffix}",
    )
    session.add(run)
    session.flush()
    skill_run = SkillRun(
        org_id=org.id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        task_id=task.id,
        idempotency_key=f"diagnosis-{suffix}-v1",
        skill_code="account.diagnosis",
        skill_version=1,
        status="completed",
    )
    session.add(skill_run)
    session.commit()
    return thread, turn, run, skill_run, task, content_item


def _provenance(source: tuple) -> dict[str, int]:
    thread, turn, run, skill_run, _, _ = source
    return {
        "thread_id": thread.id,
        "turn_id": turn.id,
        "run_id": run.id,
        "skill_run_id": skill_run.id,
    }


def _formal_records(source: tuple) -> list:
    thread, turn, run, skill_run, task, content_item = source
    provenance = _provenance(source)
    return [
        Deliverable(
            content_item_id=content_item.id,
            agent_code="positioning",
            type=DeliverableType.REVIEW_REPORT,
            version=1,
            status=DeliverableStatus.APPROVED,
            payload={"summary": "traceable"},
            **provenance,
        ),
        StrategyPlan(
            org_id=thread.org_id,
            task_id=task.id,
            goal="Improve account positioning",
            **provenance,
        ),
        DecisionTrace(
            org_id=thread.org_id,
            task_id=task.id,
            trace_key="positioning-v1",
            goal="Choose positioning",
            **provenance,
        ),
        ReflectionRecord(
            org_id=thread.org_id,
            task_id=task.id,
            status="pending_observation",
            conclusion="Awaiting evidence",
            **provenance,
        ),
        AgentQualityScore(
            org_id=thread.org_id,
            task_id=task.id,
            score=90,
            passed=True,
            iteration=0,
            **provenance,
        ),
        Event(
            type="brain.runtime.deliverable_completed",
            payload={"task_id": task.id},
            **provenance,
        ),
    ]


def test_deliverable_round_trip_retains_all_source_ids(session: Session) -> None:
    source = _source_scope(session)
    deliverable = _formal_records(source)[0]
    session.add(deliverable)
    session.commit()
    session.expire_all()

    loaded = session.get(Deliverable, deliverable.id)

    assert loaded is not None
    assert {
        "thread_id": loaded.thread_id,
        "turn_id": loaded.turn_id,
        "run_id": loaded.run_id,
        "skill_run_id": loaded.skill_run_id,
    } == _provenance(source)


def test_ledgers_and_event_are_queryable_by_exact_source_turn(
    session: Session,
) -> None:
    source = _source_scope(session)
    records = _formal_records(source)
    session.add_all(records)
    session.commit()

    models = (
        Deliverable,
        StrategyPlan,
        DecisionTrace,
        ReflectionRecord,
        AgentQualityScore,
        Event,
    )
    for model, record in zip(models, records, strict=True):
        matching_ids = session.scalars(
            select(model.id).where(
                model.thread_id == source[0].id,
                model.turn_id == source[1].id,
                model.skill_run_id == source[3].id,
            )
        ).all()
        assert matching_ids == [record.id]
        assert record.turn_id == source[1].id
        assert record.skill_run_id == source[3].id


def test_legacy_records_remain_valid_with_null_provenance(session: Session) -> None:
    source = _source_scope(session)
    thread, _, _, _, task, content_item = source
    records = [
        Deliverable(
            content_item_id=content_item.id,
            agent_code="legacy",
            type=DeliverableType.REVIEW_REPORT,
            version=1,
            status=DeliverableStatus.APPROVED,
            payload={"summary": "legacy"},
        ),
        StrategyPlan(
            org_id=thread.org_id,
            task_id=task.id,
            goal="Legacy strategy",
        ),
        DecisionTrace(
            org_id=thread.org_id,
            task_id=task.id,
            trace_key="legacy-decision",
            goal="Legacy decision",
        ),
        ReflectionRecord(
            org_id=thread.org_id,
            task_id=task.id,
            status="pending_observation",
            conclusion="Legacy reflection",
        ),
        AgentQualityScore(
            org_id=thread.org_id,
            task_id=task.id,
            score=80,
            passed=True,
            iteration=0,
        ),
        Event(type="legacy.event", payload={"legacy": True}),
    ]
    session.add_all(records)
    session.commit()

    for record in records:
        assert record.thread_id is None
        assert record.turn_id is None
        assert record.run_id is None
        assert record.skill_run_id is None


def test_deleting_sources_clears_provenance_without_deleting_formal_records(
    session: Session,
) -> None:
    source = _source_scope(session)
    thread, turn, run, skill_run, _, _ = source
    records = _formal_records(source)
    session.add_all(records)
    session.commit()
    record_keys = [(type(record), record.id) for record in records]

    session.delete(skill_run)
    session.commit()
    for model, record_id in record_keys:
        record = session.get(model, record_id)
        assert record is not None
        assert record.skill_run_id is None

    session.delete(run)
    session.commit()
    for model, record_id in record_keys:
        record = session.get(model, record_id)
        assert record is not None
        assert record.run_id is None

    session.delete(turn)
    session.commit()
    for model, record_id in record_keys:
        record = session.get(model, record_id)
        assert record is not None
        assert record.turn_id is None

    session.delete(thread)
    session.commit()
    for model, record_id in record_keys:
        record = session.get(model, record_id)
        assert record is not None
        assert record.thread_id is None


def test_composite_runtime_foreign_keys_reject_cross_source_rows(
    session: Session,
) -> None:
    source_a = _source_scope(session, suffix="constraint-a")
    source_b = _source_scope(session, suffix="constraint-b")
    thread_a, turn_a, run_a, skill_a, task_a, content_a = source_a
    thread_b, turn_b, run_b, skill_b, task_b, _content_b = source_b

    invalid_rows = [
        Deliverable(
            content_item_id=content_a.id,
            thread_id=thread_a.id,
            turn_id=turn_b.id,
            run_id=run_a.id,
            skill_run_id=skill_a.id,
            agent_code="positioning",
            type=DeliverableType.REVIEW_REPORT,
            version=2,
            status=DeliverableStatus.PENDING_REVIEW,
            payload={"summary": "cross turn"},
        ),
        AgentInvocation(
            task_id=task_a.id,
            run_id=run_b.id,
            skill_run_id=skill_b.id,
            thread_id=thread_b.id,
            turn_id=turn_b.id,
            step_key="cross-run-task",
            agent_code=AgentCode.POSITIONING,
            agent_name="Positioning",
            status=AgentInvocationStatus.RUNNING,
        ),
        AgentToolCall(
            org_id=thread_b.org_id,
            task_id=task_b.id,
            invocation_id=None,
            skill_run_id=skill_a.id,
            thread_id=thread_a.id,
            turn_id=turn_a.id,
            tool_code="account.profile",
            tool_name="Account profile",
            idempotency_key="cross-skill-task",
            status="success",
        ),
    ]

    for row in invalid_rows:
        session.add(row)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_agent_run_task_org_and_skill_run_task_graph_are_constrained(
    session: Session,
) -> None:
    source_a = _source_scope(session, suffix="task-constraint-a")
    source_b = _source_scope(session, suffix="task-constraint-b")
    thread_a, turn_a, run_a, _skill_a, _task_a, _content_a = source_a
    _thread_b, _turn_b, _run_b, _skill_b, task_b, _content_b = source_b

    session.add(
        AgentRun(
            org_id=thread_a.org_id,
            requested_by_id=turn_a.created_by_id,
            task_id=task_b.id,
            thread_id=thread_a.id,
            turn_id=turn_a.id,
            client_message_id="cross-task-org",
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    session.add(
        SkillRun(
            org_id=thread_a.org_id,
            thread_id=thread_a.id,
            turn_id=turn_a.id,
            run_id=run_a.id,
            task_id=task_b.id,
            idempotency_key="cross-task-skill",
            skill_code="account.diagnosis",
            skill_version=1,
            status="running",
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
