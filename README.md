# AI-process-DCP（AI-DCP）

本仓库用于维护 AI-DCP 的“可上传 Git 的干净源码工作区”（不包含运行数据、依赖目录、密钥文件）。

## 快速开始（V15 基线）

V15 对应 Git 标签：`v15`。运行默认使用：

- 后端：`http://127.0.0.1:8000/`
- 前端：`http://127.0.0.1:5175/`

版本说明见：[docs/versions/v15.md](docs/versions/v15.md)

## 一键启动（推荐）

前提：你已经完成过一次依赖安装（见下文“首次安装依赖”）。

双击运行：

- 启动：[tools/web/start.command](tools/web/start.command)
- 停止：[tools/web/stop.command](tools/web/stop.command)

日志目录：`./.run/`（该目录不会被上传到 Git）

## 首次安装依赖

### 1) 后端（Python）

```bash
cd backend
python3 -m venv venv
./venv/bin/pip install -r requirements.runtime.v1.txt
./venv/bin/python -m playwright install chromium
```

启动后端：

```bash
cd backend
./venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

### 2) 前端（Node.js）

```bash
cd frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5175
```

## 目录结构（你主要会用到）

- `backend/`：本地后端服务（FastAPI + Playwright 等）
- `frontend/`：前端 Web UI（Vite + React）
- `tools/web/`：一键启动/停止脚本（开发用）
- `docs/versions/`：每个版本的运行说明与变更记录（建议每个版本都新增一份）

## 版本维护建议（你后续照着做就行）

- 日常开发：正常 `git add/commit`
- 发布一个版本：打 tag（例如 `v15`），并新增版本说明文件 `docs/versions/v15.md`
- 同步快照（双保险）：在 `_versioning/source/AI-DCP-src-v15/` 另存一份源码快照（不包含 `node_modules/dist/venv/.run/data/logs/.env` 等）

详细流程见：[VERSIONING.md](VERSIONING.md)

## 常见问题

### 1) 看到 LibreSSL / urllib3 的警告

如果你在后端日志里看到类似：

`urllib3 v2 only supports OpenSSL 1.1.1+ ... LibreSSL 2.8.3`

多数情况下只是警告，不影响基本运行；若遇到 HTTPS 请求异常，建议使用 Homebrew 安装的 Python（OpenSSL 版本更合适）后重建 venv。
