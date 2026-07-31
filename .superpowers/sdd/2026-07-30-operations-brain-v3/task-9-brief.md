# Task 9 Brief — Turn 可观测性、能力矩阵与发布门禁

## Ownership and workflow

负责计划 Task 9 列出的模型、0600 迁移、Gateway/Turn telemetry、脱敏 API、前端指标、CI、E2E 和 runbook。必须等待并复用 Task 8 单一 Turn 投影，不得恢复 legacy UI。

严格 TDD；先模型/时间/预算/脱敏 RED，再 E2E 和门禁。你不是代码库唯一执行者，不得回退他人提交。

## Persisted telemetry

在 `ConversationTurn` 增加 nullable/nonnegative Integer：

- route_ms
- first_token_ms
- completion_ms
- total_ms
- model_call_count

历史保持 NULL；新 Turn 初始化 model_call_count=0。0600 迁移只做加法，不伪造回填。

时间语义：

- T0：Turn.created_at
- T1：Worker 真正开始执行
- T2：route decision 完成
- T3：面向用户的 `00-decision` 首个非空 provider delta
- T4：assistant_response/message_done 持久化
- T5：terminal/paused 状态持久化

指标：

- route_ms=T2-T1
- first_token_ms=T3-T0；无用户流式 token 为 NULL
- completion_ms=T4-T0；无回答为 NULL
- total_ms=T5-T0；resume 后更新
- model_call_count=该 Turn 下所有真实 provider attempts 累计

route/first token 首次值保留；completion/total 可在最终 resume 更新；retry/resume 不清零。

## Counting architecture

- 新 `turn_observability.py` 使用 ContextVar 绑定 org/thread/turn/run。
- `execute_conversation_turn` 整体 bind，异步专家任务继承 scope。
- `LLMGateway._record` 在真实 `LLMCall` 审计旁原子 UPDATE Turn model_call_count；不能由业务 JSON 推断。
- provider retry/fallback 每 attempt 计数；终态 replay 不调用 Gateway。
- 只把 `agent_code=="00-decision"` 首个 provider delta 计为用户 TTFT；系统动画/专家 token 不算。

## Model budgets

- `你好` / `你能做什么`：router=0，answer=1，总模型=1。
- 显式 query：router=0，总模型通常=0。
- 显式 skill：router=0；专家/critic 调用另计。
- 模糊请求：router=1；后续 answer/expert另计。
- 按 LLMCall.agent_code 断言 router=`00-router`、answer=`00-decision`，不能只看总数。

## Safe technical projection

后端返回强类型 allowlist：

- turn id/status/mode/route_source
- 5 个指标
- skill public code/version/status/quality score
- expert public code/name/status/attempt/duration
- tool public code/name/status/duration/retry_count/requires_confirmation/safe side-effect level
-稳定 error_code + allowlisted recovery action
- artifact/evidence id

禁止 Prompt、raw provider body/error、token/key/idempotency key、schema/model params、raw tool input/output/meta、stack、思维链。

## Ten-case capability matrix

1. 问候：ANSWER/completed/router0/answer1。
2. 能力询问：ANSWER/completed/router0/answer1。
3. 只查账号数据不生成策略：QUERY/completed/router0/无策略成果。
4. 一键账号体检：SKILL，报告或安全 blocked，router0。
5. performance_review：复盘成果+证据，router0。
6. topic_planning：选题成果，router0。
7. publishing_preparation：发布准备包，不真实发布，router0。
8. 请求真实发布：ACTION/waiting_permission，审批前无 dispatch。
9. 专家/Tool 强制失败：安全失败、无伪造成果/raw error。
10. 同线程追问：复用当前账号/成果，无跨账号泄漏、不发布。

后端 Worker 测试直接调用 worker 并 `asyncio.wait_for`，不 sleep；UI 使用 `expect.poll`，不 `waitForTimeout`。

## Migration and CI gates

- 当前 migration chain 含在线数据查询，不能诚实生成完整 offline SQL。
- `env.py` offline 对数据依赖链明确 CommandError/fail-fast，不能 AttributeError。
- 生产阻断门禁：临时 PostgreSQL 从 current-prod revision/snapshot online upgrade 到 head，跑 preflight/smoke。
- CI：
  - V3 directed <=5min
  - migration Postgres online lane
  - backend full timeout 15min + durations
  - frontend test/type/lint/build
  - Playwright
- 936+ 后端全量超过本地 120s 不等于失败；记录真实完成/CI结果，不虚报。

## Required verification

- 0600 upgrade/downgrade/constraints。
- fake-clock timing。
- 并行原子计数。
- 模型预算。
- 敏感字段投影。
- 10 类后端矩阵与 UI。
- full backend/frontend/type/lint/build/Playwright。
- migration online smoke。
- code review and rollout runbook。

## Out of scope

- 不重写全部历史数据迁移以支持完整 offline SQL；如业务强制需要，另立迁移重写 Task。
- 不部署生产。
