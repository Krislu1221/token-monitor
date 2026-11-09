#!/bin/bash
# =============================================================================
# Token Monitor 安装器（macOS）
#   生成 launchd plist → 注册开机自启 → 启动数据服务与菜单栏
#
# 用法:
#   ./scripts/install.sh              # 安装并立即启动（数据服务 + 菜单栏）
#   ./scripts/install.sh --panel      # 只启动网页面板（前台，Ctrl-C 结束）
#   ./scripts/install.sh unload       # 移除自启并停止
#   ./scripts/install.sh status       # 查看运行状态
#
# 环境变量:
#   TOKEN_MONITOR_LABEL_PREFIX   launchd 标签前缀（默认 com.tokenmonitor）
#   PORT                         面板端口（默认 8317）
# =============================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UID_NUM="$(id -u)"
AG="$HOME/Library/LaunchAgents"
PREFIX="${TOKEN_MONITOR_LABEL_PREFIX:-com.tokenmonitor}"
SVC="$PREFIX.token-monitor"
BAR="$PREFIX.token-monitor-menubar"
PORT="${PORT:-8317}"
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

mkdir -p "$AG" "$ROOT/.creds"

# ---------------------------------------------------------------- plist 生成
write_plist() { # $1=label $2=target(poller.py|menubar.py) $3=LogPrefix
  local label="$1" target="$2" logp="$3"
  cat > "$AG/$label.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array><string>$PY</string><string>$ROOT/$target</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>EnvironmentVariables</key>
  <dict><key>PORT</key><string>$PORT</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key>$4
  <key>StandardOutPath</key><string>$ROOT/$logp.out.log</string>
  <key>StandardErrorPath</key><string>$ROOT/$logp.err.log</string>
</dict>
</plist>
PLIST
}

unload_one() {
  launchctl bootout "gui/$UID_NUM/$1" 2>/dev/null || true
}

start_one() { # $1=label
  local label="$1"
  launchctl bootstrap "gui/$UID_NUM" "$AG/$label.plist" 2>/dev/null || true
  launchctl enable "gui/$UID_NUM/$label" 2>/dev/null || true
  launchctl kickstart -k "gui/$UID_NUM/$label" 2>/dev/null || true
}

deps_check() {
  if [ ! -x "$ROOT/.venv/bin/python" ]; then
    echo "⚠️  未发现 .venv，将使用系统 python3 ($PY)"
    echo "   建议先执行: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  fi
  if ! "$PY" -c "import rumps" 2>/dev/null; then
    echo "⚠️  缺少菜单栏依赖 rumps。执行: .venv/bin/pip install -r requirements.txt"
    echo "   只想要网页面板的话不受影响。"
  fi
}

case "${1:-install}" in
  --panel|panel)
    echo "==> 前台启动网页面板: http://127.0.0.1:$PORT  (Ctrl-C 结束)"
    cd "$ROOT" && PORT="$PORT" "$PY" poller.py
    ;;

  install|load|"")
    deps_check
    echo "==> 写入 launchd plist（标签前缀: $PREFIX）"
    # 数据服务常驻；菜单栏崩溃即重启
    write_plist "$SVC" "poller.py" "poller" "true"
    if [ "${WITH_MENUBAR:-1}" = "1" ]; then
      write_plist "$BAR" "menubar.py" "menubar" "true"
    fi

    echo "==> 停止旧实例，避免重复"
    pkill -f "$ROOT/poller.py" 2>/dev/null || true
    pkill -f "$ROOT/menubar.py" 2>/dev/null || true
    unload_one "$SVC"; unload_one "$BAR"
    sleep 1

    echo "==> 启动"
    start_one "$SVC"
    [ "${WITH_MENUBAR:-1}" = "1" ] && start_one "$BAR"
    sleep 3

    echo
    echo "✅ 安装完成"
    echo "   · 网页面板   http://127.0.0.1:$PORT   （仅本机 127.0.0.1，不对外开放）"
    echo "   · 菜单栏     $([ "${WITH_MENUBAR:-1}" = "1" ] && echo '顶部图标已常驻（不占 Dock）' || echo '已跳过')"
    echo "   · 停止       ./scripts/install.sh unload"
    ;;

  unload|uninstall|stop)
    echo "==> 移除自启并停止"
    unload_one "$SVC"; unload_one "$BAR"
    pkill -f "$ROOT/poller.py" 2>/dev/null || true
    pkill -f "$ROOT/menubar.py" 2>/dev/null || true
    rm -f "$AG/$SVC.plist" "$AG/$BAR.plist"
    echo "✅ 已停止"
    ;;

  status)
    echo "==> 服务状态"
    launchctl print "gui/$UID_NUM/$SVC" 2>/dev/null | sed -n '1,8p' || echo "数据服务未加载"
    launchctl print "gui/$UID_NUM/$BAR" 2>/dev/null | sed -n '1,8p' || echo "菜单栏未加载"
    echo "==> 面板探活"
    curl -s -o /dev/null -w "   HTTP %{http_code}\n" --max-time 5 "http://127.0.0.1:$PORT/api/status" \
      || echo "   面板未响应"
    ;;

  *)
    echo "用法: $0 [install|unload|status|--panel]"
    ;;
esac
