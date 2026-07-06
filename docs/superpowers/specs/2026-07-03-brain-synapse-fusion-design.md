# 运营大脑融合设计

## 目标

把 SYNAPSEAUTOMATION、OpenManus、social-auto-upload、Douyin_TikTok_Download_API 中可复用的执行模型融合到同舟行，而不是照搬它们的后台工具形态。运营大脑的第一版应当让用户先选择平台和账号，再用一句话交给主 Agent，由主 Agent 编排专家团完成可验收的运营任务。

## 借鉴内容

### SYNAPSEAUTOMATION

- 任务必须绑定 `platform + account_id`，避免脱离账号上下文执行。
- 任务状态采用可执行语义：待确认、执行中、待验收、已完成、失败。
- 专家步骤保留依赖关系、当前状态、风险等级、预期产出。
- 高风险或最终交付必须进入人工审批/验收，而不是直接自动发布。
- 后续内容生产、人工审批、运营复盘可以复用同一套任务与交付物状态。

### OpenManus

- 借鉴 `BaseAgent.run -> step` 的执行循环，但同舟行的步骤要持久化到 `BrainTask/OrchestrationPlan/AgentInvocation`。
- 借鉴 `PlanningFlow` 的计划状态：未开始、进行中、完成、阻塞，并映射到当前 `planned/running/done/blocked/failed`。
- 借鉴 tool-call 轨迹展示：每次专家调用要能展示“思考、工具、结果、失败原因”。
- 借鉴“卡住检测/最大步数”思想，后续为运营大脑增加重复输出和超预算保护。

### social-auto-upload

- 借鉴发布任务的素材校验：视频/图片格式、标题长度、正文长度、发布时间提前量。
- 借鉴发布任务参数模型：标题、正文、话题、封面、图文/视频类型、定时发布时间。
- 当前阶段只进入“发布准备/人工审批”，不直接浏览器自动发布。

### Douyin_TikTok_Download_API

- 借鉴链接解析和内容数据适配：作品 ID、用户 ID、评论、作品详情等标准化输入输出。
- 正式抖音接入仍以官方 OAuth/OpenAPI 为主；非官方解析仅作为可选研究参考，不作为默认生产路径。

## 不采用内容

- 不采用 Cookie/Playwright 作为抖音主接入方式，抖音继续走官方 OAuth/OpenAPI。
- 不复制 Douyin_TikTok_Download_API 中涉及 `A-Bogus/X-Bogus` 或绕过签名/反爬的实现。
- 不复制 social-auto-upload 的 Creator Center 自动化发布实现到当前阶段。
- 不在当前阶段做自动发布和投流。
- 不照搬 SYNAPSE 的暗色后台 UI、Electron、Celery 任务体系。

## 第一阶段范围

1. 运营大脑页面读取全局当前平台与账号。当前仅启用抖音。
2. 未选择账号时，页面展示账号上下文 gate，引导用户先切换账号。
3. 输入目标后，前端把 `platforms:["douyin"]` 和当前账号 `account_ids` 一起提交。
4. 后端校验账号属于当前组织、平台匹配、账号可用于运营任务。
5. 后端计划步骤补充 `execution_kind` 与 `human_gate` 语义，前端可区分“分析、生成、审批、发布准备、复盘”。
6. 任务返回的 brief、plan、trace、acceptance 继续作为前端渲染来源。
7. 前端页面从传统表单改为“主 Agent 输入 + 专家编排 + 当前账号任务流”的工作台。

## 验收标准

- 用户进入运营大脑能明显看到当前账号上下文。
- 未选账号不能创建运营任务。
- 创建 draft 后能看到 Brief、专家步骤、人工确认动作。
- 后端拒绝账号缺失、账号不存在或平台不匹配的任务。
- 页面保持全局黑白灰极简和 ChatGPT 风格字体，不出现传统后台堆表单感。
