from app.models import WechatComponentCredential
from app.models.enums import Platform


def test_wechat_component_credential_contract():
    assert Platform.WECHAT_OFFICIAL_ACCOUNT.value == "wechat_official_account"
    row = WechatComponentCredential(platform_integration_id=7)
    assert row.platform_integration_id == 7
    assert row.component_verify_ticket_encrypted is None
    assert row.component_access_token_encrypted is None
