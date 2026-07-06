---
name: 同舟行 AI 新媒体运营平台
description: 冷白磨砂纸质感 + 黑白灰极简的 AI Agent 编排工作台，核心体验是主 Agent 调度专家 Agent 的运营大脑
colors:
  canvas: "#f6f7f8"
  canvas-subtle: "#fbfbfc"
  paper: "#ffffff"
  paper-muted: "#f1f2f4"
  paper-strong: "#e8eaed"
  ink: "#111315"
  ink-soft: "#30343a"
  ink-muted: "#68707a"
  ink-faint: "#8d949d"
  line: "#dfe2e6"
  line-subtle: "#eceef1"
  line-strong: "#c9ced6"
  graphite: "#1c1f23"
  graphite-hover: "#2a2e33"
  success: "#1f8f4d"
  warning: "#a66a00"
  error: "#c43d4b"
  info: "#59616c"
typography:
  body:
    fontFamily: '"OpenAI Sans", "Söhne", "Helvetica Neue", -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", Arial, sans-serif'
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.58
  ui:
    fontFamily: '"OpenAI Sans", "Söhne", "Helvetica Neue", -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", Arial, sans-serif'
    fontSize: "13px"
    fontWeight: 500
    lineHeight: 1.35
  title:
    fontFamily: '"OpenAI Sans", "Söhne", "Helvetica Neue", -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", Arial, sans-serif'
    fontSize: "22px"
    fontWeight: 620
    lineHeight: 1.25
  display:
    fontFamily: '"OpenAI Sans", "Söhne", "Helvetica Neue", -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", Arial, sans-serif'
    fontSize: "34px"
    fontWeight: 650
    lineHeight: 1.12
rounded:
  xs: "8px"
  sm: "10px"
  md: "14px"
  lg: "18px"
  xl: "24px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "24px"
  xxl: "32px"
---

# Design System: 同舟行

## 1. Creative Direction

**North Star：冷白磨砂纸上的专业运营大脑。**

同舟行的界面默认走浅色，不再以深色金属银作为主方向。它应该像一张经过精密排版的冷白纸面：安静、清楚、细腻、有秩序。高级感来自比例、留白、字重、细线和状态节奏，而不是大面积颜色或装饰效果。

这套系统服务的是日常高频运营工作。用户需要长期盯着账号、任务、Agent 结果、平台授权和风险状态，所以页面必须清爽、可扫读、可连续使用。第一眼应该像一个成熟的 AI 运营产品，而不是技术 demo。

## 2. Visual Language

### Palette

主色只使用黑白灰：

- **Canvas** `#f6f7f8`：全局背景，冷白、轻微灰度。
- **Paper** `#ffffff`：主要内容表面。
- **Paper Muted** `#f1f2f4`：输入区、轻分组、次级表面。
- **Ink** `#111315`：主文本、关键标题、主按钮。
- **Ink Muted** `#68707a`：说明文字、元信息。
- **Line** `#dfe2e6`：默认边线。
- **Line Subtle** `#eceef1`：轻分隔。

语义色只用于状态：

- **Success**：授权完成、任务完成、验收通过。
- **Warning**：待确认、待审核、权限不足。
- **Error**：失败、阻塞、打回。
- **Info**：系统提示、同步中、普通信息。

禁止把蓝色、紫色、绿色、渐变色作为大面积视觉主题。

### Frosted Paper Background

允许使用非常轻的冷白磨砂纸质感：

- 只能出现在页面底层或大工作区背景。
- 纹理必须非常细，不影响文字阅读。
- 不做米黄、复古纸、羊皮纸、明显噪点。
- 不把所有面板做成毛玻璃。背景有质感，内容容器要保持清晰。

### Shape

- 卡片/面板圆角控制在 8-12px。
- 按钮圆角 6-8px。
- 输入框圆角 10-12px，可以略大但不能过圆。
- 不使用 24px+ 的圆角卡片。

### Elevation

默认不用大阴影。层级靠：

1. 背景明度差。
2. 1px 边线。
3. 轻微内阴影或极弱阴影，仅用于浮层。
4. 焦点态的清晰描边。

禁止“1px 边框 + 大软阴影”的装饰卡片套路。

## 3. Layout System

### App Shell

采用 **升级版左侧固定导航 + 顶部轻工具栏 + 大工作区**。

- 左侧导航：图标 + 文本，不折叠。更窄、更轻、更精致。
- 顶部工具栏：只放全局状态、搜索、主题切换、用户菜单等轻工具。
- 主工作区：优先让运营大脑成为焦点，避免被传统 dashboard 卡片淹没。

### Navigation

第一阶段导航要突出当前可用和最重要的模块：

1. 运营大脑
2. 账号矩阵
3. 内容流水线 / 任务验收
4. 运营复盘
5. 专家团队
6. 风险 / 成本 / 设置等辅助模块

投流先不作为近期主交付目标。可以保留后续能力，但不应在第一阶段抢占主导航心智。

### Page Density

页面不是越空越高级。规则是：

- 首屏要干净，让用户知道当前能做什么。
- 二级区域要有足够信息密度，适合运营长期使用。
- 表格和状态列表要更精致，不要粗糙堆字段。

## 4. Core Experience: Agent Orchestration

运营大脑首页采用 **顶部大输入框 + 下方专家编排任务流**。

### User Flow

1. 用户输入运营目标。
2. 主 Agent 生成一句回应，并说明准备调用哪个专家。
3. 专家 Agent 以专业身份卡片浮现。
4. 卡片进入“分析中 / 处理中 / 已完成 / 需确认”状态。
5. 完成后默认显示一句核心结论。
6. 用户可展开查看分析详情、依据和建议。
7. 主 Agent 继续交接给下一个专家。

### Agent Cards

专家不使用真人头像或卡通形象。使用克制的专业身份卡片：

- 专家名称：账号定位专家、内容策略专家、选题专家等。
- 当前状态：待启动、分析中、已完成、需确认、阻塞。
- 一句核心结论。
- 可展开详情。
- 轻微浮现动效，后续流转高效。

### Motion

动效要“先有仪式感，再高效流转”：

- 第一个专家出现可以缓慢、精致。
- 后续专家切换要快速，避免演戏感。
- 动效使用 opacity、transform、blur 的轻微组合，不动画布局尺寸。
- 必须支持 `prefers-reduced-motion: reduce`。

## 5. Login Page

登录页走 **官网首屏型**，但不是营销页。

目标：

- 第一眼建立“同舟行 AI 新媒体运营平台”的产品感。
- 展示运营大脑、专家编排、账号矩阵、多平台接入等核心能力。
- 表单保持干净、可信、易用。

视觉：

- 冷白磨砂纸背景。
- 黑白灰极简。
- 不用夸张大标题，不写官网电话、联系方式、最高级宣传。
- 可以用产品界面片段或专家编排流作为背景式预览，但不能让登录表单失焦。

## 6. Components

### Buttons

- 主按钮：石墨黑背景、白字。
- 次按钮：白底、细边框、黑字。
- 图标按钮：优先使用 lucide 图标；有明确 tooltip。
- 禁止默认 Ant Design 蓝。

### Inputs

- 大输入框是运营大脑的核心交互，应该有足够空间。
- 默认细边框，聚焦时石墨描边。
- Placeholder 必须可读，不用过浅灰。

### Panels

- 面板是信息容器，不是装饰卡片。
- 不做卡片套卡片。
- 关键面板可用更强边线或背景明度差，而不是彩色条。

### Tables

- 表格要紧凑、清楚、可扫读。
- 表头不厚重，行高稳定。
- 状态列必须用文字 + 图标/形态，不只靠颜色。

### Status

状态命名必须贴近业务：

- 已配置
- 已授权
- 待同步
- 权限不足
- 等待平台审核
- 需重新授权
- 已完成
- 已阻塞

## 7. Do / Don't

### Do

- 用冷白、雾白、石墨黑和细灰线建立高级感。
- 用主 Agent 的语言把系统动作说清楚。
- 用专家身份卡片表现 Agent，而不做人脸头像。
- 默认展示核心结论，详情可展开。
- 全局页面统一导航、标题、间距、按钮、状态语言。

### Don't

- 不做大面积彩色科技风。
- 不做深色作战室作为默认主题。
- 不做真人数字员工头像。
- 不做渐变文字、装饰光斑、卡片套卡片。
- 不让登录后首页变成传统数据 dashboard。
- 不把高保真演示流程写死在页面组件里，必须预留真实 Agent 接口。
