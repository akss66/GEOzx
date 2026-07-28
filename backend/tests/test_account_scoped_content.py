"""Account-scoped content remains valid without a project container."""

from sqlalchemy import select

from app.models import Account, ContentItem, Deliverable, Org, Project
from app.models.enums import AccountStatus, DeliverableType, Platform


async def test_active_authorized_account_can_own_content_without_project(session) -> None:
    org = Org(name="Account content org")
    account = Account(
        org=org,
        platform=Platform.DOUYIN,
        nickname="Authorized account",
        status=AccountStatus.ACTIVE,
        auth={"access_token": "test-token"},
    )
    session.add(account)
    await session.commit()

    account_content = ContentItem(
        account_id=account.id,
        project_id=None,
        title="Account diagnostic content",
    )
    session.add(account_content)
    await session.commit()
    await session.refresh(account_content)

    assert account.auth_status == "authorized"
    assert account_content.account_id == account.id
    assert account_content.project_id is None


async def test_project_and_account_scoped_content_keep_deliverable_versioning(session) -> None:
    org = Org(name="Content scope org")
    account = Account(
        org=org,
        platform=Platform.DOUYIN,
        nickname="Active account",
        status=AccountStatus.ACTIVE,
        auth={"access_token": "test-token"},
    )
    project = Project(org=org, name="Project content")
    session.add_all([account, project])
    await session.commit()

    account_content = ContentItem(
        account_id=account.id,
        project_id=None,
        title="Account diagnostic content",
    )
    project_content = ContentItem(
        account_id=account.id,
        project_id=project.id,
        title="Project content",
    )
    session.add_all([account_content, project_content])
    await session.flush()
    session.add_all(
        [
            Deliverable(
                content_item_id=account_content.id,
                agent_code="diagnostic",
                type=DeliverableType.POSITIONING_STRATEGY,
                version=1,
                payload={"source": "account"},
            ),
            Deliverable(
                content_item_id=account_content.id,
                agent_code="diagnostic",
                type=DeliverableType.POSITIONING_STRATEGY,
                version=2,
                payload={"source": "account"},
            ),
        ]
    )
    await session.commit()

    persisted = (
        await session.scalars(
            select(ContentItem).where(ContentItem.id.in_([account_content.id, project_content.id]))
        )
    ).all()
    versions = (
        await session.scalars(
            select(Deliverable.version)
            .where(Deliverable.content_item_id == account_content.id)
            .order_by(Deliverable.version)
        )
    ).all()
    assert {content.project_id for content in persisted} == {None, project.id}
    assert versions == [1, 2]
