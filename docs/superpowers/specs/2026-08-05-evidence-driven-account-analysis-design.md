# 运营大脑：证据驱动的账号数据分析设计

## 1. 文档状态

- 日期：2026-08-05
- 状态：设计已收敛，等待用户书面审阅
- 实现基线：`codex/main-agent-v4-codex-interaction`，提交 `66479a4`
- 产品边界：只分析运营者已经确认导入的抖音数据并给出建议；不自动发布、不自动监测、不自动归因、不自动调整策略
- 隔离边界：所有查询、证据、会话、运行与交付物必须同时绑定 `org_id + user_id + account_id + thread_id + turn_id`

## 2. 目标

把运营大脑从“能够读取数据摘要”提升为“能够可靠回答账号数据问题”。用户可以直接用自然语言询问账号现状、趋势、异常、作品差异和下一步建议，系统必须：

1. 只使用当前账号已经确认写入的数据；
2. 先进行确定性计算，再由运营专家解释业务含义；
3. 每个重要结论都能追溯到数据周期、指标、数值与来源；
4. 数据不足时明确区分“可以回答”“只能部分回答”“不能回答”；
5. 不得把相关性描述为因果关系；
6. 在 V4 的同一个 WorkTurn 中实时展示检查、计算、解释和质量验证过程；
7. 交付采用用户能理解的“分析结论与下一步建议”，不使用抽象的“成果/采用成果”措辞。

首批必须可靠支持的问题：

- “我现在账号有数据吗？”
- “最近 30 天账号表现怎么样？”
- “播放量从什么时候开始下降？”
- “哪个指标变化最大？”
- “哪些作品拖累了整体表现？”
- “点赞下降但分享上涨说明什么？”
- “目前数据够不够做账号体检？”
- “下一批内容优先测试什么？”

## 3. 方案选择

### 方案 A：继续扩充 `account_inspection`

把所有自然语言分析都塞入现有一键账号体检 Skill。

优点是代码入口少；缺点是“固定体检报告”和“任意指标问题”会共享一套庞大逻辑，用户问一个简单问题也会触发多专家完整体检，响应慢、成本高，并容易再次产生无关策略。该方案不采用。

### 方案 B：确定性分析 Tool + 独立数据分析 Skill（采用）

新增只读 `account.metrics_analysis` Tool，负责时间窗口、指标聚合、环比、趋势、异常和作品排序等确定性计算；新增 `account_data_analysis` Skill，负责可回答性门控、调用运营专家解释、质量验证与结构化交付。

优点是事实计算可测试、证据可追溯、查询范围受控，简单问题可以快速回答，正式分析仍保留专家和质量门。该方案与现有 Skill Runtime、Tool 权限、Evidence、Critic 和 V4 WorkTurn 原生兼容。

### 方案 C：让模型直接生成 SQL 或分析代码

模型根据用户问题生成 SQL、Pandas 或 DuckDB 代码并执行。

优点是演示灵活；缺点是查询权限、口径一致性、账号隔离、资源消耗和结果复现都难以保证，也会绕过现有数据合并与冲突规则。该方案不进入生产主链路。

## 4. 总体架构

```text
用户自然语言问题
    → Capability Router
      ├─ 是否有数据 / 最近数据周期
      │    → account.data_context（快速确定性回答）
      └─ 趋势 / 对比 / 异常 / 作品表现 / 建议
           → account_data_analysis Skill
                → account.metrics_analysis Tool
                   ├─ AccountDataViewService
                   ├─ 指标口径注册表
                   ├─ 时间窗口与对比周期
                   ├─ 聚合、变化、异常、排序
                   └─ 证据与可回答性结果
                → 06-operator 运营专家
                → deterministic evidence validator
                → Critic
                → analysis_answer 交付
                → V4 WorkTurn 原位展示
```

原则是“Tool 产生事实，专家解释事实，Critic 检查事实与建议是否一致，主 Agent 只负责选择 Skill、监督和汇总”。

## 5. 输入契约

`account_data_analysis` 使用严格输入模型：

```python
class AccountDataAnalysisInput(BaseModel):
    question: str
    days: int = Field(default=30, ge=1, le=90)
    comparison: Literal["auto", "previous_period", "none"] = "auto"
    requested_metrics: list[str] = Field(default_factory=list, max_length=12)
    top_n: int = Field(default=5, ge=1, le=20)
```

约束：

- `account_id`、`org_id`、`user_id`、`thread_id` 与 `turn_id` 只能来自认证后的 Runtime Context，模型和前端不得提交或覆盖；
- `requested_metrics` 只能来自统一指标注册表；未知指标返回可操作提示，不做模糊字段猜测；
- `days` 默认 30 天，用户明确给出的时间范围优先；
- 用户只问“有没有数据”时不启动专家，使用现有确定性数据查询路径；
- 用户要求原因时，只能输出“数据支持的相关判断”和“待验证假设”，不得输出未经实验验证的因果结论。

## 6. 确定性分析 Tool

### 6.1 工具契约

新增只读 Tool：

```python
class AccountMetricsAnalysisParams(BaseModel):
    days: int = Field(ge=1, le=90)
    comparison: Literal["previous_period", "none"]
    metric_codes: list[str] = Field(max_length=12)
    top_n: int = Field(ge=1, le=20)

class AccountMetricsAnalysisResult(BaseModel):
    account_id: int
    query_window: DateRange
    comparison_window: DateRange | None
    answerability: Answerability
    facts: list[AnalysisFact]
    content_rankings: list[ContentRanking]
    data_quality: DataQualitySummary
    evidence_refs: list[BusinessEvidenceRef]
```

Tool 的权限固定为：

- `side_effect_level="read"`
- `scope="account"`
- 仅 `ADMIN` 与 `USER` 可调用
- 每次执行重新校验当前用户是否可访问当前账号
- 只读取已经确认写入的投影数据；待确认批次只作为“存在待确认数据”的提示，不能参与计算

### 6.2 指标口径

第一版使用已有 `AccountDataViewService` 和现有指标，不引入第二份事实表：

- 流量：`play`、`exposure`、`profile_visit_count`
- 留存：`avg_watch_time_seconds`、`completion_rate`、`completion_rate_5s`、`bounce_rate_2s`
- 互动：`like_count`、`comment_count`、`share_count`、`favorite_count`、对应比率
- 粉丝：`follower_count`、`follower_delta`、`unfollow_count`
- 内容：作品数量、内容形式、审核状态与单作品指标

每个指标在统一注册表声明：业务名称、单位、聚合方式、有效粒度、允许的比较方式、最少样本量和显示精度。累计量使用求和，存量使用周期末值，比率使用有权重数据时的加权口径；无法获得分母时明确标记为简单均值，不伪装成加权结果。

### 6.3 分析能力

第一版只实现可确定性验证的分析：

1. 当前周期汇总；
2. 与紧邻前一等长周期比较；
3. 绝对变化与相对变化；
4. 按日趋势和变化起点；
5. 指标变化幅度排序；
6. 单作品 Top/Bottom 排名；
7. 数据新鲜度、覆盖范围、冲突数量与样本量；
8. 基于预先声明阈值的异常候选，不使用模型凭感觉判断异常。

第一版不实现因果推断、行业基准推断、跨账号对比或自动策略效果归因。

## 7. 证据契约与可回答性门

### 7.1 业务证据

每条 `AnalysisFact` 必须包含：

```python
class BusinessEvidenceRef(BaseModel):
    source_type: str
    source_id: str
    account_id: int
    batch_id: int | None
    metric_code: str
    period_start: date
    period_end: date
    observed_at: date | None
    value: int | float | None
    unit: str
    content_hash: str
```

`content_hash` 对规范化后的证据字段计算，用于检测证据在重试、恢复或交付版本之间是否发生变化。用户界面默认显示聚合后的业务依据，原始 ID 和哈希只出现在技术详情中。

### 7.2 可回答性

```python
class Answerability(BaseModel):
    status: Literal["sufficient", "partial", "insufficient"]
    confidence: Decimal = Field(ge=0, le=1)
    supported_claims: list[str]
    unsupported_claims: list[str]
    missing_metrics: list[str]
    missing_periods: list[DateRange]
    reasons: list[str]
```

规则：

- 没有已确认数据：`insufficient`，禁止生成专业诊断，只说明如何补充数据；
- 有当前周期、无对比周期：可以描述当前值，不能描述上升或下降，状态为 `partial`；
- 指标缺少必要分母：可以描述计数，不能描述对应比率；
- 样本少于指标声明的最少样本量：结论必须标记为初步观察；
- 数据过期、存在未解决冲突或来源混合：降低置信度并在回答中披露；
- 专家输出中的数值、时间、方向和作品排名必须能在 Tool 事实中逐项匹配，否则质量门不通过。

## 8. 专家与质量门

第一版不新增 Agent 枚举与第二套专家管理链路。`account_data_analysis` 调用现有 `06-operator`，其职责仅为：

- 解释确定性事实的运营含义；
- 区分结论、假设和限制；
- 基于已有证据提出短周期可验证建议；
- 不改变 Tool 计算出的数值、方向、排名和置信度。

质量检查分两层：

1. 确定性验证：数值、周期、方向、证据引用、数据缺口与可回答性必须逐项一致；
2. Critic：检查建议是否回应用户问题、是否可执行、是否越过证据边界、是否偷偷生成用户未要求的长期策略。

确定性验证失败不允许通过模型重试掩盖，应记录为实现错误并终止；专家表达或建议质量不足时最多重做一次，仍未通过则交付“需要复核”的草稿，不能显示普通“已完成”。

## 9. 输出与用户交互

正式交付类型为 `account_analysis_answer`，结构固定为：

```python
class AccountDataAnalysisAnswer(BaseModel):
    artifact_type: Literal["account_analysis_answer"]
    question: str
    answerability: Answerability
    conclusion: str
    key_facts: list[AnalysisFact]
    interpretation: list[str]
    recommendations: list[Recommendation]
    data_limits: list[str]
    next_action: str
    evidence_refs: list[BusinessEvidenceRef]
    participating_experts: list[str]
    critic: SkillQualityResult
```

推荐项必须包含“做什么、为什么、验证指标、建议观察周期”。没有数据支持时不得给出伪精确提升目标。

V4 WorkTurn 的用户可见过程示例：

```text
✓ 已确认当前账号和数据范围
✓ 已读取 07/01—07/30 的已确认数据
✓ 已计算播放、互动和粉丝变化
● 正在检查结论与数据依据
○ 正在整理下一步建议
```

完成后在原位置展示：

1. 直接回答；
2. 关键数据；
3. 运营判断；
4. 下一步建议；
5. 数据限制；
6. “查看分析依据”折叠区；
7. 技术详情二级折叠区。

不得新增第二套聊天气泡、独立结果流或全屏报告页。成果中心只保存同一交付物的引用，不复制一份独立内容。

## 10. 路由与性能

路由顺序：

1. 显式 `account_data_analysis` 快捷入口；
2. 确定性“是否有数据/数据截止日期/有哪些指标”查询；
3. 指标趋势、对比、异常、排名与建议进入 `account_data_analysis`；
4. 固定全量体检进入 `account_inspection`；
5. 无法确定时间或对象且会改变结果时才向用户澄清。

性能目标：

- 确定性“是否有数据”回答：P95 小于 1 秒；
- 数据分析 Tool：30 天、12 个指标、500 个作品以内 P95 小于 1.5 秒；
- 首个真实工作状态：P95 小于 500 毫秒；
- 专家首段流式内容：排除供应商排队后 P95 小于 2 秒；
- 同一 WorkTurn 的业务进度间隔不超过 3 秒；
- 不因简单指标查询启动多专家完整体检。

## 11. 错误与恢复

- 未选择账号：阻止执行并提示选择账号；
- 账号无权访问：返回权限错误，不重试；
- 指标未知：列出可查询的相近业务指标，不猜测字段；
- 数据为空或只有待确认批次：不调用专家，直接说明当前没有可分析数据；
- 对比周期不足：保留当前周期分析，明确不能判断趋势；
- 数据冲突：披露冲突数量，并排除未解决冲突值；
- Tool 暂时失败：最多重试两次，复用同一 ToolCall 幂等身份；
- 专家失败：保留已完成的确定性分析，只重试专家阶段；
- SSE 断开或页面刷新：从同一 WorkTurn 快照恢复，不重复计算和交付；
- 任一失败必须让 Turn、Run、SkillRun 进入一致终态。

## 12. 测试策略

所有行为按失败测试 → 最小实现 → 回归验证实施。

### 12.1 单元测试

- 指标注册表聚合口径；
- 当前周期与前一等长周期；
- 变化起点、幅度排序和作品 Top/Bottom；
- 缺少分母、缺少对比周期、样本不足和过期数据；
- 可回答性与置信度规则；
- 证据规范化和哈希稳定性；
- 专家输出中的数值、方向和引用逐项验证。

### 12.2 Tool 与 Skill 合约测试

- Tool 只读取当前账号的已确认数据；
- 其他账号、其他用户和其他组织的数据不可见；
- 待确认批次不参与计算；
- `account_data_analysis` 只调用 `account.metrics_analysis` 和 `06-operator`；
- 数据不足时不调用专家；
- 专家失败只重试专家阶段；
- Critic 失败最多重做一次；
- 正式交付物包含完整证据和质量状态。

### 12.3 API、前端与浏览器测试

- 自然语言问题正确路由到确定性查询、数据分析或账号体检；
- WorkTurn 实时显示读取、计算、专家与验证进度；
- 完成前后保持相同对齐、宽度和组件；
- 结论、依据、建议和限制可理解，技术细节默认折叠；
- 刷新和断线恢复后不出现重复回答；
- 切换账号立即取消旧请求，绝不闪现旧账号数据；
- 移动端和桌面端均可阅读关键数据与建议。

### 12.4 真实数据验收语句

至少使用一个已导入多类抖音数据的测试账号验证：

1. “我现在账号有数据吗？”
2. “最近 30 天账号表现怎么样？”
3. “播放量从什么时候开始下降？”
4. “哪个指标变化最大？”
5. “表现最差的 5 条作品是什么？”
6. “点赞下降但分享上涨说明什么？”
7. “目前的数据够不够判断留存问题？”
8. “只分析现状，不生成 30 天策略。”

每条回答人工核对数据库计算结果、证据周期、账号归属、限制措辞和下一步动作。

## 13. 发布顺序

1. 将已经完整验证的 V4 分支合并为交互基线；
2. 在新短生命周期分支实现指标注册表、分析服务与证据门；
3. 上线只读 `account.metrics_analysis` Tool；
4. 上线 `account_data_analysis` Skill 与路由；
5. 在 V4 WorkTurn 中加入结构化事实、依据和限制展示；
6. 完成全量自动化测试和真实账号本地验收；
7. 生产迁移前备份数据库并准备代码回滚点；
8. 先对内部测试账号开启，完成线上冒烟后再扩大范围。

## 14. 非目标

本轮不实现：

- 对标账号分析；
- 行业基准自动抓取；
- 任意 SQL 或 Python 执行；
- 自动发布、自动监测和自动策略调整；
- 因果推断或运营效果归因；
- 新增通用 Agent 框架；
- 新增数据分析专家枚举；
- 重写账号数据中心或导入解析器；
- 自动修改 Prompt 或 Skill。

## 15. 完成定义

本轮只有同时满足以下条件才算完成：

- 上述八条真实数据验收语句均产生正确路由与可核验结果；
- 所有关键结论都能追溯到当前账号、数据周期、指标和来源；
- 数据不足时输出确定性结论或可操作补数建议，不调用专家编造诊断；
- 模型无法改变 Tool 计算的数值、方向、排名和置信度；
- 未导入数据却声称已读取的次数为 0；
- 缺少证据却输出确定性专业结论的次数为 0；
- 其他账号数据、证据、会话或交付物泄露次数为 0；
- 简单数据查询不启动多专家完整体检；
- Turn、Run、SkillRun 和交付物终态一致；
- 后端测试、前端测试、静态检查、生产构建、浏览器回归和真实账号冒烟全部通过；
- 生产发布具备明确回滚点，且不影响既有批量导入和 V4 对话恢复能力。
