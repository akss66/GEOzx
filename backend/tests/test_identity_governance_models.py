import pytest

from app.models import (
    Account,
    AccountMembership,
    AdminSecurityCredential,
    BrainTask,
    ContentItem,
    LLMCall,
)
from app.models.enums import Platform


@pytest.fixture
async def account(session, admin) -> Account:
    item = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="Governed account",
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


@pytest.mark.asyncio
async def test_identity_governance_models_persist(session, admin, member, account):
    member.account_scope_mode = "selected"
    membership = AccountMembership(user_id=member.id, account_id=account.id)
    credential = AdminSecurityCredential(user_id=admin.id, password_hash="hash")
    session.add_all([membership, credential])
    await session.commit()

    assert member.account_scope_mode == "selected"
    assert membership.user_id == member.id
    assert membership.account_id == account.id
    assert credential.failed_attempts == 0
    assert credential.changed_at is not None
    assert credential.delete_available_at is not None


def test_creator_foreign_keys_restrict_direct_user_deletion():
    for model in (BrainTask, ContentItem, LLMCall):
        column = model.__table__.c.created_by_id
        foreign_key = next(iter(column.foreign_keys))

        assert column.nullable is True
        assert foreign_key.target_fullname == "users.id"
        assert foreign_key.ondelete == "RESTRICT"
