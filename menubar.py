#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Model Monitor 菜单栏常驻(rumps)。
菜单栏标题实时显示 DeepSeek 余额;下拉看全部平台;点击项打开面板。
环境: ~/model-monitor/.venv  (pip install rumps)
"""
import json
import os
import urllib.request
import webbrowser

import rumps

PANEL = "http://127.0.0.1:8317"
API = PANEL + "/api/status"
ORDER = ["deepseek", "zhipu", "volc", "qwen", "minimax", "mimo", "tencent"]

def load_disabled_balances():
    """config.json 的 disabled_balances：这些平台不建「余额」行，只留套餐行"""
    import json as _json
    cfgp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    try:
        with open(cfgp, encoding="utf-8") as f:
            _d = _json.load(f) or {}
        return {str(x).strip() for x in (_d.get("disabled_balances") or [])}
    except Exception:
        return set()


def load_disabled():
    """与 poller 同一份 config.json: disabled_providers 里的平台不建菜单行"""
    import json as _json
    cfgp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    try:
        with open(cfgp, encoding="utf-8") as f:
            d = _json.load(f) or {}
        return {str(x).strip() for x in (d.get("disabled_providers") or [])}
    except Exception:
        return set()

def fetch():
    with urllib.request.urlopen(API, timeout=8) as r:
        return json.loads(r.read().decode("utf-8"))

def _fmt_pct(v):
    try:
        v = float(v)
    except Exception:
        return "—"
    # 火山套餐从 0.02% 到 95% 都有，小于 10% 时保留 1 位小数
    return f"{v:.1f}%" if v < 10 else f"{v:.0f}%"

_PERIOD_SHORT = {"weekly": "w", "monthly": "m", "session": "5h", "daily": "d", "5h": "5h", "w": "w", "m": "m", "d": "d"}

def _fmt_periods(periods):
    out = []
    for p in periods or []:
        if not isinstance(p.get("pct"), (int, float)):
            continue
        label = _PERIOD_SHORT.get(str(p.get("label", "")), p.get("label", "?"))
        out.append(f"{label} {_fmt_pct(p['pct'])}")
    return " ".join(out)

def short(prov):
    a = prov.get("auto") or {}
    pid = prov["id"]
    if pid == "deepseek":
        if a.get("ok") and a.get("balance_infos"):
            b = a["balance_infos"][0]
            return f"DeepSeek: ¥{float(b['total']):.2f}"
        return "DeepSeek: 查询失败"
    if pid == "zhipu":
        if a.get("ok") and a.get("available") is not None:
            return f"智谱: ¥{float(a['available']):.2f}"
        if a.get("needs_login"):
            return "智谱: 登录态失效"
        return "智谱: 待配置"
    if pid == "volc":
        if a.get("needs_login"):
            return "火山AP: SSO 需重登"
        if a.get("needs_setup"):
            return "火山AP: 未启用(arkcli)"
        if a.get("ok") and a.get("subscribed"):
            ps = _fmt_periods(a.get("periods"))
            return f"火山AP: {ps}" if ps else "火山AP: 无数据"
        return "火山AP: 查询失败"
    if pid == "qwen":
        if a.get("ok") and a.get("available") is not None:
            return f"千问: ¥{float(a['available']):.2f}"
        if a.get("needs_setup"):
            return "千问: 待配 AK/SK"
        mv = prov.get("manual")
        return f"千问: 备忘 {mv['value']}" if mv and mv.get("value") else "千问: 待配置"
    if pid == "tencent":
        b = a.get("balance") or {}
        if b.get("ok") and b.get("available") is not None:
            return f"腾讯: ¥{float(b['available']):.2f}"
        if a.get("subscribed"):
            ps = _fmt_periods(a.get("periods"))
            return f"腾讯: {ps}" if ps else "腾讯: 套餐生效中"
        if a.get("needs_setup"):
            return "腾讯: 待配密钥"
        if b.get("needs_permission"):
            return "腾讯: 缺 billing 权限"
        if a.get("subscribed") is False:
            return "腾讯: 未购套餐"
        return "腾讯: 查询失败"
    if pid == "minimax":
        if a.get("balance_off"):
            return None  # 余额已关：不显示主行，只留 MiniMax M 行
        if a.get("ok") and a.get("available") is not None:
            return f"MiniMax: ¥{float(a['available']):.2f}"
        if a.get("needs_login"):
            return "MiniMax: 需重登"
        if a.get("ok") and a.get("subscribed") is False:
            return "MiniMax: 无订阅"
        return "MiniMax: 查询失败"
    if pid == "mimo":
        if a.get("balance_off") and a.get("balance") is None:
            return None  # 余额已关：只留 MiMo TP 行
        if a.get("balance") is not None:
            return f"MiMo: ¥{float(a['balance']):.2f}"
        if a.get("needs_login"):
            return "MiMo: 需重登"
        return "MiMo: 查询失败"
    return f"{prov['label']}: —"

def volc_coding_short(prov):
    """火山的 Coding Plan 单独一行：火山CP 5h 0.3% w 0.0% m 0.0%"""
    if not prov:
        return "火山CP: 缺失"
    c = (prov.get("auto") or {}).get("coding")
    if not c:
        return "火山CP: 待查询"
    if c.get("ok") and c.get("subscribed"):
        ps = _fmt_periods(c.get("periods"))
        return f"火山CP: {ps}" if ps else "火山CP: 无数据"
    if c.get("ok") and c.get("subscribed") is False:
        return "火山CP: 未订阅"
    if c.get("needs_permission") or c.get("needs_login"):
        return "火山CP: 需授权(补权限/重登)"
    return f"火山CP: {(c.get('error') or '查询失败')[:60]}"

def minimax_plan_short(prov):
    """MiniMax M Plan 单独一行：MiniMax M: 5h 4% w 1%"""
    if not prov:
        return "MiniMax M: 缺失"
    c = (prov.get("auto") or {}).get("plan")
    if not c:
        return "MiniMax M: 待查询"
    if c.get("ok") and c.get("subscribed"):
        ps = _fmt_periods(c.get("periods"))
        return f"MiniMax M: {ps}" if ps else "MiniMax M: 无数据"
    if c.get("ok") and c.get("subscribed") is False:
        return "MiniMax M: 未订阅"
    if c.get("needs_key"):
        return "MiniMax M: 缺 key"
    return f"MiniMax M: {(c.get('error') or '查询失败')[:60]}"

def mimo_plan_short(prov):
    """MiMo Token Plan 单独一行：MiMo TP: 12.3% 月 4.5%"""
    if not prov:
        return "MiMo TP: 缺失"
    a = prov.get("auto") or {}
    if not a:
        return "MiMo TP: 待查询"
    if a.get("needs_login"):
        return "MiMo TP: 需重登"
    periods = a.get("periods") or []
    plan = next((x for x in periods if x.get("label") == "plan"), None)
    month = next((x for x in periods if x.get("label") == "month"), None)
    if plan and isinstance(plan.get("pct"), (int, float)):
        s = f"MiMo TP: {_fmt_pct(plan['pct'])}"
        if month and isinstance(month.get("pct"), (int, float)):
            s += f" 月 {_fmt_pct(month['pct'])}"
        return s
    if a.get("ok") and a.get("subscribed") is False:
        return "MiMo TP: 未订阅"
    return f"MiMo TP: {(a.get('error') or '查询失败')[:60]}"

def volc_balance_short(prov):
    """火山按量付费余额单独一行：火山余额: ¥19.87"""
    if not prov:
        return "火山余额: 缺失"
    b = (prov.get("auto") or {}).get("balance")
    if not b:
        return "火山余额: 待查询"
    if b.get("ok") and b.get("available") is not None:
        return f"火山余额: ¥{float(b['available']):.2f}"
    if b.get("needs_permission"):
        return "火山余额: 缺 billing 权限"
    if b.get("needs_setup"):
        return "火山余额: 未配 AK/SK"
    return f"火山余额: {(b.get('error') or '查询失败')[:50]}"

class ModelMonitorApp(rumps.App):
    def __init__(self):
        icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "menubar.png")
        super().__init__("Model Monitor", title="…", icon=icon, template=True)
        self.quit_button = None
        # 每个 provider 一行；火山在 AP 行下面再加一行 Coding Plan
        self.items_by_id = {}
        self.volc_coding_item = None
        self.volc_balance_item = None
        self.minimax_plan_item = None
        self.mimo_plan_item = None
        menu_items = []
        self.active = [p for p in ORDER if p not in load_disabled()]
        self.bal_off = load_disabled_balances()
        for pid in self.active:
            if pid in self.bal_off:
                # 余额已关：不建主行，只保留该平台的套餐行
                if pid == "minimax":
                    self.minimax_plan_item = rumps.MenuItem("MiniMax M: 加载中…")
                    menu_items.append(self.minimax_plan_item)
                elif pid == "mimo":
                    self.mimo_plan_item = rumps.MenuItem("MiMo TP: 加载中…")
                    menu_items.append(self.mimo_plan_item)
                continue
            item = rumps.MenuItem("加载中…")
            self.items_by_id[pid] = item
            menu_items.append(item)
            if pid == "volc":
                self.volc_coding_item = rumps.MenuItem("火山CP: 加载中…")
                menu_items.append(self.volc_coding_item)
                self.volc_balance_item = rumps.MenuItem("火山余额: 加载中…")
                menu_items.append(self.volc_balance_item)
            if pid == "minimax":
                self.minimax_plan_item = rumps.MenuItem("MiniMax M: 加载中…")
                menu_items.append(self.minimax_plan_item)
            if pid == "mimo":
                self.mimo_plan_item = rumps.MenuItem("MiMo TP: 加载中…")
                menu_items.append(self.mimo_plan_item)
        self.provider_items = menu_items
        self.updated_item = rumps.MenuItem("—")
        self.menu = menu_items + [
            rumps.MenuItem("打开面板 ↗", callback=self._open_panel),
            rumps.MenuItem("调用明细 ↗", callback=self._open_calls),
            rumps.MenuItem("按 Agent 拆分 ↗", callback=self._open_agents),
            rumps.MenuItem("对话体检 ↗", callback=self._open_convo),
            rumps.MenuItem("立即刷新", callback=self._refresh),
            self.updated_item,
            rumps.MenuItem("退出", callback=rumps.quit_application),
        ]
        self.timer = rumps.Timer(self.tick, 60)
        self.timer.start()
        self.refresh_now()

    def refresh_now(self):
        try:
            data = fetch()
            byid = {p["id"]: p for p in data.get("providers", [])}
            for pid in self.active:
                item = self.items_by_id.get(pid)
                if item is None:
                    continue  # 余额已关的平台没有主行
                p = byid.get(pid)
                label = short(p) if p else f"{pid}: 缺失"
                if label is None:
                    continue
                item.title = label
            if self.volc_coding_item is not None:
                self.volc_coding_item.title = volc_coding_short(byid.get("volc"))
            if self.volc_balance_item is not None:
                self.volc_balance_item.title = volc_balance_short(byid.get("volc"))
            if self.minimax_plan_item is not None:
                self.minimax_plan_item.title = minimax_plan_short(byid.get("minimax"))
            if self.mimo_plan_item is not None:
                self.mimo_plan_item.title = mimo_plan_short(byid.get("mimo"))
            ds = byid.get("deepseek")
            if ds and (ds.get("auto") or {}).get("ok") and ds["auto"].get("balance_infos"):
                self.title = f"¥{float(ds['auto']['balance_infos'][0]['total']):.2f}"
            else:
                self.title = "面板离线"
            self.updated_item.title = f"更新于 {data.get('updated_at','?')}"
        except Exception as e:
            self.title = "离线"
            self.updated_item.title = f"刷新失败: {e}"

    def tick(self, _):
        self.refresh_now()

    def _open_panel(self, _):
        webbrowser.open(PANEL)

    def _open_agents(self, _):
        webbrowser.open(PANEL + "/agents")

    def _open_calls(self, _):
        webbrowser.open(PANEL + "/calls")

    def _open_convo(self, _):
        webbrowser.open(PANEL + "/convo")

    def _refresh(self, _):
        try:
            with urllib.request.urlopen(PANEL + "/api/poll", timeout=15) as r:
                r.read()
        except Exception:
            pass
        self.refresh_now()

if __name__ == "__main__":
    # Accessory 模式: 只在菜单栏显示图标, 不占 Dock / Cmd-Tab
    try:
        from AppKit import NSApplication
        NSApplication.sharedApplication().setActivationPolicy_(1)  # 1 = NSApplicationActivationPolicyAccessory
    except Exception:
        pass
    ModelMonitorApp().run()
