#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BACKEND_DIR="$ROOT/backend"
FRONTEND_DIR="$ROOT/frontend"
RUN_DIR="$ROOT/.run"

mkdir -p "$RUN_DIR"

echo "[AI-DCP] 项目目录: $ROOT"

is_port_listening() {
  local port="$1"
  lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
}

find_free_port() {
  local start_port="$1"
  local end_port="$2"
  local p
  p="$start_port"
  while [[ "$p" -le "$end_port" ]]; do
    if ! is_port_listening "$p"; then
      echo "$p"
      return 0
    fi
    p=$((p+1))
  done
  return 1
}

wait_port() {
  local port="$1"
  local name="$2"
  local tries=50
  local i=1
  while [[ $i -le $tries ]]; do
    if is_port_listening "$port"; then
      echo "[AI-DCP] $name 已就绪 (port=$port)"
      return 0
    fi
    sleep 0.2
    i=$((i+1))
  done
  echo "[AI-DCP] $name 启动超时 (port=$port)。你可以查看日志：$RUN_DIR"
  return 1
}

BACKEND_PORT="$(find_free_port 8000 8010 || true)"
if [[ -z "${BACKEND_PORT:-}" ]]; then
  echo "[AI-DCP] 错误：8000-8010 都被占用，无法启动后端。"
  exit 1
fi

FRONTEND_PORT="$(find_free_port 5175 5190 || true)"
if [[ -z "${FRONTEND_PORT:-}" ]]; then
  echo "[AI-DCP] 错误：5175-5190 都被占用，无法启动前端。"
  exit 1
fi

BACKEND_BASE="http://127.0.0.1:${BACKEND_PORT}"
FRONTEND_URL="http://127.0.0.1:${FRONTEND_PORT}/"

echo "[AI-DCP] 计划启动："
echo "  - 后端: $BACKEND_BASE"
echo "  - 前端: $FRONTEND_URL"

echo "[AI-DCP] 启动后端..."
(
  cd "$BACKEND_DIR"
  ./venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port "$BACKEND_PORT" >"$RUN_DIR/backend-${BACKEND_PORT}.log" 2>&1 &
  echo $! >"$RUN_DIR/backend.pid"
  echo "$BACKEND_PORT" >"$RUN_DIR/backend.port"
)
wait_port "$BACKEND_PORT" "后端"

echo "[AI-DCP] 启动前端..."
(
  cd "$FRONTEND_DIR"
  VITE_API_BASE="$BACKEND_BASE" npm run dev -- --host 127.0.0.1 --port "$FRONTEND_PORT" --strictPort >"$RUN_DIR/frontend-${FRONTEND_PORT}.log" 2>&1 &
  echo $! >"$RUN_DIR/frontend.pid"
  echo "$FRONTEND_PORT" >"$RUN_DIR/frontend.port"
  echo "$BACKEND_BASE" >"$RUN_DIR/api_base.txt"
)
wait_port "$FRONTEND_PORT" "前端"

echo ""
echo "[AI-DCP] 打开 Web 页面: $FRONTEND_URL"
echo "[AI-DCP] 后端健康检查: $BACKEND_BASE/docs"
echo "[AI-DCP] API_BASE(给前端填的后端地址): $BACKEND_BASE"
echo ""
echo "[AI-DCP] 提示：你可以双击 tools/web/stop.v2.command 一键停止（不会误杀别的项目进程）。"
exit 0
