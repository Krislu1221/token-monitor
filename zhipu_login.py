#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智谱登录桥 v2(一次性): 弹出独立 Chrome 窗口 → 用户在窗口内完成登录
(手机号验证码 / 微信扫码)。判定条件(避免误判):
  1) 当前 URL 已离开 /login
  2) 页面出现「退出登录/退出」
两者都满足才落盘并结束, 窗口在此之前一直保持打开。
"""
import json
import os
import time

BASE = os.path.dirname(os.path.abspath(__file__))
CREDS = os.path.join(BASE, ".creds")
PROFILE = os.path.join(BASE, ".profiles", "zhipu")
os.makedirs(CREDS, exist_ok=True)
os.makedirs(PROFILE, exist_ok=True)
os.chmod(CREDS, 0o700)

from playwright.sync_api import sync_playwright

CONSOLE_URL = "https://open.bigmodel.cn/console/"
MAX_WAIT_S = 15 * 60

def main():
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE, channel="chrome", headless=False,
            viewport={"width": 1280, "height": 860}, locale="zh-CN",
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(CONSOLE_URL, wait_until="domcontentloaded", timeout=60000)
        page.screenshot(path=os.path.join(CREDS, "zhipu_v2_initial.png"))
        print("窗口已打开(请勿关闭): " + CONSOLE_URL, flush=True)
        print("请在窗口里用 手机号验证码 或 微信扫码 登录;登录成功后窗口会自动关闭。", flush=True)
        print("最多等待 15 分钟…", flush=True)

        deadline = time.time() + MAX_WAIT_S
        logged = False
        while time.time() < deadline:
            try:
                url = page.url
                if "login" not in url.lower() and not url.rstrip("/").endswith("/console"):
                    # 仍在登录途中(可能是验证页),继续等
                    pass
                body = page.inner_text("body", timeout=4000)
                if "login" not in url.lower() and ("退出" in body):
                    logged = True
                    break
            except Exception:
                pass
            time.sleep(4)

        state = ctx.storage_state()
        state_path = os.path.join(CREDS, "zhipu_state.json")
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.chmod(state_path, 0o600)
        page.screenshot(path=os.path.join(CREDS, "zhipu_v2_after.png"))
        print(json.dumps({
            "logged_in": logged,
            "final_url": page.url,
            "cookies": len(state.get("cookies", [])),
            "state_file": state_path,
        }, ensure_ascii=False), flush=True)
        if logged:
            print("登录成功,登录态已落盘。", flush=True)
        else:
            print("超时未检测到登录完成。若你其实已登录,请告诉我,我再校验一次。", flush=True)
        ctx.close()

if __name__ == "__main__":
    main()
