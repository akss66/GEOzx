from __future__ import annotations

from dataclasses import dataclass


def normalize_header_value(value: object | None) -> str:
    if value is None:
        return ""
    text = str(value).replace("\ufeff", "").strip()
    return "".join(text.split())


@dataclass(frozen=True, slots=True)
class ColumnDefinition:
    canonical_header: str
    field_name: str
    value_type: str
    aliases: tuple[str, ...] = ()

    @property
    def accepted_headers(self) -> tuple[str, ...]:
        return (self.canonical_header, *self.aliases)


@dataclass(frozen=True, slots=True)
class TemplateDefinition:
    code: str
    display_name: str
    data_domain: str
    columns: tuple[ColumnDefinition, ...]

    @property
    def required_headers(self) -> tuple[str, ...]:
        return tuple(column.canonical_header for column in self.columns)

    @property
    def field_map(self) -> dict[str, str]:
        return {column.canonical_header: column.field_name for column in self.columns}

    def matches(self, headers: list[str]) -> bool:
        if len(headers) != len(self.columns):
            return False
        for header, column in zip(headers, self.columns, strict=True):
            normalized = normalize_header_value(header)
            accepted = {normalize_header_value(item) for item in column.accepted_headers}
            if normalized not in accepted:
                return False
        return True


DAILY_PLAY_TEMPLATE = TemplateDefinition(
    code="douyin_daily_play_v1",
    display_name="抖音每日播放量趋势",
    data_domain="account_metrics",
    columns=(
        ColumnDefinition("日期", "stat_date", "date"),
        ColumnDefinition("播放量", "play", "int"),
    ),
)

SINGLE_CONTENT_TEMPLATE = TemplateDefinition(
    code="douyin_single_content_v1",
    display_name="抖音单条作品表现",
    data_domain="content_metrics",
    columns=(
        ColumnDefinition("视频名称", "title", "string", aliases=("作品名称",)),
        ColumnDefinition("发布时间", "published_at", "datetime"),
        ColumnDefinition("播放量", "play", "int"),
        ColumnDefinition("5s完播率", "completion_rate_5s", "ratio"),
        ColumnDefinition("2s跳出率", "bounce_rate_2s", "ratio"),
        ColumnDefinition("平均播放时长", "avg_watch_time_seconds", "float"),
    ),
)

PERIOD_AGGREGATE_TEMPLATE = TemplateDefinition(
    code="douyin_period_aggregate_v1",
    display_name="抖音周期聚合表现",
    data_domain="benchmarks",
    columns=(
        ColumnDefinition("发布时间", "period", "date_range"),
        ColumnDefinition("体裁", "content_format", "string"),
        ColumnDefinition("垂类", "vertical", "string"),
        ColumnDefinition("周期内投稿量", "publish_count", "int"),
        ColumnDefinition("条均点击率", "click_rate", "ratio"),
        ColumnDefinition("条均5s完播率", "completion_rate_5s", "ratio"),
        ColumnDefinition("条均2s跳出率", "bounce_rate_2s", "ratio"),
        ColumnDefinition("条均播放时长", "avg_watch_time_seconds", "float"),
        ColumnDefinition("播放量中位数", "median_play", "float"),
        ColumnDefinition("条均点赞数", "avg_like_count", "float"),
        ColumnDefinition("条均评论量", "avg_comment_count", "float"),
        ColumnDefinition("条均分享量", "avg_share_count", "float"),
    ),
)

WORK_LIST_TEMPLATE = TemplateDefinition(
    code="douyin_work_list_v1",
    display_name="抖音作品列表",
    data_domain="content_metrics",
    columns=(
        ColumnDefinition("作品名称", "title", "string", aliases=("视频名称",)),
        ColumnDefinition("发布时间", "published_at", "datetime"),
        ColumnDefinition("体裁", "content_format", "string"),
        ColumnDefinition("审核状态", "review_status", "string"),
        ColumnDefinition("播放量", "play", "int"),
        ColumnDefinition("完播率", "completion_rate", "ratio"),
        ColumnDefinition("5s完播率", "completion_rate_5s", "ratio"),
        ColumnDefinition("封面点击率", "cover_click_rate", "ratio"),
        ColumnDefinition("2s跳出率", "bounce_rate_2s", "ratio"),
        ColumnDefinition("平均播放时长", "avg_watch_time_seconds", "float"),
        ColumnDefinition("点赞量", "like_count", "int", aliases=("点赞",)),
        ColumnDefinition("分享量", "share_count", "int", aliases=("分享",)),
        ColumnDefinition("评论量", "comment_count", "int", aliases=("评论",)),
        ColumnDefinition("收藏量", "favorite_count", "int", aliases=("收藏",)),
        ColumnDefinition("主页访问量", "profile_visit_count", "int", aliases=("主页访问",)),
        ColumnDefinition("粉丝增量", "follower_delta", "int"),
    ),
)

KNOWN_TEMPLATES: tuple[TemplateDefinition, ...] = (
    DAILY_PLAY_TEMPLATE,
    SINGLE_CONTENT_TEMPLATE,
    PERIOD_AGGREGATE_TEMPLATE,
    WORK_LIST_TEMPLATE,
)


def detect_template(headers: list[str]) -> TemplateDefinition:
    matches = [template for template in KNOWN_TEMPLATES if template.matches(headers)]
    if not matches:
        raise ValueError("unknown")
    if len(matches) > 1:
        raise ValueError("ambiguous")
    return matches[0]
