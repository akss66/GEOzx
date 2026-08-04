from app.models.enums import DeliverableType
from app.orchestrator.brain_adapter import _DELIVERABLE_TITLE


def test_publish_package_has_a_stable_brain_label_and_wire_value() -> None:
    assert DeliverableType.PUBLISH_PACKAGE.value == "publish_package"
    assert _DELIVERABLE_TITLE[DeliverableType.PUBLISH_PACKAGE] == "周运营发布包"
