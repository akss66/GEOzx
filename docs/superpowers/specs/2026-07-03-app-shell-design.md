# App Shell Design

## Goal

Build the first accepted system-inside frontend step: a unified AI-native app shell and base component language for 同舟行AI新媒体运营平台.

## Scope

This step covers:

- Left navigation information architecture.
- Top bar with current platform/account context.
- Account switcher panel.
- Base page header, panel, button, input, tag, and status styling direction.
- Admin-only entries for user management and Agent configuration.

This step does not redesign individual business pages beyond making them fit the shared shell.

## Navigation

Primary navigation:

1. 运营大脑
2. 专家团
3. 账号矩阵
4. 内容生产
5. 人工审批
6. 运营复盘
7. 使用成本
8. 知识库

Admin-only navigation:

1. 用户管理
2. Agent 配置

Deferred routes such as 投流 and 客服 remain available in routing only if already wired, but they are not shown in the primary navigation.

## Account Context

The top bar shows the current work context:

- Platform: currently only 抖音 is enabled.
- Account: selected account nickname, or a clear empty state.
- Status: authorized / unauthorized / expired / manual.

Clicking the context opens a switcher panel:

- 抖音 account list.
- Disabled future platform slots for 小红书 and 视频号.
- Entry to 账号矩阵 for adding or reauthorizing accounts.

运营大脑, 内容生产, 人工审批, and 运营复盘 inherit this context. If no account is selected, the shell makes that visible before the user starts work.

## Visual Direction

The system should feel like AI + Agent + 运营, not a traditional operations admin tool.

- Font stack stays aligned with ChatGPT-style UI: OpenAI Sans, Söhne, Helvetica Neue, Apple/system fonts, PingFang SC.
- Use restrained black/white/gray with the red logo as brand identity only.
- Avoid Ant Design blue defaults.
- Keep iOS-like rounded controls without over-rounding.
- Prefer quiet surfaces, fine lines, and clear state language.

## Acceptance Criteria

- Left navigation matches the agreed IA and hides deferred modules.
- Admin entries are shown only for admin users.
- Top bar shows current 抖音 account context.
- Account switcher can select and persist an account locally.
- If no account is selected, the top bar shows a clear “选择抖音账号” state.
- Base components share one visual language across pages.
- Build passes.
