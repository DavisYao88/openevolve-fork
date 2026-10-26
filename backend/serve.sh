#!/usr/bin/env bash
# OpenEvolve 后端服务（gunicorn，单 worker + 多线程，daemon 脱离终端）
#
#   ./backend/serve.sh start      # 启动（默认 127.0.0.1:8080）
#   ./backend/serve.sh stop       # 停止
#   ./backend/serve.sh restart    # 重启
#   ./backend/serve.sh status     # 查看状态
#   ./backend/serve.sh logs       # 跟踪日志
#
# 环境变量：OPENEVOLVE_HOST / OPENEVOLVE_PORT / OPENEVOLVE_THREADS / OPENEVOLVE_LOG
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${OPENEVOLVE_HOST:-127.0.0.1}"
PORT="${OPENEVOLVE_PORT:-8080}"
THREADS="${OPENEVOLVE_THREADS:-8}"
PID_FILE="${OPENEVOLVE_PID:-/tmp/openevolve-backend.pid}"
LOG_FILE="${OPENEVOLVE_LOG:-/tmp/openevolve-backend.log}"
PYTHON="${OPENEVOLVE_PYTHON:-python3}"

is_running() {
  [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

start() {
  if is_running; then
    echo "已在运行：pid $(cat "$PID_FILE")  http://$HOST:$PORT"
    return 0
  fi
  rm -f "$PID_FILE"
  "$PYTHON" -m gunicorn \
    --chdir "$ROOT" \
    --bind "$HOST:$PORT" \
    --workers 1 \
    --worker-class gthread \
    --threads "$THREADS" \
    --timeout 0 \
    --graceful-timeout 10 \
    --daemon \
    --pid "$PID_FILE" \
    --access-logfile "$LOG_FILE" \
    --error-logfile "$LOG_FILE" \
    backend.server:app
  for _ in $(seq 1 40); do
    if is_running && curl -s -o /dev/null -m 1 "http://$HOST:$PORT/api/settings"; then
      echo "已启动：pid $(cat "$PID_FILE")  http://$HOST:$PORT  日志 $LOG_FILE"
      return 0
    fi
    sleep 0.25
  done
  echo "启动失败，检查日志：$LOG_FILE" >&2
  tail -20 "$LOG_FILE" >&2 || true
  return 1
}

stop() {
  if ! is_running; then
    echo "未在运行"
    rm -f "$PID_FILE"
    return 0
  fi
  local pid
  pid="$(cat "$PID_FILE")"
  kill "$pid" 2>/dev/null || true
  for _ in $(seq 1 40); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.25
  done
  kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null || true
  rm -f "$PID_FILE"
  echo "已停止（已在跑的演化子进程不受影响）"
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  restart) stop; start ;;
  status)
    if is_running; then
      echo "运行中：pid $(cat "$PID_FILE")  http://$HOST:$PORT"
    else
      echo "未运行"
      exit 1
    fi
    ;;
  logs) tail -f "$LOG_FILE" ;;
  *)
    echo "用法：$0 {start|stop|restart|status|logs}" >&2
    exit 2
    ;;
esac
