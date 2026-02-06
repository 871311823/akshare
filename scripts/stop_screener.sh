#!/usr/bin/env bash
# 停止脚本：AkShare 周线 MACD 选股器（Flask）
# 用法：
#   bash scripts/stop_screener.sh
#   PORT=5001 bash scripts/stop_screener.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${PORT:-5001}"
PID_FILE="$REPO_ROOT/.screener_${PORT}.pid"

say() { printf '%s\n' "$*"; }

if [ -f "$PID_FILE" ]; then
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "$pid" ]; then
    kill "$pid" 2>/dev/null || true
    sleep 0.3
    kill -9 "$pid" 2>/dev/null || true
    say "[OK] 已停止: pid=${pid}"
  fi
  rm -f "$PID_FILE" || true
else
  say "[INFO] 未找到 PID 文件: $PID_FILE"
fi

# 如果 PID 文件不存在，也尝试按端口查找
if command -v ss >/dev/null 2>&1; then
  pids="$(ss -ltnp 2>/dev/null | awk -v port=":${PORT}" '$4 ~ port"$" {print $NF}' | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' | sort -u)"
  if [ -n "$pids" ]; then
    say "[INFO] 发现端口 ${PORT} 的监听进程: $pids"
    for pid in $pids; do
      kill "$pid" 2>/dev/null || true
      kill -9 "$pid" 2>/dev/null || true
    done
    say "[OK] 已停止端口 ${PORT} 相关进程"
  fi
fi
