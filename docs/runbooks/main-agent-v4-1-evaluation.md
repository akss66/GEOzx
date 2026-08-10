# Main Agent V4.1 质量评测运行手册

本阶段只增加开发与 CI 质量门，不改变生产运行时，也不部署评测服务。评测对象是“基于当前账号已确认导入数据，回答现状并给出有证据、可验证建议”的主 Agent 链路。

## 质量门组成

- `account_analysis_v1.json` 固定 30 条、6 类业务用例，所有用例均带语义版本号。
- 确定性门检查账号隔离、路由、工具、专家与重试预算、证据值和单位、回答边界、建议完整性及终态一致性。
- 30 条契约矩阵经由公开对话 API、真实 Worker 状态机、数据库行和采集器；为保证 CI 离线可重复，分类器与 Skill 输出使用独立于期望断言的确定性场景夹具。它只证明传输、持久化、采集和业务门组合，不证明模型本身。
- 当前 18 条可确定性识别的请求会直接调用生产 `capability_router` 验证；其余 12 条模型路由请求必须使用受控环境捕获的真实观察文件回放，离线 CI 不伪装成模型验收。
- DeepEval 是可选的离线 judge；CI 不安装它、不读取模型密钥、不访问模型网络。

## CI 等价确定性验证

```powershell
cd backend
uv sync --frozen --extra dev
uv run python -m pytest -q `
  tests/test_main_agent_eval_models.py `
  tests/test_main_agent_eval_cases.py `
  tests/test_main_agent_eval_checks.py `
  tests/test_main_agent_eval_collector.py `
  tests/test_main_agent_eval_runner.py `
  tests/test_main_agent_eval_integration.py `
  tests/test_main_agent_deepeval_adapter.py
```

Windows 环境使用 `python -m pytest`，避免系统应用控制策略拦截 `pytest.exe` 启动器。报告回放命令：

```powershell
uv run python scripts/run_main_agent_evals.py `
  --mode deterministic `
  --observations .eval-inputs/redacted-observations.json
```

报告默认写入 `backend/.eval-results/`，该目录已忽略。命令只打印通过数、失败数和报告路径，不打印账号原始数据。

## 可选语义评测

```powershell
cd backend
uv sync --frozen --extra dev --extra eval
$env:DEEPEVAL_DISABLE_DOTENV='1'
$env:MAIN_AGENT_EVAL_JUDGE_MODEL='gpt-4.1'
$env:MAIN_AGENT_EVAL_JUDGE_API_KEY='<专用离线评测密钥>'
# 自建 OpenAI 兼容 judge 时才设置：
# $env:MAIN_AGENT_EVAL_JUDGE_BASE_URL='https://judge.example.com/v1'
uv run python scripts/run_main_agent_evals.py `
  --mode live-model `
  --allow-model-calls `
  --max-cost-cny 2 `
  --usd-cny-rate 7.0 `
  --observations .eval-inputs/redacted-live-observations.json
```

`--allow-model-calls`、正数 `--max-cost-cny` 和本次运行明确采用的 `--usd-cny-rate` 缺一不可。适配器读取每个 DeepEval 指标的实际 `evaluation_cost`，换算后写入 `semantic_cost_cny`；它会用上一笔实测成本保守预留下一次调用预算，达到或预计超过上限时停止后续 judge 调用并让评测失败。第一笔调用或供应商价格突变仍可能造成单次超限，因此供应商侧硬额度和告警仍是必需的最终防线。禁止复用生产推理密钥。

## 输入、输出与脱敏

观察文件必须是 `EvaluationObservation` JSON 数组，并与 case ID 完全一一对应。只允许包含标准化证据、路由、工具、专家、终态、时延及成本元数据；禁止原始 Prompt、Authorization、API key、provider body、错误详情、密码和密钥。报告写入前还会递归移除这些敏感键。

输出目录默认必须位于仓库内。确需写到仓库外时显式使用 `--allow-external-output`，并自行确保目录访问权限和生命周期。

## 用例版本与回归流程

1. 生产问题出现后，先新增能稳定复现的最小用例和失败断言，再修复生产问题。
2. 行为契约未改变时增加 patch 版本；兼容扩展用 minor；删除或重定义契约用 major。
3. 同一批次只运行一个 suite 版本，case ID 与 version 组合必须唯一。
4. 修复前保存红色报告，修复后用同一用例和同一观察输入重跑。
5. 比较 `failure_reasons`、P0 check、语义分数、总时延和模型成本；不能只比较总通过率。

## 阈值治理

- P0 任一失败即阻断；P1 初期只记录；延迟门初期为 info。
- 单个语义指标默认阈值 `0.8`，整批语义平均分不得低于 `0.85`。
- 调阈值必须有历史报告、人工复核样本和变更说明，不得为了让 CI 变绿而降阈值。
- judge 模型、case 版本、代码 commit 必须同时记录，避免跨模型分数直接比较。

## 为什么暂不引入其他运行时依赖

Langfuse 适合生产可观测性，AG-UI 适合前端事件协议，StaffDeck 适合多 Agent 组织形态；它们都不能替代本阶段的可重复业务评测。为避免扩大生产依赖、权限面和故障面，V4.1 只使用已有运行时数据库行与本地/CI 评测工具。后续若引入，必须以独立 ADR、威胁模型、成本评估和回滚方案推进。

## 故障处理

- CLI 返回 `0`：全部门通过；`1`：评测完成但存在阻断项；`2`：调用、配置或输入无效。
- `runner.exception` 表示执行器失败，报告不会保存供应商异常正文。
- `semantic.exception` 表示 judge 不可用或失败，不能静默降级为通过。
- 外部输出被拒绝、模型授权缺失或 case/observation 不一致时，修正配置后重跑，不要绕过安全检查。
