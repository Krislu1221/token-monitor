#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MiniMax 登录桥: 弹出独立 Chrome 窗口 → 用户登录 platform.minimaxi.com
→ 登录态落盘 .creds/minimax_state.json(600)。检测到登录成功窗口即自动关闭。
"""
import json
import os
import time

BASE = os.path.dirname(os.path.abspath(__file__))
CREDS = os.path.join(BASE, ".creds")
PROFILE = os.path.join(BASE, ".profiles", "minimax")
os.makedirs(CREDS, exist_ok=True)
os.makedirs(PROFILE, exist_ok=True)
os.chmod(CREDS, 0o700)

from playwright.sync_api import sync_playwright

URL = "https://platform.minimax.cn/console/recharge-records"
MAX_WAIT_S = 15 * 60

def main():
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE, channel="chrome", headless=False,
            viewport={"width": 1280, "height": 880}, locale="zh-CN")
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        print("窗口已打开(请勿关闭): " + URL, flush=True)
        print("请用 MiniMax 账号登录(短信/扫码);登录成功后窗口会自动关闭。", flush=True)

        deadline = time.time() + MAX_WAIT_S
        logged = False
        while time.time() < deadline:
            try:
                url = page.url
                body = page.inner_text("body", timeout=4000)
                # 严格判定: 钱包页出现「可用额度/账户钱包」且不出现「立即登录」
                authed = ("可用额度" in body or "账户钱包" in body) and ("立即登录" not in body)
                if authed and "login" not in url.lower():
                    logged = True
                    break
            except Exception:
                pass
            time.sleep(4)

        state = ctx.storage_state()
        sp = os.path.join(CREDS, "minimax_state.json")
        with open(sp, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.chmod(sp, 0o600)
        print(json.dumps({"logged_in": logged, "final_url": page.url,
                          "cookies": len(state.get("cookies", []))}, ensure_ascii=False), flush=True)
        if not logged:
            print("超时未确认登录态;若你已登录请告知,我再校验。", flush=True)
        ctx.close()

if __name__ == "__main__":
    main()
