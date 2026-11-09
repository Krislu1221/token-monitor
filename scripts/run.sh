#!/bin/bash
# Model Monitor 启停脚本
cd "$(dirname "$0")/.."
PORT="${PORT:-8317}"
PIDFILE="monitor.pid"
PY=python3
[ -x ".venv/bin/python" ] && PY=".venv/bin/python"
export PATH="$HOME/bin:$PATH"

start() {
  if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "已在运行 (pid $(cat "$PIDFILE")) → http://127.0.0.1:$PORT"
    return 0
  fi
  nohup "$PY" poller.py > monitor.log 2>&1 &
  echo $! > "$PIDFILE"
  sleep 1
  echo "已启动 (pid $!) → http://127.0.0.1:$PORT  (日志 monitor.log)"
}

stop() {
  if [ -f "$PIDFILE" ]; then
    kill "$(cat "$PIDFILE")" 2>/dev/null && rm -f "$PIDFILE" && echo "已停止"
  else
    echo "未在运行"
  fi
}

case "${1:-start}" in
  start) start ;;
  stop)  stop ;;
  restart) stop; sleep 1; start ;;
  *) echo "用法: $0 {start|stop|restart}" ;;
esac
