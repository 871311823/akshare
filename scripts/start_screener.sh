#!/usr/bin/env bash
# 一键启动脚本：AkShare 周线 MACD 选股器（Flask）
#
# 功能：
# 1) 关闭占用端口的进程（默认 5001，可用 PORT 环境变量覆盖）
# 2) 清理 Python 缓存（__pycache__/pyc）与旧的 pid/log
# 3) 自动创建/复用 venv、安装依赖，并后台启动应用
# 4) 等待端口真正监听后再返回（避免“已启动但脚本误判失败”）
#
# 用法（在仓库根目录执行）：
#   bash scripts/start_screener.sh
#   PORT=5001 HOST=0.0.0.0 bash scripts/start_screener.sh
#
# 注意：该脚本面向 Linux/macOS。Windows 可在 WSL/Git Bash 中运行。

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_SCRIPT="$REPO_ROOT/examples/stock_screener_web.py"
VENV_DIR="$REPO_ROOT/.venv"

PORT="${PORT:-5001}"
HOST="${HOST:-0.0.0.0}"

LOG_FILE="$REPO_ROOT/.screener_${PORT}.log"
PID_FILE="$REPO_ROOT/.screener_${PORT}.pid"

PYTHON_BIN="${PYTHON_BIN:-python3}"

say() { printf '%s\n' "$*"; }

require_file() {
  if [ ! -f "$1" ]; then
    say "[ERROR] 未找到文件: $1"
    exit 1
  fi
}

port_pids() {
  # 优先使用 ss；若不存在则尝试 lsof。
  if command -v ss >/dev/null 2>&1; then
    ss -ltnp 2>/dev/null \
      | awk -v port=":${PORT}" '$4 ~ port"$" {print $NF}' \
      | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' \
      | sort -u
    return 0
  fi

  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null | sort -u
    return 0
  fi

  return 0
}

is_listening() {
  # 通过 Python 尝试连通本地端口，比解析 ss 更稳。
  "$VENV_DIR/bin/python" - <<PY
import socket
import sys
port = int(sys.argv[1])
try:
    s = socket.create_connection(("127.0.0.1", port), timeout=0.3)
    s.close()
    sys.exit(0)
except Exception:
    sys.exit(1)
PY
  "$PORT"
}

say "[INFO] 端口: ${PORT}"

require_file "$APP_SCRIPT"

# 1) 停止占用端口的进程
pids="$(port_pids || true)"
if [ -n "$pids" ]; then
  say "[INFO] 发现端口 ${PORT} 被占用，准备停止进程: ${pids}"
  for pid in $pids; do
    kill "$pid" 2>/dev/null || true
  done
  sleep 0.8

  pids2="$(port_pids || true)"
  if [ -n "$pids2" ]; then
    say "[WARN] 端口仍被占用，强制停止: ${pids2}"
    for pid in $pids2; do
      kill -9 "$pid" 2>/dev/null || true
    done
  fi
fi

# 如果存在旧 PID 文件，也尝试停止
if [ -f "$PID_FILE" ]; then
  oldpid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "$oldpid" ]; then
    kill "$oldpid" 2>/dev/null || true
  fi
  rm -f "$PID_FILE" || true
fi

# 2) 清理缓存/旧日志
say "[INFO] 清理缓存与旧日志..."
find "$REPO_ROOT" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
find "$REPO_ROOT" -type f -name '*.pyc' -delete 2>/dev/null || true
rm -f "$LOG_FILE" 2>/dev/null || true

# 3) venv + 依赖
say "[INFO] 准备 venv 与依赖..."
if [ ! -x "$VENV_DIR/bin/python" ]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install -U pip >/dev/null

# akshare（editable）+ 依赖
"$VENV_DIR/bin/python" -m pip install -e "$REPO_ROOT" >/dev/null

# Web 示例依赖（不是 akshare 核心依赖）
"$VENV_DIR/bin/python" -m pip install -U flask >/dev/null

# 4) 启动（后台）
say "[INFO] 启动服务..."
nohup env HOST="$HOST" PORT="$PORT" "$VENV_DIR/bin/python" -u "$APP_SCRIPT" > "$LOG_FILE" 2>&1 &
newpid=$!
echo "$newpid" > "$PID_FILE"

# 等待端口真正可用（避免之前出现的误判）
say "[INFO] 等待服务就绪..."
ready=0
for _ in $(seq 1 30); do
  if is_listening; then
    ready=1
    break
  fi
  sleep 0.3
done

if [ "$ready" != "1" ]; then
  say "[ERROR] 启动后端口仍不可用，日志如下（$LOG_FILE）："
  tail -n 200 "$LOG_FILE" || true
  exit 1
fi

say "[OK] 启动成功: pid=${newpid}"
if [ "$HOST" = "0.0.0.0" ] || [ "$HOST" = "::" ]; then
  say "[INFO] 访问地址: http://<服务器IP>:${PORT}"
else
  say "[INFO] 访问地址: http://${HOST}:${PORT}"
fi
say "[INFO] PID 文件: $PID_FILE"
say "[INFO] 日志文件: $LOG_FILE"
