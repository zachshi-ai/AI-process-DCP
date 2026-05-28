# 成品入口（仅保留 2 个版本）

本仓库已将“客户端（macOS App）”和“HTML 版本（浏览器 UI + 本地后端）”拆分到 `products/` 下，旧版 macOS / Windows 发布物已清理。

## 1) HTML 版本（浏览器 UI + 本地后端）

目录：`products/html-web/`

- 推荐启动（macOS）：双击 `products/html-web/start-macos.command`（或 `start.command`）
- 打开地址：启动成功后会自动打开 `http://127.0.0.1:<端口>/ui/`
- 前后端都在本目录内：`frontend/dist`（前端） + `backend/ai-dcp-backend-webui`（后端） + `ms-playwright`（浏览器内核）

## 2) macOS 客户端版本（Electron App）

目录：`products/macos-client/`

- 安装包（优先）：`AI-DCP-0.1.8-arm64.dmg`
- 压缩包（备用）：`AI-DCP-0.1.8-arm64-mac.zip`
- App 目录（内部产物）：`mac-arm64/AI-DCP.app`
