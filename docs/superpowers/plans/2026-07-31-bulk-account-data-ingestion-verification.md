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
  - 最终代码结果：见“最终发布验证”
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
- `data_import_backfill_checkpoints`

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

部署完成后补充：

- 精确发布目录与 Git 提交
- 归档 SHA-256
- Alembic 升级结果
- 容器健康状态
- Worker 注册与恢复巡检日志
- HTTPS 健康检查
- 生产数据库结构与账号隔离冒烟
- 回滚目标
