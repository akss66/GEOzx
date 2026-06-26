"""核心模型 CRUD 冒烟测试。

用内存 SQLite（StaticPool 共享单连接）建表并跑关键关系，验证模型自洽：
组织→用户、项目→内容→交付物（JSON payload + 版本唯一约束）、任务/事件/门。
不依赖真实 Postgres（真实迁移由 alembic 在容器内验证）。
"""

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import (
    Account,
    AccountGroup,
    AgentTask,
    ContentItem,
    Deliverable,
    Event,
    GateApproval,
    Org,
    Project,
    User,
)
from app.models.enums import (
    DeliverableType,
    GateType,
    Platform,
    UserRole,
)


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
    Base.metadata.drop_all(engine)


def test_org_user_relationship(session: Session) -> None:
    org = Org(name="Acme 运营")
    user = User(
        org=org,
        email="admin@acme.com",
        hashed_password="x",
        display_name="管理员",
        role=UserRole.ADMIN,
    )
    session.add(user)
    session.commit()

    loaded = session.scalar(select(User).where(User.email == "admin@acme.com"))
    assert loaded is not None
    assert loaded.role is UserRole.ADMIN
    assert loaded.is_active is True
    assert loaded.org.name == "Acme 运营"
    assert loaded.created_at is not None


def test_matrix_and_content_chain(session: Session) -> None:
    org = Org(name="Acme")
    group = AccountGroup(org=org, name="数码赛道")
    account = Account(org=org, group=group, platform=Platform.DOUYIN, nickname="数码菌")
    project = Project(org=org, name="618 大促")
    session.add_all([account, project])
    session.commit()

    content = ContentItem(project=project, account_id=account.id, title="新品开箱")
    deliverable = Deliverable(
        content_item=content,
        agent_code="01-positioning",
        type=DeliverableType.POSITIONING_STRATEGY,
        version=1,
        payload={"account_persona": "硬核测评"},
    )
    session.add(deliverable)
    session.commit()

    assert account.group.name == "数码赛道"
    assert content.deliverables[0].payload["account_persona"] == "硬核测评"
    assert content.project.name == "618 大促"


def test_deliverable_version_unique(session: Session) -> None:
    org = Org(name="Acme")
    project = Project(org=org, name="P")
    content = ContentItem(project=project, title="C")
    session.add(content)
    session.commit()

    common = dict(
        content_item_id=content.id,
        agent_code="01",
        type=DeliverableType.POSITIONING_STRATEGY,
        payload={},
    )
    session.add(Deliverable(version=1, **common))
    session.commit()
    session.add(Deliverable(version=1, **common))  # 同 (content,type,version) 重复
    with pytest.raises(IntegrityError):
        session.commit()


def test_task_event_gate(session: Session) -> None:
    org = Org(name="Acme")
    project = Project(org=org, name="P")
    content = ContentItem(project=project, title="C")
    session.add(content)
    session.commit()

    from app.models.enums import ContentStage

    task = AgentTask(content_item_id=content.id, agent_code="01", stage=ContentStage.POSITIONING)
    event = Event(type="content.created", content_item_id=content.id, payload={"k": "v"})
    gate = GateApproval(content_item_id=content.id, gate=GateType.SCRIPT_COMPLIANCE)
    session.add_all([task, event, gate])
    session.commit()

    assert session.scalar(select(AgentTask)).status.value == "pending"
    assert session.scalar(select(Event)).type == "content.created"
    assert session.scalar(select(GateApproval)).status.value == "pending"
    assert session.scalar(select(Event)).created_at is not None
