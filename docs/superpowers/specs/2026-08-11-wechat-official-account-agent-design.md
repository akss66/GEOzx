# 微信公众号主 Agent 首版设计规格

## 1. 文档状态

- 日期：2026-08-11
- 状态：待用户书面审核
- 适用系统：同舟行 AI 新媒体运营平台
- 目标平台：微信公众号
- 首版边界：单篇原创长图文生产、配图、预览和同步草稿箱
- 明确不包含：自动发布、多篇合集、图片消息、转载、无人值守选题、自动数据复盘

本规格是后续实施计划、任务拆分、测试和验收的唯一需求来源。实现过程中如果产品边界或接口能力发生变化，必须先更新本规格，再修改代码。

## 2. Objective

### 2.1 产品目标

运营者通过唯一入口“主 Agent”提出公众号文章目标。系统在当前账号和绑定品牌知识库的范围内，完成：

1. 理解文章目标并补齐必要信息；
2. 调用专业 Agent 生成可编辑的完整初稿；
3. 生成正文图位、图片用途和隐藏提示词；
4. 支持一键生成全部图片、逐张生成、上传替换和外部生成回传；
5. 渲染接近微信公众号的排版预览；
6. 在用户明确确认后，将指定不可变版本同步到指定公众号草稿箱；
7. 记录知识依据、操作者、版本、外部草稿标识和失败原因。

主 Agent 是决策和交互入口，不越权编造产品事实，也不绕过用户执行外部写入。

### 2.2 主要用户

- 代运营团队负责人：管理客户、账号、品牌知识库和质量标准；
- 运营者：通过主 Agent 创建、修改、配图和同步文章；
- 编辑或审核者：检查内容、知识依据、版本差异和微信预览；
- 管理员：配置微信第三方平台、模型和系统权限。

### 2.3 首版成功标准

#### 功能完整性

- 一篇文章包含标题、摘要、作者、封面、正文、正文配图、行动引导和微信兼容排版。
- 用户可以在主 Agent 对话中发起任务，并从同一 WorkTurn 打开文章工作区。
- 用户可以修改文章、保存版本、查看版本差异和恢复历史版本。
- 用户可以一键生成全部配图，也可以逐张生成、重新生成或上传替换。
- 图片提示词默认不显示；只有用户点击“获取提示词”后才展示。
- 用户明确确认后，指定版本可以进入指定微信公众号草稿箱。
- 同步结果保存微信 media_id、同步时间、操作者、版本和请求幂等键。

#### 体验完整性

- 熟练用户从提出明确目标到确认同步草稿箱，主路径不超过 5 次关键交互；图片微调和自由编辑不计入。
- 消息提交后 500 毫秒内出现真实 WorkTurn，长任务进度无需刷新页面即可更新。
- 普通用户默认只看到业务步骤；专家调用、Tool、模型、耗时和重试位于折叠技术详情。
- 产品文案不使用“成果”“采用”等抽象动作，统一使用“文章初稿”“生成配图”“预览公众号效果”“同步到草稿箱”等具体业务语言。

#### 质量和业务指标

- 首批 30 个有效文章任务中，至少 30% 的文章无需结构性重写即可同步草稿箱。
- “无需结构性重写”定义为：初始生成版本与同步版本的正文语义变更率不超过 20%，且未发生章节级整体替换。
- 微信草稿同步成功率不低于 99%，不计微信平台不可用、账号权限不足和用户主动取消。
- 跨组织、跨账号、跨品牌知识错误引用为 0。
- 没有可靠来源的产品参数、案例、承诺或数据进入微信草稿箱的次数为 0。

### 2.4 用户故事

- 作为运营者，我可以说“给悠护贴膜公众号写一篇夏季落地窗隔热文章”，得到完整初稿，而不是一张抽象结果卡。
- 作为运营者，我可以看到文章为什么需要每张图，并选择一次生成全部或只生成某一张。
- 作为运营者，我可以点击获取某张图片的提示词，去外部工具生成后上传替换。
- 作为审核者，我可以查看文章引用了哪些品牌知识和来源文件。
- 作为团队成员，我不能覆盖别人已经更新的文章版本。
- 作为管理员，我可以让公众号管理员扫码授权，不需要向客户索取公众号 AppSecret。
- 作为操作者，我只有在确认目标账号和版本后才会把内容写入微信草稿箱。

## 3. 范围与非目标

### 3.1 首版范围

- 微信开放平台第三方平台代理授权；
- 公众号授权账号创建、权限快照、令牌刷新和取消授权；
- 品牌知识库及账号绑定；
- 单篇原创长图文 ArticleBrief、ArticleDocument 和微信渲染；
- 文章工作副本、不可变版本、差异和冲突处理；
- 图片图位、图片生成、上传替换和提示词按需展示；
- 微信正文图片上传、封面永久素材上传、草稿新增和草稿更新；
- 主 Agent 实时进度、人工确认、错误恢复和审计；
- 产品分析事件和首版成功指标。

### 3.2 明确不做

- 自动点击发布或调用 freepublish_submit；
- 一次组合多篇文章；
- newspic 图片消息；
- 原创声明、转载、付费阅读、商品卡和广告投放；
- 多人实时光标和字符级协同编辑；
- 自动热点选题、自动排期和无人值守生产；
- 自动拉取发布效果并调整后续文章；
- 同一账号同时绑定多个产品品牌知识库；
- 重新实现一套与现有 ConversationThread、WorkTurn、SkillRun、ContentItem、Deliverable 平行的运行系统。

## 4. 已选择方案

### 4.1 方案比较

#### 方案 A：每个公众号保存 AppID 和 AppSecret

开发简单，但新增客户需要人工收集秘密、难以统一撤销授权，且不适合代运营 SaaS。拒绝。

#### 方案 B：微信开放平台第三方平台代理授权

客户管理员扫码授权，系统集中处理 component ticket、平台令牌、授权账号令牌、权限集和取消授权。首期基础设施较多，但新增账号成本最低，安全边界最好。采用。

#### 方案 C：只生成文章，用户手工复制到微信

无法验证排版、图片地址和草稿写入，不能形成可靠闭环。仅作为权限不足时的降级能力，不作为主链路。

### 4.2 总体架构

    用户
      ↓
    主 Agent / WorkTurn
      ↓ 选择 Skill
    wechat_article_production
      ├─ 内容策划专家
      ├─ 文章编辑专家
      ├─ 视觉编辑专家
      ├─ 合规检查
      ├─ 品牌知识检索 Tool
      ├─ 图片生成 Tool
      ├─ 微信文章渲染 Tool
      └─ 微信草稿同步 Tool
      ↓
    文章工作副本 → 不可变版本 → 用户确认 → 微信草稿箱

Runtime 继续负责状态、幂等、重试、人工中断、审批和审计。微信服务负责第三方平台协议、令牌和外部 API；知识服务负责可见范围和引用；文章服务负责文档、版本和冲突。

## 5. 用户体验设计

### 5.1 主路径

理想路径包含以下关键交互：

1. 用户发送完整文章目标；
2. 主 Agent 直接生成文章初稿和全部图位；
3. 用户选择“一键生成全部配图”，也可跳过或上传自己的图片；
4. 用户查看公众号预览并点击“同步到草稿箱”；
5. 用户在确认窗核对公众号、标题和版本后确认。

如果必填信息可以从当前消息、会话或品牌知识库可靠推断，不重复追问。

### 5.2 结构化补齐

ArticleBrief 的必填字段：

| 字段 | 说明 | 允许推断 |
| --- | --- | --- |
| objective | 品牌认知、科普教育、获客咨询、活动转化等 | 是 |
| target_audience | 目标读者及其主要场景 | 是 |
| topic_or_product | 文章主题或目标产品 | 是 |
| primary_cta | 希望读者采取的主要行动 | 是 |

可选字段：

- core_selling_points：指定核心卖点；
- must_include：必须出现的信息；
- forbidden_expressions：禁用表达；
- tone：专业、亲和、故事化等；
- target_length：期望篇幅；
- reference_urls：参考文章；
- source_material_ids：指定素材；
- image_style：图片风格；
- author_name：作者；
- content_source_url：阅读原文地址；
- comment_policy：评论设置。

“允许推断”不等于可以猜测产品事实。只有推断置信度达到系统阈值且不涉及产品参数、案例、承诺或具体数据时才可自动补齐。否则 WorkTurn 进入 waiting_user，并展示单个结构化补齐面板。

### 5.3 文章工作区

对话是控制面，文章工作区是编辑面。文章产生后仍留在原 WorkTurn 下，显示：

- 文章标题和当前状态；
- “打开文章”；
- “查看知识依据”；
- “继续让主 Agent 修改”；
- 当前版本、最后编辑者和保存状态。

文章工作区包含三个视图：

1. 编辑：结构化正文和图位；
2. 公众号预览：微信兼容渲染结果；
3. 版本：不可变版本、操作者和差异。

### 5.4 图片交互

初稿只生成图位和图片方案，不自动消耗生图额度。

每个图位展示：

- 图片用途；
- 建议画面；
- 推荐比例；
- 状态；
- “生成该图片”；
- “上传替换”；
- “获取提示词”。

文章顶部提供“一键生成全部配图”。点击后只生成尚未完成且未上传替换的图位。提示词默认不返回到主界面；点击“获取提示词”后按需读取。

生成完成后提供：

- 使用这张；
- 重新生成；
- 修改要求后生成；
- 上传替换；
- 查看历史候选。

### 5.5 同步确认

确认窗必须显示：

- 目标公众号头像和名称；
- 文章标题；
- 不可变文章版本；
- 封面和正文图片数量；
- 未确认事实数量；
- 最近一次微信同步状态；
- 本次操作是“新建草稿”“更新已有草稿”还是“创建新草稿避免覆盖”。

按钮文案必须为“确认同步到公众号「名称」草稿箱”。不得使用“采用”“执行”或“继续”等模糊文案。

## 6. 品牌知识库

### 6.1 作用域

检索范围固定为：

    org_id
      → account_id
        → primary_brand_knowledge_base_id
        → organization_shared_knowledge_base

一个账号最多绑定一个启用中的主品牌知识库，可以附加组织共享合规知识库。多个平台账号可以绑定同一个品牌知识库。

未绑定品牌知识库时：

- 可以生成不包含品牌事实的通用行业文章；
- 涉及产品、案例或企业承诺时必须提示绑定知识库或上传资料；
- 不得从同组织其他账号的品牌知识库中自动选取内容。

### 6.2 数据结构

新增 KnowledgeBase：

- id、org_id、client_id；brand 类型必须关联 client_id，organization_shared 类型的
  client_id 为空；
- name、description；
- kind：brand 或 organization_shared；
- status；
- version；
- created_by_id。

新增 AccountKnowledgeBinding：

- org_id、account_id、knowledge_base_id；
- binding_type：primary_brand 或 shared；
- status；
- bound_by_id、bound_at；
- 同一账号只能有一个 active primary_brand 绑定。

扩展现有 KnowledgeEntry：

- knowledge_base_id；
- entry_kind：document、product_fact、policy、case、brand_voice、asset_reference；
- verification_status：draft、verified、rejected、expired；
- source_attachment_id；
- effective_at、expires_at；
- allowed_for_external_claim；
- 继续保留 version、source_label、source_url 和 KnowledgeCitation。

产品事实使用经过 Pydantic 校验的 payload：

    {
      "schemaVersion": 1,
      "kind": "product_fact",
      "productCode": "YH-001",
      "factKey": "warrantyYears",
      "value": 10,
      "unit": "year",
      "claimText": "质保 10 年",
      "allowedForExternalClaim": true
    }

模型输出不能直接写入 verified 知识。Agent 只能创建 KnowledgeSuggestion，由有权限的用户审核后进入知识库。

### 6.3 检索优先级

1. 当前账号专属规则；
2. 当前账号绑定的主品牌知识库；
3. 组织共享合规知识库；
4. 经允许的公开可追溯资料；
5. 无可靠依据时标记待确认。

所有进入文章的品牌事实必须保存 KnowledgeCitation。公开资料必须保存 URL、标题、抓取时间和用于支持的具体声明。

### 6.4 Prompt Injection 防护

知识文档、网页和用户上传文件全部视为不可信内容，只能作为资料，不得作为系统指令。检索层必须将内容与系统指令分区，忽略来源文本中的工具调用、权限修改和“覆盖之前指令”等内容。

## 7. 微信第三方平台代理授权

### 7.1 平台级配置

复用 PlatformIntegration，platform 新增 wechat_official_account。平台级配置包括：

- component_appid；
- component_appsecret 的 secret reference；
- 授权事件接收 URL；
- 消息校验 Token 的 secret reference；
- EncodingAESKey 的 secret reference；
- 授权回调 URL；
- 已申请的权限集；
- 平台审核状态。

新增 WechatComponentCredential，用于隔离微信特有的运行凭证：

- platform_integration_id；
- component_verify_ticket_encrypted；
- ticket_received_at；
- component_access_token_encrypted；
- token_expires_at；
- last_error。

### 7.2 授权流程

1. 后端取得 component_access_token；
2. 后端创建有效期 1800 秒的 pre_auth_code；
3. 前端跳转微信官方授权页面；
4. 管理员扫码授权；
5. 回调取得 authorization_code；
6. 后端换取 authorizer_refresh_token、authorizer_access_token 和 func_info；
7. 获取授权账号详情；
8. 创建或更新 Account 和 PlatformAccountAuth；
9. 执行能力检测并保存快照；
10. 用户选择并绑定品牌知识库。

PlatformAccountAuth 复用现有字段：

- external_open_id 保存 authorizer_appid；
- refresh_token_encrypted 保存 authorizer_refresh_token；
- access_token_encrypted 保存短期 authorizer_access_token；
- scopes 保存 func_info 权限集；
- raw_profile 保存公众号资料和认证类型；
- auth_status 保存 authorized、unauthorized、expired、reauthorization_required。

### 7.3 票据和回调

- component_verify_ticket 回调必须验签、AES 解密、幂等处理并快速返回 success；
- 授权、更新授权和取消授权事件使用事件唯一键去重；
- authorizer_access_token 到期前刷新；
- authorizer_refresh_token 丢失或失效时，不自动降级使用客户 AppSecret，必须要求重新授权；
- 回调正文、票据、令牌和密钥不得写入普通日志。

### 7.4 能力检测

授权后生成能力快照：

| 能力 | 首版用途 |
| --- | --- |
| upload_article_image | 上传正文图片 |
| add_permanent_material | 上传封面永久素材 |
| draft_add | 新建草稿 |
| draft_get | 读取远端草稿用于冲突检测 |
| draft_update | 更新已有草稿 |
| analytics | 仅记录是否可用，首版不执行复盘 |
| freepublish | 仅记录是否可用，首版强制关闭 |

第三方平台权限集不能替代公众号自身资质。能力检测必须同时考虑平台权限、管理员授权范围和授权公众号自身能力。

## 8. 文章文档、版本和冲突

### 8.1 复用现有内容模型

- ContentItem 表示一篇公众号文章；
- Deliverable 保存不可变的文章、视觉方案和微信渲染版本；
- 新增 DeliverableType：
  - wechat_article；
  - wechat_image_plan；
  - wechat_rendered_article；
- ContentItem.account_id 固定文章所属公众号；
- 所有查询同时校验 org_id、account_id、content_item_id。

### 8.2 结构化 ArticleDocument

模型不直接输出可执行 HTML。正文保存为受约束文档：

    ArticleDocument
      title
      digest
      author
      blocks[]
        heading
        paragraph
        quote
        list
        callout
        imageSlot
        divider
        cta

渲染器将 ArticleDocument 转换为微信兼容 HTML，并执行标签、属性和 CSS allowlist。JavaScript、事件处理器、iframe、未知 URL 协议和外部图片全部移除。

### 8.3 工作副本

新增 ArticleWorkingCopy：

- content_item_id；
- based_on_deliverable_id；
- document；
- lock_version；
- updated_by_id；
- updated_at。

编辑器每 2 秒防抖自动保存工作副本，不为每次键盘输入创建不可变版本。

更新请求必须提交 expectedLockVersion。版本不一致时返回 HTTP 409：

    {
      "error": {
        "code": "ARTICLE_VERSION_CONFLICT",
        "message": "文章已被其他成员更新",
        "retryable": false,
        "details": {
          "expectedLockVersion": 12,
          "currentLockVersion": 14,
          "latestEditor": 8
        }
      }
    }

前端提供“查看差异”“基于新版本继续修改”“放弃本地修改”，禁止自动覆盖。

### 8.4 不可变版本触发规则

以下操作生成新 Deliverable 版本：

- 首次 AI 初稿完成；
- AI 完成一次用户明确要求的整体改写；
- 用户点击“保存版本”；
- 同步微信前冻结版本；
- 微信同步成功后记录同步快照。

单次文字输入、光标移动、折叠面板、尚未选中的图片候选不创建不可变版本。

### 8.5 微信远端冲突

第一次同步调用 draft/add。后续同步同一 media_id 前：

1. 读取微信远端草稿；
2. 对规范化正文、标题、摘要和封面计算 remote_hash；
3. 与最近一次同步保存的 remote_hash 比较；
4. 不一致时返回 WECHAT_DRAFT_CONFLICT。

用户可以：

- 创建一个新微信草稿，推荐；
- 查看远端差异；
- 明确确认覆盖。

不得默认覆盖客户在微信后台进行的修改。

## 9. 图片与素材

### 9.1 图位

新增 ArticleImageSlot：

- content_item_id；
- stable_key；
- purpose；
- placement_after_block_id；
- aspect_ratio；
- visual_brief；
- prompt_internal；该字段不是安全秘密，但默认不随文章主响应返回，只通过“获取提示词”
  接口按需读取；
- status：planned、generating、ready、selected、failed；
- selected_material_id；
- lock_version。

图位 stable_key 在文章改写后尽量保持稳定，避免正文修改导致已选图片丢失。

### 9.2 生图提供商

图片生成通过 ImageGenerationProvider 接口：

    generate(prompt, aspectRatio, references, idempotencyKey) -> GenerationResult

首版只要求一个可配置实现，但数据库和 API 不暴露厂商专有字段。生成任务必须有成本记录、状态、失败原因和幂等键。

### 9.3 微信图片处理

- 正文图片通过微信“上传发表内容中的图片”接口转换成微信托管 URL；
- 封面通过永久素材接口取得 thumb_media_id；
- 预览可以使用系统对象存储 URL；
- 同步草稿前必须生成一份微信资产映射；
- 外部图片 URL 不直接写入微信 content。

## 10. Skill 和 Agent 责任

### 10.1 Skill

新增 wechat_article_production Skill，输入契约包括：

- account_id；
- thread_id、turn_id、run_id；
- ArticleBrief；
- current_working_copy_id；
- requested_action：create、revise、plan_images、render、prepare_sync。

输出是文章工作副本、不可变 Deliverable、图位、知识引用、质量检查和用户待办，不是泛化“成果卡”。

### 10.2 专家

- 内容策划专家：确定文章结构、受众、叙事和 CTA；
- 文章编辑专家：依据 ArticleBrief 和知识证据生成或修改 ArticleDocument；
- 视觉编辑专家：定义图位、画面用途、提示词和风格一致性；
- 合规检查：识别无来源声明、禁用表达、过度承诺和版权风险；
- 主 Agent：选择 Skill、处理缺失信息、展示进度、汇总状态和等待用户决策。

正式文章正文必须来源于文章编辑专家的结构化输出。主 Agent 不得在专家未执行时自行伪造正式文章。

### 10.3 质量门

同步微信前必须全部满足：

- ArticleBrief 必填字段完整；
- 标题、作者、摘要和正文满足微信限制；
- 已选择封面；
- 所有正文图位已选择图片或被用户明确删除；
- 产品事实均有可用 KnowledgeCitation；
- 无 unresolved_claim；
- 微信能力快照允许草稿写入；
- 冻结版本与确认版本一致；
- 用户明确确认同步。

自动质量审核不可用时，状态为 quality_review_unavailable，不得显示为 0 分，也不得伪装为已通过。

## 11. API 契约

### 11.1 通用错误

所有新增接口使用统一错误结构：

    {
      "error": {
        "code": "MACHINE_READABLE_CODE",
        "message": "用户可理解的信息",
        "retryable": false,
        "details": {}
      }
    }

- 400：请求结构错误；
- 401：未登录；
- 403：没有组织、账号或角色权限；
- 404：资源不存在，或为防止越权而隐藏；
- 409：文章版本、远端草稿或授权状态冲突；
- 422：业务校验失败；
- 502：微信或生图提供商返回无效响应；
- 503：外部平台暂不可用。

外部平台响应一律经过 Pydantic 验证后才能进入业务逻辑。

### 11.2 第三方平台授权

- POST /platform-integrations/wechat/authorization-sessions
  - 输入：可选目标 client_id、project_id、knowledge_base_id；
  - 输出：authorizationUrl、expiresAt、stateId。
- GET /platform-integrations/wechat/oauth/callback
  - 公共回调；验证 state 和 authorization_code；
  - 成功后跳转账号授权结果页。
- POST /platform-integrations/wechat/events
  - 公共加密事件入口；验签、解密、幂等。
- GET /accounts/{accountId}/platform-capabilities
  - 输出权限快照和可操作的修复建议。

### 11.3 知识库

- POST /knowledge-bases
- GET /knowledge-bases
- GET /knowledge-bases/{knowledgeBaseId}
- PATCH /knowledge-bases/{knowledgeBaseId}
- GET /knowledge-bases/{knowledgeBaseId}/entries
- POST /knowledge-bases/{knowledgeBaseId}/entries
- PUT /accounts/{accountId}/knowledge-binding
- GET /accounts/{accountId}/knowledge-binding
- DELETE /accounts/{accountId}/knowledge-binding

列表接口必须分页。绑定操作检查同组织、账号访问权和主品牌唯一约束。

### 11.4 文章

- POST /accounts/{accountId}/wechat-articles
- GET /wechat-articles/{articleId}
- PATCH /wechat-articles/{articleId}/working-copy
  - 请求头或请求体携带 expectedLockVersion；
  - 成功返回新 lockVersion。
- POST /wechat-articles/{articleId}/versions
- GET /wechat-articles/{articleId}/versions
- GET /wechat-articles/{articleId}/versions/{versionId}/diff
- GET /wechat-articles/{articleId}/preview

### 11.5 图片

- POST /wechat-articles/{articleId}/image-generations
  - 批量生成全部未完成图位。
- POST /wechat-articles/{articleId}/image-slots/{slotId}/generations
- GET /wechat-articles/{articleId}/image-slots/{slotId}/prompt
- POST /wechat-articles/{articleId}/image-slots/{slotId}/uploads
- PUT /wechat-articles/{articleId}/image-slots/{slotId}/selection

### 11.6 草稿同步

- POST /wechat-articles/{articleId}/draft-syncs
  - 输入：articleVersionId、idempotencyKey、expectedRemoteHash、conflictStrategy；
  - conflictStrategy：fail、create_new、overwrite_confirmed；
  - 首次请求默认 fail。
- GET /wechat-draft-syncs/{syncId}

相同 org_id 和 idempotencyKey 重复请求必须返回同一同步任务，不重复写入微信。

## 12. 实时状态与恢复

WorkTurn 可见业务步骤：

1. 正在确认文章目标；
2. 正在读取品牌知识；
3. 正在生成文章初稿；
4. 已规划配图位置；
5. 正在生成所选图片；
6. 正在检查公众号格式；
7. 等待你确认同步；
8. 正在同步微信草稿；
9. 微信草稿已同步。

页面刷新、SSE 断线或 Worker 重启后，前端从服务端恢复同一个 WorkTurn、文章工作副本和同步任务。外部写入使用幂等键，不能因重试生成重复草稿。

## 13. Security

- component_appsecret、Token、EncodingAESKey、component ticket、平台令牌、授权账号令牌全部只在服务端保存；
- 长期秘密使用 secret reference；需要持久化的刷新令牌使用现有 credential encryption；
- API、日志、事件、错误、浏览器存储和技术详情不得出现明文令牌；
- 微信回调必须验签、校验时间窗、AES 解密和幂等；
- OAuth state 绑定 org_id、发起用户、目标上下文和过期时间；
- HTML 渲染使用 allowlist 和 URL 协议校验；
- 上传文件验证 MIME、扩展名、大小和图片解码结果；
- 图片生成和公开网页内容不得被解释成 Agent 指令；
- 每次知识检索、文章读取、图片操作和草稿同步同时校验 org_id 和 account_id；
- 删除知识库或解除绑定不能删除已存在的不可变文章引用快照。

## 14. Observability

记录但不暴露秘密：

- 授权会话创建、成功、失败、取消和重新授权；
- component ticket 新鲜度；
- 平台令牌刷新成功率；
- 微信 API endpoint、状态码、errcode、rid、耗时和重试；
- 文章生成阶段耗时；
- 关键交互次数；
- 初稿到同步版本的变更率；
- 图片生成数量、失败率和成本；
- 草稿同步成功率、冲突率和重复写入拦截次数；
- 知识引用数量、未确认事实拦截次数；
- 跨作用域访问拒绝事件。

生产告警：

- component ticket 超过 20 分钟未更新；
- authorizer token 连续刷新失败；
- 微信草稿同步 5 分钟失败率超过 5%；
- 同一幂等键出现不同请求摘要；
- 任意跨组织范围校验异常。

## 15. Tech Stack

- 前端：React 18、TypeScript 5.6、Vite 6、Ant Design、TanStack Query、Zustand、Vitest、Playwright；
- 后端：Python 3.11、FastAPI、Pydantic 2、SQLAlchemy Async、Alembic、PostgreSQL、Redis、ARQ；
- Agent Runtime：现有 ConversationThread、WorkTurn、AgentRun、SkillRun 和 LangGraph；
- 外部平台：微信开放平台第三方平台 API、微信公众号素材和草稿 API；
- 对象存储：沿用现有素材存储抽象；
- 生图：新增可替换 ImageGenerationProvider。

不因本功能引入另一套 Web 框架、任务队列、数据库或 Agent Runtime。

## 16. Commands

### Backend

    cd backend
    uv sync --extra dev
    uv run alembic upgrade head
    uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
    uv run pytest
    uv run ruff check app tests
    uv run ruff format --check app tests
    uv run mypy app

### Frontend

    cd frontend
    npm install
    npm run dev -- --host 127.0.0.1 --port 5173
    npm test
    npm run lint
    npm run build
    npm run check:main-agent-bundle
    npm run test:e2e

## 17. Project Structure

预计沿用或新增以下边界，具体文件拆分在实施计划中确定：

    backend/app/models/
      knowledge.py             品牌知识库和引用
      platform.py              平台与授权账号
      content.py               ContentItem 和 Deliverable
      wechat_article.py        工作副本、图位和同步映射

    backend/app/schemas/
      knowledge.py
      platform_integrations.py
      wechat_article.py

    backend/app/services/
      knowledge_workspace.py
      wechat_component.py      第三方平台票据和授权
      wechat_articles.py       文档、版本和冲突
      wechat_renderer.py       微信 HTML 渲染
      wechat_drafts.py         图片上传和草稿同步
      image_generation.py      生图提供商抽象

    backend/app/api/
      platform_integrations.py
      knowledge.py
      wechat_articles.py

    backend/app/orchestrator/
      skill_runtime.py
      skills/                  公众号文章 Skill 定义

    backend/tests/
      test_wechat_component_*.py
      test_brand_knowledge_*.py
      test_wechat_articles_*.py

    frontend/src/
      pages/BrainHome.tsx
      pages/Knowledge.tsx
      pages/WechatArticleWorkspace.tsx
      components/wechat-article/
      services/
      types.ts

    frontend/e2e/
      wechat-article-flow.spec.ts

    docs/
      superpowers/specs/
      superpowers/plans/
      adr/

## 18. Code Style

新增接口遵循现有 Pydantic 边界验证和结构化错误：

    class SyncWechatDraftRequest(BaseModel):
        article_version_id: int = Field(gt=0)
        idempotency_key: str = Field(min_length=8, max_length=160)
        expected_remote_hash: str | None = Field(default=None, max_length=128)
        conflict_strategy: Literal["fail", "create_new", "overwrite_confirmed"] = "fail"


    async def sync_draft(
        request: SyncWechatDraftRequest,
        *,
        user: User,
        session: AsyncSession,
    ) -> WechatDraftSyncOut:
        scope = await require_article_scope(session, user, request.article_version_id)
        return await wechat_draft_service.sync(scope, request)

约定：

- Python 行长 100，使用类型注解和 async SQLAlchemy；
- 外部响应先通过 Pydantic schema；
- React 使用函数组件和明确 Props；
- TypeScript 不使用 any 绕过协议；
- 状态机使用显式枚举；
- 用户可见错误与技术错误分离；
- 不在单个页面组件继续堆积微信业务逻辑。

## 19. Testing Strategy

### 19.1 单元测试

- ArticleBrief 推断和必填判断；
- ArticleDocument schema 和 HTML allowlist；
- 知识库绑定唯一性和检索优先级；
- 产品事实引用和未确认事实拦截；
- 工作副本 lock_version；
- 不可变版本触发规则；
- 图位稳定性和批量生成幂等；
- 微信响应 schema、错误分类和 token 刷新；
- remote_hash 和冲突策略；
- 关键交互计数和正文变更率。

### 19.2 集成测试

- 组织 A 无法访问组织 B 的知识库、文章和授权账号；
- 账号 A 无法检索账号 B 的品牌知识；
- 授权回调完整流程；
- component ticket、component token 和 authorizer token 生命周期；
- 取消授权后禁止草稿同步；
- 上传正文图片、上传封面、新建草稿和更新草稿；
- 同一 idempotencyKey 不产生重复草稿；
- Worker 重试恢复同一同步任务；
- 远端微信草稿被修改时返回冲突。

微信 API 使用可控 mock server；CI 不调用真实微信。

### 19.3 前端测试

- 结构化补齐只在必填信息缺失时出现；
- 初稿、图位和图片状态原位更新；
- 提示词默认隐藏；
- 一键生成全部不重复生成已完成图位；
- 自动保存状态和 409 冲突界面；
- 同步确认展示账号、版本和操作类型；
- 技术日志默认折叠；
- 不同账号切换不残留文章或知识内容。

### 19.4 E2E

覆盖 1440×900 桌面端和 390×844 移动端：

1. 授权测试公众号；
2. 绑定品牌知识库；
3. 主 Agent 创建文章；
4. 一键生成配图；
5. 修改正文并保存版本；
6. 查看微信预览；
7. 确认同步草稿；
8. 模拟断线、刷新和恢复；
9. 模拟多人版本冲突；
10. 模拟远端草稿冲突。

### 19.5 真实验收

在受控测试公众号中执行一次真实授权和草稿同步。检查：

- 微信后台可见草稿；
- 标题、摘要、封面、正文和图片正确；
- 预览与微信后台不存在明显布局差异；
- 数据库和日志无明文秘密；
- 重复提交不产生第二份草稿；
- 不调用发布接口。

## 20. Boundaries

### Always

- 使用当前账号作为文章和知识作用域；
- 外部写入前冻结不可变版本；
- 微信调用使用幂等键；
- 产品事实保存引用；
- 外部响应和 HTML 在边界验证；
- 数据库迁移可回滚；
- 每个实现任务包含测试、lint 和构建验证；
- 用户界面明确说明当前操作会影响哪个公众号。

### Ask First

- 改变“一账号一个主品牌知识库”的约束；
- 开启自动发布；
- 新增第三方付费生图依赖或更换默认厂商；
- 改变已确认的关键交互目标；
- 修改现有公共 API 的字段含义；
- 在生产环境执行真实授权或真实草稿写入；
- 删除或覆盖现有微信草稿。

### Never

- 保存客户公众号 AppSecret 作为正式接入方式；
- 在浏览器暴露平台或授权账号秘密；
- 未经确认写入微信；
- 将第三方平台调用成功等同于业务最终成功；
- 把质量审核不可用显示为 0 分；
- 让主 Agent 在专家未执行时伪造正式文章；
- 从其他账号知识库补全当前账号的产品事实；
- 对远端微信草稿进行静默覆盖；
- 将原始模型 HTML 或外部网页 HTML 直接写入微信。

## 21. 官方能力依据

- 微信开放平台第三方平台概述：
  https://developers.weixin.qq.com/doc/oplatform/Third-party_Platforms/2.0/product/Third_party_platform_appid.html
- 获取预授权码：
  https://developers.weixin.qq.com/doc/oplatform/openApi/OpenApiDoc/ticket-token/getPreAuthCode.html
- 获取授权信息与刷新令牌：
  https://developers.weixin.qq.com/doc/oplatform/openApi/OpenApiDoc/ticket-token/getAuthorizerRefreshToken.html
- 获取授权账号调用令牌：
  https://developers.weixin.qq.com/doc/oplatform/openApi/OpenApiDoc/ticket-token/getAuthorizerAccessToken.html
- 获取授权账号详情：
  https://developers.weixin.qq.com/doc/oplatform/openApi/OpenApiDoc/authorization-management/getAuthorizerInfo.html
- 新增草稿：
  https://developers.weixin.qq.com/doc/service/api/draftbox/draftmanage/api_draft_add
- 上传发表内容中的图片：
  https://developers.weixin.qq.com/doc/service/api/material/permanent/api_uploadimage
- 上传永久素材：
  https://developers.weixin.qq.com/doc/service/api/material/permanent/api_addmaterial
- 发布草稿，首版不调用：
  https://developers.weixin.qq.com/doc/service/api/public/api_freepublish_submit
- 数据统计，首版只记录能力：
  https://developers.weixin.qq.com/doc/offiaccount/Analytics/Graphic_Analysis_Data_Interface.html

## 22. 外部前置条件

以下不是产品需求缺口，但在真实联调前必须具备：

- 已创建并审核通过的微信开放平台第三方平台；
- Component AppID 和安全保存的 Component AppSecret；
- 可由微信访问的 HTTPS 授权事件接收 URL；
- 消息校验 Token 和 EncodingAESKey；
- 已配置授权回调域名；
- 已申请草稿、素材等所需公众号权限集；
- 一个允许用于真实授权和草稿测试的认证企业公众号；
- 至少一个已审核品牌知识库；
- 一个已配置的图片生成提供商和测试额度。

## 23. 规格验收清单

- [ ] 目标、用户、范围和非目标无矛盾；
- [ ] 第三方平台代理授权是唯一正式接入主链路；
- [ ] 知识库、账号和文章作用域可验证；
- [ ] 版本触发规则和两类冲突已经明确；
- [ ] ArticleBrief 必填和可选字段已经明确；
- [ ] 图片主路径和提示词展示规则已经明确；
- [ ] 外部写入审批和幂等规则已经明确；
- [ ] API 输入、输出和错误语义足以生成实施计划；
- [ ] 测试覆盖授权、知识、文章、图片、冲突和同步；
- [ ] 首版没有自动发布和自动复盘。
