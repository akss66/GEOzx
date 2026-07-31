# 批量账号数据导入验证记录

日期：2026-07-31  
功能分支：`codex/bulk-account-data-ingestion`

## 验证范围

- 多文件、多工作表识别与独立失败隔离
- 同一业务日期、同一字段的确定性覆盖
- 空值保留旧值、显式零覆盖旧值
- 来源优先级、撤销与永久删除后的回退重建
- 导入任务幂等、失败文件单独重新上传
- 服务重启后的等待任务自动恢复
- 数据覆盖状态、导入记录恢复与账号隔离
- 桌面端和 390px 窄屏页面

## 自动化验证

### 后端

- 完整测试：`python -m pytest -q`
  - 基线结果：1056 passed
  - 最终代码结果：1057 passed
- 导入任务 API 与 Worker：11 passed
- Worker 丢队列恢复：5 passed
- Ruff：修改过的 Worker、任务服务、API、模型、解析器、投影和测试文件通过

### 前端

- 完整 Vitest：72 files / 347 tests passed
- ESLint：通过
- TypeScript 与 Vite 生产构建：通过
- 构建仅保留既有的大 chunk 提示，不影响发布

## PostgreSQL 生产等价验证

使用独立 PostgreSQL 数据库
`dyflow_bulk_ingestion_verify_20260731` 从空库执行完整 Alembic
升级至 `20260731_0200`，成功创建：

- `data_import_jobs`
- `data_import_files`
- `data_field_observations`
- `account_data_backfill_checkpoints`

服务级冒烟结果：

```text
POSTGRES_SMOKE_OK files=5 completed=4 failed=1 overlap=0 fallback=81
```

覆盖场景：

- 四个合法平台导出与一个损坏文件同批上传
- 合法文件全部写入，损坏文件独立失败
- 重叠日期显式零覆盖旧非零值
- 缺失字段不覆盖既有值
- 撤销来源后从仍有效的旧观察值重建

本地历史数据库曾缺少早期迁移定义中的两个会话唯一约束。确认重复数均为
0 后，只在本地开发库补回精确约束，再成功升级至
`20260731_0200`。空库迁移验证未依赖这项修复。

## 浏览器验证

在本地 Docker 生产构建中完成：

- 同时选择日播放、作品列表和损坏 XLSX
- 两个合法文件显示“已写入”，损坏文件显示“导入失败”
- 失败文件显示“重新上传此文件”，重新上传合法文件后成功写入
- 刷新页面后最近导入任务和每个文件状态仍保留
- 重启 Worker 后，数据库中遗留的 queued 任务被启动巡检重新投递并完成
- 第二账号不显示第一账号的任何文件或导入任务
- 390 × 844 视口下页面无横向溢出
- 浏览器控制台无新增错误，请求无失败

浏览器测试产生的临时组织、账号、文件和独立验证数据库均已删除。

## 生产上线前检查

- 当前生产发布：
  `/home/admin/releases/dyflow-20260731-account-data-center-db62896`
- 当前生产 Alembic：`20260731_0100`
- 磁盘：40G，总使用 56%，可用 17G
- backend、frontend、worker、PostgreSQL、Redis、MinIO 均运行
- 两个历史会话唯一约束均存在
- 对应会话表重复记录均为 0
- 四张新表在升级前均不存在，符合迁移预期

## 最终发布验证

- 发布提交：`f2d3ad2`
- 发布目录：
  `/home/admin/releases/dyflow-20260731-bulk-data-f2d3ad2`
- 归档：
  `dyflow-20260731-bulk-data-f2d3ad2.tar.gz`
- 归档 SHA-256：
  `5c127a7165ffa0e851e50aae2e21e6b80b626dc159eab8e3ce33004aefb58d02`
- Alembic：`20260731_0100 -> 20260731_0200 (head)`
- 新表：4/4 存在，发布时 `data_import_jobs` 为 0
- backend、frontend、PostgreSQL、Redis 均为 healthy，worker 正常运行
- Worker 已注册 `execute_account_data_import_job` 和
  `recover_account_data_import_jobs`
- Worker 启动巡检成功，遗留任务数为 0
- `https://tzxai.top/api/health/ready`：
  `{"status":"ready","checks":{"db":true,"redis":true}}`
- 站点根路径：HTTP 200，TLS 校验结果 0
- 未认证访问新导入任务 API：HTTP 401，证明路由已上线且鉴权生效
- 回滚目标：
  `/home/admin/releases/dyflow-20260731-account-data-center-db62896`

数据库迁移仅增加表、列、索引和约束；旧应用可忽略新增结构。若应用层需要
回滚，可原子切回上述旧目录并重新启动 Compose。
