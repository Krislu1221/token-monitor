#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小米 MiMo Token Plan 登录桥。
弹出独立 Chrome 窗口 → 用户登录 platform.xiaomimimo.com
→ 登录态落盘 .creds/mimo_state.json(600)，并立刻验证三个控制台接口。

用法:
  .venv/bin/python mimo_login.py           # 手动登录(弹窗)
  .venv/bin/python mimo_login.py --auto    # 无头刷新(复用 profile 里的 SSO 会话)
"""
import json
import os
import sys
import time
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
CREDS = os.path.join(BASE, ".creds")
PROFILE = os.path.join(BASE, ".profiles", "mimo")
os.makedirs(CREDS, exist_ok=True)
os.makedirs(PROFILE, exist_ok=True)
os.chmod(CREDS, 0o700)

from playwright.sync_api import sync_playwright

URL = "https://platform.xiaomimimo.com/#/console/balance"
API = "https://platform.xiaomimimo.com/api/v1"
STATE_FILE = os.path.join(CREDS, "mimo_state.json")
MAX_WAIT_S = 15 * 60
REQUIRED = ("api-platform_serviceToken", "userId")
OPTIONAL = ("api-platform_ph", "api-platform_slh")


def collect_cookie_header(state):
    names = {}
    for c in state.get("cookies", []):
        dom = str(c.get("domain", ""))
        if "xiaomimimo" in dom:
            names[c.get("name")] = c.get("value")
    missing = [k for k in REQUIRED if not names.get(k)]
    if missing:
        return None, "缺少 cookie: " + ", ".join(missing)
    return "; ".join(f"{k}={v}" for k, v in names.items() if k in REQUIRED + OPTIONAL), "ok"


def api_get(path, cookie_header):
    """优先复用 poller 的带签名实现（控制台接口已要求 X-Sign），失败再裸 GET 排障。"""
    try:
        import sys
        if BASE not in sys.path:
            sys.path.insert(0, BASE)
        import poller
        return poller._mimo_get(path, cookie_header)
    except Exception:
        pass
    req = urllib.request.Request(
        API + path,
        headers={
            "Cookie": cookie_header,
            "Accept": "application/json",
            "Referer": "https://platform.xiaomimimo.com/",
            "Origin": "https://platform.xiaomimimo.com",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def verify(cookie_header):
    out = {}
    for name, path in (("usage", "/tokenPlan/usage"),
                       ("detail", "/tokenPlan/detail"),
                       ("balance", "/balance")):
        try:
            out[name] = api_get(path, cookie_header)
        except Exception as e:
            out[name] = {"_error": f"{type(e).__name__}: {e}"}
    return out


def summarize(ver):
    """尽量从三个接口里提炼关键数字，用于登录后立即给人看。"""
    s = {}
    usage = (ver.get("usage") or {}).get("data") or {}
    def items_map(section):
        return {str(it.get("name")): it for it in (section or {}).get("items") or []
                if isinstance(it, dict) and it.get("name")}
    cur = items_map(usage.get("usage"))
    mon = items_map(usage.get("monthUsage"))
    s["plan"] = {k: {f: cur[k].get(f) for f in ("used", "limit", "percent")} for k in cur}
    s["month"] = {k: {f: mon[k].get(f) for f in ("used", "limit", "percent")} for k in mon}
    d = (ver.get("detail") or {}).get("data") or {}
    s["detail"] = {k: d.get(k) for k in ("planCode", "planName", "currentPeriodEnd", "expired")}
    b = (ver.get("balance") or {}).get("data") or {}
    s["balance"] = b if isinstance(b, (dict, list)) else b
    return s


def main():
    auto = "--auto" in sys.argv[1:]
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE, channel="chrome", headless=auto,
            viewport={"width": 1280, "height": 880}, locale="zh-CN")
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print(f"打开页面失败: {e}", flush=True)
        if not auto:
            print("窗口已打开(请勿关闭): " + URL, flush=True)
            print("请用小米账号登录;检测到登录态后窗口会自动关闭。", flush=True)

        deadline = time.time() + (60 if auto else MAX_WAIT_S)
        logged = False
        while time.time() < deadline:
            state = ctx.storage_state()
            header, note = collect_cookie_header(state)
            if header:
                # cookie 齐了再用接口验证一次（页面 JS 可能还没设完）
                try:
                    ver = verify(header)
                    if "data" in (ver.get("usage") or {}) or "data" in (ver.get("balance") or {}):
                        logged = True
                        break
                except Exception:
                    pass
            time.sleep(4)

        state = ctx.storage_state()
        header, note = collect_cookie_header(state)
        # 只有拿到完整 cookie 才覆盖状态文件，避免 --auto 刷新失败时把好状态写坏
        if header:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f)
            os.chmod(STATE_FILE, 0o600)
        else:
            print(f"未写入状态文件({note})，保留旧登录态", flush=True)

        result = {"logged_in": logged, "cookie_note": note,
                  "final_url": page.url, "cookies": len(state.get("cookies", []))}
        if header:
            ver = verify(header)
            result["verify_errors"] = {k: v.get("_error") for k, v in ver.items() if "_error" in v}
            result["summary"] = summarize(ver)
        print(json.dumps(result, ensure_ascii=False, indent=1), flush=True)
        ctx.close()
        if not logged:
            print("未确认登录态;请在有界面的终端里重跑 `mimo_login.py` 手动登录。", flush=True)
            sys.exit(2)


if __name__ == "__main__":
    main()
