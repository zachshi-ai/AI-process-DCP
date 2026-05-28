#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BACKEND_DIR="$ROOT/backend"
FRONTEND_DIR="$ROOT/frontend"
RUN_DIR="$ROOT/.run"

mkdir -p "$RUN_DIR"

echo "[AI-DCP] 项目目录: $ROOT"

wait_port() {
  local port="$1"
  local name="$2"
  local tries=30
  local i=1
  while [[ $i -le $tries ]]; do
    if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
      echo "[AI-DCP] $name 已就绪 (port=$port)"
      return 0
    fi
    sleep 0.2
    i=$((i+1))
  done
  echo "[AI-DCP] $name 启动超时 (port=$port)。你可以查看日志：$RUN_DIR"
  return 1
}

echo "[AI-DCP] 检查后端(8000)..."
if lsof -nP -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "[AI-DCP] 后端已在 8000 运行"
else
  echo "[AI-DCP] 启动后端: http://127.0.0.1:8000"
  (
    cd "$BACKEND_DIR"
    ./venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000 >"$RUN_DIR/backend-8000.log" 2>&1 &
    echo $! >"$RUN_DIR/backend.pid"
  )
fi
wait_port 8000 "后端"

echo "[AI-DCP] 检查前端(5175)..."
if lsof -nP -iTCP:5175 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "[AI-DCP] 前端已在 5175 运行"
else
  echo "[AI-DCP] 启动前端: http://127.0.0.1:5175"
  (
    cd "$FRONTEND_DIR"
    npm run dev -- --host 127.0.0.1 --port 5175 >"$RUN_DIR/frontend-5175.log" 2>&1 &
    echo $! >"$RUN_DIR/frontend.pid"
  )
fi
wait_port 5175 "前端"

echo ""
echo "[AI-DCP] 打开 Web 页面: http://127.0.0.1:5175/"
echo "[AI-DCP] 后端健康检查: http://127.0.0.1:8000/"
echo ""
echo "[AI-DCP] 提示：你可以双击 tools/web/stop.command 一键停止。"
exit 0
