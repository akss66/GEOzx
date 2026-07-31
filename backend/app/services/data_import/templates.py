from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

MAX_IMPORT_TITLE_CHARS = 230  # Leaves room for the timestamp in the 255-char weak key.


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
    required: bool = False
    minimum: float | None = None
    max_length: int | None = None

    @property
    def accepted_headers(self) -> tuple[str, ...]:
        return (self.canonical_header, *self.aliases)


@dataclass(frozen=True, slots=True)
class TemplateMatch:
    template: TemplateDefinition
    column_indexes: dict[str, int]
    ignored_headers: tuple[str, ...]

    @property
    def recognized_count(self) -> int:
        return len(self.column_indexes)

    @property
    def coverage(self) -> Fraction:
        return Fraction(self.recognized_count, len(self.template.columns))


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

    def match_headers(self, headers: list[str]) -> TemplateMatch | None:
        accepted_columns: dict[str, ColumnDefinition] = {}
        for column in self.columns:
            for accepted_header in column.accepted_headers:
                normalized = normalize_header_value(accepted_header)
                existing = accepted_columns.get(normalized)
                if existing is not None and existing.field_name != column.field_name:
                    raise RuntimeError(
                        f"Template {self.code} maps one header to multiple fields"
                    )
                accepted_columns[normalized] = column

        column_indexes: dict[str, int] = {}
        ignored_headers: list[str] = []
        duplicate_fields: set[str] = set()
        for index, header in enumerate(headers):
            column = accepted_columns.get(normalize_header_value(header))
            if column is None:
                ignored_headers.append(header)
                continue
            if column.field_name in column_indexes:
                duplicate_fields.add(column.field_name)
                continue
            column_indexes[column.field_name] = index

        required_fields = {
            column.field_name for column in self.columns if column.required
        }
        if not required_fields.issubset(column_indexes):
            return None
        if duplicate_fields:
            raise ValueError("duplicate_canonical_field")
        return TemplateMatch(
            template=self,
            column_indexes=column_indexes,
            ignored_headers=tuple(ignored_headers),
        )


DAILY_PLAY_TEMPLATE = TemplateDefinition(
    code="douyin_daily_play_v1",
    display_name="抖音每日播放量趋势",
    data_domain="account_metrics",
    columns=(
        ColumnDefinition("日期", "stat_date", "date", required=True),
        ColumnDefinition("播放量", "play", "int", required=True, minimum=0),
    ),
)


def _daily_account_metric_template(
    *,
    code: str,
    display_name: str,
    header: str,
    field_name: str,
    value_type: str = "int",
    aliases: tuple[str, ...] = (),
    minimum: float | None = None,
) -> TemplateDefinition:
    return TemplateDefinition(
        code=code,
        display_name=display_name,
        data_domain="account_metrics",
        columns=(
            ColumnDefinition("日期", "stat_date", "date", required=True),
            ColumnDefinition(
                header,
                field_name,
                value_type,
                aliases=aliases,
                required=True,
                minimum=minimum,
            ),
        ),
    )


DAILY_PROFILE_VISIT_TEMPLATE = _daily_account_metric_template(
    code="douyin_daily_profile_visit_v1",
    display_name="抖音每日主页访问趋势",
    header="主页访问",
    aliases=("主页访问量",),
    field_name="profile_visit_count",
    minimum=0,
)

DAILY_FOLLOWER_TOTAL_TEMPLATE = _daily_account_metric_template(
    code="douyin_daily_follower_total_v1",
    display_name="抖音每日总粉丝量趋势",
    header="总粉丝量",
    aliases=("粉丝总量",),
    field_name="follower_count",
    minimum=0,
)

DAILY_UNFOLLOW_TEMPLATE = _daily_account_metric_template(
    code="douyin_daily_unfollow_v1",
    display_name="抖音每日取关粉丝趋势",
    header="取关粉丝",
    aliases=("取关粉丝量",),
    field_name="unfollow_count",
    minimum=0,
)

DAILY_FOLLOWER_DELTA_TEMPLATE = _daily_account_metric_template(
    code="douyin_daily_follower_delta_v1",
    display_name="抖音每日净增粉丝趋势",
    header="净增粉丝",
    aliases=("粉丝净增", "粉丝增量"),
    field_name="follower_delta",
)

DAILY_COVER_CLICK_RATE_TEMPLATE = _daily_account_metric_template(
    code="douyin_daily_cover_click_rate_v1",
    display_name="抖音每日封面点击率趋势",
    header="封面点击率",
    field_name="cover_click_rate",
    value_type="ratio",
)

DAILY_COMMENT_TEMPLATE = _daily_account_metric_template(
    code="douyin_daily_comment_v1",
    display_name="抖音每日作品评论趋势",
    header="作品评论",
    aliases=("作品评论数",),
    field_name="comment_count",
)

DAILY_SHARE_TEMPLATE = _daily_account_metric_template(
    code="douyin_daily_share_v1",
    display_name="抖音每日作品分享趋势",
    header="作品分享",
    aliases=("作品分享数",),
    field_name="share_count",
)

DAILY_LIKE_TEMPLATE = _daily_account_metric_template(
    code="douyin_daily_like_v1",
    display_name="抖音每日作品点赞趋势",
    header="作品点赞",
    aliases=("作品点赞数",),
    field_name="like_count",
)

DAILY_ACCOUNT_METRIC_TEMPLATE_CODES = frozenset(
    {
        DAILY_PLAY_TEMPLATE.code,
        DAILY_PROFILE_VISIT_TEMPLATE.code,
        DAILY_FOLLOWER_TOTAL_TEMPLATE.code,
        DAILY_UNFOLLOW_TEMPLATE.code,
        DAILY_FOLLOWER_DELTA_TEMPLATE.code,
        DAILY_COVER_CLICK_RATE_TEMPLATE.code,
        DAILY_COMMENT_TEMPLATE.code,
        DAILY_SHARE_TEMPLATE.code,
        DAILY_LIKE_TEMPLATE.code,
    }
)

SINGLE_CONTENT_TEMPLATE = TemplateDefinition(
    code="douyin_single_content_v1",
    display_name="抖音单条作品表现",
    data_domain="content_metrics",
    columns=(
        ColumnDefinition(
            "视频名称",
            "title",
            "string",
            aliases=("作品名称",),
            required=True,
            max_length=MAX_IMPORT_TITLE_CHARS,
        ),
        ColumnDefinition("发布时间", "published_at", "datetime", required=True),
        ColumnDefinition("播放量", "play", "int", required=True, minimum=0),
        ColumnDefinition("5s完播率", "completion_rate_5s", "ratio"),
        ColumnDefinition("2s跳出率", "bounce_rate_2s", "ratio"),
        ColumnDefinition("平均播放时长", "avg_watch_time_seconds", "float", minimum=0),
    ),
)

PERIOD_AGGREGATE_TEMPLATE = TemplateDefinition(
    code="douyin_period_aggregate_v1",
    display_name="抖音周期聚合表现",
    data_domain="benchmarks",
    columns=(
        ColumnDefinition("发布时间", "period", "date_range", required=True),
        ColumnDefinition("体裁", "content_format", "string"),
        ColumnDefinition("垂类", "vertical", "string"),
        ColumnDefinition("周期内投稿量", "publish_count", "int", required=True, minimum=0),
        ColumnDefinition("条均点击率", "click_rate", "ratio"),
        ColumnDefinition("条均5s完播率", "completion_rate_5s", "ratio"),
        ColumnDefinition("条均2s跳出率", "bounce_rate_2s", "ratio"),
        ColumnDefinition("条均播放时长", "avg_watch_time_seconds", "float", minimum=0),
        ColumnDefinition("播放量中位数", "median_play", "float", minimum=0),
        ColumnDefinition("条均点赞数", "avg_like_count", "float", minimum=0),
        ColumnDefinition("条均评论量", "avg_comment_count", "float", minimum=0),
        ColumnDefinition("条均分享量", "avg_share_count", "float", minimum=0),
    ),
)

WORK_LIST_TEMPLATE = TemplateDefinition(
    code="douyin_work_list_v1",
    display_name="抖音作品列表",
    data_domain="content_metrics",
    columns=(
        ColumnDefinition(
            "作品名称",
            "title",
            "string",
            aliases=("视频名称",),
            required=True,
            max_length=MAX_IMPORT_TITLE_CHARS,
        ),
        ColumnDefinition("发布时间", "published_at", "datetime", required=True),
        ColumnDefinition("体裁", "content_format", "string", max_length=120),
        ColumnDefinition("审核状态", "review_status", "string", max_length=80),
        ColumnDefinition("播放量", "play", "int", required=True, minimum=0),
        ColumnDefinition("完播率", "completion_rate", "ratio"),
        ColumnDefinition("5s完播率", "completion_rate_5s", "ratio"),
        ColumnDefinition("封面点击率", "cover_click_rate", "ratio"),
        ColumnDefinition("2s跳出率", "bounce_rate_2s", "ratio"),
        ColumnDefinition("平均播放时长", "avg_watch_time_seconds", "float", minimum=0),
        ColumnDefinition("点赞量", "like_count", "int", aliases=("点赞",), minimum=0),
        ColumnDefinition("分享量", "share_count", "int", aliases=("分享",), minimum=0),
        ColumnDefinition("评论量", "comment_count", "int", aliases=("评论",), minimum=0),
        ColumnDefinition("收藏量", "favorite_count", "int", aliases=("收藏",), minimum=0),
        ColumnDefinition(
            "主页访问量",
            "profile_visit_count",
            "int",
            aliases=("主页访问",),
            minimum=0,
        ),
        ColumnDefinition("粉丝增量", "follower_delta", "int"),
    ),
)

KNOWN_TEMPLATES: tuple[TemplateDefinition, ...] = (
    DAILY_PLAY_TEMPLATE,
    DAILY_PROFILE_VISIT_TEMPLATE,
    DAILY_FOLLOWER_TOTAL_TEMPLATE,
    DAILY_UNFOLLOW_TEMPLATE,
    DAILY_FOLLOWER_DELTA_TEMPLATE,
    DAILY_COVER_CLICK_RATE_TEMPLATE,
    DAILY_COMMENT_TEMPLATE,
    DAILY_SHARE_TEMPLATE,
    DAILY_LIKE_TEMPLATE,
    SINGLE_CONTENT_TEMPLATE,
    PERIOD_AGGREGATE_TEMPLATE,
    WORK_LIST_TEMPLATE,
)


def detect_template(headers: list[str]) -> TemplateMatch:
    matches: list[TemplateMatch] = []
    has_duplicate_canonical_field = False
    for template in KNOWN_TEMPLATES:
        try:
            match = template.match_headers(headers)
        except ValueError as exc:
            if str(exc) != "duplicate_canonical_field":
                raise
            has_duplicate_canonical_field = True
            continue
        if match is not None:
            matches.append(match)
    if not matches:
        if has_duplicate_canonical_field:
            raise ValueError("duplicate_canonical_field")
        raise ValueError("unknown")

    best_score = max((match.coverage, match.recognized_count) for match in matches)
    best_matches = [
        match
        for match in matches
        if (match.coverage, match.recognized_count) == best_score
    ]
    if len(best_matches) > 1:
        raise ValueError("ambiguous")
    return best_matches[0]
