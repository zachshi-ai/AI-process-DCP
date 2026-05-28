#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
RUN_DIR="$ROOT/.run"

echo "[AI-DCP] 停止服务..."

kill_pid_file() {
  local pid_file="$1"
  local name="$2"
  if [[ -f "$pid_file" ]]; then
    local pid
    pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [[ -n "${pid:-}" ]]; then
      if kill -0 "$pid" >/dev/null 2>&1; then
        echo "[AI-DCP] 停止 $name (pid=$pid)"
        kill "$pid" >/dev/null 2>&1 || true
      fi
    fi
    rm -f "$pid_file" >/dev/null 2>&1 || true
  fi
}

kill_pid_file "$RUN_DIR/frontend.pid" "前端(5175)"
kill_pid_file "$RUN_DIR/backend.pid" "后端(8000)"

echo "[AI-DCP] 额外兜底：按端口清理残留进程..."
for p in 5175 8000; do
  pid="$(lsof -nP -iTCP:$p -sTCP:LISTEN -t 2>/dev/null | head -n 1 || true)"
  if [[ -n "${pid:-}" ]]; then
    echo "[AI-DCP] kill -9 $pid (port=$p)"
    kill -9 "$pid" >/dev/null 2>&1 || true
  fi
done

echo "[AI-DCP] 完成。"
exit 0
