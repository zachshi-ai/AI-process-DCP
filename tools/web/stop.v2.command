#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
RUN_DIR="$ROOT/.run"

echo "[AI-DCP] 停止服务..."

pid_matches_project() {
  local pid="$1"
  local cmd
  cmd="$(ps -ww -p "$pid" -o command= 2>/dev/null || true)"
  if [[ -z "${cmd:-}" ]]; then
    return 1
  fi
  [[ "$cmd" == *"$ROOT/backend"* || "$cmd" == *"$ROOT/frontend"* || "$cmd" == *"uvicorn main:app"* || "$cmd" == *"vite"* ]]
}

kill_pid_file() {
  local pid_file="$1"
  local name="$2"
  if [[ -f "$pid_file" ]]; then
    local pid
    pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [[ -n "${pid:-}" ]]; then
      if kill -0 "$pid" >/dev/null 2>&1; then
        if pid_matches_project "$pid"; then
          echo "[AI-DCP] 停止 $name (pid=$pid)"
          kill "$pid" >/dev/null 2>&1 || true
        else
          echo "[AI-DCP] 跳过：$name(pid=$pid) 看起来不是本项目进程，避免误杀"
        fi
      fi
    fi
    rm -f "$pid_file" >/dev/null 2>&1 || true
  fi
}

kill_pid_file "$RUN_DIR/frontend.pid" "前端"
kill_pid_file "$RUN_DIR/backend.pid" "后端"

rm -f "$RUN_DIR/frontend.port" "$RUN_DIR/backend.port" "$RUN_DIR/api_base.txt" >/dev/null 2>&1 || true

echo "[AI-DCP] 完成。"
exit 0
