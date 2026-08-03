# 生产性能优化与上线设计

## 目标

在不改变桌面端业务行为和视觉结构的前提下，降低运营大脑首屏传输量，消除无关图表资源的首屏加载，并为哈希静态资源启用压缩和长期缓存。通过不可变发布目录部署到 `https://tzxai.top`，保留当前生产版本作为即时回滚目标。

## 已测基线

- 当前生产首页会预加载 `vendor-charts`，首屏 JS/CSS 原始传输量约 3.0 MB。
- 当前生产静态资源在客户端声明支持 `gzip`/`br` 时仍未返回 `Content-Encoding`。
- 当前生产哈希资源没有 `Cache-Control`，重复访问无法获得一年期不可变缓存。
- 当前分支的路由懒加载构建不再从首页预加载图表包；首屏资源原始大小为 1,599,735 bytes，gzip 估算为 490,088 bytes。
- 当前构建包含 103 个 Noto Sans SC 字体切片，共 4,583,636 bytes，并在主样式中生成 106 条 `@font-face`。
- 生产首页和就绪接口的五次客户端 TTFB 均约为 100–145 ms，服务端响应不是本轮首要瓶颈。
- Chrome DevTools 性能采样 MCP 在当前会话不可用，因此本轮不把 LCP、INP、CLS 写成已测结论。

## 方案

### 1. 保留已经验证的路由级懒加载

延续当前分支的页面级 `React.lazy` 和稳定 vendor chunk 配置。构建门禁必须证明首页 HTML 不预加载 `vendor-charts`，防止图表、复盘和管理页面重新进入运营大脑首屏依赖链。

### 2. 减少中文字体静态负担

删除全量 `@fontsource-variable/noto-sans-sc` CSS 导入，继续使用现有字体栈中的系统中文字体：macOS 使用 PingFang SC，Windows 使用 Microsoft YaHei UI，其他平台回退到 Segoe UI/sans-serif。保留 Geist Variable 用于拉丁字符和数字，避免改变现有数字与英文视觉风格。

这项调整不会删除字体栈中的 `Noto Sans SC Variable` 名称，以兼容用户系统已经安装该字体的情况；只停止随应用分发 103 个字体切片。

### 3. 优化 Nginx 静态资源交付

- 对 JavaScript、CSS、JSON、SVG 和文本资源启用 gzip，并设置 `Vary: Accept-Encoding`。
- 对 `/assets/` 下带内容哈希的构建产物设置 `Cache-Control: public, max-age=31536000, immutable`。
- 对 `index.html` 设置 `Cache-Control: no-cache`，确保发布切换后客户端能发现新的哈希资源。
- 保持现有 TLS、HSTS、API、平台集成和 WebSocket 反向代理行为不变。

标准 `nginx:alpine` 不内置 Brotli 模块，因此本轮使用 gzip，不引入自定义 Nginx 构建链。

### 4. 增加可重复的性能门禁

新增一个 Node 脚本读取生产构建的 `dist/index.html`，计算首页直接引用资源的原始与 gzip 大小，并检查：

- 首页不能引用或预加载 `vendor-charts`。
- 首页 gzip 资源总量不得超过 500 KiB。
- 构建产物中的 WOFF2 总数不得超过 5 个，防止全量中文字体切片回归。

该脚本作为 `pnpm perf:check` 运行，并加入 CI 前端作业的生产构建之后。

## 成功标准

- 首页构建依赖中不存在 `vendor-charts`。
- 首页 gzip 估算资源量不高于 500 KiB。
- 构建产物中的 WOFF2 文件不超过 5 个。
- 生产哈希资源返回 gzip 编码和一年期 immutable 缓存。
- 生产 `index.html` 返回 `no-cache`。
- 全量前端测试、Lint、TypeScript、生产构建和桌面端 E2E 通过。
- 后端受影响检查通过；本轮没有数据库迁移。
- 生产就绪接口返回数据库和 Redis 健康，首页 TLS 校验通过。

## 发布与回滚

从最终验证提交生成 Git archive 和 SHA-256，上传到 `/home/admin/releases/`，解压到新的不可变发布目录，复制当前活动版本的 `.env`，构建 Compose 镜像并执行 Alembic `upgrade head`。确认新容器健康后，原子切换 `/home/admin/dyflow`。

当前活动版本 `/home/admin/releases/dyflow-20260731-import-retry-01af9de` 保持不变，作为回滚目标。任何健康检查、关键桌面流程、静态资源头或控制台验收失败时，立即把活动链接切回该目录并重新启动 Compose。

## 明确不在本轮范围

- 移动端适配与移动端性能。
- Ant Design 深度按组件拆包或替换 UI 框架。
- 运营大脑后端 Router、模型调用和 telemetry 的大规模性能重构。
- 新增 CDN、Brotli 自定义模块或第三方可观测平台。
