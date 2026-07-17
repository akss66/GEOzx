"""Platform publisher adapters inspired by SYNAPSE-style registries.

Adapters prepare safe publish packages and manual execution steps. They do not
perform browser automation or cookie-based publishing.
"""

from dataclasses import dataclass
from datetime import datetime

from fastapi import HTTPException, status

from app.models import Account, MaterialAsset
from app.models.enums import Platform
from app.schemas.orchestrator import PublishPackageOut


@dataclass(frozen=True)
class PublishDraft:
    title: str
    body: str
    topics: list[str]
    scheduled_at: datetime | None
    cover_material_id: int | None


class PlatformPublisherAdapter:
    platform: Platform
    platform_label: str

    def get_account_publish_status(self, account: Account) -> str:
        if account.platform != self.platform:
            return "platform_mismatch"
        if account.auth_status != "authorized":
            return "unauthorized"
        return "prepare_only"

    def validate_package(
        self,
        account: Account,
        material: MaterialAsset,
        draft: PublishDraft,
    ) -> None:
        publish_status = self.get_account_publish_status(account)
        if publish_status == "platform_mismatch":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="账号平台不匹配")
        if publish_status == "unauthorized":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="账号尚未授权，无法进入发布准备",
            )
        if material.status.value != "ready":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="素材尚未就绪")
        if material.kind not in {"video", "image"}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="素材类型不可发布")
        if not draft.title.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="标题不能为空",
            )

    def prepare_publish_package(
        self,
        account: Account,
        material: MaterialAsset,
        draft: PublishDraft,
    ) -> PublishPackageOut:
        self.validate_package(account, material, draft)
        content_type = "video" if material.kind == "video" else "image_text"
        package = PublishPackageOut(
            platform=self.platform,
            account_id=account.id,
            content_type=content_type,
            title=draft.title.strip(),
            body=draft.body.strip(),
            topics=[topic.strip() for topic in draft.topics if topic.strip()],
            scheduled_at=draft.scheduled_at,
            material_ids=[material.id],
            cover_material_id=draft.cover_material_id,
            visibility="public",
            allow_comment=True,
            execution_mode="manual_checklist",
            manual_steps=[],
        )
        package.manual_steps = self.build_manual_steps(account, package)
        return package

    def build_manual_steps(self, account: Account, package: PublishPackageOut) -> list[str]:
        schedule_text = (
            f"设置定时发布时间：{package.scheduled_at.isoformat()}"
            if package.scheduled_at
            else "按审批结论选择立即发布或手动设置发布时间"
        )
        return [
            f"打开{self.platform_label}，切换到账号：{account.nickname}。",
            f"上传素材：{', '.join(f'#{material_id}' for material_id in package.material_ids)}。",
            f"填写标题：{package.title}。",
            "粘贴正文与话题，并核对平台规则提示。",
            schedule_text,
            "发布前再次核对封面、素材、标题、话题和合规提示。",
        ]

    async def sync_metrics(self, account: Account) -> list[dict]:
        return []


class DouyinPublisherAdapter(PlatformPublisherAdapter):
    platform = Platform.DOUYIN
    platform_label = "抖音创作者服务中心"


class XiaohongshuPublisherAdapter(PlatformPublisherAdapter):
    platform = Platform.XIAOHONGSHU
    platform_label = "小红书创作服务平台"


class ShipinhaoPublisherAdapter(PlatformPublisherAdapter):
    platform = Platform.SHIPINHAO
    platform_label = "视频号助手"


PUBLISHER_REGISTRY: dict[Platform, PlatformPublisherAdapter] = {
    Platform.DOUYIN: DouyinPublisherAdapter(),
    Platform.XIAOHONGSHU: XiaohongshuPublisherAdapter(),
    Platform.SHIPINHAO: ShipinhaoPublisherAdapter(),
}


def get_publisher_adapter(platform: Platform) -> PlatformPublisherAdapter:
    return PUBLISHER_REGISTRY[platform]
