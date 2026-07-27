# AI COO 与抖音官方投稿回流闭环设计

日期：2026-07-27

状态：待书面验收

## 1. 背景与目标

同舟行现有系统已经具备：

- 客户、项目、账号上下文与权限边界
- `BrainTask`、`AgentInvocation`、`AgentToolCall`、`Deliverable`、`Event`
- LangGraph Runtime、专家隔离、工具权限与人工审批
- 发布包、人工发布清单与审批展示
- 多源账号数据中心、表格导入和统一数据视图
- 抖音 OAuth、账号授权、能力状态诊断和 H5 投稿所需基础配置

当前缺口不是再造一套 Agent，而是补齐两个相互依赖的生产闭环：

1. 主 Agent 从“动态任务调度者”升级为“AI 运营负责人（AI COO）”。
2. 新内容从同舟行发起抖音官方投稿，建立可追踪的作品身份和后续数据回流链路。

本次是增量升级。已有 API、数据库模型、权限体系和页面入口保持兼容，不删除现有能力，不引入 Mock 数据。

## 2. 官方能力边界

当前生产主路径采用“网站应用 H5 投稿 + 投稿任务”，不采用浏览器自动化，也不默认使用服务端代发。

抖音开放平台当前公开说明：

- 网站应用可申请 `h5.share` 和 `open.get.ticket`，由用户从第三方应用唤起抖音并确认发布。
- 投稿任务使用 `task.posting.create`、`posting.behavior` 和 `task.posting.user_verification`。
- 绑定作品时，作者必须与授权 OpenID 一致，作品必须公开，必须由当前应用发布，且发布时间不得早于任务开始时间。
- 视频基础信息接口可返回作品 ID、标题、发布时间、媒体类型和作品状态等允许字段。

参考：

- [能力概览](https://developer.open-douyin.com/docs/resource/zh-CN/dop/ability/common-solution/)
- [创建投稿任务](https://developer.open-douyin.com/docs/resource/zh-CN/dop/develop/openapi/video-management/posting-task/create-posting-task)
- [绑定视频](https://developer.open-douyin.com/docs/resource/zh-CN/dop/develop/openapi/video-management/posting-task/bind-video)
- [查询视频基础信息](https://developer.open-douyin.com/docs/resource/zh-CN/dop/develop/openapi/video-management/posting-task/video-basic-info)
- [能力申请及使用规范](https://developer.open-douyin.com/docs/resource/zh-CN/dop/operation-standard/platform-capabilities/usage-spec)

系统不得承诺网站应用能够自动获得完整历史经营数据。播放量、完播率、互动指标、粉丝画像等字段若当前能力未返回，继续通过抖音后台表格、经确认的截图或结构化人工录入进入数据中心。

“平台投稿回流”只表示：

- 作品由同舟行发起投稿；
- 投稿任务与作品身份已被官方接口确认；
- 官方当前允许返回的字段已同步。

它不等同于“所有经营指标均可自动回流”。

## 3. 方案选择

### 3.1 已采用方案

采用“官方 H5 投稿 + 投稿任务账本”：

1. 同舟行准备发布包。
2. 人工确认账号、内容、素材和风险。
3. 后端创建抖音投稿任务。
4. 前端生成 H5 投稿唤起参数，由用户在抖音完成发布。
5. 系统取得或由用户提交作品 ID。
6. 后端调用绑定视频接口验证作品归属。
7. 绑定成功后写入作品主档并开始官方字段同步。

### 3.2 暂不采用

- Cookie、Playwright 或浏览器自动点击抖音创作者中心。
- 未经用户确认的自动发布。
- 在未获批专属能力前调用服务端代发接口。
- 仅凭标题和发布时间猜测平台作品身份。

服务端代发未来仅可作为独立 `feature flag` 后的执行适配器，并且必须通过应用能力、权限、审计和人工确认四重校验。

## 4. 双数据线设计

### 4.1 历史与补充数据线

```text
Excel / CSV / 经确认截图 / 人工录入
  -> DataImportBatch
  -> DataArtifact
  -> PlatformContentRecord / 指标快照
  -> AccountDataViewService
```

规则：

- 原始证据、导入人、统计周期和确认状态必须保留。
- 导入数据标记明确来源，不冒充官方 API 数据。
- 数据冲突不静默覆盖。
- 同一作品后续获得官方作品 ID 时，提升既有作品身份，不新建重复作品。

### 4.2 平台投稿数据线

```text
PublishPackage
  -> Human Approval
  -> PlatformPublishJob
  -> Douyin Posting Task
  -> H5 User Publish
  -> Official Video Binding
  -> PlatformContentRecord
  -> Official Field Sync
  -> AccountDataViewService
```

规则：

- 只有官方绑定成功的作品才能标记为 `official_bound`。
- 没有作品 ID 时保持 `waiting_bind`，不得按标题自动认领。
- 官方数据与人工数据冲突时，统一视图优先使用官方观测，但保留全部来源和冲突记录。
- 完整经营指标不可用时，数据中心提示用户按观测节点补充导入。

## 5. AI COO Runtime

### 5.1 主 Agent 定位

主 Agent 是运营决策者，不替代专家直接完成全部专业工作。职责包括：

- 理解业务目标和约束
- 读取真实客户、项目、账号与数据上下文
- 判断当前运营态势
- 制定阶段策略
- 动态选择必要专家
- 监督质量、成本、风险和执行进度
- 根据真实结果复盘
- 提交经过验证的经验候选

专家可以被独立调用；当主 Agent 执行复杂目标时，由主 Agent 绝对控制专家选择、顺序、预算、工具权限、重试和停止条件。禁止固定调用全部专家。

### 5.2 LangGraph 节点

```text
GoalUnderstanding
  -> ContextResolution
  -> SituationAwareness
  -> StrategyPlanning
  -> TaskPlanning
  -> ExpertDispatch
  -> ExpertExecution
  -> CriticReview
  -> ImproveLoop
  -> HumanApprovalGate
  -> ActionExecution
  -> WaitForMeasurement
  -> PerformanceAnalysis
  -> Reflection
  -> ExperienceVerification
  -> NextStrategy
```

关键分支：

- 简单问候或知识问答不创建运营任务，不调用专家。
- 数据不足时先请求用户补充或降低结论置信度。
- Critic 低于阈值时最多自动改进两轮，防止无限循环。
- 投稿、删除、投流和未来 MCP 高风险动作必须经过权限门。
- 等待发布或效果观测时保存 checkpoint，worker 可恢复执行。

### 5.3 Runtime State

新增 `COORuntimeState`，在现有 task/thread/checkpoint 上增量扩展：

- `task_id`、`run_id`、`thread_id`
- `org_id`、`available_client_ids`、`available_project_ids`
- `active_client_id`、`active_project_id`、`account_id`
- `user_goal`、`normalized_goal`
- `evidence_refs`
- `situation_summary`
- `strategy_plan_id`
- `task_plan`
- `agent_invocation_ids`
- `deliverable_ids`
- `quality_score_ids`
- `pending_approval_ids`
- `publish_job_ids`
- `performance_snapshot_ids`
- `reflection_record_id`
- `experience_candidate_ids`
- `next_strategy`
- `phase`、`iteration`、`budgets`、`cost`
- `status`、`errors`

State 只存流程所需的结构化信息和引用，不存模型私有思维链。

## 6. Agent Prompt 与 Harness

新增或升级以下 Prompt 契约：

- `coo-main`：只负责决策、调度、监督、汇总和下一步选择。
- `goal-understanding`：将用户目标标准化，识别成功指标和缺失条件。
- `situation-awareness`：基于证据输出账号阶段、问题、证据和置信度。
- `strategy-planner`：回答“为什么这样做”，生成阶段策略和 KPI。
- `task-planner`：把策略拆成可执行任务，不直接生成专业成果。
- `critic`：评价品牌一致性、用户价值、传播能力、商业转化和事实准确性。
- `reflection`：比较目标与结果，区分流量、互动和商业价值。
- `experience-verifier`：验证经验候选是否有真实数据或人工确认支持。
- 各专家 Prompt：保持独立输入包、工具白名单和输出契约。

Harness 统一提供：

- 账号和项目作用域校验
- 工具白名单
- 结构化输出验证
- Token、时间、轮次和成本预算
- 幂等键
- checkpoint
- 可取消、可重试和失败分类
- Prompt、模型、工具和证据版本记录

模型输出始终是不可信输入；外部动作的安全边界由代码和权限策略保证。

## 7. 数据库增量

### 7.1 AI COO 运营语义表

新增：

- `strategy_plans`
- `decision_traces`
- `experience_memories`
- `reflection_records`
- `agent_quality_scores`

`decision_traces` 记录目标、数据依据、备选策略、选择理由、执行动作和结果，不记录私有思维链。

`experience_memories` 只接受以下来源：

- 有明确证据引用的历史项目结果
- 规则验证通过的结果
- 人工确认

模型自行总结只能成为候选，不得直接成为已验证经验。

### 7.2 投稿任务账本

新增 `platform_publish_jobs`：

- 作用域：`org_id`、可选 `client_id`、可选 `project_id`、`account_id`
- 关联：`brain_task_id`、`content_item_id`、`deliverable_id`、`agent_tool_call_id`
- 平台与模式：`platform`、`execution_mode`
- 发布包快照：标题、正文、话题、素材、封面、可见性、评论开关
- 官方身份：`posting_task_id`、`open_id`、`video_id`、`item_id`
- 状态、状态版本、幂等键
- `approved_at`、`task_created_at`、`published_at`、`bound_at`
- `next_sync_at`、`retry_count`
- 标准化错误码、可安全展示的错误摘要
- 能力和 Scope 快照

账号本身继续支持绑定多个客户和多个项目。每条投稿任务只冻结用户发起任务时选择的一个活动客户和一个活动项目；未绑定客户或项目的账号仍可投稿，对应字段为空。这样既保留账号矩阵的多归属能力，也保证单次执行、权限校验和复盘口径明确。

`AgentToolCall` 和现有人工审批记录仍是唯一的权限与审批事实源。`platform_publish_jobs` 只通过 `agent_tool_call_id` 引用已审批动作，不新增第二套批准结果；投稿状态不得绕过现有审批状态自行进入 `task_created`。

状态：

```text
draft
pending_approval
task_created
handoff_ready
user_publishing
waiting_bind
bound
observing
completed
failed
expired
cancelled
```

`PlatformContentRecord` 继续作为作品主档。投稿绑定成功后向其补充官方 ID 和来源引用，不复制一套作品表。

### 7.3 约束

- 同一幂等键只能创建一条投稿任务。
- 同一账号和平台下，同一官方作品 ID 只能绑定一次。
- 投稿任务、作品和账号必须属于同一组织作用域。
- 账号 OpenID 与投稿绑定 OpenID 必须一致。
- 已绑定作品不可退回 `waiting_bind`，只能记录同步失败并重试。

## 8. API 增量

保留现有接口，增加：

### 8.1 AI COO

- `GET /brain/tasks/{id}/strategy`
- `GET /brain/tasks/{id}/decisions`
- `GET /brain/tasks/{id}/quality-scores`
- `GET /brain/tasks/{id}/reflection`
- `GET /accounts/{id}/situation`
- `GET /experience-memories`
- `POST /experience-memories/{id}/verify`
- `POST /brain/tasks/{id}/resume-observation`

### 8.2 投稿任务

- `POST /publish-jobs`
- `GET /publish-jobs/{id}`
- `GET /publish-jobs`
- `POST /publish-jobs/{id}/create-posting-task`
- `POST /publish-jobs/{id}/handoff`
- `POST /publish-jobs/{id}/bind`
- `POST /publish-jobs/{id}/sync`
- `POST /publish-jobs/{id}/retry`
- `POST /publish-jobs/{id}/cancel`

审批继续使用现有 `AgentToolCall` 或人工审批接口。`create-posting-task` 必须验证其关联审批已经通过；不存在关联审批、审批被驳回或审批已失效时返回稳定业务错误。

所有外部写操作都要求 `Idempotency-Key`。错误使用统一结构：

```json
{
  "error": {
    "code": "DOUYIN_VIDEO_NOT_FROM_APPLICATION",
    "message": "该作品不是通过当前应用发布，无法绑定。",
    "retryable": false,
    "details": {}
  }
}
```

第三方响应必须先验证结构，再写入数据库或渲染。

## 9. 前端交互

运营大脑保持 ChatGPT / Claude 式单一对话流，不增加复杂 Dashboard。

对话中展示：

- 主 Agent 对目标的理解
- 当前引用的数据范围和时效
- 专家接力与关键产出
- 关键决策及其证据
- 紧凑的人工确认条或方案选择器
- 投稿状态时间线
- 正式成果、效果结果和下一步建议

不展示：

- 原始 JSON
- 私有思维链
- 默认展开的 Token、工具参数和底层错误栈

管理员可在“执行详情”查看调用链、模型、Prompt 版本、Token、成本、工具、错误和重试。

内容生产与人工审批展示真实账号头像、平台、发布内容、素材、封面、话题、可见性和风险。用户批准后进入“提交至抖音”，而不是只变更数据库状态。

数据中心给每条数据标记：

- `历史导入`
- `人工确认`
- `平台投稿回流`
- `官方 API`

缺少自动指标时显示具体缺口和建议导入时间，不显示伪造的 0。

## 10. 错误恢复与可观测性

- 创建投稿任务失败：按错误类型区分配置错误、权限错误、参数错误、限流和平台暂时故障。
- H5 发布中断：保留任务，可重新唤起，不重复创建投稿任务。
- 未取得作品 ID：保持 `waiting_bind`，允许用户补充或稍后继续。
- 绑定失败：显示官方原因；可重试错误进入退避队列，不可重试错误等待人工处理。
- 数据同步失败：不影响已绑定身份；保留上次成功快照。
- worker 重启：从 checkpoint 和 `next_sync_at` 恢复。
- 每次状态变化写入 `Event` 和审计日志。

监控至少包括：

- 投稿任务创建成功率
- H5 唤起到绑定成功转化率
- 平均绑定耗时
- 同步成功率与数据时效
- Runtime 节点耗时、失败率、重试和成本
- Critic 改进轮次与通过率
- 经验候选验证通过率

## 11. 灰度与功能开关

- `COO_RUNTIME_V1_ENABLED`
- `DOUYIN_H5_PUBLISH_ENABLED`
- `DOUYIN_POSTING_TASK_ENABLED`
- `DOUYIN_DIRECT_PUBLISH_ENABLED=false`
- `AUTO_EXPERIENCE_MEMORY_ENABLED=false`

灰度顺序：

1. 本地测试账号，仅创建投稿任务，不唤起。
2. 单一内部账号完成 H5 投稿与绑定。
3. 内部三个账号启用回流和观测。
4. AI COO 只读态势与策略。
5. AI COO 生成发布包并进入人工审批。
6. 经稳定性验收后扩大内部账号范围。

## 12. 首个真实垂直切片

第一阶段只交付一个可验收闭环：

1. 选择已授权且能力就绪的抖音账号。
2. 由内容生产或运营大脑生成一个发布包。
3. 人工审批发布包。
4. 系统创建官方投稿任务。
5. 前端 H5 唤起抖音，用户确认发布。
6. 返回平台后提交作品 ID 并完成官方绑定。
7. `PlatformContentRecord` 出现该作品的官方身份。
8. 数据中心显示“平台投稿回流”来源。
9. 运营大脑能引用该作品的官方基础信息。
10. 未开放的经营指标明确提示继续导入，不编造数据。

这一切片通过后，再增加自动观测、Performance Analysis、Reflection 和 Experience Verification。

## 13. 验收标准

- 旧任务、旧发布包、旧人工审批和历史导入数据仍可读取。
- 未授权账号或能力未获批时，投稿在执行前被明确阻断。
- 重复请求不会创建重复投稿任务或重复绑定作品。
- 外部动作均有人工确认、权限校验、审计和可恢复状态。
- AI COO 的态势判断和策略必须引用真实数据证据。
- 主 Agent 不固定调用全部专家，也不直接冒充专家生成专业结论。
- Critic 低于阈值时最多改进两轮，之后交给人工判断。
- 经验只从真实结果和确认流程产生。
- 页面不展示底层 JSON，不使用 Mock 指标。
- 投稿任务、作品记录、指标快照、复盘与经验可以沿同一 task/thread 追踪。

## 14. 非目标

- 本阶段不开发抖音小程序。
- 不恢复已下线的历史粉丝或视频数据接口。
- 不使用浏览器自动化发布。
- 不承诺自动获得完整经营数据。
- 不在未获批能力前开放服务端代发。
- 不把模型推理文本当作决策证据或运营事实。
