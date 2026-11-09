#!/bin/bash
# =============================================================================
# Token monitor 启动器
#   双击 App → 确保后台服务在跑（面板 8317 + 菜单栏图标）→ 打开面板 → 自己退出。
#   关闭本 App / 关掉面板页面，监控都继续在后台运行（由 launchd 托管）。
#
# 手动用法： tokenmonitor          # 启动（幂等）
#            tokenmonitor --stop   # 停止后台监控
#            tokenmonitor --status # 查看状态
# =============================================================================
set -u

PANEL="http://127.0.0.1:8317"
UID_NUM="$(id -u)"
MM_DIR="${TOKEN_MONITOR_DIR:-$HOME/model-monitor}"
LA_DIR="$HOME/Library/LaunchAgents"
PREFIX="${TOKEN_MONITOR_LABEL_PREFIX:-com.tokenmonitor}"
POLLER_LABEL="$PREFIX.token-monitor"
MENUBAR_LABEL="$PREFIX.token-monitor-menubar"
PY="$MM_DIR/.venv/bin/python"

notify() { /usr/bin/osascript -e "display notification \"$1\" with title \"Token monitor\"" >/dev/null 2>&1 || true; }
panel_up() { /usr/bin/curl -s -o /dev/null --max-time 3 "$PANEL/api/status"; }
menubar_up() { /usr/bin/pgrep -f "$MM_DIR/menubar.py" >/dev/null 2>&1; }

start_service() { # $1 label  $2 plist
  local label="$1" plist="$2"
  /bin/launchctl kickstart -k "gui/$UID_NUM/$label" >/dev/null 2>&1 && return 0
  /bin/launchctl bootstrap "gui/$UID_NUM" "$plist" >/dev/null 2>&1 || true
  /bin/launchctl kickstart -k "gui/$UID_NUM/$label" >/dev/null 2>&1 && return 0
  return 1
}

stop_service() { # $1 label
  /bin/launchctl kill TERM "gui/$UID_NUM/$1" >/dev/null 2>&1 || true
}

case "${1:-}" in
  --stop)
    stop_service "$MENUBAR_LABEL"
    stop_service "$POLLER_LABEL"
    /usr/bin/pkill -f "$MM_DIR/menubar.py" >/dev/null 2>&1 || true
    /usr/bin/pkill -f "$MM_DIR/poller.py" >/dev/null 2>&1 || true
    notify "后台监控已停止"
    exit 0
    ;;
  --status)
    panel_up && echo "面板(8317): 运行中" || echo "面板(8317): 未运行"
    menubar_up && echo "菜单栏图标: 运行中" || echo "菜单栏图标: 未运行"
    exit 0
    ;;
esac

# ---------- 启动面板后端 ----------
if ! panel_up; then
  start_service "$POLLER_LABEL" "$LA_DIR/$POLLER_LABEL.plist" || {
    ( cd "$MM_DIR" && /usr/bin/nohup "$PY" poller.py >> monitor.log 2>&1 & )
  }
  for _ in $(seq 1 20); do panel_up && break; sleep 0.5; done
fi

# ---------- 启动菜单栏图标 ----------
if ! menubar_up; then
  start_service "$MENUBAR_LABEL" "$LA_DIR/$MENUBAR_LABEL.plist" || {
    ( cd "$MM_DIR" && /usr/bin/nohup "$PY" menubar.py >> menubar.log 2>&1 & )
  }
  sleep 2
fi

# ---------- 打开面板 + 提示 ----------
/usr/bin/open "$PANEL" >/dev/null 2>&1 || true
if panel_up && menubar_up; then
  notify "已在后台运行：顶部菜单栏图标 + 面板（关掉本窗口不影响）"
else
  notify "部分服务未启动成功，请查看 $MM_DIR/monitor.log"
fi

# 启动器本身不需要常驻：退出后由 launchd 继续托管后台监控
exit 0
