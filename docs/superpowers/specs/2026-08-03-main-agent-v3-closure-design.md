# 主 Agent V3 运营闭环整改设计

## 1. 目标与边界

本设计在现有 Main Agent V3 会话、Turn/Run、Skill Runtime、专家 Harness、Tool 和成果中心之上完成三阶段整改：

1. 修复能力契约，使主 Agent 的参数、附件、工具、审批、质量门和删除语义真实生效。
2. 把账号运营生命周期补齐为可发现、可追踪、可验收的业务 Skill。
3. 优化确定性路由、流式反馈和成果展示，使常见对话快速、清晰且可恢复。

完成后，主 Agent 是唯一用户入口；主 Agent 选择 Skill，Skill 编排专家与 Tool，Runtime 负责隔离、幂等、审批、重试、质量和审计，成果同时投影到原对话与成果中心。

真实外部动作必须满足三个条件：用户明确提出动作、人工审批通过、平台 Tool Adapter 可用。任何条件不满足时，运行进入 `waiting_user` 或 `blocked`，不得生成近似成果后标记为完成。

本轮不重写已有账号数据中心，不替换模型供应商，不把专家压缩为普通提示词，也不允许主 Agent 冒充专家生成正式专业成果。

## 2. 设计原则

- **真实契约**：Skill 声明的输入、Tool、质量门和审批门必须逐项执行并留下运行记录。
- **按账号隔离**：会话、附件、成果、Tool 调用和专家调用必须绑定 `org_id + user_id + account_id + thread_id + turn_id + run_id`。
- **确定性优先**：可用规则或 Tool 直接回答的问题不先调用模型分类。
- **正式成果有来源**：正式成果必须来自 Skill，包含专家、数据周期、证据摘要、质量状态和下一步。
- **失败可操作**：错误输出业务原因、恢复动作和可重试性；业务冲突不自动重试。
- **幂等执行**：相同 `client_message_id`、Skill 输入指纹和 Tool 幂等键不得产生重复消息、成果或外部动作。
- **渐进发布**：新 Runtime 契约先通过后端开关与内部账号验证，再替换旧通用任务图。

## 3. 总体架构

```text
用户消息 / 快捷 Skill / 附件
        ↓
Conversation API
        ↓
CapabilityRequest Builder
  - scope
  - intent
  - constraints
  - structured_input
  - attachments
  - requested_output
        ↓
Capability Router
  ├─ deterministic answer
  ├─ deterministic query Tool
  ├─ typed Skill
  ├─ clarification
  └─ unsupported / blocked
        ↓
Skill Runtime
  ├─ input validation
  ├─ data Tool
  ├─ expert stages
  ├─ side-effect Tool
  ├─ critic
  ├─ approval gate
  └─ artifact projection
        ↓
对话成果卡 + 成果中心 + 折叠技术日志
```

旧 `FORMAL_TASK → REVIEW_OPTIMIZATION → runtime_graph.start_routed` 仅作为迁移期兼容路径。每增加一个正式 Skill，就从兼容路径移除对应意图；所有目标 Skill 迁移完成后删除泛化正式任务入口。

## 4. 阶段一：Runtime 契约修复

### 4.1 CapabilityRequest

新增不可变请求模型，作为路由与 Skill 的唯一业务输入：

```python
class CapabilityRequest(BaseModel):
    org_id: int
    user_id: int
    account_id: int
    thread_id: int
    turn_id: int
    run_id: int
    message: str
    requested_skill_code: str | None
    execution_preference: Literal["AUTO", "DISCUSS_ONLY", "FORMAL_TASK"]
    structured_input: dict[str, JsonValue]
    constraints: list[str]
    attachment_ids: list[int]
```

`structured_input` 由显式快捷入口参数、确定性解析器和一次受约束的模型抽取合并。优先级为：用户显式字段 > 当前消息明确约束 > 会话记忆 > Skill 默认值。解析结果必须经过目标 Skill 的 Pydantic 输入模型验证。

支持的第一批公共约束包括 `days`、`topic_count`、`duration_seconds`、`content_item_id`、`compare_period`、`generate_strategy` 和 `requested_output`。

### 4.2 Tool 与审批执行

Skill Runtime 不再按硬编码白名单跳过 Tool。执行顺序由 Tool 元数据决定：

- `read` Tool 可在专家前执行。
- `prepare` Tool 在专家成果满足输入要求后执行。
- `side_effect` Tool 必须在审批通过后执行。
- 未注册、无权限或 Adapter 不可用的 Tool 立即进入 `blocked`。

`approval_policy` 的语义固定为：

- `none`：不需要审批。
- `before_tools`：任何副作用 Tool 前暂停。
- `before_finish`：成果草稿产生后暂停，审批通过才可进入完成态。

`publishing_preparation` 必须真正创建 `publish_package_prepare` ToolCall；审批前 Skill 状态为 `waiting_user`，审批通过后才生成已批准发布包。若平台发布 Adapter 不可用，发布动作保持 `blocked`，但已批准发布包可作为独立成果保存。

### 4.3 质量门

账号体检、选题、脚本、发布准备、数据复盘均采用统一质量结果：

```python
class SkillQualityResult(BaseModel):
    passed: bool
    score: int
    issues: list[str]
    retryable: bool
    evidence_coverage: float
```

正式成果必须通过结构校验和证据校验。Critic 不通过时最多重做一次；仍不通过则生成 `ready_for_review` 成果并明确标记“质量待复核”，Skill 终态为 `needs_review`，不得显示普通“已完成”。

### 4.4 附件链路

附件建立独立资源表，保存所有者、账号、会话、MIME、大小、存储键、哈希、解析状态和安全扫描状态。提交 Turn 时只接受当前用户、当前账号、当前会话可访问的附件。

附件解析形成只读 `AttachmentContext`，专家只能看到允许的文本、表格摘要、图片描述和资源引用。原始文件不进入普通日志。前端加号菜单在链路上线前保持禁用说明，上线后支持上传进度、失败重试、移除和附件预览。

### 4.5 删除语义

用户只能删除自己创建的会话。二次确认后永久删除：

- 对话消息和流式消息事件；
- Turn、Run、SkillRun、AgentInvocation、ToolCall 和 LLMCall 的技术执行记录；
- 仅属于该会话的附件及解析产物；
- 未被其他业务对象引用的草稿成果。

依法或业务上必须保留的正式审批、发布和费用审计事件进入独立 AuditRecord，保留最小字段且不可再还原消息正文、提示词或技术日志。删除响应必须返回删除计数和被保留的审计类别。

## 5. 阶段二：运营生命周期 Skill

### 5.1 能力目录

正式能力按运营生命周期组织：

| 环节 | Skill | 核心成果 | 主要执行者 |
|---|---|---|---|
| 诊断 | `account_inspection` | 账号体检报告 | 数据、定位、内容、运营专家 |
| 定位 | `account_positioning` | 定位与人设方案 | 定位专家 |
| 选题 | `topic_planning` | 选题计划 | 内容策略专家 |
| 脚本 | `script_generation` | 分镜脚本 | 内容策略专家 |
| 视觉 | `visual_brief_generation` | 封面和素材 Brief | 视觉专家 |
| 排期 | `content_calendar_planning` | 发布日历 | 运营专家 |
| 发布准备 | `publishing_preparation` | 可审批发布包 | 运营专家 + Tool |
| 发布 | `content_publishing` | 发布回执 | 平台 Tool + 人工审批 |
| 互动 | `engagement_review` | 评论/客服处理建议 | 客服专家 |
| 复盘 | `performance_review` | 数据复盘报告 | 数据、内容、运营专家 |
| 迭代 | `operation_iteration` | 下一周期调整计划 | 主 Agent 组合多个 Skill |

`operation_iteration` 是组合 Skill，不直接生成专业结论；它依次消费已确认的复盘成果、定位约束和内容成果，生成下一周期执行图。

### 5.2 任意环节进入

用户可以从任意环节开始。每个 Skill 声明 `required_context`，缺少必要上下文时只询问最少问题或调用数据 Tool 补齐，不强迫用户从账号体检重新开始。

例如用户直接要求脚本时，只需要主题、目标和时长；账号定位数据不存在时可以采用用户本轮提供的临时约束，并在成果中标记来源。

### 5.3 真实能力与可发现性

Composer 只展示当前平台、当前账号和当前权限下真正可运行的 Skill。能力状态分为：

- `available`：可直接执行。
- `needs_input`：缺少用户输入。
- `needs_connection`：缺少平台或 Tool 连接。
- `coming_soon`：只允许查看说明，不允许提交执行。

主 Agent 的能力说明接口从同一 Capability Registry 生成，避免界面、Prompt 和实际 Runtime 三套口径。

## 6. 阶段三：性能与交付体验

### 6.1 路由与响应预算

- 明确 Skill 快捷入口：路由 P95 小于 50ms。
- 问候、身份、能力、账号是否有数据、常用指标查询：确定性路由 P95 小于 100ms。
- 普通回答首字 P95 小于 3 秒。
- 数据查询总耗时 P95 小于 2 秒。
- Skill 在 1 秒内返回阶段状态，并至少每 10 秒更新一次进度。

Router 采用“显式 Skill → 确定性规则 → 轻量分类 → 澄清”的顺序。普通回答不得先分类模型再回答模型；可直接回答时只调用一次模型。

### 6.2 流式状态

每个 Turn 只显示一个实时状态区域，不同时出现“思考中”和“正在思考”。状态文案按阶段变化：理解需求、读取数据、调用专家、质量复核、等待审批、生成成果。模型文本继续流式输出；Skill 在正式成果未完成前输出阶段事件，不伪造逐字流。

### 6.3 成果与证据

成果卡默认展示业务信息：成果名称、摘要、参与专家、数据周期、质量状态、下一步操作。证据按来源和指标聚合，例如“引用账号数据中心 4 类指标、79 条记录”。原始 `field_observation #id` 只在二级技术日志中分页展示。

对话内成果与成果中心引用同一 Artifact 版本；修改、采用、重做和审批在两处同步。

### 6.4 多平台

前端按当前账号平台加载 Capability Registry，不再硬编码抖音。Skill 的平台差异通过 Adapter 和平台约束表达，业务层不复制 Skill 实现。

## 7. 错误、重试与状态机

统一错误分类：

- `validation`：输入不合法，返回字段级提示，不重试。
- `scope_conflict`：账号、项目、会话不一致，不重试。
- `permission`：等待授权或人工审批，不重试。
- `capability_unavailable`：Tool/Adapter 不可用，阻塞并给出接入动作。
- `transient`：网络或限流，可指数退避重试，最多两次。
- `quality`：Critic 不通过，只允许一次成果重做。
- `terminal`：不可恢复失败，关闭 Turn、Run、SkillRun，显示安全错误。

Run 与 Turn 必须保持终态一致。每次重试复用消息、SkillRun 和 ToolCall 幂等身份，不产生重复回复或成果。

## 8. 测试与验收

每项行为先写失败测试，再实现。最低测试层级包括：

- 单元测试：参数合并、确定性路由、错误分类、证据聚合。
- 契约测试：Skill 输入输出、Tool 执行、审批与 Critic 状态机。
- API 测试：附件权限、账号隔离、幂等提交、会话删除。
- 前端测试：能力状态、附件上传、单一流式状态、成果聚合证据、多平台加载。
- 集成测试：从用户消息到成果卡和成果中心的完整 Turn。
- 生产冒烟：每个平台使用测试账号验证查询、一个只读 Skill 和一个审批 Skill。

关键回归用例：

1. “规划未来14天的10个选题”必须得到 14 天、10 个选题。
2. “只诊断，不生成策略”不得创建策略或进入组合运营图。
3. 发布准备必须存在真实 ToolCall，审批前不得完成。
4. 未接入发布 Adapter 时，真实发布必须显示阻塞。
5. 其他账号、其他用户的附件、会话和成果不可读取。
6. 删除会话后无法恢复消息、提示词和技术执行日志。
7. 简单“我现在账号有数据吗”不得调用意图分类模型。
8. 79 条证据在业务界面显示为聚合摘要，不逐条铺开。

## 9. 交付切片与发布顺序

三个阶段拆成独立可上线切片：

1. `CapabilityRequest + Skill 参数`。
2. `Tool/审批/质量状态机`。
3. `附件 + 删除语义`。
4. `运营生命周期 Skill`，每个 Skill 独立上线。
5. `确定性路由与性能预算`。
6. `流式状态、证据聚合和多平台体验`。

每个切片必须通过测试、代码审查、迁移验证和生产冒烟后再开启功能开关。旧路径只在对应新路径验证通过后下线。

## 10. 完成定义

本轮完成需要同时满足：

- Capability Registry、界面能力和 Runtime 实际能力一致。
- 所有正式 Skill 使用结构化输入并执行声明的 Tool/审批/质量门。
- 运营者可以从任意环节开始，并得到明确成果或可操作的阻塞原因。
- 对话、成果、附件和技术日志按用户与账号隔离。
- 常用回答和查询达到性能预算，Skill 有持续进度反馈。
- 不再出现“未执行专家/Tool，但页面显示完成”的状态。
- 全量自动化测试、前端生产构建和生产冒烟通过。
